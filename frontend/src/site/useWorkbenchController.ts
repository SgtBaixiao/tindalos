import { useCallback, useRef, useState } from 'react';
import { useGraphStore } from '../store/useGraphStore';
import { loadRevision, request, requestKey, submitJob } from '../lib/workspace';
import type { GenerationInput } from '../features/workbench/SourceForm';
import { useCatalog } from '../features/workbench/useCatalog';

export function useWorkbenchController() {
  const { modules, campaigns, error: catalogError, refresh } = useCatalog();
  const [jobId, setJobId] = useState<string | null>(() =>
    localStorage.getItem('tindalos-active-job'),
  );
  const [busy, setBusy] = useState(Boolean(jobId));
  const [error, setError] = useState('');
  const [opening, setOpening] = useState(false);
  const campaignId = useGraphStore((s) => s.campaignId);
  const title = useGraphStore((s) => s.campaign?.title);
  const revision = useGraphStore((s) => s.revision);
  const mode = useGraphStore((s) => s.campaign?.generation);
  const adopt = useGraphStore((s) => s.adoptCampaign);
  const pending = useRef<{ key: string; body: string } | null>(null);
  const terminal = useCallback(() => {
    setBusy(false);
    pending.current = null;
    void refresh();
  }, [refresh]);
  const start = useCallback(
    async (
      body: GenerationInput | { campaign_id: string; expected_revision: number; llm: boolean },
    ) => {
      if (busy) return;
      setBusy(true);
      setError('');
      const encoded = JSON.stringify(body);
      try {
        if (pending.current?.body !== encoded)
          pending.current = { key: requestKey(), body: encoded };
        const job = await submitJob(body, pending.current!.key);
        localStorage.setItem('tindalos-active-job', job.id);
        setJobId(job.id);
      } catch (e) {
        setError(String(e));
        setBusy(false);
      }
    },
    [busy],
  );
  const generate = useCallback((input: GenerationInput) => void start(input), [start]);
  const continueCampaign = useCallback(
    (llm: boolean) => {
      if (campaignId && revision)
        void start({ campaign_id: campaignId, expected_revision: revision, llm });
    },
    [campaignId, revision, start],
  );
  const cancelJob = useCallback(async () => {
    if (!jobId) return;
    await request(`/api/jobs/${encodeURIComponent(jobId)}`, { method: 'DELETE' }).catch((e) =>
      setError(String(e)),
    );
  }, [jobId]);
  const openCampaign = useCallback(
    async (id: string) => {
      if (!id) return;
      setOpening(true);
      setError('');
      try {
        const doc = await loadRevision(id);
        adopt(doc.campaign, doc.revision, false);
      } catch (err) {
        setError(String(err));
      } finally {
        setOpening(false);
      }
    },
    [adopt],
  );
  return {
    modules,
    campaigns,
    catalogError,
    campaignId,
    title,
    revision,
    mode,
    jobId,
    busy,
    opening,
    error,
    generate,
    continueCampaign,
    cancelJob,
    openCampaign,
    terminal,
  };
}
