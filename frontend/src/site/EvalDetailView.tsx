/**
 * site/EvalDetailView.tsx —— 评测详情页（#/eval/<run_id>）。
 *
 * GET /api/eval/runs/<run_id> → {run, annotations}。
 * 渲染 run 概要（verdict / status / 耗时 / 预算 / 模组参数）、L1..L6 分层 trace
 * （各层按后端实际形状差异化展示：L1 分数+证据 / L2 问题清单 / L3 LLM 裁判维度
 *  / L4 声明支持比 / L5 人工 / L6 回归），以及各层标注（含 evidence_refs）。
 *
 * 注：GET 端点落盘行没有 judge_model / 嵌套 budget，裁判信息取自 L3 layer 的
 * judge 字段，预算用扁平 budget_spent_usd —— 不臆造后端不存在的字段。
 */

import { useEffect, useState } from 'react';
import { getEvalRun } from './api';
import type { EvalAnnotation, EvalDim, EvalLayer, EvalRun } from './types';
import {
  DIM_LABELS,
  LAYERS,
  formatDuration,
  formatUsd,
  layerReasonLabel,
  layerStatusLabel,
  ratioPercent,
  runStatusLabel,
  verdictLabel,
} from './evalFormat';

type EvalDetailViewProps = {
  runId: string;
  navigate: (to: string) => void;
};

export function EvalDetailView({ runId, navigate }: EvalDetailViewProps) {
  const [run, setRun] = useState<EvalRun | null>(null);
  const [annotations, setAnnotations] = useState<EvalAnnotation[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setRun(null);
    setAnnotations([]);
    setError(null);
    getEvalRun(runId)
      .then(({ run: r, annotations: a }) => {
        if (alive) {
          setRun(r);
          setAnnotations(a ?? []);
        }
      })
      .catch((err: unknown) => {
        if (alive) setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      alive = false;
    };
  }, [runId]);

  return (
    <section className="sx-eval" data-testid="sx-eval-detail">
      <div className="sx-eval__nav">
        <button type="button" className="sx-btn sx-btn--ghost" onClick={() => navigate('#/eval')}>
          ← 评测列表
        </button>
      </div>

      {error ? (
        <p className="sx-error" data-testid="eval-detail-error">
          {error}
        </p>
      ) : run ? (
        <RunDetail run={run} annotations={annotations} />
      ) : (
        <p className="sx-empty">载入中…</p>
      )}
    </section>
  );
}

/* ---------------------------------------------------------------- 概要 */

function RunDetail({ run, annotations }: { run: EvalRun; annotations: EvalAnnotation[] }) {
  const layers = run.layers ?? {};
  const subject = run.campaign_title || run.campaign_id || run.subject_ref || '评测';

  return (
    <>
      <div className="sx-eval-runhead">
        <h1 className="sx-eval-runhead__title">{subject}</h1>
        <p className="sx-eval-runhead__meta" data-testid="eval-run-meta">
          {run.run_id}
          {run.created_at ? ` · ${run.created_at.replace('T', ' ').slice(0, 19)}` : ''}
        </p>
      </div>

      <div className="sx-eval-stats">
        <div className="sx-eval-stat">
          <span className="sx-eval-stat__label">verdict</span>
          <span className={`sx-verdict is-${run.verdict ?? 'none'}`}>{verdictLabel(run.verdict)}</span>
        </div>
        <div className="sx-eval-stat">
          <span className="sx-eval-stat__label">状态</span>
          <span className="sx-eval-stat__value">{runStatusLabel(run.status)}</span>
        </div>
        <div className="sx-eval-stat">
          <span className="sx-eval-stat__label">耗时</span>
          <span className="sx-eval-stat__value">{formatDuration(run.duration_ms)}</span>
        </div>
        <div className="sx-eval-stat">
          <span className="sx-eval-stat__label">预算</span>
          <span className="sx-eval-stat__value">{formatUsd(run.budget_spent_usd)}</span>
        </div>
        {run.params?.module_id != null && (
          <div className="sx-eval-stat">
            <span className="sx-eval-stat__label">模组</span>
            <span className="sx-eval-stat__value">{run.params.module_id}</span>
          </div>
        )}
        {run.params?.max_usd != null && (
          <div className="sx-eval-stat">
            <span className="sx-eval-stat__label">预算上限</span>
            <span className="sx-eval-stat__value">{formatUsd(run.params.max_usd)}</span>
          </div>
        )}
      </div>

      <div className="sx-eval-layers">
        <h2>分层 trace</h2>
        {LAYERS.map(({ id, name }) => (
          <LayerCard key={id} layerId={id} name={name} layer={layers[id]} />
        ))}
      </div>

      <AnnotationsBlock annotations={annotations} />
    </>
  );
}

/* ---------------------------------------------------------------- 分层卡片 */

function LayerCard({
  layerId,
  name,
  layer,
}: {
  layerId: string;
  name: string;
  layer: EvalLayer | undefined;
}) {
  if (!layer) return null;
  const status = layer.status ?? 'unknown';
  const reason = layer.reason;

  return (
    <div className="sx-layer" data-testid={`layer-${layerId}`}>
      <div className="sx-layer__head">
        <span className="sx-layer__id">{layerId}</span>
        <span className="sx-layer__name">{name}</span>
        <span className={`sx-layer__status is-${status}`}>{layerStatusLabel(status)}</span>
      </div>
      <div className="sx-layer__body">
        {reason && <p className="sx-layer__reason">{layerReasonLabel(reason)}</p>}
        <LayerBody layerId={layerId} layer={layer} />
      </div>
    </div>
  );
}

function LayerBody({ layerId, layer }: { layerId: string; layer: EvalLayer }) {
  const status = layer.status ?? 'unknown';
  if (status !== 'passed' && status !== 'failed') return null;

  switch (layerId) {
    case 'L1':
      return <L1Body layer={layer} />;
    case 'L2':
      return layer.problems && layer.problems.length > 0 ? (
        <ul className="sx-problems">
          {layer.problems.map((p, i) => (
            <li key={i}>{p}</li>
          ))}
        </ul>
      ) : (
        <p className="sx-ok-note">无一致性问题</p>
      );
    case 'L3':
      return <L3Body layer={layer} />;
    case 'L4':
      return <L4Body layer={layer} />;
    case 'L6':
      return <L6Body layer={layer} />;
    default:
      return null;
  }
}

function L1Body({ layer }: { layer: EvalLayer }) {
  const dims = layer.dims ?? {};
  const checks = layer.checks ?? [];

  return (
    <>
      <p className="sx-layer__total">总分 {layer.total ?? '—'} / 5</p>
      {Object.keys(dims).length > 0 && (
        <div className="sx-dims">
          {Object.entries(dims).map(([dim, d]: [string, EvalDim]) => (
            <div key={dim} className="sx-dim">
              <div className="sx-dim__head">
                <span className="sx-dim__name">{DIM_LABELS[dim] ?? dim}</span>
                <span className="sx-dim__score">{d.score ?? '—'}</span>
              </div>
              {d.evidence && d.evidence.length > 0 && (
                <ul className="sx-dim__evidence">
                  {d.evidence.map((e, i) => (
                    <li key={i}>{e}</li>
                  ))}
                </ul>
              )}
            </div>
          ))}
        </div>
      )}
      {checks.length > 0 && (
        <ul className="sx-checks">
          {checks.map((c) => (
            <li key={c.id} className={`sx-check ${c.passed ? 'is-passed' : 'is-failed'}`}>
              <span className="sx-check__mark">{c.passed ? '✓' : '✕'}</span>
              <span className="sx-check__name">
                {c.name}
                {c.evidence ? <span className="sx-check__evidence">{c.evidence}</span> : null}
              </span>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

function L3Body({ layer }: { layer: EvalLayer }) {
  const dims = layer.dims ?? {};

  return (
    <>
      <p className="sx-layer__reason">
        {layer.judge === 'llm' ? '裁判：LLM 裁判' : layer.judge ? `裁判：${layer.judge}` : '裁判：未启用'}
      </p>
      {layer.estimate_usd != null && (
        <p className="sx-layer__reason">预估成本：${Number(layer.estimate_usd).toFixed(4)}</p>
      )}
      {Object.keys(dims).length > 0 && (
        <div className="sx-dims">
          {Object.entries(dims).map(([dim, d]: [string, EvalDim]) => (
            <div key={dim} className="sx-dim">
              <div className="sx-dim__head">
                <span className="sx-dim__name">{DIM_LABELS[dim] ?? dim}</span>
                <span className="sx-dim__score">{d.score ?? '—'}</span>
              </div>
              {d.comment ? <p className="sx-dim__comment">{d.comment}</p> : null}
              {d.suggestion ? <p className="sx-dim__suggestion">建议：{d.suggestion}</p> : null}
            </div>
          ))}
        </div>
      )}
    </>
  );
}

function L4Body({ layer }: { layer: EvalLayer }) {
  if (layer.claim_count == null) return null;
  const pct = ratioPercent(layer.support_ratio);

  return (
    <div className="sx-l4">
      <p className="sx-layer__reason">
        声明 {layer.claim_count} 条 · 支持 {layer.supported ?? 0} 条 · 支持比 {pct}
      </p>
      {layer.support_ratio != null && (
        <div className="sx-l4__bar">
          <span className="sx-l4__fill" style={{ width: pct }} />
        </div>
      )}
    </div>
  );
}

function L6Body({ layer }: { layer: EvalLayer }) {
  const dimDeltas = layer.dim_deltas ?? {};
  const delta = layer.delta;

  return (
    <div className="sx-l6">
      <p className="sx-layer__reason">
        当前 {layer.current_total ?? '—'} vs 历史 {layer.prior_total ?? '—'}
        {delta != null ? ` · 变化 ${delta > 0 ? '+' : ''}${delta}` : ''}
      </p>
      {layer.regression ? (
        <p className="sx-ok-note sx-ok-note--warn" data-testid="l6-regression">
          检测到回归：分数较历史下降
        </p>
      ) : (
        <p className="sx-ok-note">无回归</p>
      )}
      {Object.keys(dimDeltas).length > 0 && (
        <ul className="sx-checks">
          {Object.entries(dimDeltas).map(([dim, d]) => (
            <li key={dim} className="sx-check">
              <span className="sx-check__name">{DIM_LABELS[dim] ?? dim}</span>
              <span className="sx-check__mark">{d > 0 ? `+${d}` : d}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/* ---------------------------------------------------------------- 标注 */

function AnnotationsBlock({ annotations }: { annotations: EvalAnnotation[] }) {
  return (
    <div className="sx-eval-annotations">
      <h2>标注</h2>
      {annotations.length === 0 ? (
        <p className="sx-empty">本次运行没有标注</p>
      ) : (
        <ul className="sx-eval-ann-list">
          {annotations.map((a) => {
            const supported = (a.score ?? 0) >= 0.5;
            return (
              <li key={a.annotation_id} className="sx-eval-ann">
                <div className="sx-eval-ann__head">
                  <span className="sx-badge sx-badge--layer">{a.layer ?? '—'}</span>
                  <span className={`sx-eval-ann__score ${supported ? 'is-passed' : 'is-failed'}`}>
                    {ratioPercent(a.score)}
                  </span>
                  <span className="sx-eval-ann__subject">{a.subject_ref ?? '—'}</span>
                </div>
                {a.explanation ? <p className="sx-eval-ann__explanation">{a.explanation}</p> : null}
                {a.evidence_refs && a.evidence_refs.length > 0 && (
                  <ul className="sx-eval-ann__refs">
                    {a.evidence_refs.map((ev, i) => (
                      <li key={i}>
                        模组 {ev.module_id ?? '—'} · 块 {ev.chunk_index ?? '—'} · 相似度 {ev.score ?? '—'}
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
