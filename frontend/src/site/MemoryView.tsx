/**
 * site/MemoryView.tsx —— 记忆可视化（#/memories/:campaignId）。
 *
 * 消费真实后端：
 *  - GET /api/memories/{campaign_id}
 *      → {campaign_id, status, play_status, briefing, memories:{episodic, semantic, shortterm, longterm}}
 *  - GET /api/sessions/{campaign_id}
 *      → {campaign_id, current_play_status, sessions:[{session_index, summary, play_status, conflicts, created_at}]}
 *
 * 页面结构：briefing 卡片（「上次停在哪」+ play_status）→ 剧情线状态（longterm 按
 * subject_key 取 synopsis/plotline）→ 四类记忆分区 → 会话时间线（conflicts 有则
 * 徽标折叠）。campaign 无记忆 / 请求失败均有占位；会话请求失败仅降级该分区，
 * 不影响记忆主体（优雅降级）。
 */

import { useEffect, useState } from 'react';
import { getMemories, getSessions, listCampaigns } from './api';
import {
  MEMORY_TYPES,
  MEMORY_TYPE_LABELS,
  type CampaignMeta,
  type MemoryEntry,
  type MemoryType,
  type MemoriesResponse,
  type PlaySession,
} from './types';

/** longterm 里按 subject_key 展示的剧情线条目（有则展示，无则占位）。 */
const LONGTERM_KEYS: { key: string; label: string }[] = [
  { key: 'synopsis', label: '剧情概要' },
  { key: 'plotline', label: '主线脉络' },
  { key: 'npc_arcs', label: 'NPC 弧光' },
];

/** sqlite 里 conflicts 是 JSON TEXT，后端可能原样返回字符串或已解析数组。 */
function parseConflicts(conflicts: unknown): unknown[] {
  if (Array.isArray(conflicts)) return conflicts;
  if (typeof conflicts === 'string' && conflicts.trim().length > 0) {
    try {
      const parsed = JSON.parse(conflicts) as unknown;
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      return [];
    }
  }
  return [];
}

/** 冲突条目的可读文本：dict 优先取 description/detail/reason/type，否则原样字符串。 */
function conflictText(c: unknown): string {
  if (c == null) return '';
  if (typeof c === 'string') return c;
  if (typeof c === 'object') {
    const obj = c as Record<string, unknown>;
    const detail = obj.description ?? obj.detail ?? obj.reason ?? obj.type;
    if (typeof detail === 'string' && detail.trim().length > 0) return detail;
    try {
      return JSON.stringify(c);
    } catch {
      return '';
    }
  }
  return String(c);
}

type MemoryViewProps = {
  campaignId: string;
  navigate: (to: string) => void;
};

export function MemoryView({ campaignId, navigate }: MemoryViewProps) {
  const [memories, setMemories] = useState<MemoriesResponse | null>(null);
  const [sessions, setSessions] = useState<PlaySession[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sessionsError, setSessionsError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setMemories(null);
    setSessions(null);
    setError(null);
    setSessionsError(null);

    getMemories(campaignId)
      .then((data) => {
        if (alive) setMemories(data);
      })
      .catch((err: unknown) => {
        if (alive) setError(err instanceof Error ? err.message : String(err));
      });

    getSessions(campaignId)
      .then((data) => {
        if (alive) setSessions(data.sessions ?? []);
      })
      .catch((err: unknown) => {
        if (alive) setSessionsError(err instanceof Error ? err.message : String(err));
      });

    return () => {
      alive = false;
    };
  }, [campaignId]);

  return (
    <section className="sx-memory" data-testid="sx-memory">
      <div className="sx-memory__nav">
        <button type="button" className="sx-btn sx-btn--ghost" onClick={() => navigate('#/memories')}>
          ← 记忆
        </button>
        <span className="sx-memory__campaign">#{campaignId}</span>
      </div>

      {error ? (
        <p className="sx-error" data-testid="memory-error">
          {error}
        </p>
      ) : memories ? (
        <>
          <BriefingCard campaignId={campaignId} response={memories} />
          <PlotlineBlock longterm={memories.memories?.longterm ?? []} />
          <MemoriesBlock memories={memories.memories} />
          <SessionsBlock sessions={sessions} error={sessionsError} />
        </>
      ) : (
        <p className="sx-empty">载入中…</p>
      )}
    </section>
  );
}

/* ---------------------------------------------------------------- briefing */

function BriefingCard({ campaignId, response }: { campaignId: string; response: MemoriesResponse }) {
  const briefing = response.briefing?.trim();
  const playStatus = response.play_status;

  return (
    <div className="sx-briefing" data-testid="memory-briefing">
      <div className="sx-briefing__card">
        <div className="sx-briefing__top">
          <h2 className="sx-briefing__kicker">上次停在哪</h2>
          <span className="sx-briefing__campaign">#{campaignId}</span>
        </div>
        {briefing ? (
          <p className="sx-briefing__text">{briefing}</p>
        ) : (
          <p className="sx-empty">
            该战役暂无游玩记录与长期记忆 —— 还没有「上次停在哪」可回叙。
          </p>
        )}
        <div className="sx-briefing__meta">
          {playStatus ? (
            <span className="sx-badge">状态：{playStatus}</span>
          ) : (
            <span className="sx-note">暂无游玩状态</span>
          )}
        </div>
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- 剧情线 */

function PlotlineBlock({ longterm }: { longterm: MemoryEntry[] }) {
  const byKey = new Map<string, string>();
  for (const entry of longterm) {
    if (entry.subject_key && entry.content) byKey.set(entry.subject_key, entry.content);
  }
  const items = LONGTERM_KEYS.filter(({ key }) => byKey.has(key));

  return (
    <div className="sx-plotline" data-testid="memory-plotline">
      <h2>剧情线状态</h2>
      {items.length === 0 ? (
        <p className="sx-empty" data-testid="plotline-empty">
          暂无剧情线状态 —— 长期记忆整合（sleep-time consolidate）后，剧情概要 / 主线脉络会在此展示。
        </p>
      ) : (
        <ul className="sx-plotline__list">
          {items.map(({ key, label }) => (
            <li key={key} className="sx-plotline__item">
              <span className="sx-plotline__key">{label}</span>
              <span className="sx-plotline__content">{byKey.get(key)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/* ---------------------------------------------------------------- 四类记忆 */

function MemoriesBlock({ memories }: { memories: MemoriesResponse['memories'] }) {
  return (
    <div className="sx-memory__sections" data-testid="memory-sections">
      <h2>四类记忆</h2>
      <div className="sx-mem-grid">
        {MEMORY_TYPES.map((type) => (
          <MemoryPanel key={type} type={type} entries={memories?.[type] ?? []} />
        ))}
      </div>
    </div>
  );
}

function MemoryPanel({ type, entries }: { type: MemoryType; entries: MemoryEntry[] }) {
  return (
    <div className="sx-mem-panel" data-testid={`mem-panel-${type}`}>
      <div className="sx-mem-panel__head">
        <span className="sx-mem-panel__title">{MEMORY_TYPE_LABELS[type]}</span>
        <span className="sx-mem-panel__count">{entries.length} 条</span>
      </div>
      {entries.length === 0 ? (
        <p className="sx-empty">暂无{MEMORY_TYPE_LABELS[type]}</p>
      ) : (
        <ul className="sx-mem-list">
          {entries.map((entry) => (
            <li key={entry.id} className="sx-mem-item">
              <span className="sx-mem-item__content">{entry.content}</span>
              <span className="sx-mem-item__meta">
                {entry.importance != null && (
                  <span>重要度 {Number(entry.importance).toFixed(2)}</span>
                )}
                {entry.subject_key && <span>{entry.subject_key}</span>}
                {entry.source_episode && <span>溯源 {entry.source_episode}</span>}
                {entry.valid_from && <span>自 {entry.valid_from.slice(0, 10)}</span>}
                {entry.valid_to && <span>至 {entry.valid_to.slice(0, 10)}</span>}
                {entry.status && entry.status !== 'active' && <span>{entry.status}</span>}
                {entry.created_at && <span>{entry.created_at.slice(0, 10)}</span>}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/* ---------------------------------------------------------------- 会话时间线 */

function SessionsBlock({ sessions, error }: { sessions: PlaySession[] | null; error: string | null }) {
  return (
    <div className="sx-memory__sessions" data-testid="memory-sessions">
      <h2>会话时间线</h2>
      {error && (
        <p className="sx-error" data-testid="sessions-error">
          {error}
        </p>
      )}
      {sessions === null && !error ? (
        <p className="sx-empty">载入中…</p>
      ) : sessions && sessions.length > 0 ? (
        <ol className="sx-session-list">
          {sessions.map((s) => (
            <SessionItem key={s.session_index} session={s} />
          ))}
        </ol>
      ) : (
        <p className="sx-empty" data-testid="sessions-empty">
          暂无游玩会话记录
        </p>
      )}
    </div>
  );
}

function SessionItem({ session }: { session: PlaySession }) {
  const conflicts = parseConflicts(session.conflicts);

  return (
    <li className="sx-session" data-testid={`session-${session.session_index}`}>
      <div className="sx-session__head">
        <span className="sx-session__index">第 {session.session_index} 场</span>
        {session.play_status && (
          <span className="sx-badge sx-badge--status">{session.play_status}</span>
        )}
        {session.created_at && (
          <span className="sx-session__date">
            {session.created_at.replace('T', ' ').slice(0, 16)}
          </span>
        )}
      </div>
      <p className="sx-session__summary">{session.summary}</p>
      {conflicts.length > 0 && (
        <details className="sx-session__conflicts">
          <summary className="sx-conflict-badge" data-testid="conflict-badge">
            冲突 {conflicts.length} 条
          </summary>
          <ul className="sx-conflict-list">
            {conflicts.map((c, i) => (
              <li key={i} className="sx-conflict-item">
                {conflictText(c)}
              </li>
            ))}
          </ul>
        </details>
      )}
    </li>
  );
}

/* ---------------------------------------------------------------- 索引（#/memories） */

type MemoryIndexProps = {
  navigate: (to: string) => void;
};

/** #/memories 索引：选择战役查看记忆（沿用 HistoryView 拿 campaignId 的模式）。 */
export function MemoryIndex({ navigate }: MemoryIndexProps) {
  const [campaigns, setCampaigns] = useState<CampaignMeta[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setError(null);
    listCampaigns()
      .then(({ campaigns: list }) => {
        if (alive) setCampaigns(list ?? []);
      })
      .catch((err: unknown) => {
        if (alive) setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      alive = false;
    };
  }, []);

  return (
    <section className="sx-memory" data-testid="sx-memory-index">
      <div className="sx-memory__head">
        <h1 className="sx-memory__title">记忆</h1>
        <p className="sx-memory__desc">
          选择一场战役，查看它的四类记忆、剧情线状态与「上次停在哪」。
        </p>
      </div>

      {error && <p className="sx-error">{error}</p>}

      {campaigns === null ? (
        <p className="sx-empty">载入中…</p>
      ) : campaigns.length === 0 ? (
        <p className="sx-empty">还没有生成的剧本 —— 在剧本工作台生成后即可查看记忆。</p>
      ) : (
        <ul className="sx-card-list">
          {campaigns.map((c) => (
            <li key={c.id}>
              <button
                type="button"
                className="sx-card"
                onClick={() => navigate(`#/memories/${c.id}`)}
                aria-label={`查看记忆 ${c.title}`}
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
    </section>
  );
}
