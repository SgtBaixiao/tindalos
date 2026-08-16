/**
 * eval.test.tsx —— 评测页验收（P0-b 收尾）：
 * ① 列表渲染（run 卡片：标题/verdict/状态/预算/耗时/run_id）+ 点击进入详情
 * ② 首页「评测」栏目入口跳转
 * ③ 详情渲染（L1..L6 分层 trace + 各层证据 + 标注 evidence_refs）
 * ④ 列表空态
 * ⑤ 列表错误态
 * ⑥ 详情错误态（404）
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createRoot, type Root } from 'react-dom/client';
import { act, createElement } from 'react';
import { SiteApp } from '../src/site/SiteApp';
import { LOADING_MS } from '../src/site/Loading';
import type { EvalAnnotation, EvalRun } from '../src/site/types';

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

const sampleRun: EvalRun = {
  run_id: 'abc123',
  campaign_id: 'c1',
  campaign_title: '雾港之夜',
  subject_type: 'campaign',
  subject_ref: 'c1',
  params: { module_id: 'm1', max_usd: 2.0 },
  layers: {
    L1: {
      status: 'passed',
      total: 4.8,
      dims: {
        structural: { score: 4.8, evidence: ['关键事件存在唯一前驱'] },
      },
      checks: [
        { id: 'chk-1', name: '结构：事件链完整', dims: ['structural'], passed: true },
      ],
    },
    L2: { status: 'passed', problems: [] },
    L3: {
      status: 'passed',
      judge: 'llm',
      dims: { structural: { score: 4.5, comment: '结构紧凑', suggestion: '补充线索关联' } },
    },
    L4: { status: 'passed', claim_count: 3, supported: 3, support_ratio: 1.0 },
    L5: { status: 'skipped', reason: 'manual_only' },
    L6: { status: 'skipped', reason: 'no_prior_run' },
  },
  verdict: 'pass',
  status: 'completed',
  budget_spent_usd: 0.42,
  duration_ms: 2100,
  created_at: '2026-08-16T10:00:00+00:00',
  updated_at: '2026-08-16T10:00:03+00:00',
};

const warningRun: EvalRun = {
  ...sampleRun,
  run_id: 'def456',
  campaign_title: '孤岛回声',
  verdict: 'warning',
  status: 'short_circuited',
  budget_spent_usd: 0.05,
  duration_ms: 320,
  created_at: '2026-08-16T09:00:00+00:00',
};

const detailRun: EvalRun = {
  ...sampleRun,
  run_id: 'detail-1',
  layers: {
    ...sampleRun.layers,
    L3: {
      status: 'passed',
      judge: 'llm',
      dims: {
        structural: { score: 4.5, comment: '结构紧凑', suggestion: '补充线索关联' },
        consistency: { score: 3.8, comment: 'NPC 动机合理' },
      },
    },
    L6: {
      status: 'passed',
      prior_total: 4.2,
      current_total: 4.5,
      delta: 0.3,
      dim_deltas: { structural: 0.3, consistency: 0 },
      regression: false,
    },
  },
  verdict: 'pass',
  status: 'completed',
  budget_spent_usd: 1.2,
  duration_ms: 3200,
};

const sampleAnnotations: EvalAnnotation[] = [
  {
    annotation_id: 'ann-1',
    run_id: 'detail-1',
    layer: 'L4',
    subject_ref: 'scene:sc-1',
    score: 1.0,
    explanation: '与模组语料逐句可对齐',
    evidence_refs: [
      { module_id: 'm1', chunk_index: 3, score: 0.94 },
      { module_id: 'm1', chunk_index: 7, score: 0.87 },
    ],
  },
  {
    annotation_id: 'ann-2',
    run_id: 'detail-1',
    layer: 'L4',
    subject_ref: 'event:evt-2',
    score: 0.0,
    explanation: '条件与语料不匹配',
    evidence_refs: [],
  },
];

/* ---------------------------------------------------------------- 工具 */

type JsonBody = { ok: boolean; status: number; json: () => Promise<unknown> };

function jsonResponse(data: unknown, ok = true, status = 200): JsonBody {
  return { ok, status, json: async () => data };
}

type EvalOverrides = {
  runs?: unknown[];
  run?: EvalRun;
  annotations?: unknown[];
  runsError?: boolean;
  runError?: boolean;
};

/** eval 端点的按 URL 路由 fetch mock（列表 + 详情 + health）。 */
function evalFetch(overrides: EvalOverrides = {}): ReturnType<typeof vi.fn> {
  return vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes('/api/eval/runs')) {
      // 详情：/api/eval/runs/<run_id>
      if (/\/api\/eval\/runs\/[^/]+$/.test(url)) {
        if (overrides.runError) return jsonResponse({ error: 'eval run not found' }, false, 404);
        return jsonResponse({
          run: overrides.run ?? detailRun,
          annotations: overrides.annotations ?? sampleAnnotations,
        });
      }
      // 列表：/api/eval/runs
      if (overrides.runsError) return jsonResponse({ error: 'boom' }, false, 500);
      return jsonResponse({ runs: overrides.runs ?? [sampleRun] });
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

/* ---------------------------------------------------------------- ① 列表 */

describe('评测列表', () => {
  it('渲染运行卡片（标题 / verdict / 状态 / 预算 / 耗时 / run_id）并可点击进入详情', async () => {
    const fetchMock = evalFetch({ runs: [sampleRun, warningRun] });
    await bootFast(root, fetchMock);
    await go('#/eval');

    expect(container.querySelector('[data-testid="sx-eval"]')).toBeTruthy();
    expect(container.querySelector('.sx-site')!.getAttribute('data-route')).toBe('eval');
    expect(container.textContent).toContain('雾港之夜');
    expect(container.textContent).toContain('孤岛回声');
    expect(container.textContent).toContain('通过');
    expect(container.textContent).toContain('警告');
    expect(container.textContent).toContain('完成');
    expect(container.textContent).toContain('$0.42');
    expect(container.textContent).toContain('2.1s');
    expect(container.textContent).toContain('abc123');
    expect(container.textContent).toContain('def456');

    // 点击卡片 → 进入详情
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
    expect(container.querySelector('.sx-site')!.getAttribute('data-route')).toBe('eval-detail');
    expect(container.querySelector('[data-testid="sx-eval-detail"]')).toBeTruthy();
  });

  it('首页「评测」栏目入口可跳转', async () => {
    const fetchMock = evalFetch();
    await bootFast(root, fetchMock);
    await act(async () => {
      findButton(container, '评测').click();
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(container.querySelector('.sx-site')!.getAttribute('data-route')).toBe('eval');
    expect(container.querySelector('[data-testid="sx-eval"]')).toBeTruthy();
  });
});

/* ---------------------------------------------------------------- ② 详情 */

describe('评测详情', () => {
  it('渲染 run 概要 + L1..L6 分层 trace + 标注（含 evidence_refs）', async () => {
    const fetchMock = evalFetch({ run: detailRun, annotations: sampleAnnotations });
    await bootFast(root, fetchMock);
    await go('#/eval/detail-1');

    expect(container.querySelector('[data-testid="sx-eval-detail"]')).toBeTruthy();
    // run 概要
    expect(container.querySelector('[data-testid="eval-run-meta"]')!.textContent).toContain('detail-1');
    expect(container.textContent).toContain('雾港之夜');
    expect(container.textContent).toContain('$1.20');
    expect(container.textContent).toContain('3.2s');
    expect(container.textContent).toContain('预算上限');
    expect(container.textContent).toContain('$2.00');

    // 六层 trace
    for (const l of ['L1', 'L2', 'L3', 'L4', 'L5', 'L6']) {
      expect(container.querySelector(`[data-testid="layer-${l}"]`)).toBeTruthy();
    }
    // L1：维度分数 + 证据 + 检查
    expect(container.textContent).toContain('总分 4.8 / 5');
    expect(container.textContent).toContain('结构性');
    expect(container.textContent).toContain('关键事件存在唯一前驱');
    expect(container.textContent).toContain('事件链完整');
    // L2
    expect(container.textContent).toContain('无一致性问题');
    // L3：LLM 裁判 + comment / suggestion
    expect(container.textContent).toContain('裁判：LLM 裁判');
    expect(container.textContent).toContain('结构紧凑');
    expect(container.textContent).toContain('建议：补充线索关联');
    expect(container.textContent).toContain('NPC 动机合理');
    // L4：声明支持比
    expect(container.textContent).toContain('声明 3 条 · 支持 3 条 · 支持比 100%');
    // L5：仅人工
    expect(container.textContent).toContain('该层仅支持人工评测');
    // L6：回归对比
    expect(container.textContent).toContain('当前 4.5 vs 历史 4.2');
    expect(container.textContent).toContain('无回归');
    expect(container.textContent).toContain('+0.3');

    // 标注 + evidence_refs
    expect(container.textContent).toContain('scene:sc-1');
    expect(container.textContent).toContain('与模组语料逐句可对齐');
    expect(container.textContent).toContain('模组 m1 · 块 3 · 相似度 0.94');
    expect(container.textContent).toContain('模组 m1 · 块 7 · 相似度 0.87');
    expect(container.textContent).toContain('event:evt-2');

    // 返回列表按钮
    expect(findButton(container, '评测列表')).toBeTruthy();
  });
});

/* ---------------------------------------------------------------- ③ 空态 / 错误态 */

describe('评测空态与错误态', () => {
  it('列表空态占位', async () => {
    const fetchMock = evalFetch({ runs: [] });
    await bootFast(root, fetchMock);
    await go('#/eval');
    expect(container.querySelector('[data-testid="eval-empty"]')).toBeTruthy();
    expect(container.textContent).toContain('还没有评测记录');
  });

  it('列表错误态：请求失败展示错误信息', async () => {
    const fetchMock = evalFetch({ runsError: true });
    await bootFast(root, fetchMock);
    await go('#/eval');
    expect(container.querySelector('[data-testid="eval-error"]')).toBeTruthy();
    expect(container.textContent).toContain('boom');
  });

  it('详情错误态：404 展示服务端错误', async () => {
    const fetchMock = evalFetch({ runError: true });
    await bootFast(root, fetchMock);
    await go('#/eval/nope');
    expect(container.querySelector('[data-testid="eval-detail-error"]')).toBeTruthy();
    expect(container.textContent).toContain('eval run not found');
  });
});
