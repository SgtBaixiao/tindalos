import { useEffect, useState } from 'react';
import { useGraphStore } from '../store/useGraphStore';
import { loadRevision, request, type Job } from '../lib/workspace';
import type { CampaignView } from '../lib/types';

/** Subscription effects never create a generation job. Browser SSE reconnects with its cursor. */
export function JobProgress({ jobId, onTerminal }: { jobId: string; onTerminal: () => void }) {
  const adopt = useGraphStore((s) => s.adoptCampaign);
  const [message, setMessage] = useState('读取任务状态…');
  const [error, setError] = useState('');
  useEffect(() => {
    let alive = true;
    let source: EventSource | null = null;
    let cursor = 0;
    const adoptCurrent = (campaign: CampaignView, revision: number | null) => {
      const state = useGraphStore.getState();
      if (
        state.campaignId === campaign.id &&
        state.revision &&
        revision &&
        state.revision > revision
      )
        return;
      adopt(campaign, revision, false);
    };
    const finish = () => {
      source?.close();
      localStorage.removeItem('tindalos-active-job');
      onTerminal();
    };
    const receive = (data: {
      message?: string;
      error?: string;
      done?: boolean;
      status?: string;
      campaign?: CampaignView;
      revision?: number;
    }) => {
      if (!alive) return;
      if (data.message) setMessage(data.message);
      if (data.error) setError(data.error);
      if (data.done) {
        if (data.status === 'completed' && data.campaign) {
          adoptCurrent(data.campaign, data.revision ?? null);
          setMessage(`任务已完成，保存版本 ${data.revision}`);
        } else setError(data.error ?? `任务${data.status ?? '未完成'}`);
        finish();
      }
    };
    request<{ job: Job }>(`/api/jobs/${encodeURIComponent(jobId)}`)
      .then(async ({ job }) => {
        if (!alive) return;
        if (job.status === 'completed' && job.campaign_id) {
          const doc = await loadRevision(job.campaign_id);
          if (alive) {
            adoptCurrent(doc.campaign, doc.revision);
            setMessage(`已恢复版本 ${doc.revision}`);
            finish();
          }
        } else if (['failed', 'cancelled', 'interrupted'].includes(job.status)) {
          setError(job.error ?? job.status);
          finish();
        } else {
          source = new EventSource(`/api/jobs/${encodeURIComponent(jobId)}/events`);
          source.onmessage = (event) => {
            const sequence = Number(event.lastEventId);
            if (sequence && sequence <= cursor) return;
            if (sequence) cursor = sequence;
            try {
              receive(JSON.parse(event.data));
            } catch (err) {
              setError(String(err));
            }
          };
          source.onerror = () => {
            if (alive) setMessage('连接中断，正在重连原任务…');
          };
        }
      })
      .catch((err) => {
        if (alive) setError(String(err));
      });
    return () => {
      alive = false;
      source?.close();
    };
  }, [jobId, adopt, onTerminal]);
  return (
    <div className="sx-job-progress" aria-label="生成进度">
      <p role="status">{message}</p>
      {error && <p role="alert">{error}</p>}
    </div>
  );
}
