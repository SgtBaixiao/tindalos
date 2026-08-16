/**
 * site/ReplayView.tsx —— 历史记录 · 重放页：按 campaignId 拉取完整剧本交给 ReplayPlayer。
 */

import { useEffect, useState } from 'react';
import { getCampaign } from './api';
import type { Campaign } from './types';
import { ReplayPlayer } from './ReplayPlayer';

type ReplayViewProps = {
  campaignId: string;
  navigate: (to: string) => void;
};

export function ReplayView({ campaignId, navigate }: ReplayViewProps) {
  const [campaign, setCampaign] = useState<Campaign | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setCampaign(null);
    setError(null);
    getCampaign(campaignId)
      .then(({ campaign: c }) => {
        if (alive) setCampaign(c);
      })
      .catch((err: unknown) => {
        if (alive) setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      alive = false;
    };
  }, [campaignId]);

  return (
    <section className="sx-replay" data-testid="sx-replay">
      <div className="sx-replay__nav">
        <button type="button" className="sx-btn sx-btn--ghost" onClick={() => navigate('#/history')}>
          ← 历史记录
        </button>
      </div>
      {error ? (
        <p className="sx-error">{error}</p>
      ) : campaign ? (
        <ReplayPlayer campaign={campaign} />
      ) : (
        <p className="sx-empty">载入中…</p>
      )}
    </section>
  );
}
