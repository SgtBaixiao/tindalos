/**
 * ScriptGraph：剧本 JSON → React Flow nodes/edges 映射 + dagre 自动布局。
 *
 * 规则（确定性，供测试断言）：
 *
 * 节点（五类）：
 *  - act   ：每个 act 一个节点（红粗顶条）
 *  - scene ：每个 scene 一个节点（橙顶条）
 *  - event ：每个 event 一个节点（铜锈绿虚线框）
 *  - npc   ：npcs 字典每个成员一个节点（墨蓝胶囊）
 *  - clue  ：每个 clue 一个节点
 *
 * 边（三类）：
 *  - flow（顺序流·实线）：
 *      act[i]→act[i+1]（幕间顺序）、act→scene、scene→event（每个）、
 *      event→next_event_ids[0]（主线推进）
 *  - branch（分支·虚线）：
 *      event→next_event_ids[1:]（备选/条件路径，label 取 condition 或「分支」）
 *  - reference（引用·点划线）：
 *      scene 首事件→npc（scene.npc_ids）、npc→clue（clue.linked_npc_ids）、
 *      event→clue（clue.linked_event_ids）
 *
 * 布局：dagre rankdir=LR（横向层级），position 由中心坐标换算为左上角。
 */

import dagre from '@dagrejs/dagre';
import type {
  CampaignView,
  GraphEdge,
  GraphNode,
  NodeType,
  NodeTypeMeta,
  ActView,
  SceneView,
} from './types';

/** 各类型节点的估算尺寸 —— 与 nodes.css 中卡片实际宽度保持一致。 */
export const NODE_DIMS: Record<NodeType, { width: number; height: number }> = {
  act: { width: 280, height: 112 },
  scene: { width: 230, height: 96 },
  event: { width: 210, height: 88 },
  npc: { width: 200, height: 72 },
  clue: { width: 210, height: 84 },
};

/** 五类节点的展示元信息（图例 / MiniMap / 徽章）。 */
export const NODE_TYPE_META: NodeTypeMeta[] = [
  { type: 'act', label: '幕 Act', color: 'var(--t-rule-red)', cssClass: 'tn-node--act' },
  { type: 'scene', label: '场景 Scene', color: 'var(--t-orange)', cssClass: 'tn-node--scene' },
  { type: 'event', label: '事件 Event', color: 'var(--t-verdigris)', cssClass: 'tn-node--event' },
  { type: 'npc', label: 'NPC', color: 'var(--t-inkblue)', cssClass: 'tn-node--npc' },
  { type: 'clue', label: '线索 Clue', color: 'var(--t-sepia-ink)', cssClass: 'tn-node--clue' },
];

export const EDGE_KIND_META = [
  { kind: 'flow', label: '顺序流（实线）' },
  { kind: 'branch', label: '分支（虚线）' },
  { kind: 'reference', label: '引用（点划线）' },
] as const;

export const NODE_TYPE_COLORS: Record<NodeType, string> = {
  act: 'var(--t-rule-red)',
  scene: 'var(--t-orange)',
  event: 'var(--t-verdigris)',
  npc: 'var(--t-inkblue)',
  clue: 'var(--t-sepia-ink)',
};

/** 默认空节点（未知类型兜底）。 */
const DEFAULT_DIMS = NODE_DIMS.event;

function actNode(act: ActView, index: number): GraphNode {
  return {
    id: act.id,
    type: 'act',
    position: { x: 0, y: 0 },
    data: {
      title: act.title,
      summary: act.summary,
      roman: act.roman ?? `第${index + 1}幕`,
      sceneCount: act.scenes.length,
    },
  };
}

function sceneNode(scene: SceneView): GraphNode {
  return {
    id: scene.id,
    type: 'scene',
    position: { x: 0, y: 0 },
    data: {
      title: scene.title,
      time: scene.setting.time,
      place: scene.setting.place,
      status: scene.events.length > 0 ? 'done' : '待写',
      eventCount: scene.events.length,
    },
  };
}

function eventNodes(scene: SceneView): GraphNode[] {
  return scene.events.map((ev) => ({
    id: ev.id,
    type: 'event',
    position: { x: 0, y: 0 },
    data: {
      title: ev.title,
      kind: ev.kind,
      description: ev.description,
      conditions: ev.conditions ?? [],
    },
  }));
}

function npcNodes(campaign: CampaignView): GraphNode[] {
  return Object.values(campaign.npcs).map((npc) => ({
    id: npc.id,
    type: 'npc',
    position: { x: 0, y: 0 },
    data: {
      name: npc.name,
      archetype: npc.archetype ?? '佚名角色',
      personality: npc.personality ?? [],
      description: npc.description ?? '',
    },
  }));
}

function clueNodes(campaign: CampaignView): GraphNode[] {
  return campaign.clues.map((clue) => ({
    id: clue.id,
    type: 'clue',
    position: { x: 0, y: 0 },
    data: {
      name: clue.name,
      description: clue.description ?? '',
    },
  }));
}

function edgeId(kind: string, source: string, target: string): string {
  return `e-${kind}-${source}->${target}`;
}

/**
 * 剧本 JSON → nodes/edges 单向映射（确定性、可测试）。
 */
export function buildScriptGraph(campaign: CampaignView): {
  nodes: GraphNode[];
  edges: GraphEdge[];
} {
  const nodes: GraphNode[] = [];
  const edges: GraphEdge[] = [];
  const seen = new Set<string>();

  const addEdge = (kind: GraphEdge['kind'], source: string, target: string, label?: string) => {
    const id = edgeId(kind, source, target);
    if (seen.has(id)) return; // 去重（同源同目标同类型）
    seen.add(id);
    edges.push({ id, source, target, kind, label });
  };

  // 幕 → 场景 → 事件
  campaign.acts.forEach((act, actIdx) => {
    nodes.push(actNode(act, actIdx));
    if (actIdx > 0) {
      addEdge('flow', campaign.acts[actIdx - 1].id, act.id); // 幕间顺序流
    }
    for (const scene of act.scenes) {
      nodes.push(sceneNode(scene));
      addEdge('flow', act.id, scene.id);
      const evNodes = eventNodes(scene);
      nodes.push(...evNodes);
      const firstEvent = evNodes[0];
      for (const ev of scene.events) {
        addEdge('flow', scene.id, ev.id); // 场景 → 每个事件
        ev.next_event_ids.forEach((nextId, idx) => {
          if (idx === 0) {
            addEdge('flow', ev.id, nextId); // 主线推进
          } else {
            const condition = ev.conditions?.[idx - 1];
            addEdge('branch', ev.id, nextId, condition && condition.length > 0 ? condition : '分支');
          }
        });
      }
      // 引用：场景首事件 → NPC（该场景登场的 NPC）
      for (const npcId of scene.npc_ids ?? []) {
        if (firstEvent && npcId in campaign.npcs) {
          addEdge('reference', firstEvent.id, npcId);
        }
      }
    }
  });

  nodes.push(...npcNodes(campaign), ...clueNodes(campaign));

  // 引用：NPC → 线索（linked_npc_ids）、事件 → 线索（linked_event_ids）
  for (const clue of campaign.clues) {
    for (const npcId of clue.linked_npc_ids ?? []) {
      if (npcId in campaign.npcs) {
        addEdge('reference', npcId, clue.id, '指向线索');
      }
    }
    for (const evId of clue.linked_event_ids ?? []) {
      if (nodes.some((n) => n.id === evId)) {
        addEdge('reference', evId, clue.id, '线索');
      }
    }
  }

  return { nodes, edges };
}

/**
 * dagre 自动布局：横向层级（rankdir=LR）。
 * 返回带 position（左上角坐标）的节点数组，节点 id/顺序保持不变。
 */
export function layoutGraph(nodes: GraphNode[], edges: GraphEdge[]): GraphNode[] {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({
    rankdir: 'LR',
    nodesep: 48,
    ranksep: 96,
    marginx: 32,
    marginy: 32,
  });

  for (const node of nodes) {
    const dims = NODE_DIMS[node.type] ?? DEFAULT_DIMS;
    g.setNode(node.id, { width: dims.width, height: dims.height });
  }
  for (const edge of edges) {
    if (edge.source === edge.target) continue;
    g.setEdge(edge.source, edge.target);
  }

  dagre.layout(g);

  return nodes.map((node) => {
    const dims = NODE_DIMS[node.type] ?? DEFAULT_DIMS;
    const pos = g.node(node.id) as { x: number; y: number };
    const x = Number.isFinite(pos?.x) ? pos.x : 0;
    const y = Number.isFinite(pos?.y) ? pos.y : 0;
    return {
      ...node,
      position: { x: x - dims.width / 2, y: y - dims.height / 2 },
    };
  });
}

/** 布局后坐标健康检查：全节点位置有限（无 NaN/Infinity）。 */
export function positionsAreFinite(nodes: GraphNode[]): boolean {
  return nodes.every(
    (n) =>
      Number.isFinite(n.position.x) &&
      Number.isFinite(n.position.y),
  );
}
