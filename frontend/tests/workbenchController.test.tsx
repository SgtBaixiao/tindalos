import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { WorkbenchView } from '../src/site/WorkbenchView';
import { useGraphStore } from '../src/store/useGraphStore';

vi.mock('../src/App', () => ({ default: () => null }));
(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
const initial = { id: 'c1', title: 'Current', acts: [], npcs: {}, clues: [] };
const opened = { ...initial, id: 'c2', title: 'Opened' };
const reply = (body: unknown, status = 200) => ({
  ok: status < 400,
  status,
  json: async () => body,
});
let root: Root | undefined;
let container: HTMLDivElement;
let sources: FakeSource[];
class FakeSource {
  onmessage: any;
  onerror: any;
  close = vi.fn();
  constructor(public url: string) {
    sources.push(this);
  }
}
beforeEach(() => {
  localStorage.clear();
  sources = [];
  useGraphStore.getState().adoptCampaign(initial, 7, false);
  vi.stubGlobal('EventSource', FakeSource);
});
afterEach(async () => {
  if (root) await act(async () => root!.unmount());
  root = undefined;
  container?.remove();
  vi.unstubAllGlobals();
});
function fetchBase(url: string, init?: RequestInit) {
  if (url === '/api/modules') return reply({ modules: [{ id: 'm1', filename: 'Source' }] });
  if (url === '/api/campaigns') return reply({ campaigns: [initial, opened] });
  if (url === '/api/campaigns/c2') return reply({ campaign: opened, meta: { revision: 11 } });
  if (url === '/api/jobs' && init?.method === 'POST')
    return reply({ job: { id: 'j', status: 'queued' } }, 202);
  if (url === '/api/jobs/j') return reply({ job: { id: 'j', status: 'running' } });
  throw new Error(`Unexpected request: ${url}`);
}
async function mount(fetch: ReturnType<typeof vi.fn>) {
  vi.stubGlobal('fetch', fetch);
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root!.render(createElement(WorkbenchView)));
}
async function typeText(value: string) {
  const field = container.querySelector('textarea[aria-label="模组文本"]')!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(
      field,
      value,
    );
    field.dispatchEvent(new Event('input', { bubbles: true }));
  });
}
async function click(label: string) {
  await act(async () => {
    [...container.querySelectorAll('button')].find((b) => b.textContent!.includes(label))!.click();
  });
}
const postCalls = (fetch: ReturnType<typeof vi.fn>) =>
  fetch.mock.calls.filter(([url, init]) => url === '/api/jobs' && init?.method === 'POST');
async function selectCampaign() {
  await act(async () => {
    const select = container.querySelector<HTMLSelectElement>(
      'select[aria-label="打开已保存剧本"]',
    )!;
    select.value = 'c2';
    select.dispatchEvent(new Event('change', { bubbles: true }));
  });
}

it('retains the retry key for identical failed input, changes it for changed input, and persists the admitted job', async () => {
  let attempts = 0;
  const fetch = vi.fn((url: string, init?: RequestInit) => {
    if (url === '/api/jobs' && ++attempts < 3) return reply({ detail: 'temporary failure' }, 503);
    return fetchBase(url, init);
  });
  await mount(fetch);
  await typeText('Original');
  await click('生成备团材料');
  await click('生成备团材料');
  await typeText('Changed');
  await click('生成备团材料');
  const calls = postCalls(fetch);
  expect(calls).toHaveLength(3);
  expect(calls[0][1].headers['Idempotency-Key']).toBe(calls[1][1].headers['Idempotency-Key']);
  expect(calls[2][1].headers['Idempotency-Key']).not.toBe(calls[1][1].headers['Idempotency-Key']);
  expect(JSON.parse(calls[2][1].body)).toEqual({ module_text: 'Changed', llm: false });
  expect(localStorage.getItem('tindalos-active-job')).toBe('j');
});

it('submits continuation using the adopted campaign and revision', async () => {
  const fetch = vi.fn(fetchBase);
  await mount(fetch);
  await click('准备下一场');
  expect(JSON.parse(postCalls(fetch)[0][1].body)).toEqual({
    campaign_id: 'c1',
    expected_revision: 7,
    llm: false,
  });
});

it('opens the full saved revision and disables preparation while loading', async () => {
  let resolve!: (value: unknown) => void;
  const loading = new Promise((r) => {
    resolve = r;
  });
  const fetch = vi.fn((url: string, init?: RequestInit) =>
    url === '/api/campaigns/c2' ? loading : fetchBase(url, init),
  );
  await mount(fetch);
  await selectCampaign();
  expect(
    container.querySelector<HTMLTextAreaElement>('textarea[aria-label="模组文本"]')!.disabled,
  ).toBe(true);
  await act(async () => resolve(reply({ campaign: opened, meta: { revision: 11 } })));
  expect(useGraphStore.getState().campaign?.title).toBe('Opened');
  expect(useGraphStore.getState().revision).toBe(11);
  expect(container.textContent).toContain('当前保存版本 11');
  expect(postCalls(fetch)).toHaveLength(0);
});

it('keeps the adopted revision on an open failure and releases opening controls', async () => {
  const fetch = vi.fn((url: string, init?: RequestInit) =>
    url === '/api/campaigns/c2' ? reply({ detail: 'cannot open' }, 500) : fetchBase(url, init),
  );
  await mount(fetch);
  await selectCampaign();
  expect(useGraphStore.getState().campaignId).toBe('c1');
  expect(useGraphStore.getState().revision).toBe(7);
  expect(container.querySelector('[role="alert"]')!.textContent).toContain('cannot open');
  expect(
    container.querySelector<HTMLTextAreaElement>('textarea[aria-label="模组文本"]')!.disabled,
  ).toBe(false);
});

it('waits for terminal cancellation before clearing active storage and retains the terminal progress display', async () => {
  localStorage.setItem('tindalos-active-job', 'j');
  const fetch = vi.fn(fetchBase);
  await mount(fetch);
  await click('取消任务');
  expect(
    fetch.mock.calls.some(([url, init]) => url === '/api/jobs/j' && init?.method === 'DELETE'),
  ).toBe(true);
  expect(localStorage.getItem('tindalos-active-job')).toBe('j');
  expect(
    container.querySelector<HTMLTextAreaElement>('textarea[aria-label="模组文本"]')!.disabled,
  ).toBe(true);
  await act(async () =>
    sources
      .at(-1)!
      .onmessage({ lastEventId: '2', data: JSON.stringify({ done: true, status: 'cancelled' }) }),
  );
  expect(localStorage.getItem('tindalos-active-job')).toBeNull();
  expect(container.querySelector('[aria-label="生成进度"]')).not.toBeNull();
  expect(container.textContent).toContain('任务cancelled');
  expect(
    container.querySelector<HTMLTextAreaElement>('textarea[aria-label="模组文本"]')!.disabled,
  ).toBe(false);
  expect(fetch.mock.calls.filter(([url]) => url === '/api/modules')).toHaveLength(2);
  expect(postCalls(fetch)).toHaveLength(0);
});

it('restores a completed saved job through the latest saved revision and does not submit again', async () => {
  localStorage.setItem('tindalos-active-job', 'j');
  const fetch = vi.fn((url: string, init?: RequestInit) =>
    url === '/api/jobs/j'
      ? reply({ job: { id: 'j', status: 'completed', campaign_id: 'c2', revision: 3 } })
      : fetchBase(url, init),
  );
  await mount(fetch);
  expect(useGraphStore.getState().revision).toBe(11);
  expect(container.textContent).toContain('已恢复版本 11');
  expect(localStorage.getItem('tindalos-active-job')).toBeNull();
  expect(postCalls(fetch)).toHaveLength(0);
});

it('preserves the successful catalog when the other catalog fails', async () => {
  const fetch = vi.fn((url: string, init?: RequestInit) =>
    url === '/api/campaigns' ? reply({ detail: 'catalog failed' }, 500) : fetchBase(url, init),
  );
  await mount(fetch);
  expect(container.querySelector('select[aria-label="已上传模组"]')!.textContent).toContain(
    'Source',
  );
  expect(container.querySelector('[role="alert"]')!.textContent).toContain('catalog failed');
});

it('ignores an earlier catalog response after a terminal refresh', async () => {
  localStorage.setItem('tindalos-active-job', 'j');
  let moduleReads = 0;
  let resolve!: (value: unknown) => void;
  const old = new Promise((r) => {
    resolve = r;
  });
  const fetch = vi.fn((url: string, init?: RequestInit) => {
    if (url === '/api/modules') {
      moduleReads++;
      return moduleReads === 1
        ? old
        : reply({ modules: [{ id: 'new', filename: 'Fresh catalog' }] });
    }
    if (url === '/api/jobs/j')
      return reply({ job: { id: 'j', status: 'failed', error: 'failed' } });
    return fetchBase(url, init);
  });
  await mount(fetch);
  await act(async () => resolve(reply({ modules: [{ id: 'old', filename: 'Stale catalog' }] })));
  const text = container.querySelector('select[aria-label="已上传模组"]')!.textContent;
  expect(text).toContain('Fresh catalog');
  expect(text).not.toContain('Stale catalog');
});
