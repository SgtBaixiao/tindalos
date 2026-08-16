/**
 * site/api.ts —— 站点层类型化 fetch 客户端（API 契约）。
 *
 * Base：fetch('/api/...')（vite dev proxy → 127.0.0.1:8347 / 生产同源）。
 * 生成流复用 lib/live 的 fetchGenerateStream（POST SSE 已实现），不重复造轮子。
 *
 * 路径约定（后端并行实现对齐）：
 *  - GET  /api/health
 *  - GET  /api/modules                       → {modules}
 *  - POST /api/modules                       → {module}（FormData multipart，file + rules?）
 *  - GET  /api/modules/<id>                  → {module: ModuleDetail}
 *  - POST /api/modules/<id>/ingest           → {indexed, chunks}
 *  - POST /api/modules/<id>/confirm-image    → {ok, images}
 *  - GET  /api/modules/history               → {modules}
 *  - POST /api/rag/search                    → {results}
 *  - POST /api/qa                            → {answer, sources, mode}
 *  - GET  /api/campaigns                     → {campaigns}
 *  - GET  /api/campaigns/<id>                → {campaign, meta}
 */

import { fetchGenerateStream, type SseFrame } from '../lib/live';
import type {
  Campaign,
  CampaignMeta,
  Module,
  ModuleDetail,
  QaResult,
  RagHit,
  VisionResult,
} from './types';

type ApiErrorBody = { error?: string };

/** 统一 JSON 请求：非 2xx 抛服务端 error 消息（或 HTTP 状态）。 */
async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) {
    const data = (await res.json().catch(() => null)) as ApiErrorBody | null;
    throw new Error(data?.error ?? `HTTP ${res.status}`);
  }
  return (await res.json()) as T;
}

/** 健康检查：失败/非 2xx 一律返回 {ok:false}，不抛错。 */
export async function health(): Promise<{ ok: boolean }> {
  try {
    const data = await request<{ ok?: boolean }>('/api/health');
    return { ok: data.ok === true };
  } catch {
    return { ok: false };
  }
}

/**
 * 生成剧本：直接复用 lib/live 的 fetchGenerateStream（POST /api/generate，SSE 流）。
 * 工作台侧由 ProgressBand（live + moduleText）消费。
 */
export function generate(
  moduleText: string,
  opts: { llm?: boolean; signal?: AbortSignal } = {},
): AsyncGenerator<SseFrame> {
  return fetchGenerateStream(moduleText, opts);
}

/** 上传模组 PDF（multipart：file + rules?）。 */
export async function uploadModule(file: File, rules?: string): Promise<{ module: Module }> {
  const fd = new FormData();
  fd.append('file', file);
  if (rules) fd.append('rules', rules);
  return request<{ module: Module }>('/api/modules', { method: 'POST', body: fd });
}

/** 已入库模组列表（资料库）。 */
export async function listModules(): Promise<{ modules: Module[] }> {
  return request<{ modules: Module[] }>('/api/modules');
}

/** 模组详情：文本预览 + 视觉结果。 */
export async function getModule(id: string): Promise<{ module: ModuleDetail }> {
  return request<{ module: ModuleDetail }>(`/api/modules/${encodeURIComponent(id)}`);
}

/** 人工确认低置信视觉项：kind（人物像/地图/场景/封面）+ 名字。 */
export async function confirmImage(
  moduleId: string,
  body: { image_path: string; kind: string; name?: string; caption?: string },
): Promise<{ ok: boolean; images: VisionResult[] }> {
  return request<{ ok: boolean; images: VisionResult[] }>(
    `/api/modules/${encodeURIComponent(moduleId)}/confirm-image`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    },
  );
}

/** 建立模组索引（RAG 分块）。 */
export async function ingestModule(moduleId: string): Promise<{ indexed: boolean; chunks: number }> {
  return request<{ indexed: boolean; chunks: number }>(
    `/api/modules/${encodeURIComponent(moduleId)}/ingest`,
    { method: 'POST' },
  );
}

/** RAG 全文检索（模组材料）。 */
export async function searchRag(
  query: string,
  opts: { module_id?: string; top_k?: number } = {},
): Promise<{ results: RagHit[] }> {
  return request<{ results: RagHit[] }>('/api/rag/search', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, module_id: opts.module_id, top_k: opts.top_k }),
  });
}

/** 规则问答：规则书/模组材料 RAG 问答。 */
export async function qa(
  question: string,
  opts: { module_id?: string; rules?: string } = {},
): Promise<QaResult> {
  return request<QaResult>('/api/qa', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, module_id: opts.module_id, rules: opts.rules }),
  });
}

/** 历史记录 · 上传的模组。 */
export async function listModulesHistory(): Promise<{ modules: Module[] }> {
  return request<{ modules: Module[] }>('/api/modules/history');
}

/** 历史记录 · 生成的剧本元信息列表。 */
export async function listCampaigns(): Promise<{ campaigns: CampaignMeta[] }> {
  return request<{ campaigns: CampaignMeta[] }>('/api/campaigns');
}

/** 重放：按 id 取完整剧本。 */
export async function getCampaign(id: string): Promise<{ campaign: Campaign; meta?: CampaignMeta }> {
  return request<{ campaign: Campaign; meta?: CampaignMeta }>(
    `/api/campaigns/${encodeURIComponent(id)}`,
  );
}
