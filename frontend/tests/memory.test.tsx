/**
 * memory.test.tsx —— 记忆可视化验收（P3-2）：
 * ① 详情渲染（briefing 卡片 + 剧情线状态 + 四类记忆分区 + 会话时间线含 conflicts 折叠）
 * ② 首页「记忆」栏目入口 → 索引选择战役 → 进入详情
 * ③ 空态占位（无记忆 / 无会话）
 * ④ 记忆请求失败错误态
 * ⑤ 会话请求失败优雅降级（记忆主体仍渲染）
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createRoot, type Root } from 'react-dom/client';
import { act, createElement } from 'react';
import { SiteApp } from '../src/site/SiteApp';
import { LOADING_MS } from '../src/site/Loading';
import type { MemoriesResponse, SessionsResponse } from '../src/site/types';

// App 是 ReactFlow 工作台，jsdom 无 ResizeObserver → 轻量 mock。
vi.mock('../src/App', async () => {
  const React = await import('react');
  return {
    default: () => React.createElement('div', { 'data-testid': 'mock-app' }, 'mock-app'),
  };
});

// eslint-disable-next-line @typescript-eslint/no-explicit-any
(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

/* ---------------------------------------------------------------- 样例数据 */

const sampleMemories: MemoriesResponse = {
  campaign_id: 'c1',
  status: 'ok',
  play_status: '进行中 · 第 2 幕',
  briefing:
    '【上次停在哪】\n最近游玩（第 2 场）：调查员们在雾港码头追查失踪案。\n当前状态：进行中 · 第 2 幕\n\n剧情概要：整合了 5 条情景记忆。\n主线脉络：覆盖 2 个幕。',
  memories: {
    episodic: [
      {
        id: 'evm:c1:evt-1',
        memory_type: 'episodic',
        content: '第 I 幕 · 场景一：浓雾弥漫的码头，调查员抵达现场。',
        importance: 0.65,
        subject_key: null,
        source_episode: 'act-1/scene-1/evt-1',
        status: 'active',
        created_at: '2026-08-16T10:00:00+00:00',
      },
    ],
    semantic: [
      {
        id: 'sem:c1:npc:n1',
        memory_type: 'semantic',
        content: '老吴（富商）：码头商会主事，说话留三分。',
        importance: 0.8,
        subject_key: 'npc:n1',
        source_episode: 'n1',
        status: 'active',
        created_at: '2026-08-16T10:00:00+00:00',
      },
    ],
    shortterm: [
      {
        id: 'stm:c1:t3',
        memory_type: 'shortterm',
        content: '第 3 场线索：货仓铁皮箱内侧的抓痕。',
        importance: 0.4,
        subject_key: 'clue:box-scratch',
        source_episode: 'act-1/scene-2/evt-3',
        status: 'active',
        created_at: '2026-08-16T10:30:00+00:00',
      },
    ],
    longterm: [
      {
        id: 'ltm:c1:synopsis',
        memory_type: 'longterm',
        content: '剧情概要：整合了 5 条情景记忆，事件序列从抵达码头到发现抓痕。',
        importance: 0.8,
        subject_key: 'synopsis',
        source_episode: null,
        status: 'active',
        created_at: '2026-08-16T10:00:00+00:00',
      },
      {
        id: 'ltm:c1:plotline',
        memory_type: 'longterm',
        content: '主线脉络：覆盖 2 个幕，围绕码头失踪案与商会暗线展开。',
        importance: 0.8,
        subject_key: 'plotline',
        source_episode: null,
        status: 'active',
        created_at: '2026-08-16T10:00:00+00:00',
      },
    ],
  },
};

const sampleSessions: SessionsResponse = {
  campaign_id: 'c1',
  current_play_status: '进行中 · 第 2 幕',
  sessions: [
    {
      id: 's1',
      session_index: 1,
      summary: '第一场：调查员抵达雾港，在码头集结。',
      play_status: '开局',
      created_at: '2026-08-16T09:00:00+00:00',
    },
    {
      id: 's2',
      session_index: 2,
      summary: '第二场：追查失踪案，发现铁皮箱抓痕。',
      play_status: '进行中 · 第 2 幕',
      // sqlite 存 JSON TEXT，后端可能原样返回字符串 → 前端需防御性解析。
      conflicts: JSON.stringify([
        { description: '玩家要求立刻搜查灯塔，KP 裁定暂不开放。' },
        { description: '规则分歧：幸运检定难度从极难降为困难。' },
      ]),
      created_at: '2026-08-16T10:00:00+00:00',
    },
    {
      id: 's3',
      session_index: 3,
      summary: '第三场：商会晚宴上正面接触老吴。',
      play_status: '进行中 · 第 2 幕',
      // 已解析数组也应兼容。
      conflicts: [{ description: '与 NPC 交互方式裁定：允许单独密谈。' }],
      created_at: '2026-08-16T11:00:00+00:00',
    },
  ],
};

const emptyMemories: MemoriesResponse = {
  campaign_id: 'c1',
  status: 'ok',
  play_status: null,
  briefing: null,
  memories: { episodic: [], semantic: [], shortterm: [], longterm: [] },
};

const emptySessions: SessionsResponse = {
  campaign_id: 'c1',
  current_play_status: null,
  sessions: [],
};

const sampleCampaigns = [
  {
    id: 'c1',
    title: '雾港之夜',
    created_at: '2026-08-16T08:00:00+00:00',
    acts_count: 2,
    premise: '浓雾笼罩的临海小镇，失踪案频发。',
  },
];

/* ---------------------------------------------------------------- 工具 */

type JsonBody = { ok: boolean; status: number; json: () => Promise<unknown> };

function jsonResponse(data: unknown, ok = true, status = 200): JsonBody {
  return { ok, status, json: async () => data };
}

type MemoryOverrides = {
  memories?: MemoriesResponse;
  sessions?: SessionsResponse;
  campaigns?: unknown[];
  memoriesError?: boolean;
  sessionsError?: boolean;
};

/** memories / sessions / campaigns 端点按 URL 路由的 fetch mock。 */
function memoryFetch(overrides: MemoryOverrides = {}): ReturnType<typeof vi.fn> {
  return vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes('/api/memories/')) {
      if (overrides.memoriesError) return jsonResponse({ error: 'memories boom' }, false, 500);
      return jsonResponse(overrides.memories ?? sampleMemories);
    }
    if (url.includes('/api/sessions/')) {
      if (overrides.sessionsError) return jsonResponse({ error: 'sessions boom' }, false, 500);
      return jsonResponse(overrides.sessions ?? sampleSessions);
    }
    if (url.includes('/api/campaigns')) {
      return jsonResponse({ campaigns: overrides.campaigns ?? sampleCampaigns });
    }
    if (url.includes('/api/health')) return jsonResponse({ ok: true });
    return jsonResponse({ error: `未 mock 的请求：${url}` }, false, 404);
  });
}

function stubMatchMedia(reduced = false): void {
  vi.stubGlobal(
    'matchMedia',
    vi.fn(() => ({
      matches: reduced,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  );
}

function findButton(container: HTMLElement, text: string): HTMLButtonElement {
  const buttons = [...container.querySelectorAll('button')];
  const btn = buttons.find((b) => b.textContent?.includes(text));
  if (!btn) throw new Error(`未找到按钮：${text}`);
  return btn as HTMLButtonElement;
}

/** 设置 hash 并派发 hashchange（重复无害）。 */
async function go(hash: string): Promise<void> {
  window.location.hash = hash;
  await act(async () => {
    window.dispatchEvent(new HashChangeEvent('hashchange'));
    await new Promise((r) => setTimeout(r, 0));
  });
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
}

/** 快速启动：fake timers 走完 Loading，再切回真实 timers。 */
async function bootFast(root: Root, fetchMock: ReturnType<typeof vi.fn>): Promise<void> {
  vi.stubGlobal('fetch', fetchMock);
  stubMatchMedia(false);
  vi.useFakeTimers();
  try {
    act(() => {
      root.render(createElement(SiteApp));
    });
    act(() => {
      vi.advanceTimersByTime(LOADING_MS.done + 100);
    });
  } finally {
    vi.useRealTimers();
  }
  expect(container.querySelector('.sx-loading')).toBeNull();
  expect(rootContainer()).not.toBeNull();
}

let container: HTMLDivElement;
let root: Root;

function rootContainer(): HTMLElement | null {
  return container.querySelector('[data-testid="sx-home"]');
}

beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  history.replaceState({}, '', '/');
});

afterEach(async () => {
  await act(async () => {
    root.unmount();
  });
  container.remove();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

/* ---------------------------------------------------------------- ① 详情 */

describe('记忆详情', () => {
  it('渲染 briefing 卡片、剧情线状态、四类记忆分区与会话时间线（含 conflicts 折叠）', async () => {
    const fetchMock = memoryFetch();
    await bootFast(root, fetchMock);
    await go('#/memories/c1');

    expect(container.querySelector('[data-testid="sx-memory"]')).toBeTruthy();
    expect(container.querySelector('.sx-site')!.getAttribute('data-route')).toBe('memories');
    expect(container.querySelector('[data-testid="route-title"]')!.textContent).toBe('记忆');

    // briefing 卡片
    expect(container.querySelector('[data-testid="memory-briefing"]')).toBeTruthy();
    expect(container.textContent).toContain('上次停在哪');
    expect(container.textContent).toContain('最近游玩（第 2 场）');
    expect(container.textContent).toContain('状态：进行中 · 第 2 幕');

    // 剧情线状态（longterm 按 subject_key 取 synopsis / plotline）
    expect(container.textContent).toContain('剧情线状态');
    expect(container.textContent).toContain('剧情概要');
    expect(container.textContent).toContain('整合了 5 条情景记忆');
    expect(container.textContent).toContain('主线脉络');
    expect(container.textContent).toContain('覆盖 2 个幕');

    // 四类记忆分区
    for (const type of ['episodic', 'semantic', 'shortterm', 'longterm']) {
      expect(container.querySelector(`[data-testid="mem-panel-${type}"]`)).toBeTruthy();
    }
    expect(container.textContent).toContain('情景记忆');
    expect(container.textContent).toContain('语义记忆');
    expect(container.textContent).toContain('短期记忆');
    expect(container.textContent).toContain('长期记忆');
    expect(container.textContent).toContain('重要度 0.65');
    expect(container.textContent).toContain('重要度 0.80');
    expect(container.textContent).toContain('溯源 act-1/scene-1/evt-1');
    expect(container.textContent).toContain('浓雾弥漫的码头');
    expect(container.textContent).toContain('老吴（富商）');
    expect(container.textContent).toContain('货仓铁皮箱内侧的抓痕');

    // 会话时间线（含 conflicts）
    expect(container.querySelector('[data-testid="memory-sessions"]')).toBeTruthy();
    expect(container.textContent).toContain('第 1 场');
    expect(container.textContent).toContain('第 2 场');
    expect(container.textContent).toContain('第一场：调查员抵达雾港');
    expect(container.textContent).toContain('第二场：追查失踪案');
    // 第 2 场（JSON 字符串 conflicts，2 条）+ 第 3 场（数组 conflicts，1 条）
    expect(container.querySelectorAll('[data-testid="conflict-badge"]').length).toBe(2);
    expect(container.textContent).toContain('冲突 2 条');
    expect(container.textContent).toContain('冲突 1 条');
    expect(container.textContent).toContain('玩家要求立刻搜查灯塔');
    expect(container.textContent).toContain('规则分歧：幸运检定难度');
    expect(container.textContent).toContain('与 NPC 交互方式裁定');

    // 返回按钮
    expect(findButton(container, '记忆')).toBeTruthy();
  });
});

/* ---------------------------------------------------------------- ② 入口与索引 */

describe('记忆入口与索引', () => {
  it('首页「记忆」栏目入口可跳转，索引选择战役进入详情', async () => {
    const fetchMock = memoryFetch();
    await bootFast(root, fetchMock);

    // 首页 → #/memories（索引）
    await act(async () => {
      findButton(container, '记忆').click();
      await new Promise((r) => setTimeout(r, 0));
    });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(container.querySelector('.sx-site')!.getAttribute('data-route')).toBe('memories');
    expect(container.querySelector('[data-testid="sx-memory-index"]')).toBeTruthy();
    expect(container.textContent).toContain('雾港之夜');

    // 索引 → 详情
    act(() => {
      findButton(container, '雾港之夜').click();
    });
    await act(async () => {
      window.dispatchEvent(new HashChangeEvent('hashchange'));
      await new Promise((r) => setTimeout(r, 0));
    });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(container.querySelector('[data-testid="sx-memory"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="memory-briefing"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="route-title"]')!.textContent).toBe('记忆');
  });
});

/* ---------------------------------------------------------------- ③ 空态 */

describe('记忆空态与错误态', () => {
  it('无记忆与无会话时空态占位', async () => {
    const fetchMock = memoryFetch({ memories: emptyMemories, sessions: emptySessions });
    await bootFast(root, fetchMock);
    await go('#/memories/c1');

    expect(container.querySelector('[data-testid="sx-memory"]')).toBeTruthy();
    // briefing 占位
    expect(container.textContent).toContain('还没有「上次停在哪」可回叙');
    // 剧情线占位
    expect(container.querySelector('[data-testid="plotline-empty"]')).toBeTruthy();
    expect(container.textContent).toContain('暂无剧情线状态');
    // 四类分区占位
    expect(container.textContent).toContain('暂无情景记忆');
    expect(container.textContent).toContain('暂无语义记忆');
    expect(container.textContent).toContain('暂无短期记忆');
    expect(container.textContent).toContain('暂无长期记忆');
    // 会话时间线占位
    expect(container.querySelector('[data-testid="sessions-empty"]')).toBeTruthy();
    expect(container.textContent).toContain('暂无游玩会话记录');
  });

  it('记忆请求失败展示错误占位', async () => {
    const fetchMock = memoryFetch({ memoriesError: true });
    await bootFast(root, fetchMock);
    await go('#/memories/c1');

    expect(container.querySelector('[data-testid="memory-error"]')).toBeTruthy();
    expect(container.textContent).toContain('memories boom');
  });

  it('会话请求失败优雅降级：记忆主体仍渲染', async () => {
    const fetchMock = memoryFetch({ sessionsError: true });
    await bootFast(root, fetchMock);
    await go('#/memories/c1');

    expect(container.querySelector('[data-testid="sx-memory"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="memory-error"]')).toBeNull();
    expect(container.querySelector('[data-testid="memory-briefing"]')).toBeTruthy();
    expect(container.textContent).toContain('四类记忆');
    expect(container.querySelector('[data-testid="sessions-error"]')).toBeTruthy();
    expect(container.textContent).toContain('sessions boom');
  });
});
