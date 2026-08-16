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

/* ----------------------------------------------------------------
 * Eval —— 评测 trace（后端 eval_store.eval_runs / eval_annotations）。
 * 字段按 web.py 三个 eval 端点真实响应声明，不臆造。
 * ---------------------------------------------------------------- */

/** 单条确定性检查（L1 checks）。 */
export type EvalCheck = {
  id: string;
  name: string;
  dims?: string[];
  passed: boolean;
  evidence?: string;
};

/** 维度结果：L1 为 {score, evidence[]}，L3（LLM judge）为 {score, comment, suggestion}。 */
export type EvalDim = {
  score?: number;
  evidence?: string[];
  comment?: string;
  suggestion?: string;
};

/** 各层结果（L1..L6），字段按层存在与否宽松声明。 */
export type EvalLayer = {
  status?: string; // passed | failed | skipped | degraded | running
  reason?: string; // skipped/degraded 的原因键（cascade_gate_failed 等）
  total?: number;
  dims?: Record<string, EvalDim>;
  checks?: EvalCheck[];
  problems?: string[];
  judge?: string; // 'llm' | 'none'
  estimate_usd?: number;
  claim_count?: number;
  supported?: number;
  support_ratio?: number;
  prior_total?: number;
  current_total?: number;
  delta?: number;
  dim_deltas?: Record<string, number>;
  regression?: boolean;
};

/** 评测运行（eval_runs 行；列表与详情共用）。 */
export type EvalRun = {
  run_id: string;
  campaign_id?: string;
  campaign_title?: string;
  subject_type?: string;
  subject_ref?: string;
  params?: { module_id?: string; max_usd?: number };
  layers?: Record<string, EvalLayer>;
  verdict?: string | null; // pass | warning | fail | error
  status?: string; // running | completed | short_circuited | error
  budget_spent_usd?: number;
  duration_ms?: number;
  created_at?: string;
  updated_at?: string;
};

/** 标注里的证据引用（L4 faithfulness 的 evidence_refs）。 */
export type EvalEvidenceRef = {
  module_id?: string;
  chunk_index?: number;
  score?: number;
};

/** 单条标注（eval_annotations 行；GET /api/eval/runs/{id} → annotations）。 */
export type EvalAnnotation = {
  annotation_id: string;
  run_id?: string;
  layer?: string;
  subject_ref?: string;
  score?: number;
  explanation?: string;
  evidence_refs?: EvalEvidenceRef[];
  created_at?: string;
};
