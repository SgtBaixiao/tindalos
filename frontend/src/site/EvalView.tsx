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
      <div className="sx-eval__head">
        <h1 className="sx-eval__title">评测</h1>
        <p className="sx-eval__desc">
          六层评测 trace：L1 结构确定性 → L2 图谱一致性 → L3 内容质量 → L4 忠实度 → L5 KP 可用性 → L6 回归。
        </p>
      </div>

      {error && (
        <p className="sx-error" data-testid="eval-error">
          {error}
        </p>
      )}

      {runs === null ? (
        <p className="sx-empty">载入中…</p>
      ) : runs.length === 0 ? (
        <p className="sx-empty" data-testid="eval-empty">
          还没有评测记录 —— 在剧本工作台生成后运行一次评测即可看到 trace。
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
                  {run.created_at ? run.created_at.replace('T', ' ').slice(0, 19) : ''}
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
