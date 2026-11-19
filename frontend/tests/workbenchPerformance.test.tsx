import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { WorkbenchView } from '../src/site/WorkbenchView';
import { useGraphStore } from '../src/store/useGraphStore';

const graphRender = vi.hoisted(() => vi.fn());
vi.mock('../src/App', () => ({
  default: () => {
    graphRender();
    return null;
  },
}));
(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
let root: Root;
let container: HTMLDivElement;
afterEach(async () => {
  await act(async () => root?.unmount());
  container?.remove();
  vi.unstubAllGlobals();
});

describe('workbench input isolation', () => {
  it('typing preparation and session notes never renders the graph or refetches catalogs', async () => {
    localStorage.clear();
    useGraphStore
      .getState()
      .adoptCampaign({ id: 'c1', title: 'Harbour', acts: [], npcs: {}, clues: [] }, 1, false);
    const fetch = vi
      .fn()
      .mockResolvedValue({ ok: true, json: async () => ({ modules: [], campaigns: [] }) });
    vi.stubGlobal('fetch', fetch);
    container = document.createElement('div');
    document.body.append(container);
    root = createRoot(container);
    await act(async () => root.render(createElement(WorkbenchView)));
    graphRender.mockClear();
    fetch.mockClear();
    const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!;
    for (const label of ['模组文本', '实际游玩记录', '确认事实']) {
      const field = container.querySelector(`textarea[aria-label="${label}"]`)!;
      await act(async () => {
        for (let i = 1; i <= 20; i++) {
          setter.call(field, '调查记录'.repeat(i));
          field.dispatchEvent(new Event('input', { bubbles: true }));
        }
      });
    }
    expect(graphRender).not.toHaveBeenCalled();
    expect(fetch).not.toHaveBeenCalled();
  });
});
