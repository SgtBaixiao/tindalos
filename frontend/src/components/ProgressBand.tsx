import { useEffect, useMemo, useState } from 'react';
import { parseProgressEvents, type ProgressEvent } from '../lib/progress';
import { usePrefersReducedMotion, useTypewriter } from '../lib/hooks';

/**
 * ProgressBand：底部进度带 —— 多智能体工作流可视化（AI 产品差异点）。
 * - fetch public/progress.jsonl（示例进度事件）
 * - KP 主控步骤时间线 + NPC 小胶囊列
 * - 打字机滚动最新事件文本；prefers-reduced-motion → 直接静止
 */
export function ProgressBand() {
  const [events, setEvents] = useState<ProgressEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const reduced = usePrefersReducedMotion();

  useEffect(() => {
    let alive = true;
    fetch(`${import.meta.env.BASE_URL}progress.jsonl`)
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.text();
      })
      .then((text) => {
        if (alive) setEvents(parseProgressEvents(text));
      })
      .catch(() => {
        if (alive) setEvents([]);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, []);

  const { kpEvents, npcEvents, activeText } = useMemo(() => {
    const kp = events.filter((e) => e.agent === 'kp');
    const npc = events.filter((e) => e.agent === 'npc');
    const last = events[events.length - 1];
    return { kpEvents: kp, npcEvents: npc, activeText: last ? last.text : '' };
  }, [events]);

  const shown = useTypewriter(activeText, 30, !reduced);

  if (loading) {
    return <footer className="tn-progress tn-progress--loading">雾从港口漫上来…</footer>;
  }
  if (events.length === 0) return null;

  return (
    <footer className="tn-progress" aria-label="生成进度">
      <div className="tn-progress__bar" aria-hidden="true" />
      <div className="tn-progress__inner">
        <div className="tn-progress__timeline">
          {kpEvents.map((e, i) => (
            <div
              key={`${e.ts}-${i}`}
              className={`tn-progress__step${i === kpEvents.length - 1 ? ' is-active' : ''}`}
            >
              <span className="tn-progress__dot" aria-hidden="true" />
              <span>{e.step}</span>
            </div>
          ))}
        </div>
        <div className="tn-progress__npc">
          {npcEvents.map((e, i) => (
            <span key={`${e.ts}-${i}`} className="tn-progress__npc-chip">
              {e.npc ?? 'NPC'}
            </span>
          ))}
        </div>
        <div className="tn-progress__typewriter">
          <span>{shown}</span>
          <span className="tn-progress__caret" aria-hidden="true" />
        </div>
      </div>
    </footer>
  );
}
