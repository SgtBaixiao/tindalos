import { memo, useRef, useState, type FormEvent } from 'react';
import { json, request, requestKey } from '../../lib/workspace';
export const SessionPanel = memo(function SessionPanel({
  campaignId,
  revision,
  busy,
}: {
  campaignId: string;
  revision: number;
  busy: boolean;
}) {
  const [summary, setSummary] = useState('');
  const [facts, setFacts] = useState('');
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [evaluating, setEvaluating] = useState(false);
  const pending = useRef<{ key: string; body: string } | null>(null);
  const record = async (event: FormEvent) => {
    event.preventDefault();
    if (saving || busy) return;
    setSaving(true);
    setError('');
    setMessage('');
    try {
      const parsed = facts
        .split('\n')
        .filter((line) => line.trim())
        .map((line) => {
          const at = line.indexOf('=');
          if (at < 1) throw new Error('事实每行填写：主体.状态=内容');
          return {
            subject_key: line.slice(0, at).trim(),
            content: line.slice(at + 1).trim(),
            audience: 'kp',
          };
        });
      const encoded = JSON.stringify({ campaignId, summary, facts: parsed });
      if (pending.current?.body !== encoded) pending.current = { key: requestKey(), body: encoded };
      await request(
        `/api/sessions/${encodeURIComponent(campaignId)}`,
        json('POST', { summary, facts: parsed, request_key: pending.current!.key }),
      );
      pending.current = null;
      setSummary('');
      setFacts('');
      setMessage('本场记录已保存，续团将引用这些实际记录。');
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  };
  const evaluate = async () => {
    if (evaluating || busy) return;
    setEvaluating(true);
    setError('');
    try {
      const d = await request<{ trace: { verdict: string; run_id: string } }>(
        '/api/eval/run',
        json('POST', { campaign_id: campaignId }),
      );
      setMessage(`版本 ${revision} 的评测已保存。`);
      window.location.hash = `#/eval/${d.trace.run_id}`;
    } catch (e) {
      setError(String(e));
    } finally {
      setEvaluating(false);
    }
  };
  return (
    <details className="wb-session">
      <summary>
        记录本场与评测<span>02 / 延续</span>
      </summary>
      <div className="wb-session__body">
        <form onSubmit={record}>
          <label className="wb-field">
            <span>实际游玩记录</span>
            <textarea
              aria-label="实际游玩记录"
              value={summary}
              onChange={(e) => setSummary(e.target.value)}
              required
              rows={4}
            />
          </label>
          <label className="wb-field">
            <span>KP 确认事实</span>
            <textarea
              aria-label="确认事实"
              value={facts}
              onChange={(e) => setFacts(e.target.value)}
              placeholder="每行：主体.状态=内容"
              rows={3}
            />
          </label>
          <button className="sx-btn sx-btn--ink" disabled={busy || saving || !summary.trim()}>
            {saving ? '保存中…' : '保存本场'}
          </button>
        </form>
        <button
          className="sx-btn sx-btn--ghost"
          disabled={busy || evaluating}
          onClick={() => void evaluate()}
        >
          {evaluating ? '评测中…' : '评测当前版本'}
        </button>
        {message && (
          <p role="status" className="wb-hint">
            {message}
          </p>
        )}
        {error && <p role="alert">{error}</p>}
      </div>
    </details>
  );
});
