import type { CampaignView } from './types';
export type Job = {
  id: string;
  status: string;
  campaign_id: string | null;
  revision: number | null;
  error: string | null;
};
export function requestKey(): string {
  if (typeof globalThis.crypto?.randomUUID === 'function') return crypto.randomUUID();
  const bytes = new Uint8Array(16);
  if (typeof globalThis.crypto?.getRandomValues === 'function') crypto.getRandomValues(bytes);
  else for (let i = 0; i < bytes.length; i++) bytes[i] = Math.floor(Math.random() * 256);
  return `${Date.now().toString(36)}-${Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')}`;
}
export async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  const doc = await res.json().catch(() => null);
  if (!res.ok) throw new Error(doc?.detail ?? doc?.error ?? `HTTP ${res.status}`);
  return doc as T;
}
export function json(method: string, body: unknown): RequestInit {
  return { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
}
export async function submitJob(body: unknown, key: string): Promise<Job> {
  const init = json('POST', body);
  init.headers = { ...init.headers, 'Idempotency-Key': key };
  return (await request<{ job: Job }>('/api/jobs', init)).job;
}
export async function loadRevision(id: string) {
  const doc = await request<{ campaign: CampaignView; meta: { revision: number } }>(
    `/api/campaigns/${encodeURIComponent(id)}`,
  );
  return { campaign: doc.campaign, revision: doc.meta.revision };
}
export function saveNode(
  id: string,
  node: string,
  revision: number,
  patch: Record<string, unknown>,
) {
  return request<{ campaign: CampaignView; revision: number }>(
    `/api/campaigns/${encodeURIComponent(id)}/nodes/${encodeURIComponent(node)}`,
    json('PATCH', { expected_revision: revision, patch }),
  );
}
