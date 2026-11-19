/**
 * site.test.tsx —— Tindalos 站点外壳验收：
 * ① parseHash + api.health
 * ③ SiteApp 启动：立即显示首页
 * ④ hash 路由导航（#/ → workbench / library / qa / history）
 * ⑤ 规则问答交互（提问 → 回答 + 来源 + 模式徽标）
 * ⑥ 历史记录 + 点击剧本进入重放
 * ⑦ ReplayPlayer 分步播放（下一步/上一步/进度条/重放完毕）
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createRoot, type Root } from 'react-dom/client';
import { act, createElement } from 'react';
import { parseHash } from '../src/site/router';
import { health } from '../src/site/api';
import { SiteApp } from '../src/site/SiteApp';
import { navigateRoute, settleRoute } from './siteHarness';
import { ReplayPlayer, buildReplaySteps } from '../src/site/ReplayPlayer';
import type { Campaign } from '../src/site/types';

// App 是 ReactFlow 工作台，jsdom 无 ResizeObserver → 用轻量 mock（站点路由/布局不受影响）。
vi.mock('../src/App', async () => {
  const React = await import('react');
  return {
    default: () => React.createElement('div', { 'data-testid': 'mock-app' }, 'mock-app'),
  };
});

// eslint-disable-next-line @typescript-eslint/no-explicit-any
(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

/* ---------------------------------------------------------------- 样例数据 */

const replayCampaign: Campaign = {
  id: 'c1',
  title: '雾港之夜',
  premise: '港口失踪案调查',
  acts: [
    {
      id: 'act-1',
      title: '第I幕',
      roman: 'I',
      summary: '幕一：抵达雾港',
      scenes: [
        {
          id: 'act-1-scene-1',
          title: '场景·其一',
          setting: { time: '夜', place: '码头' },
          events: [
            {
              id: 'evt-1',
              title: '抵达现场',
              kind: 'trigger',
              description: '浓雾弥漫的码头',
              conditions: [],
              next_event_ids: ['evt-2'],
            },
            {
              id: 'evt-2',
              title: '发现线索',
              kind: 'action',
              description: '找到浸水的纸片',
              conditions: ['幸运检定成功'],
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

/* ---------------------------------------------------------------- 工具 */

type JsonBody = { ok: boolean; status: number; json: () => Promise<unknown> };

function jsonResponse(data: unknown, ok = true, status = 200): JsonBody {
  return { ok, status, json: async () => data };
}

type SiteOverrides = {
  modules?: unknown[];
  modulesHistory?: unknown[];
  campaigns?: unknown[];
  campaign?: Campaign;
  qa?: { answer: string; sources: unknown[]; mode: 'llm' | 'local' };
};

/** 站点 API 的按 URL 路由 fetch mock（契约路径见 site/api.ts 头注释）。 */
function siteFetch(overrides: SiteOverrides = {}): ReturnType<typeof vi.fn> {
  return vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes('/api/health')) return jsonResponse({ ok: true });
    if (url.includes('/api/modules/history'))
      return jsonResponse({ modules: overrides.modulesHistory ?? [] });
    if (url.includes('/api/modules')) return jsonResponse({ modules: overrides.modules ?? [] });
    if (url.includes('/api/campaigns')) {
      if (/\/api\/campaigns\/[^/]+$/.test(url)) {
        return jsonResponse({ campaign: overrides.campaign ?? replayCampaign });
      }
      return jsonResponse({ campaigns: overrides.campaigns ?? [] });
    }
    if (url.includes('/api/qa')) {
      return jsonResponse(
        overrides.qa ?? {
          answer: '需要掷出 5 或更低。',
          sources: [{ text: '技能检定相关条文。', module_title: '守秘人规则书', score: 0.82 }],
          mode: 'llm' as const,
        },
      );
    }
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

/** 原生 value setter + input 事件（React 受控输入必需）。 */
function setInputValue(input: HTMLInputElement, value: string): void {
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
  setter?.call(input, value);
  input.dispatchEvent(new Event('input', { bubbles: true }));
}

function findButton(container: HTMLElement, text: string): HTMLButtonElement {
  const buttons = [...container.querySelectorAll('button')];
  const btn = buttons.find((b) => b.textContent?.includes(text));
  if (!btn) throw new Error(`未找到按钮：${text}`);
  return btn as HTMLButtonElement;
}

async function go(hash: string): Promise<void> {
  await navigateRoute(container, hash);
}

async function bootFast(root: Root, fetchMock: ReturnType<typeof vi.fn>): Promise<void> {
  vi.stubGlobal('fetch', fetchMock);
  stubMatchMedia(false);
  await act(async () => {
    root.render(createElement(SiteApp));
  });
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

/* ---------------------------------------------------------------- ① 单元 */

describe('parseHash + health', () => {
  it('parseHash：路由段解析', () => {
    expect(parseHash('#/')).toEqual([]);
    expect(parseHash('#/workbench')).toEqual(['workbench']);
    expect(parseHash('#/history/c1')).toEqual(['history', 'c1']);
    expect(parseHash('')).toEqual([]);
    expect(parseHash('#/history/  ')).toEqual(['history']);
  });

  it('health：成功 → {ok:true}；失败/非 2xx → {ok:false}，不抛错', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ ok: true })));
    await expect(health()).resolves.toEqual({ ok: true });

    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('network')));
    await expect(health()).resolves.toEqual({ ok: false });
  });
});

/* ---------------------------------------------------------------- ③ 启动 */

describe('SiteApp 启动', () => {
  it('立即显示首页与六个栏目，不等待启动动画', async () => {
    await bootFast(root, siteFetch());
    expect(container.querySelector('.sx-loading')).toBeNull();
    expect(container.textContent).toContain('未知有边界');
    for (const title of ['剧本工作台', '模组资料库', '规则问答', '历史记录', '评测', '记忆']) {
      expect(container.textContent).toContain(title);
    }
  });
});

/* ---------------------------------------------------------------- ④ 路由 */

describe('hash 路由导航', () => {
  it('#/workbench → library → qa → history 各栏目正确渲染', async () => {
    const fetchMock = siteFetch();
    await bootFast(root, fetchMock);

    await go('#/workbench');
    await settleRoute(container);
    expect(container.querySelector('.sx-site')!.getAttribute('data-route')).toBe('workbench');
    expect(container.textContent).toContain('生成');
    expect(container.querySelector('[data-testid="mock-app"]')).toBeTruthy();

    await go('#/library');
    await settleRoute(container);
    expect(container.querySelector('.sx-site')!.getAttribute('data-route')).toBe('library');
    expect(container.textContent).toContain('已入库模组');

    await go('#/qa');
    await settleRoute(container);
    expect(container.querySelector('.sx-site')!.getAttribute('data-route')).toBe('qa');
    expect(container.textContent).toContain('发送');

    await go('#/history');
    await settleRoute(container);
    expect(container.querySelector('.sx-site')!.getAttribute('data-route')).toBe('history');
    expect(container.textContent).toContain('生成的剧本');
  });

  it('首页节点点击同样导航', async () => {
    const fetchMock = siteFetch();
    await bootFast(root, fetchMock);
    await act(async () => {
      findButton(container, '模组资料库').click();
      await new Promise((r) => setTimeout(r, 0));
    });
    await settleRoute(container);
    expect(container.querySelector('.sx-site')!.getAttribute('data-route')).toBe('library');
  });
});

/* ---------------------------------------------------------------- ⑤ 问答 */

describe('规则问答', () => {
  it('提问 → 回答 + 来源卡片 + LLM 模式徽标', async () => {
    const fetchMock = siteFetch({
      qa: {
        answer: '需要掷出 5 或更低。',
        sources: [{ text: '技能检定相关条文。', module_title: '守秘人规则书', score: 0.82 }],
        mode: 'llm',
      },
    });
    await bootFast(root, fetchMock);
    await go('#/qa');

    const input = container.querySelector('.sx-qa__composer input') as HTMLInputElement;
    setInputValue(input, '幸运检定成功需要掷出多少？');
    findButton(container, '发送').click();
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });

    expect(container.textContent).toContain('幸运检定成功需要掷出多少？'); // user 气泡
    expect(container.textContent).toContain('需要掷出 5 或更低。'); // 回答
    expect(container.querySelector('[data-testid="qa-mode"]')!.textContent).toContain('LLM 回答');
    expect(container.textContent).toContain('守秘人规则书'); // 来源卡片
  });
});

/* ---------------------------------------------------------------- ⑥ 历史 + 重放 */

describe('历史记录 → 重放', () => {
  it('两组卡片渲染，点击剧本进入重放页', async () => {
    const fetchMock = siteFetch({
      campaigns: [{ id: 'c1', title: '雾港之夜', created_at: '2026-08-16', acts_count: 1 }],
      modulesHistory: [{ id: 'm1', title: '克苏鲁的呼唤', filename: 'coc7.pdf' }],
      campaign: replayCampaign,
    });
    await bootFast(root, fetchMock);
    await go('#/history');

    expect(container.textContent).toContain('生成的剧本');
    expect(container.textContent).toContain('雾港之夜');
    expect(container.textContent).toContain('上传的模组');
    expect(container.textContent).toContain('克苏鲁的呼唤');

    act(() => {
      findButton(container, '雾港之夜').click();
    });
    // click → navigate 设 hash → hashchange → ReplayView 挂载 → getCampaign fetch
    await act(async () => {
      window.dispatchEvent(new HashChangeEvent('hashchange'));
      await new Promise((r) => setTimeout(r, 0));
    });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    await settleRoute(container);
    expect(container.querySelector('.sx-site')!.getAttribute('data-route')).toBe('replay');
    expect(container.textContent).toContain('剧本重放');
    expect(container.querySelector('[data-testid="sx-replay-player"]')).toBeTruthy();
    expect(container.textContent).toContain('第I幕');
  });
});

/* ---------------------------------------------------------------- ⑦ ReplayPlayer */

describe('ReplayPlayer 分步播放', () => {
  it('buildReplaySteps 压平 幕→场景→事件', () => {
    const steps = buildReplaySteps(replayCampaign);
    expect(steps).toHaveLength(4);
    expect(steps[0]).toMatchObject({ kind: 'act', title: '第I幕' });
    expect(steps[1]).toMatchObject({ kind: 'scene', title: '场景·其一' });
    expect(steps[2]).toMatchObject({ kind: 'event', title: '抵达现场' });
    expect(steps[3]).toMatchObject({ kind: 'event', title: '发现线索' });
  });

  it('下一步/上一步/播放暂停/跳步/进度条/重放完毕', async () => {
    vi.useFakeTimers();
    try {
      act(() => {
        root.render(createElement(ReplayPlayer, { campaign: replayCampaign }));
      });

      const progressBar = () =>
        container.querySelector('[data-testid="progress-bar"]') as HTMLSpanElement;

      // 初始：第 1 步（幕）
      expect(container.textContent).toContain('第I幕');
      expect(container.textContent).toContain('第 1 / 4 步');
      expect(progressBar().style.width).toBe('25%');

      // 下一步 → 场景
      act(() => {
        findButton(container, '下一步').click();
      });
      expect(container.textContent).toContain('场景·其一');
      expect(container.textContent).toContain('第 2 / 4 步');
      expect(progressBar().style.width).toBe('50%');

      // 上一步 → 回到幕
      act(() => {
        findButton(container, '上一步').click();
      });
      expect(container.textContent).toContain('第I幕');
      expect(progressBar().style.width).toBe('25%');

      // 播放 / 暂停 切换
      act(() => {
        findButton(container, '暂停').click();
      });
      expect(findButton(container, '播放')).toBeTruthy();
      act(() => {
        findButton(container, '播放').click();
      });
      expect(findButton(container, '暂停')).toBeTruthy();

      // 点击时间线圆点直接跳到事件步（含 conditions 展示）
      const dots = container.querySelectorAll('.sx-replay-player__dot');
      expect(dots.length).toBe(4);
      act(() => {
        (dots[3] as HTMLButtonElement).click();
      });
      expect(container.textContent).toContain('发现线索');
      expect(container.textContent).toContain('幸运检定成功'); // conditions
      expect(container.textContent).toContain('第 4 / 4 步');
      expect(progressBar().style.width).toBe('100%');

      // 末步：重放完毕 + 下一步置灰
      expect(container.querySelector('[data-testid="replay-done"]')).toBeTruthy();
      expect(container.textContent).toContain('重放完毕');
      expect(findButton(container, '下一步').disabled).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });
});
