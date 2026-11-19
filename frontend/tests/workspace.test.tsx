import { afterEach, describe, expect, it, vi } from 'vitest';
import { Profiler, StrictMode, act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { NodeDrawer } from '../src/components/NodeDrawer';
import { JobProgress } from '../src/components/JobProgress';
import { applyNodeChanges, useGraphStore } from '../src/store/useGraphStore';
import type { CampaignView } from '../src/lib/types';
import { requestKey } from '../src/lib/workspace';
(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
const campaign: CampaignView = {
  id: 'campaign',
  title: 'Harbour',
  acts: [{ id: 'act', title: 'Act', summary: 'original', npc_ids: [], scenes: [] }],
  npcs: {},
  clues: [],
};
let root: Root;
let container: HTMLDivElement;
afterEach(async () => {
  if (root) await act(async () => root.unmount());
  container?.remove();
  vi.unstubAllGlobals();
});
function mount(component: React.ReactNode) {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => root.render(component));
}
function input(el: HTMLInputElement, value: string) {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(el, value);
  el.dispatchEvent(new Event('input', { bubbles: true }));
}

describe('durable editor and task adoption', () => {
  it('saves domain fields with the baseline revision, then adopts the complete campaign', async () => {
    useGraphStore.getState().adoptCampaign(campaign, 1, false);
    useGraphStore.getState().selectNode('act');
    const fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        campaign: { ...campaign, acts: [{ ...campaign.acts[0], title: 'Adopted' }] },
        revision: 2,
      }),
    });
    vi.stubGlobal('fetch', fetch);
    mount(createElement(NodeDrawer));
    await act(async () => input(container.querySelector('input')!, 'Adopted'));
    await act(async () =>
      [...container.querySelectorAll('button')].find((b) => b.textContent === '保存')!.click(),
    );
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({
      expected_revision: 1,
      patch: { title: 'Adopted', summary: 'original' },
    });
    expect(useGraphStore.getState().campaign!.acts[0].title).toBe('Adopted');
    expect(useGraphStore.getState().revision).toBe(2);
    expect(container.textContent).toContain('已保存版本 2');
  });
  it('preserves a conflicting draft and never shows saved', async () => {
    useGraphStore.getState().adoptCampaign(campaign, 1, false);
    useGraphStore.getState().selectNode('act');
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 409,
        json: async () => ({ detail: 'campaign changed' }),
      }),
    );
    mount(createElement(NodeDrawer));
    await act(async () => input(container.querySelector('input')!, 'Unsaved'));
    await act(async () =>
      [...container.querySelectorAll('button')].find((b) => b.textContent === '保存')!.click(),
    );
    expect(container.querySelector('input')!.value).toBe('Unsaved');
    expect(container.textContent).toContain('campaign changed');
    expect(useGraphStore.getState().revision).toBe(1);
    expect(container.textContent).not.toContain('已保存版本');
  });
  it('StrictMode subscriptions never POST and terminal event adopts every node', async () => {
    const sources: any[] = [];
    class FakeSource {
      onmessage: any;
      onerror: any;
      close = vi.fn();
      constructor(public url: string) {
        sources.push(this);
      }
    }
    vi.stubGlobal('EventSource', FakeSource);
    const fetch = vi
      .fn()
      .mockResolvedValue({ ok: true, json: async () => ({ job: { id: 'j', status: 'running' } }) });
    vi.stubGlobal('fetch', fetch);
    const finish = vi.fn();
    mount(
      createElement(
        StrictMode,
        null,
        createElement(JobProgress, { jobId: 'j', onTerminal: finish }),
      ),
    );
    await act(async () => {});
    expect(fetch.mock.calls.every((c) => !c[1]?.method || c[1].method === 'GET')).toBe(true);
    await act(async () =>
      sources.at(-1).onmessage({
        lastEventId: '3',
        data: JSON.stringify({ done: true, status: 'completed', campaign, revision: 4 }),
      }),
    );
    expect(useGraphStore.getState().nodes.map((n) => n.id)).toEqual(['act']);
    expect(useGraphStore.getState().revision).toBe(4);
    expect(finish).toHaveBeenCalledOnce();
  });
  it('movement preserves content references and undo preserves later movement', () => {
    const store = useGraphStore.getState();
    store.adoptCampaign(campaign, 1, false);
    const content = useGraphStore.getState().nodes[0].data;
    store.onNodesChange([{ type: 'position', id: 'act', position: { x: 50, y: 60 } }]);
    expect(useGraphStore.getState().nodes[0].data).toBe(content);
    store.updateNodeData('act', { summary: 'edited' });
    store.onNodesChange([{ type: 'position', id: 'act', position: { x: 80, y: 90 } }]);
    store.undo();
    expect(useGraphStore.getState().nodes[0].position).toEqual({ x: 80, y: 90 });
    expect(useGraphStore.getState().nodes[0].data).toBe(content);
    expect(
      applyNodeChanges([{ type: 'remove', id: 'missing' }], useGraphStore.getState().nodes)[0],
    ).toBe(useGraphStore.getState().nodes[0]);
  });
  it('a delayed terminal frame cannot replace a newer adopted revision', async () => {
    const sources: any[] = [];
    class FakeSource {
      onmessage: any;
      onerror: any;
      close = vi.fn();
      constructor() {
        sources.push(this);
      }
    }
    vi.stubGlobal('EventSource', FakeSource);
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ job: { id: 'j', status: 'running' } }),
      }),
    );
    const newer = { ...campaign, title: 'Adopted newer' };
    useGraphStore.getState().adoptCampaign(newer, 2, false);
    mount(createElement(JobProgress, { jobId: 'j', onTerminal: vi.fn() }));
    await act(async () => {});
    await act(async () =>
      sources.at(-1).onmessage({
        lastEventId: '2',
        data: JSON.stringify({ done: true, status: 'completed', campaign, revision: 1 }),
      }),
    );
    expect(useGraphStore.getState().revision).toBe(2);
    expect(useGraphStore.getState().campaign!.title).toBe('Adopted newer');
  });
  it('creates retry keys without secure-context randomUUID', () => {
    vi.stubGlobal('crypto', { getRandomValues: (b: Uint8Array) => b.fill(123) });
    expect(requestKey()).toMatch(/^[a-z0-9]+-(7b){16}$/);
    expect(requestKey().length).toBeGreaterThan(32);
  });
  it('dragging selected nodes does not render the open editor or lose its draft', async () => {
    useGraphStore.getState().adoptCampaign(campaign, 1, false);
    useGraphStore.getState().selectNode('act');
    const rendered = vi.fn();
    mount(createElement(Profiler, { id: 'editor', onRender: rendered }, createElement(NodeDrawer)));
    await act(async () => input(container.querySelector('input')!, 'Unsaved draft'));
    rendered.mockClear();
    await act(async () => {
      for (let i = 1; i <= 50; i++)
        useGraphStore
          .getState()
          .onNodesChange([{ type: 'position', id: 'act', position: { x: i, y: i } }]);
    });
    expect(rendered).not.toHaveBeenCalled();
    expect(container.querySelector('input')!.value).toBe('Unsaved draft');
  });
  it('measures nodes without changing fit identity; repeated changes notify no subscribers', () => {
    const store = useGraphStore.getState();
    store.adoptCampaign(campaign, 1, false);
    const structure = useGraphStore.getState().structureKey;
    store.onNodesChange([
      { type: 'dimensions', id: 'act', dimensions: { width: 280, height: 100 } },
    ]);
    expect(useGraphStore.getState().nodes[0].measured).toEqual({ width: 280, height: 100 });
    expect(useGraphStore.getState().structureKey).toBe(structure);
    const before = useGraphStore.getState().nodes;
    const notified = vi.fn();
    const unsubscribe = useGraphStore.subscribe(notified);
    store.onNodesChange([
      { type: 'dimensions', id: 'act', dimensions: { width: 280, height: 100 } },
    ]);
    store.onNodesChange([{ type: 'remove', id: 'missing' }]);
    unsubscribe();
    expect(useGraphStore.getState().nodes).toBe(before);
    expect(notified).not.toHaveBeenCalled();
  });
});
