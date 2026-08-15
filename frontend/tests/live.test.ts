/**
 * live.test.ts —— 前端三期「实时」验收（t13）：
 * ① parseSSEEvent（帧/错误/截断）+ fetchRegenerate（POST /api/regenerate，30s 超时）
 * ② ProgressBand live=1 fetch POST /api/generate 流式渲染、失败/错误回退静态 progress.jsonl
 * ③ NodeDrawer「重生成」按钮：live 调 fetchRegenerate → patch store 节点 data+edges
 * ④ App live 时 GET /api/campaigns/<id>，离线 public/campaign.json（loadCampaign 决策）
 * ⑤ fetchGenerateStream（transport）：fetch POST + ReadableStream 逐帧解析（半帧/多帧/done/非2xx）
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createRoot, type Root } from 'react-dom/client';
import { act, createElement } from 'react';
import {
  DEMO_MODULE_TEXT,
  GENERATE_SSE_URL,
  SseStreamParser,
  campaignSourceUrl,
  fetchGenerateStream,
  fetchRegenerate,
  getCampaignIdFromQuery,
  isLive,
  loadCampaign,
  parseSSEEvent,
  patchGraphFromCampaign,
  sseToProgressEvent,
  type SseFrame,
} from '../src/lib/live';
import { ProgressBand } from '../src/components/ProgressBand';
import { NodeDrawer } from '../src/components/NodeDrawer';
import { useGraphStore } from '../src/store/useGraphStore';
import type { CampaignView, GraphEdge, GraphNode } from '../src/lib/types';

// eslint-disable-next-line @typescript-eslint/no-explicit-any
(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

/* ---------------------------------------------------------------- 样例数据 */

function makeNode(id: string, title: string, type: GraphNode['type'] = 'event'): GraphNode {
  return { id, type, position: { x: 0, y: 0 }, data: { title } };
}

function freshNodes(): GraphNode[] {
  return [makeNode('evt-1', '抵达现场'), makeNode('evt-2', '发现线索')];
}

const freshEdges: GraphEdge[] = [{ id: 'e1', source: 'evt-1', target: 'evt-2', kind: 'flow' }];

/** 重生成后端返回的「新」campaign（evt-1 已重写为新标题）。 */
const regeneratedCampaign: CampaignView = {
  id: 'campaign-1',
  title: '模组《雾港之夜》',
  premise: '雾港之夜',
  acts: [
    {
      id: 'act-1',
      title: '第I幕',
      roman: 'I',
      summary: '幕一',
      scenes: [
        {
          id: 'act-1-scene-1',
          title: '场景·其一',
          setting: { time: '夜', place: '码头' },
          events: [
            {
              id: 'evt-1',
              title: '密道封条（新）',
              kind: 'trigger',
              description: '重生成后的新描述',
              conditions: [],
              next_event_ids: [],
            },
          ],
          npc_ids: [],
        },
      ],
      npc_ids: [],
    },
  ],
  npcs: {},
  clues: [],
};

/* ---------------------------------------------------------------- ① parseSSEEvent */

describe('parseSSEEvent（API 契约：data:{stage,message} → data:{done:true,campaign}）', () => {
  it('完整进度帧 → progress 事件', () => {
    expect(parseSSEEvent('data:{"stage":"plan","message":"拟定幕结构"}\n\n')).toEqual({
      kind: 'progress',
      stage: 'plan',
      message: '拟定幕结构',
    });
  });

  it('结束帧 → done + campaign', () => {
    expect(parseSSEEvent('data:{"done":true,"campaign":{"id":"c1","title":"t"}}\n\n')).toEqual({
      kind: 'done',
      campaign: { id: 'c1', title: 't' },
    });
  });

  it('结束帧 campaign 缺省 → done + null', () => {
    expect(parseSSEEvent('data:{"done":true}\n\n')).toEqual({ kind: 'done', campaign: null });
  });

  it('错误帧 → error 事件（后端 data:{error:...}）', () => {
    expect(parseSSEEvent('data:{"error":"生成失败"}\n\n')).toEqual({
      kind: 'error',
      message: '生成失败',
    });
  });

  it('截断帧（无空行结束符）→ null（调用方缓冲续接）', () => {
    expect(parseSSEEvent('data:{"stage":"plan","message":"拟定')).toBeNull();
    expect(parseSSEEvent('data:{"done":true')).toBeNull();
    expect(parseSSEEvent('data:{"stage":"a","message":"b"}')).toBeNull();
  });

  it('空输入 → null', () => {
    expect(parseSSEEvent('')).toBeNull();
    expect(parseSSEEvent('   \n ')).toBeNull();
  });

  it('CRLF 帧兼容', () => {
    expect(parseSSEEvent('data:{"stage":"a","message":"b"}\r\n\r\n')).toEqual({
      kind: 'progress',
      stage: 'a',
      message: 'b',
    });
  });

  it('多行 data 字段（JSON 跨行）合并解析', () => {
    const raw = 'data:{"stage":"write",\ndata:"message":"多行"}\n\n';
    expect(parseSSEEvent(raw)).toEqual({ kind: 'progress', stage: 'write', message: '多行' });
  });

  it('EventSource onmessage 裸 JSON 载荷（无 data: 前缀）', () => {
    expect(parseSSEEvent('{"stage":"npc","message":"NPC 顾长歌 · 注入人格"}')).toEqual({
      kind: 'progress',
      stage: 'npc',
      message: 'NPC 顾长歌 · 注入人格',
    });
  });

  it('data 字段首空格剥除（SSE 规范）', () => {
    expect(parseSSEEvent('data: {"stage":"a","message":"b"}\n\n')).toEqual({
      kind: 'progress',
      stage: 'a',
      message: 'b',
    });
  });

  it('非 JSON data → SyntaxError（错误帧由调用方兜底）', () => {
    expect(() => parseSSEEvent('data:{"broken"\n\n')).toThrow(SyntaxError);
  });

  it('缺契约字段（无 stage/message/done/error）→ SyntaxError', () => {
    expect(() => parseSSEEvent('data:{"foo":"bar"}\n\n')).toThrow(SyntaxError);
    expect(() => parseSSEEvent('data:null\n\n')).toThrow(SyntaxError);
  });

  it('注释/心跳帧（无 data 字段）→ null', () => {
    expect(parseSSEEvent(': keep-alive\n\n')).toBeNull();
  });
});

describe('SseStreamParser（分块缓冲，截断续接）', () => {
  it('半帧累积，完整帧逐条吐出，多帧一冲', () => {
    const parser = new SseStreamParser();
    expect(parser.push('data:{"stage":"plan",')).toEqual([]);
    expect(parser.push('"message":"拟定幕结构"}\n\ndata:{"done":true,"campaign":null}\n\n')).toEqual([
      { kind: 'progress', stage: 'plan', message: '拟定幕结构' },
      { kind: 'done', campaign: null },
    ]);
    expect(parser.push('')).toEqual([]);
  });

  it('CRLF 分块同样续接', () => {
    const parser = new SseStreamParser();
    parser.push('data:{"stage":"npc","message":"x"}\r\n\r');
    expect(parser.push('\ndata:{"done":true}\r\n\r\n')).toEqual([
      { kind: 'progress', stage: 'npc', message: 'x' },
      { kind: 'done', campaign: null },
    ]);
  });
});

describe('sseToProgressEvent（SSE 帧 → 进度事件）', () => {
  it('npc stage → agent=npc + 提取 NPC 名', () => {
    const ev = sseToProgressEvent({ kind: 'progress', stage: 'npc', message: 'NPC 顾长歌 · 注入人格' });
    expect(ev.agent).toBe('npc');
    expect(ev.npc).toBe('顾长歌');
    expect(ev.text).toBe('NPC 顾长歌 · 注入人格');
  });

  it('非 npc stage → agent=kp', () => {
    const ev = sseToProgressEvent({ kind: 'progress', stage: 'write', message: '写作第I幕' });
    expect(ev.agent).toBe('kp');
    expect(ev.stage).toBe('write');
  });
});

/* ---------------------------------------------------------------- ① fetchRegenerate */

describe('fetchRegenerate（POST /api/regenerate，30s 超时）', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('成功：POST /api/regenerate，body={campaign_id,node_id} → {campaign,applied}', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ ok: true, campaign: { id: 'c1' }, applied: ['evt-1'] }),
    });
    vi.stubGlobal('fetch', fetchMock);
    const result = await fetchRegenerate('c1', 'evt-1');
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/regenerate');
    expect(init.method).toBe('POST');
    expect(init.headers).toEqual({ 'Content-Type': 'application/json' });
    expect(JSON.parse(String(init.body))).toEqual({ campaign_id: 'c1', node_id: 'evt-1' });
    expect(result.campaign).toEqual({ id: 'c1' });
    expect(result.applied).toEqual(['evt-1']);
  });

  it('非 2xx：抛出服务端 error 消息', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 404,
        json: async () => ({ ok: false, error: 'campaign not found' }),
      }),
    );
    await expect(fetchRegenerate('nope', 'evt-1')).rejects.toThrow('campaign not found');
  });

  it('ok=false / 缺 campaign：抛契约错误', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({ ok: true, applied: [] }) }),
    );
    await expect(fetchRegenerate('c1', 'evt-1')).rejects.toThrow(/缺少 campaign/);
  });

  it('响应非 JSON：抛 HTTP 状态错误', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 500,
        json: async () => {
          throw new Error('bad json');
        },
      }),
    );
    await expect(fetchRegenerate('c1', 'evt-1')).rejects.toThrow(/HTTP 500/);
  });

  it('30s 超时中止请求（AbortError）', async () => {
    vi.useFakeTimers();
    try {
      vi.stubGlobal(
        'fetch',
        vi.fn(
          (_url: string, init: RequestInit) =>
            new Promise((_resolve, reject) => {
              init.signal?.addEventListener('abort', () =>
                reject(new DOMException('The operation was aborted.', 'AbortError')),
              );
            }),
        ),
      );
      const promise = fetchRegenerate('c1', 'evt-1');
      const assertion = expect(promise).rejects.toThrow(/abort/i);
      await vi.advanceTimersByTimeAsync(30_000);
      await assertion;
    } finally {
      vi.useRealTimers();
    }
  });

  it('外部 signal 中止请求', async () => {
    const ac = new AbortController();
    vi.stubGlobal(
      'fetch',
      vi.fn(
        (_url: string, init: RequestInit) =>
          new Promise((_resolve, reject) => {
            init.signal?.addEventListener('abort', () =>
              reject(new DOMException('The operation was aborted.', 'AbortError')),
            );
          }),
      ),
    );
    const promise = fetchRegenerate('c1', 'evt-1', { signal: ac.signal });
    const assertion = expect(promise).rejects.toThrow(/abort/i);
    ac.abort();
    await assertion;
  });
});

/* ---------------------------------------------------------------- ②④ live 判定与数据源 */

describe('live 判定与数据源', () => {
  it('isLive：?live=1 为真，其余为假', () => {
    expect(isLive('?live=1')).toBe(true);
    expect(isLive('?live=0')).toBe(false);
    expect(isLive('?foo=1')).toBe(false);
    expect(isLive('')).toBe(false);
  });

  it('getCampaignIdFromQuery：?campaign= 取值', () => {
    expect(getCampaignIdFromQuery('?live=1&campaign=camp-9')).toBe('camp-9');
    expect(getCampaignIdFromQuery('?live=1')).toBeNull();
  });

  it('campaignSourceUrl：live+id → /api/campaigns/<id>；离线/无 id → 静态 campaign.json', () => {
    expect(campaignSourceUrl(true, 'camp-9')).toBe('/api/campaigns/camp-9');
    expect(campaignSourceUrl(true, 'a/b')).toBe('/api/campaigns/a%2Fb');
    expect(campaignSourceUrl(true, null)).toBe(`${import.meta.env.BASE_URL}campaign.json`);
    expect(campaignSourceUrl(false, 'camp-9')).toBe(`${import.meta.env.BASE_URL}campaign.json`);
  });

  it('loadCampaign：live 时 GET /api/campaigns/<id>', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue({ ok: true, status: 200, json: async () => ({ id: 'c1', title: '实时' }) });
    vi.stubGlobal('fetch', fetchMock);
    const campaign = await loadCampaign(true, 'c1');
    expect(fetchMock).toHaveBeenCalledWith('/api/campaigns/c1');
    expect(campaign.title).toBe('实时');
    vi.unstubAllGlobals();
  });

  it('loadCampaign：live API 失败 → 回退静态 public/campaign.json', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({ ok: false, status: 404, json: async () => ({}) })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({ id: 'static-1', title: '静态剧本' }),
      });
    vi.stubGlobal('fetch', fetchMock);
    const campaign = await loadCampaign(true, 'c1');
    expect(fetchMock).toHaveBeenNthCalledWith(2, `${import.meta.env.BASE_URL}campaign.json`);
    expect(campaign.title).toBe('静态剧本');
    vi.unstubAllGlobals();
  });

  it('loadCampaign：离线直接读静态，无回退', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ id: 'static-1', title: '静态剧本' }),
    });
    vi.stubGlobal('fetch', fetchMock);
    await loadCampaign(false, null);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith(`${import.meta.env.BASE_URL}campaign.json`);
    vi.unstubAllGlobals();
  });
});

/* ---------------------------------------------------------------- ③ store 补丁 */

describe('重生成 store 补丁（patchGraphFromCampaign + store）', () => {
  beforeEach(() => {
    useGraphStore.setState({ nodes: [], edges: [], selectedId: null, past: [], campaignId: null });
  });

  it('patchGraphFromCampaign 产出目标节点新 data 与整图 edges', () => {
    const { nodeData, edges } = patchGraphFromCampaign(regeneratedCampaign, 'evt-1');
    expect(nodeData.title).toBe('密道封条（新）');
    expect(nodeData.description).toBe('重生成后的新描述');
    expect(edges.length).toBeGreaterThan(0);
    expect(edges.some((e) => e.source === 'act-1')).toBe(true); // 幕→场景边重建
  });

  it('campaign 中缺失目标节点 → 抛错', () => {
    expect(() => patchGraphFromCampaign(regeneratedCampaign, 'ghost')).toThrow(/未找到节点/);
  });

  it('loadGraph 第三参记录 campaignId；setCampaignId 可更新', () => {
    useGraphStore.getState().loadGraph([], [], 'campaign-1');
    expect(useGraphStore.getState().campaignId).toBe('campaign-1');
    useGraphStore.getState().setCampaignId('campaign-2');
    expect(useGraphStore.getState().campaignId).toBe('campaign-2');
  });

  it('updateNodeData + setEdges 应用重生成补丁（节点 data 替换 + 边重建）', () => {
    useGraphStore.getState().loadGraph(freshNodes(), freshEdges, 'campaign-1');
    const { nodeData, edges } = patchGraphFromCampaign(regeneratedCampaign, 'evt-1');
    useGraphStore.getState().updateNodeData('evt-1', nodeData);
    useGraphStore.getState().setEdges(edges);
    const n = useGraphStore.getState().nodes.find((x) => x.id === 'evt-1')!;
    expect(n.data.title).toBe('密道封条（新）');
    expect(useGraphStore.getState().edges.some((e) => e.source === 'act-1')).toBe(true);
    expect(useGraphStore.getState().edges).not.toContain(freshEdges[0]);
    expect(useGraphStore.getState().undoCount()).toBe(1); // 编辑类操作入 undo 栈
  });
});

/* ---------------------------------------------------------------- ② ProgressBand */

type FakeResponse = {
  ok: boolean;
  status: number;
  body?: ReadableStream<Uint8Array>;
  text?: () => Promise<string>;
  json?: () => Promise<unknown>;
};

/** SSE 流式响应：chunks 逐段 enqueue（可测半帧续接 / 多帧一冲）。 */
function sseStreamResponse(chunks: string[]): FakeResponse {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const c of chunks) controller.enqueue(encoder.encode(c));
      controller.close();
    },
  });
  return { ok: true, status: 200, body };
}

const STATIC_PROGRESS_LINE =
  '{"ts":"t","agent":"kp","step":"读取模组","text":"静态回退内容"}\n';

/** 契约格式 SSE 帧：`data:{json}\n\n`（与 serve.py sse_frame 一致）。 */
function sseData(payload: unknown): string {
  return `data:${JSON.stringify(payload)}\n\n`;
}

/**
 * 可控开放 SSE 流：模拟真实 serve.py —— 流保持开启、逐帧 push、
 * 最后由调用方 finish() 关闭（未 finish 前不会触发「流结束→回退」）。
 */
function sseOpenStream(): {
  response: FakeResponse;
  push(payload: unknown): void;
  finish(): void;
} {
  let controller: ReadableStreamDefaultController<Uint8Array>;
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      controller = c;
    },
  });
  return {
    response: { ok: true, status: 200, body },
    push(payload) {
      controller.enqueue(encoder.encode(sseData(payload)));
    },
    finish() {
      controller.close();
    },
  };
}

function stubBrowserApi(opts: { reducedMotion?: boolean } = {}): void {
  vi.stubGlobal(
    'matchMedia',
    vi.fn(() => ({
      matches: opts.reducedMotion ?? false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  );
}

/**
 * 按 URL 路由的 fetch mock：
 *  - /api/generate → generate()（默认空流；传 generate 抛错可模拟连接失败）
 *  - progress.jsonl → 静态 progress 文本
 *  - 其余 → 404
 */
function routeFetch(opts: {
  generate?: () => Promise<FakeResponse>;
  staticText?: string;
}): ReturnType<typeof vi.fn> {
  const generate = opts.generate ?? (() => Promise.resolve(sseStreamResponse([])));
  const staticText = opts.staticText ?? STATIC_PROGRESS_LINE;
  return vi.fn((input: string | URL | Request) => {
    const url = String(input);
    if (url.includes('/api/generate')) return generate();
    if (url.includes('progress.jsonl'))
      return Promise.resolve({ ok: true, status: 200, text: async () => staticText });
    return Promise.resolve({ ok: false, status: 404, text: async () => '' });
  });
}

/** 渲染 + 等流式读取消化（同一 act 内，所有 setState 都被包裹）。 */
async function renderProgressBand(
  root: Root,
  props: { live?: boolean; moduleText?: string },
): Promise<void> {
  await act(async () => {
    root.render(createElement(ProgressBand, props));
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

describe('ProgressBand（live=1 fetch POST /api/generate 流式；失败/错误回退静态）', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    stubBrowserApi();
    history.replaceState({}, '', '/');
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    vi.unstubAllGlobals();
  });

  it('live=1：fetch POST /api/generate，进度帧实时渲染', async () => {
    const stream = sseOpenStream();
    const fetchMock = routeFetch({ generate: () => Promise.resolve(stream.response) });
    vi.stubGlobal('fetch', fetchMock);
    await act(async () => {
      root.render(createElement(ProgressBand, { live: true, moduleText: '雾港之夜' }));
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(fetchMock).toHaveBeenCalledWith(
      GENERATE_SSE_URL,
      expect.objectContaining({ method: 'POST' }),
    );
    const body = JSON.parse(fetchMock.mock.calls[0][1].body as string);
    expect(body).toEqual({ module_text: '雾港之夜', llm: false });

    // 流持续开放，逐帧送达 → 实时渲染（未 done 前不落静态）
    await act(async () => {
      stream.push({ stage: 'plan', message: '拟定幕结构' });
      stream.push({ stage: 'npc', message: 'NPC 顾长歌 · 注入人格' });
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(container.textContent).toContain('拟定幕结构');
    expect(container.textContent).toContain('实时');
    expect(container.textContent).toContain('顾长歌');
    expect(fetchMock).not.toHaveBeenCalledWith(
      expect.stringContaining('progress.jsonl'),
      expect.anything(),
    );

    // 服务端 finally 发 done 并关闭 → 收尾记录 campaignId，不回退
    await act(async () => {
      stream.push({ done: true, campaign: { id: 'campaign-9' } });
      stream.finish();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(useGraphStore.getState().campaignId).toBe('campaign-9');
    expect(fetchMock).not.toHaveBeenCalledWith(
      expect.stringContaining('progress.jsonl'),
      expect.anything(),
    );
  });

  it('live 且无 moduleText → 用 DEMO_MODULE_TEXT 发起 POST', async () => {
    const fetchMock = routeFetch({});
    vi.stubGlobal('fetch', fetchMock);
    await renderProgressBand(root, { live: true });
    expect(fetchMock).toHaveBeenCalledWith(GENERATE_SSE_URL, expect.anything());
    const body = JSON.parse(fetchMock.mock.calls[0][1].body as string);
    expect(body.module_text).toBe(DEMO_MODULE_TEXT);
  });

  it('结束帧 data:{done:true,campaign} → 记录 campaignId，不回退静态', async () => {
    const fetchMock = routeFetch({
      generate: () =>
        Promise.resolve(
          sseStreamResponse([sseData({ done: true, campaign: { id: 'campaign-9' } })]),
        ),
    });
    vi.stubGlobal('fetch', fetchMock);
    await renderProgressBand(root, { live: true });
    expect(useGraphStore.getState().campaignId).toBe('campaign-9');
    expect(fetchMock).not.toHaveBeenCalledWith(
      expect.stringContaining('progress.jsonl'),
      expect.anything(),
    );
  });

  it('连接失败（fetch reject）→ 回退静态 progress.jsonl', async () => {
    stubBrowserApi({ reducedMotion: true });
    const fetchMock = routeFetch({
      generate: async () => {
        throw new Error('网络断开');
      },
    });
    vi.stubGlobal('fetch', fetchMock);
    await renderProgressBand(root, { live: true });
    expect(fetchMock).toHaveBeenCalledWith(`${import.meta.env.BASE_URL}progress.jsonl`);
    expect(container.textContent).toContain('静态回退内容');
  });

  it('HTTP 非 2xx（服务端 error）→ 回退静态 progress.jsonl', async () => {
    stubBrowserApi({ reducedMotion: true });
    const fetchMock = routeFetch({
      generate: () =>
        Promise.resolve({
          ok: false,
          status: 400,
          json: async () => ({ error: 'module_text 必填字符串' }),
        }),
    });
    vi.stubGlobal('fetch', fetchMock);
    await renderProgressBand(root, { live: true });
    expect(fetchMock).toHaveBeenCalledWith(`${import.meta.env.BASE_URL}progress.jsonl`);
    expect(container.textContent).toContain('静态回退内容');
  });

  it('错误帧 data:{error} → 回退静态 progress.jsonl', async () => {
    stubBrowserApi({ reducedMotion: true });
    const fetchMock = routeFetch({
      generate: () =>
        Promise.resolve(sseStreamResponse([sseData({ error: '生成失败' })])),
    });
    vi.stubGlobal('fetch', fetchMock);
    await renderProgressBand(root, { live: true });
    expect(fetchMock).toHaveBeenCalledWith(`${import.meta.env.BASE_URL}progress.jsonl`);
    expect(container.textContent).toContain('静态回退内容');
  });

  it('非 live（离线）：直接读静态 progress.jsonl，不 POST', async () => {
    stubBrowserApi({ reducedMotion: true });
    const fetchMock = routeFetch({});
    vi.stubGlobal('fetch', fetchMock);
    await renderProgressBand(root, {});
    expect(fetchMock).not.toHaveBeenCalledWith(GENERATE_SSE_URL, expect.anything());
    expect(fetchMock).toHaveBeenCalledWith(`${import.meta.env.BASE_URL}progress.jsonl`);
    expect(container.textContent).toContain('静态回退内容');
  });
});

/* ---------------------------------------------------------------- ⑤ fetchGenerateStream（transport） */

async function collectStream(gen: AsyncGenerator<SseFrame>): Promise<SseFrame[]> {
  const out: SseFrame[] = [];
  for await (const f of gen) out.push(f);
  return out;
}

describe('fetchGenerateStream：POST /api/generate 流式解析', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('POST body={module_text,llm:false}，一冲多帧按序产出', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      sseStreamResponse([
        sseData({ stage: 'plan', message: '拟定幕结构' }) +
          sseData({ stage: 'npc', message: 'NPC 顾长歌' }),
      ]),
    );
    vi.stubGlobal('fetch', fetchMock);
    const frames = await collectStream(fetchGenerateStream('雾港之夜'));
    expect(fetchMock).toHaveBeenCalledWith(
      GENERATE_SSE_URL,
      expect.objectContaining({ method: 'POST' }),
    );
    const body = JSON.parse(fetchMock.mock.calls[0][1].body as string);
    expect(body).toEqual({ module_text: '雾港之夜', llm: false });
    expect(frames).toHaveLength(2);
    expect(frames[0]).toMatchObject({ kind: 'progress', stage: 'plan' });
    expect(frames[1]).toMatchObject({ kind: 'progress', stage: 'npc' });
  });

  it('llm:true 透传 body', async () => {
    const fetchMock = vi.fn().mockResolvedValue(sseStreamResponse([]));
    vi.stubGlobal('fetch', fetchMock);
    await collectStream(fetchGenerateStream('x', { llm: true }));
    const body = JSON.parse(fetchMock.mock.calls[0][1].body as string);
    expect(body).toEqual({ module_text: 'x', llm: true });
  });

  it('半帧跨 chunk：缓冲续接后完整产出', async () => {
    const frame = sseData({ stage: 'write', message: '写作幕内容' });
    const half = Math.ceil(frame.length / 2);
    const fetchMock = vi.fn().mockResolvedValue(
      sseStreamResponse([frame.slice(0, half), frame.slice(half)]),
    );
    vi.stubGlobal('fetch', fetchMock);
    const frames = await collectStream(fetchGenerateStream('x'));
    expect(frames).toHaveLength(1);
    expect(frames[0]).toMatchObject({ kind: 'progress', stage: 'write', message: '写作幕内容' });
  });

  it('done 帧产出 campaign', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      sseStreamResponse([sseData({ done: true, campaign: { id: 'c-1' } })]),
    );
    vi.stubGlobal('fetch', fetchMock);
    const frames = await collectStream(fetchGenerateStream('x'));
    expect(frames).toHaveLength(1);
    expect(frames[0]).toEqual({ kind: 'done', campaign: expect.objectContaining({ id: 'c-1' }) });
  });

  it('HTTP 非 2xx + 服务端 error → 抛服务端消息', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 400,
      json: async () => ({ error: 'module_text 必填字符串' }),
    });
    vi.stubGlobal('fetch', fetchMock);
    await expect(collectStream(fetchGenerateStream(''))).rejects.toThrow('module_text 必填字符串');
  });

  it('响应无 body → 抛契约错误', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200 });
    vi.stubGlobal('fetch', fetchMock);
    await expect(collectStream(fetchGenerateStream('x'))).rejects.toThrow('浏览器不支持响应流');
  });
});

/* ---------------------------------------------------------------- ③ NodeDrawer 按钮 */

describe('NodeDrawer「重生成」按钮（live 调 fetchRegenerate → patch store；离线置灰）', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    vi.stubGlobal('fetch', vi.fn());
    useGraphStore.setState({ nodes: [], edges: [], selectedId: null, past: [], campaignId: null });
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    vi.unstubAllGlobals();
  });

  function renderDrawer(locationPath: string): void {
    history.replaceState({}, '', locationPath);
    useGraphStore.getState().loadGraph(freshNodes(), freshEdges, 'campaign-1');
    useGraphStore.getState().selectNode('evt-1');
    act(() => {
      root.render(createElement(NodeDrawer));
    });
  }

  function regenerateButton(): HTMLButtonElement {
    const buttons = [...container.querySelectorAll('button')];
    const btn = buttons.find((b) => b.textContent?.includes('重生成'));
    if (!btn) throw new Error('未找到重生成按钮');
    return btn as HTMLButtonElement;
  }

  it('离线（无 ?live=1）：置灰 + tooltip「需 tindalos serve」', () => {
    renderDrawer('/');
    const btn = regenerateButton();
    expect(btn.disabled).toBe(true);
    expect(btn.title).toBe('需 tindalos serve');
  });

  it('live：点击 → POST /api/regenerate → patch 节点 data + edges', async () => {
    renderDrawer('/?live=1');
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ ok: true, campaign: regeneratedCampaign, applied: ['evt-1'] }),
    });
    vi.stubGlobal('fetch', fetchMock);
    const btn = regenerateButton();
    expect(btn.disabled).toBe(false);
    await act(async () => {
      btn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    // 请求契约
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/regenerate');
    expect(init.method).toBe('POST');
    expect(JSON.parse(String(init.body))).toEqual({ campaign_id: 'campaign-1', node_id: 'evt-1' });
    // store 补丁：节点 data 替换 + 边重建
    const n = useGraphStore.getState().nodes.find((x) => x.id === 'evt-1')!;
    expect(n.data.title).toBe('密道封条（新）');
    expect(useGraphStore.getState().edges.some((e) => e.source === 'act-1')).toBe(true);
  });

  it('live：fetch 失败 → 显示错误，节点数据不变', async () => {
    renderDrawer('/?live=1');
    vi.stubGlobal(
      'fetch',
      vi.fn().mockRejectedValue(new Error('network down')),
    );
    const btn = regenerateButton();
    await act(async () => {
      btn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    const n = useGraphStore.getState().nodes.find((x) => x.id === 'evt-1')!;
    expect(n.data.title).toBe('抵达现场'); // 未被污染
    expect(container.textContent).toContain('network down');
  });

  it('live：非 2xx（404 campaign 不存在）→ 显示服务端错误', async () => {
    renderDrawer('/?live=1');
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 404,
        json: async () => ({ ok: false, error: 'campaign not found' }),
      }),
    );
    const btn = regenerateButton();
    await act(async () => {
      btn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    expect(container.textContent).toContain('campaign not found');
  });
});
