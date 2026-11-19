import { useCallback, useRef, useState } from 'react';
import App from '../App';
import { JobProgress } from '../components/JobProgress';
import { useGraphStore } from '../store/useGraphStore';
import { loadRevision, request, requestKey, submitJob } from '../lib/workspace';
import { SourceForm, type GenerationInput } from '../features/workbench/SourceForm';
import { SessionPanel } from '../features/workbench/SessionPanel';
import { useCatalog } from '../features/workbench/useCatalog';
import { PageHeader } from '../ui/PageHeader';
import { Icon } from '../ui/Icon';
import './styles/workbench.css';

export function WorkbenchView() {
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
  return (
    <section className="sx-workbench" data-testid="sx-workbench">
      <div className="wb-heading">
        <PageHeader
          eyebrow="01 / THE WORKTABLE"
          title="剧本工作台"
          description="把材料展开，在下一场开始前推敲每个细节。"
        />
        <div className="wb-heading__note">
          <Icon name="compass" />
          <span>
            准备有依据
            <br />
            保存有版本
          </span>
        </div>
      </div>
      <div className="wb-layout">
        <aside className="wb-preparation">
          <SourceForm
            modules={modules}
            busy={busy || opening}
            canContinue={Boolean(campaignId && revision)}
            onGenerate={generate}
            onContinue={continueCampaign}
          />
          {campaignId && revision && (
            <SessionPanel
              key={campaignId}
              campaignId={campaignId}
              revision={revision}
              busy={busy || opening}
            />
          )}
          {busy && jobId && (
            <button
              className="sx-btn sx-btn--ghost"
              onClick={() =>
                request(`/api/jobs/${encodeURIComponent(jobId)}`, { method: 'DELETE' }).catch((e) =>
                  setError(String(e)),
                )
              }
            >
              取消任务
            </button>
          )}
          {jobId && <JobProgress key={jobId} jobId={jobId} onTerminal={terminal} />}
          {(error || catalogError) && <p role="alert">{error || catalogError}</p>}
        </aside>
        <div className="wb-document">
          <div className="wb-document__head">
            <div>
              <span className="wb-section-label">CURRENT DRAFT</span>
              <h2>{title ?? '尚未展开的故事'}</h2>
            </div>
            <label className="wb-open">
              <span>打开已保存剧本</span>
              <select
                aria-label="打开已保存剧本"
                value={campaignId ?? ''}
                disabled={busy || opening}
                onChange={async (e) => {
                  if (!e.target.value) return;
                  setOpening(true);
                  setError('');
                  try {
                    const doc = await loadRevision(e.target.value);
                    adopt(doc.campaign, doc.revision, false);
                  } catch (err) {
                    setError(String(err));
                  } finally {
                    setOpening(false);
                  }
                }}
              >
                <option value="">选择剧本</option>
                {campaigns.map((c) => (
                  <option value={c.id} key={c.id}>
                    {c.title ?? c.id}
                  </option>
                ))}
              </select>
            </label>
          </div>
          {revision && (
            <div className="wb-revision" role="status">
              <span className="wb-revision__dot" />
              当前保存版本 {revision}
              <span>{mode?.mode === 'llm' ? '模型生成' : '离线模板'}</span>
              {mode?.degraded && <span>部分步骤已使用模板替代，请审阅</span>}
            </div>
          )}
          <div className="sx-workbench__stage">
            <App workspace />
            {!campaignId && (
              <div className="wb-empty">
                <Icon name="compass" />
                <h3>从一份材料开始</h3>
                <p>
                  输入正文或选择模组，生成第一份备团草稿。
                  <br />
                  已有故事也可以从上方重新打开。
                </p>
                <span>SOURCE → DRAFT → FIELD NOTES</span>
              </div>
            )}
          </div>
          <div className="wb-document__foot">
            <span>点击节点查看与编辑 · 拖动整理 · 滚轮缩放</span>
            <span>THE THREADS OF YOUR STORY</span>
          </div>
        </div>
      </div>
    </section>
  );
}
