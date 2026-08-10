/**
 * scriptGraph.test.ts —— ScriptGraph→nodes/edges 映射 + dagre 布局。
 * 夹具：public/campaign.json（examples/campaign-evolved.json 的 campaign 拷贝）。
 * 期望值按夹具手算：28 节点（act 2 / scene 5 / event 15 / npc 4 / clue 2）、
 * 47 边（flow 31 / branch 5 / reference 11）。
 */
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { buildScriptGraph, layoutGraph, positionsAreFinite } from '../src/lib/scriptGraph';
import type { CampaignView } from '../src/lib/types';

function loadCampaign(): CampaignView {
  const raw = readFileSync('public/campaign.json', 'utf-8');
  const json = JSON.parse(raw) as { campaign?: CampaignView } | CampaignView;
  return (json as { campaign?: CampaignView }).campaign ?? (json as CampaignView);
}

const EXPECTED = {
  nodes: 28,
  byType: { act: 2, scene: 5, event: 15, npc: 4, clue: 2 },
  edges: 47,
  byKind: { flow: 31, branch: 5, reference: 11 },
};

describe('buildScriptGraph — 五类节点映射', () => {
  const { nodes } = buildScriptGraph(loadCampaign());

  it('节点总数与按类型分布符合夹具预期', () => {
    expect(nodes).toHaveLength(EXPECTED.nodes);
    const byType = Object.fromEntries(
      (['act', 'scene', 'event', 'npc', 'clue'] as const).map((t) => [
        t,
        nodes.filter((n) => n.type === t).length,
      ]),
    );
    expect(byType).toEqual(EXPECTED.byType);
  });

  it('五类节点齐全（act/scene/event/npc/clue 均存在）', () => {
    for (const t of ['act', 'scene', 'event', 'npc', 'clue'] as const) {
      expect(nodes.some((n) => n.type === t), `缺少 ${t} 节点`).toBe(true);
    }
  });

  it('节点 id 唯一且 data 携带剧本字段', () => {
    const ids = nodes.map((n) => n.id);
    expect(new Set(ids).size).toBe(ids.length);
    const npc = nodes.find((n) => n.type === 'npc')!;
    expect(npc.data).toHaveProperty('name');
    expect(npc.data).toHaveProperty('personality');
    const event = nodes.find((n) => n.type === 'event')!;
    expect(event.data).toHaveProperty('description');
  });
});

describe('buildScriptGraph — 三类边映射', () => {
  const { nodes, edges } = buildScriptGraph(loadCampaign());
  const ids = new Set(nodes.map((n) => n.id));

  it('边总数与按类型分布符合夹具预期', () => {
    expect(edges).toHaveLength(EXPECTED.edges);
    const byKind = Object.fromEntries(
      (['flow', 'branch', 'reference'] as const).map((k) => [k, edges.filter((e) => e.kind === k).length]),
    );
    expect(byKind).toEqual(EXPECTED.byKind);
  });

  it('所有边端点都指向存在的节点（无悬空引用）', () => {
    for (const e of edges) {
      expect(ids.has(e.source), `悬空 source ${e.source}`).toBe(true);
      expect(ids.has(e.target), `悬空 target ${e.target}`).toBe(true);
    }
  });

  it('分支边带 label（条件或「分支」兜底）', () => {
    const branches = edges.filter((e) => e.kind === 'branch');
    expect(branches.length).toBeGreaterThan(0);
    for (const b of branches) {
      expect(b.label).not.toBeUndefined();
      expect(String(b.label ?? '').length).toBeGreaterThan(0);
    }
  });

  it('flow 边覆盖 act→scene→event 递进', () => {
    const pairs = edges.filter((e) => e.kind === 'flow').map((e) => `${e.source}->${e.target}`);
    // 抽查：场景归属幕、事件归属场景
    expect(pairs).toContain('act-1->act-1-scene-1');
    expect(pairs).toContain('act-1-scene-1->act-1-scene-1-ev-1');
    // 主线推进：ev-1→ev-2
    expect(pairs).toContain('act-1-scene-1-ev-1->act-1-scene-1-ev-2');
    // 幕间顺序流
    expect(pairs).toContain('act-1->act-2');
  });

  it('reference 边覆盖 event→npc / npc→clue / event→clue', () => {
    const refs = edges.filter((e) => e.kind === 'reference').map((e) => `${e.source}->${e.target}`);
    expect(refs).toContain('act-1-scene-1-ev-1->npc-1'); // 场景首事件 → NPC
    expect(refs).toContain('npc-1->clue-act-1'); // NPC → 线索
    expect(refs).toContain('act-1-scene-1-ev-3->clue-act-1'); // 事件 → 线索
  });
});

describe('layoutGraph — dagre 自动布局', () => {
  const campaign = loadCampaign();
  const { nodes, edges } = buildScriptGraph(campaign);
  const laid = layoutGraph(nodes, edges);

  it('坐标全部有限（无 NaN/Infinity）', () => {
    expect(positionsAreFinite(laid)).toBe(true);
    for (const n of laid) {
      expect(Number.isFinite(n.position.x)).toBe(true);
      expect(Number.isFinite(n.position.y)).toBe(true);
    }
  });

  it('保留全部节点（id 集合不变，顺序一致）', () => {
    expect(laid.map((n) => n.id)).toEqual(nodes.map((n) => n.id));
  });

  it('横向层级正确：flow 边 target.x > source.x', () => {
    const byId = new Map(laid.map((n) => [n.id, n]));
    const flow = edges.filter((e) => e.kind === 'flow');
    expect(flow.length).toBeGreaterThan(0);
    for (const e of flow) {
      const s = byId.get(e.source)!;
      const t = byId.get(e.target)!;
      expect(t.position.x, `flow ${e.source}→${e.target} 应向右推进`).toBeGreaterThan(s.position.x);
    }
  });

  it('层级排布非全同列（节点有横向与纵向展开）', () => {
    const xs = laid.map((n) => n.position.x);
    const ys = laid.map((n) => n.position.y);
    expect(new Set(xs.map(Math.round)).size).toBeGreaterThan(2);
    expect(new Set(ys.map(Math.round)).size).toBeGreaterThan(2);
  });
});
