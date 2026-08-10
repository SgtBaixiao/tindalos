/**
 * live.ts —— 前端三期「实时」基础设施（t13）：
 *  - parseSSEEvent：单条 SSE 帧解析（进度/结束/错误/截断）
 *  - SseStreamParser：分块缓冲流解析（半帧续接、多帧一冲）
 *  - fetchRegenerate：POST /api/regenerate（30s 超时）→ {campaign, applied}
 *  - patchGraphFromCampaign：重生成响应 → store 补丁（节点 data + 边）
 *  - isLive / getCampaignIdFromQuery / campaignSourceUrl / loadCampaign：
 *    ?live=1 判定与数据源路由（API ↔ 静态 fallback）
 *
 * API 契约（serve.py，前端依赖勿改）：
 *  POST /api/generate    → SSE：data:{stage,message} … data:{done:true,campaign}
 *  POST /api/regenerate  body={campaign_id,node_id} → {ok,campaign,applied}
 *  GET  /api/campaigns/<id> → campaign JSON（未知 id 404）
 */

import { buildScriptGraph } from './scriptGraph';
import type { CampaignView, GraphEdge } from './types';
import { parseProgressEvents, type ProgressEvent } from './progress';

/** SSE 生成流入口（ProgressBand live 模式连接地址）。 */
export const GENERATE_SSE_URL = '/api/generate';

/** 重生成请求超时：30s（与后端整链生成时长对齐的量级）。 */
export const REGENERATE_TIMEOUT_MS = 30_000;

/** 解析后的 SSE 帧（API 契约三分支）。 */
export type SseFrame =
  | { kind: 'progress'; stage: string; message: string }
  | { kind: 'done'; campaign: CampaignView | null }
  | { kind: 'error'; message: string };

/** fetchRegenerate 成功载荷。 */
export type RegenerateResult = { campaign: CampaignView; applied: string[] };

/** JSON.parse 安全包装：失败抛 SyntaxError（供调用方兜底降级）。 */
function safeParse(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    throw new SyntaxError(`SSE data 非合法 JSON: ${text.slice(0, 40)}`);
  }
}

/** 载荷分类：{stage,message} → progress；{done:true,campaign} → done；{error} → error。 */
function classify(payload: unknown): SseFrame {
  if (typeof payload !== 'object' || payload === null) {
    throw new SyntaxError('SSE data 非 JSON 对象');
  }
  const p = payload as Record<string, unknown>;
  if (p.done === true) {
    const campaign = (typeof p.campaign === 'object' && p.campaign !== null ? p.campaign : null) as
      | CampaignView
      | null;
    return { kind: 'done', campaign };
  }
  if (typeof p.error === 'string') {
    return { kind: 'error', message: p.error };
  }
  if (typeof p.stage === 'string' && typeof p.message === 'string') {
    return { kind: 'progress', stage: p.stage, message: p.message };
  }
  throw new SyntaxError('SSE data 缺少 stage/message/done/error 契约字段');
}

/**
 * 解析一条完整 SSE 帧 → SseFrame。
 * - 帧格式：`data:{json}` 行（可多行）以空行 `\n\n` 结束；`data:` 首空格剥除；
 *   注释/`event:`/`id:` 行忽略。
 * - 截断：帧未以空行结束 → 返回 null（调用方缓冲续接，见 SseStreamParser）。
 * - 容错：EventSource onmessage 的裸 JSON 载荷（无 `data:` 前缀）直接解析；
 *   CRLF（\r\n\r\n）帧兼容。
 * - 错误：完整帧内 JSON 非法或缺契约字段 → 抛 SyntaxError。
 */
export function parseSSEEvent(raw: string): SseFrame | null {
  const text = raw.replace(/\r\n/g, '\n');
  const trimmed = text.trim();
  if (trimmed.length === 0) return null;

  const sep = text.indexOf('\n\n');
  const isBarePayload = !trimmed.startsWith('data:') && sep === -1;
  if (isBarePayload) {
    return classify(safeParse(trimmed));
  }
  if (sep === -1) return null; // 截断：帧未以空行结束

  const block = text.slice(0, sep);
  const dataLines = block
    .split('\n')
    .map((line) => line.trimStart())
    .filter((line) => line.startsWith('data:'))
    .map((line) => line.slice('data:'.length).trim());
  if (dataLines.length === 0) return null; // 注释/心跳帧

  return classify(safeParse(dataLines.join('\n')));
}

/**
 * 分块缓冲流解析器：逐 chunk push，完整帧逐条吐出；
 * 半帧（截断）留在缓冲中续接，一冲多帧按序全部返回。
 */
export class SseStreamParser {
  private buffer = '';

  push(chunk: string): SseFrame[] {
    // 拼接后整体归一 CRLF：跨 chunk 拆分的 \r\n 也能续接
    this.buffer = `${this.buffer}${chunk}`.replace(/\r\n/g, '\n');
    const frames: SseFrame[] = [];
    let sep = this.buffer.indexOf('\n\n');
    while (sep !== -1) {
      const block = this.buffer.slice(0, sep);
      this.buffer = this.buffer.slice(sep + 2);
      const frame = parseSSEEvent(`${block}\n\n`);
      if (frame) frames.push(frame);
      sep = this.buffer.indexOf('\n\n');
    }
    return frames;
  }
}

/**
 * SSE 进度帧 → ProgressBand 进度事件（agent 推导：stage=npc → npc，其余 kp）。
 */
export function sseToProgressEvent(
  frame: Extract<SseFrame, { kind: 'progress' }>,
): ProgressEvent {
  const isNpc = frame.stage === 'npc';
  const npcMatch = /NPC\s+([^\s·:：]+)/.exec(frame.message);
  return {
    ts: new Date().toISOString(),
    agent: isNpc ? 'npc' : 'kp',
    step: isNpc ? 'NPC 生成' : frame.message,
    text: frame.message,
    stage: frame.stage,
    npc: isNpc && npcMatch ? npcMatch[1] : undefined,
  };
}

/**
 * fetchRegenerate：POST /api/regenerate，body={campaign_id,node_id}。
 * - 30s 超时（AbortController + setTimeout）；可传入外部 signal 协同取消。
 * - 非 2xx → 抛出服务端 error 消息；响应缺 campaign → 抛契约错误。
 * 返回 { campaign, applied }（applied：被重生成的节点 id 列表）。
 */
export async function fetchRegenerate(
  campaignId: string,
  nodeId: string,
  init: { signal?: AbortSignal } = {},
): Promise<RegenerateResult> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REGENERATE_TIMEOUT_MS);
  const onOuterAbort = () => controller.abort(init.signal?.reason);
  init.signal?.addEventListener('abort', onOuterAbort, { once: true });
  try {
    const res = await fetch('/api/regenerate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ campaign_id: campaignId, node_id: nodeId }),
      signal: controller.signal,
    });
    const data = (await res.json().catch(() => null)) as Record<string, unknown> | null;
    if (!res.ok) {
      throw new Error(typeof data?.error === 'string' ? data.error : `HTTP ${res.status}`);
    }
    if (!data || data.ok !== true || typeof data.campaign !== 'object' || data.campaign === null) {
      throw new Error('regenerate 响应缺少 campaign');
    }
    return {
      campaign: data.campaign as CampaignView,
      applied: Array.isArray(data.applied) ? (data.applied as string[]) : [],
    };
  } finally {
    clearTimeout(timer);
    init.signal?.removeEventListener('abort', onOuterAbort);
  }
}

/**
 * 重生成补丁计算：由新 campaign 单点推导目标节点 data 与整图 edges
 * （剧本 JSON 是唯一真相，边随关系重建）。调用方负责写入 store。
 */
export function patchGraphFromCampaign(
  campaign: CampaignView,
  nodeId: string,
): { nodeData: Record<string, unknown>; edges: GraphEdge[] } {
  const { nodes: mapped, edges } = buildScriptGraph(campaign);
  const target = mapped.find((n) => n.id === nodeId);
  if (!target) {
    throw new Error(`campaign 中未找到节点 ${nodeId}`);
  }
  return { nodeData: target.data, edges };
}

/** ?live=1 判定（URLSearchParams 首值，任意位置均可）。 */
export function isLive(search: string = window.location.search): boolean {
  return new URLSearchParams(search).get('live') === '1';
}

/** ?campaign=<id> 读取（App live 模式加载目标 campaign）。 */
export function getCampaignIdFromQuery(search: string = window.location.search): string | null {
  return new URLSearchParams(search).get('campaign');
}

/** 数据源路由：live+id → /api/campaigns/<id>；离线/无 id → 静态 public/campaign.json。 */
export function campaignSourceUrl(live: boolean, campaignId: string | null): string {
  if (live && campaignId) return `/api/campaigns/${encodeURIComponent(campaignId)}`;
  return `${import.meta.env.BASE_URL}campaign.json`;
}

async function fetchCampaignJson(url: string): Promise<CampaignView> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const json: unknown = await res.json();
  const campaign = (json as { campaign?: CampaignView } | null)?.campaign ?? json;
  return campaign as CampaignView;
}

/**
 * 加载 campaign：live 时 GET /api/campaigns/<id>，API 失败回退静态
 * public/campaign.json；离线直接读静态（无回退）。
 */
export async function loadCampaign(live: boolean, campaignId: string | null): Promise<CampaignView> {
  if (!live || !campaignId) {
    return fetchCampaignJson(campaignSourceUrl(false, null));
  }
  try {
    return await fetchCampaignJson(campaignSourceUrl(true, campaignId));
  } catch (err) {
    // 后端未起 / campaign 未缓存 → 离线回退静态（同 ProgressBand 哲学）
    try {
      return await fetchCampaignJson(campaignSourceUrl(false, null));
    } catch {
      throw err;
    }
  }
}

/** 静态 progress.jsonl 读取（ProgressBand 离线/回退共用）。 */
export async function fetchStaticProgress(
  baseUrl: string = import.meta.env.BASE_URL,
): Promise<ProgressEvent[]> {
  const res = await fetch(`${baseUrl}progress.jsonl`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return parseProgressEvents(await res.text());
}
