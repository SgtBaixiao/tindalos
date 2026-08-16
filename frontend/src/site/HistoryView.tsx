/**
 * site/HistoryView.tsx —— 历史记录：两组卡片。
 * 「生成的剧本」点击进入重放（#/history/<campaignId>）；「上传的模组」只读展示。
 */

import { useEffect, useState } from 'react';
import { listCampaigns, listModulesHistory } from './api';
import type { CampaignMeta, Module } from './types';

type HistoryProps = {
  navigate: (to: string) => void;
};

export function HistoryView({ navigate }: HistoryProps) {
  const [campaigns, setCampaigns] = useState<CampaignMeta[] | null>(null);
  const [modules, setModules] = useState<Module[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    Promise.all([listCampaigns(), listModulesHistory()])
      .then(([c, m]) => {
        if (!alive) return;
        setCampaigns(c.campaigns ?? []);
        setModules(m.modules ?? []);
      })
      .catch((err: unknown) => {
        if (alive) setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      alive = false;
    };
  }, []);

  return (
    <section className="sx-history" data-testid="sx-history">
      {error && <p className="sx-error">{error}</p>}

      <div className="sx-history__group">
        <h2>生成的剧本</h2>
        {campaigns === null ? (
          <p className="sx-empty">载入中…</p>
        ) : campaigns.length === 0 ? (
          <p className="sx-empty">还没有生成的剧本</p>
        ) : (
          <ul className="sx-card-list">
            {campaigns.map((c) => (
              <li key={c.id}>
                <button
                  type="button"
                  className="sx-card"
                  onClick={() => navigate(`#/history/${c.id}`)}
                >
                  <span className="sx-card__title">{c.title}</span>
                  <span className="sx-card__meta">
                    {c.created_at ?? ''}
                    {c.acts_count != null ? ` · ${c.acts_count} 幕` : ''}
                  </span>
                  {c.premise && <span className="sx-card__desc">{c.premise}</span>}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="sx-history__group">
        <h2>上传的模组</h2>
        {modules === null ? (
          <p className="sx-empty">载入中…</p>
        ) : modules.length === 0 ? (
          <p className="sx-empty">还没有上传的模组</p>
        ) : (
          <ul className="sx-card-list">
            {modules.map((m) => (
              <li key={m.id}>
                <div className="sx-card sx-card--static">
                  <span className="sx-card__title">{m.title}</span>
                  <span className="sx-card__meta">
                    {m.filename ?? m.id}
                    {m.chunk_count != null ? ` · ${m.chunk_count} 块` : ''}
                  </span>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
