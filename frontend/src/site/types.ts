/**
 * SgtXLonelyHeartsClub site 类型 —— 站点自身领域类型。
 *
 * 与 lib/types.ts 的 CampaignView 结构兼容的 Campaign 在此独立声明一份
 * （刻意不 import 原文件，站点层自包含、不耦合工作台视图）。
 */

/** 已入库模组的轻量元信息。 */
export type Module = {
  id: string;
  title: string;
  filename?: string;
  created_at?: string;
  status?: string;
  chunk_count?: number;
  rules?: string;
};

/** 多模态视觉识别结果的 kind（人物像 / 地图 / 场景 / 封面）。 */
export type VisionKind = 'portrait' | 'map' | 'scene' | 'cover';

export const VISION_KIND_LABELS: Record<VisionKind, string> = {
  portrait: '人物像',
  map: '地图',
  scene: '场景',
  cover: '封面',
};

export const VISION_KINDS: VisionKind[] = ['portrait', 'map', 'scene', 'cover'];

/** 单张图像的多模态识别结果。 */
export type VisionResult = {
  image_path: string;
  kind: VisionKind;
  confidence: number;
  needs_confirmation: boolean;
  name?: string;
  caption?: string;
};

/** 模组详情：基础信息 + 文本预览 + 视觉结果。 */
export type ModuleDetail = Module & {
  text_preview: string;
  images: VisionResult[];
};

/** RAG 命中片段。 */
export type RagHit = {
  text: string;
  module_id?: string;
  module_title?: string;
  score: number;
};

/** QA 回答的来源引用。 */
export type QaSource = {
  text: string;
  module_id?: string;
  module_title?: string;
  score: number;
};

/** QA 结果。mode：llm=云端 LLM 回答；local=本地检索。 */
export type QaResult = {
  answer: string;
  sources: QaSource[];
  mode: 'llm' | 'local';
};

/** 历史记录中的剧本元信息（重放入口）。 */
export type CampaignMeta = {
  id: string;
  title: string;
  created_at?: string;
  premise?: string;
  acts_count?: number;
};

/* ----------------------------------------------------------------
 * Campaign —— 与 lib/types.ts 的 CampaignView 结构兼容（独立副本）。
 * ---------------------------------------------------------------- */

export type Campaign = {
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
