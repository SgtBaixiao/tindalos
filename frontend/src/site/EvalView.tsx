import './styles/eval.css';
import { PageHeader } from '../ui/PageHeader';
import { formatDateTime } from '../ui/format';
/**
 * site/EvalView.tsx —— 评测列表页。
 *
 * GET /api/eval/runs → 运行卡片列表（新→旧）。每张卡片展示 campaign 标题、
 * verdict、状态、预算花费、耗时、创建时间与 run_id；点击进入详情
 * #/eval/<run_id>。载入中 / 空列表 / 请求失败均有占位。
 */

import { useEffect, useState } from 'react';
import { listEvalRuns } from './api';
import type { EvalRun } from './types';
import { formatDuration, formatUsd, runStatusLabel, verdictLabel } from './evalFormat';

type EvalViewProps = {
  navigate: (to: string) => void;
};

export function EvalView({ navigate }: EvalViewProps) {
  const [runs, setRuns] = useState<EvalRun[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setError(null);
    listEvalRuns()
      .then(({ runs: list }) => {
        if (alive) setRuns(list ?? []);
      })
      .catch((err: unknown) => {
        if (alive) setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      alive = false;
    };
  }, []);

  return (
    <section className="sx-eval" data-testid="sx-eval">
      <PageHeader
        eyebrow="05 / REVIEW & ADOPTION"
        title="评测"
        description="检查剧本修订的结构、内容与来源，留下自己的使用判断。"
      />

      {error && (
        <p className="sx-error" data-testid="eval-error">
          {error}
        </p>
      )}

      {runs === null ? (
        <p className="sx-empty">载入中…</p>
      ) : runs.length === 0 ? (
        <p className="sx-empty" data-testid="eval-empty">
          还没有评测记录。在剧本工作台打开已保存剧本，即可评测当前版本。
        </p>
      ) : (
        <ul className="sx-eval-list">
          {runs.map((run) => (
            <li key={run.run_id}>
              <button
                type="button"
                className="sx-eval-card"
                onClick={() => navigate(`#/eval/${run.run_id}`)}
                aria-label={`查看评测 ${run.campaign_title || run.run_id}`}
              >
                <span className="sx-eval-card__top">
                  <span className="sx-eval-card__title">
                    {run.campaign_title || run.campaign_id || run.subject_ref || '未命名'}
                  </span>
                  <span className={`sx-verdict is-${run.verdict ?? 'none'}`}>
                    {verdictLabel(run.verdict)}
                  </span>
                </span>
                <span className="sx-eval-card__meta">
                  <span className={`sx-badge sx-badge--status is-${run.status ?? 'none'}`}>
                    {runStatusLabel(run.status)}
                  </span>
                  {formatDateTime(run.created_at)}
                </span>
                <span className="sx-eval-card__meta">
                  {formatUsd(run.budget_spent_usd)} · {formatDuration(run.duration_ms)}
                </span>
                <span className="sx-eval-card__runid">{run.run_id}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
