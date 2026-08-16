/**
 * site/evalFormat.ts —— 评测页共享的展示映射与格式化工具。
 *
 * 文案/标签集中在渲染层之外，供 EvalView / EvalDetailView 与测试复用。
 * 所有枚举值均对齐后端真实字段（verdict/status/layer status/reason 键）。
 */

/** L1..L6 固定顺序与中文名（与 runner 的层编号一致）。 */
export const LAYERS: { id: string; name: string }[] = [
  { id: 'L1', name: '结构确定性' },
  { id: 'L2', name: '图谱一致性' },
  { id: 'L3', name: '内容质量' },
  { id: 'L4', name: '忠实度 faithfulness' },
  { id: 'L5', name: 'KP 可用性' },
  { id: 'L6', name: '回归' },
];

/** L1 确定性维度 & L3 质量维度的中文名。 */
export const DIM_LABELS: Record<string, string> = {
  structural: '结构性',
  consistency: '一致性',
  depth: '深度',
  playability: '可玩性',
};

export const VERDICT_LABELS: Record<string, string> = {
  pass: '通过',
  warning: '警告',
  fail: '失败',
  error: '错误',
};

export const RUN_STATUS_LABELS: Record<string, string> = {
  running: '运行中',
  completed: '完成',
  short_circuited: '短路',
  error: '错误',
};

export const LAYER_STATUS_LABELS: Record<string, string> = {
  passed: '通过',
  failed: '失败',
  skipped: '跳过',
  degraded: '降级',
  running: '运行中',
};

/** skipped / degraded 的 reason 键 → 中文说明（未知键原样回退）。 */
export const LAYER_REASON_LABELS: Record<string, string> = {
  llm_disabled: 'LLM 裁判未启用',
  budget_exceeded: '预算耗尽',
  cascade_gate_failed: '前置层未通过，级联跳过',
  no_claims: '没有可核验的声明',
  no_corpus: '没有语料（未 ingest 模组）',
  manual_only: '该层仅支持人工评测',
  no_prior_run: '没有历史运行可对比',
};

export function verdictLabel(v: string | null | undefined): string {
  return (v && VERDICT_LABELS[v]) || '—';
}

export function runStatusLabel(s: string | null | undefined): string {
  return (s && RUN_STATUS_LABELS[s]) || '—';
}

export function layerStatusLabel(s: string | null | undefined): string {
  return (s && LAYER_STATUS_LABELS[s]) || '—';
}

export function layerReasonLabel(reason: string | undefined): string {
  if (!reason) return '';
  return LAYER_REASON_LABELS[reason] ?? reason;
}

/** 预算花费：$xx.xx（缺省显示 $0.00）。 */
export function formatUsd(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '$0.00';
  return `$${value.toFixed(2)}`;
}

/** 耗时：<1s 显示毫秒，否则显示秒。 */
export function formatDuration(ms: number | null | undefined): string {
  if (ms == null || Number.isNaN(ms)) return '0ms';
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

/** L4 支持比 0~1 → 百分数文本（缺省 '—'）。 */
export function ratioPercent(ratio: number | null | undefined): string {
  if (ratio == null || Number.isNaN(ratio)) return '—';
  return `${Math.round(ratio * 100)}%`;
}
