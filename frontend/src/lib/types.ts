/**
 * 剧本节点图领域类型 —— 五类节点 × 三类边。
 *
 * 数据模型对齐设计调研 script-graph-frontend.md §3.1：
 * 剧本 JSON（后端 LangGraph 产出）是唯一真相，前端 nodes/edges 由
 * `buildScriptGraph` 单向推导；节点 data 直接承载剧本字段。
 *
 * 注意：data 形状用**类型别名**而非 interface —— 类型别名可赋值给
 * `Record<string, unknown>`，从而与 @xyflow/react 的 Node<DataType> 泛型
 * 无缝衔接（interface 因可能被声明合并而无法赋给索引签名类型）。
 */

import type { Node, Edge } from '@xyflow/react';

/** 五类节点。 */
export type NodeType = 'act' | 'scene' | 'event' | 'npc' | 'clue';

/** 三类边。 */
export type EdgeKind = 'flow' | 'branch' | 'reference';

/** 节点 data 载荷（直接承载剧本字段，JSON 是唯一真相）。 */
export type ActData = {
  title: string;
  summary: string;
  /** drawer 编辑写 description；节点渲染优先 description，缺省回退 summary（G5 契约修复） */
  description?: string;
  roman?: string;
  sceneCount: number;
};

export type SceneData = {
  title: string;
  time: string;
  place: string;
  status: string;
  eventCount: number;
};

export type EventData = {
  title: string;
  kind: string;
  description: string;
  conditions: string[];
};

export type NpcData = {
  name: string;
  archetype: string;
  personality: string[];
  description: string;
};

export type ClueData = {
  name: string;
  description: string;
};

export type AnyNodeData = ActData | SceneData | EventData | NpcData | ClueData;

/** 与 @xyflow/react Node 兼容的图节点（data 保持宽类型，组件内按需收窄）。 */
export type GraphNode = Node<Record<string, unknown>, NodeType>;

/** 与 @xyflow/react Edge 兼容的图边，`kind` 标注三类边语义。 */
export type GraphEdge = Edge & { kind: EdgeKind };

/** 剧本 JSON 的轻量视图（仅映射所需字段）。 */
export type CampaignView = {
  id: string;
  title: string;
  premise?: string;
  acts: ActView[];
  npcs: Record<string, NpcView>;
  clues: ClueView[];
  relations?: unknown[];
};

export type ActView = {
  id: string;
  title: string;
  roman?: string;
  summary: string;
  scenes: SceneView[];
  npc_ids: string[];
};

export type SceneView = {
  id: string;
  title: string;
  setting: { time: string; place: string };
  events: EventView[];
  npc_ids: string[];
};

export type EventView = {
  id: string;
  title: string;
  kind: string;
  description: string;
  conditions: string[];
  next_event_ids: string[];
};

export type NpcView = {
  id: string;
  name: string;
  archetype?: string;
  personality?: string[];
  description?: string;
};

export type ClueView = {
  id: string;
  name: string;
  description?: string;
  linked_npc_ids?: string[];
  linked_event_ids?: string[];
};

/** 各节点类型的展示元信息（图例 / MiniMap / 徽章共用）。 */
export type NodeTypeMeta = {
  type: NodeType;
  label: string;
  color: string;
  cssClass: string;
};
