import { useCallback, useEffect, useMemo, useState } from 'react';
import { type ProgressEvent } from '../lib/progress';
import { usePrefersReducedMotion, useTypewriter } from '../lib/hooks';
import {
  GENERATE_SSE_URL,
  fetchStaticProgress,
  isLive,
  parseSSEEvent,
  sseToProgressEvent,
  type SseFrame,
} from '../lib/live';
import { useGraphStore } from '../store/useGraphStore';

/**
 * ProgressBand：底部进度带 —— 多智能体工作流可视化（AI 产品差异点）。
 * - ?live=1：EventSource 连 API /api/generate 实时渲染（SSE data:{stage,message}
 *   → data:{done:true,campaign}）；断开 / 错误帧 → 回退静态 public/progress.jsonl
 * - 离线（无 live 参数）：直接读静态 progress.jsonl（示例进度事件）
 * - KP 主控步骤时间线 + NPC 小胶囊列 + 打字机滚动（reduced-motion 静止）
 */
export function ProgressBand({ live }: { live?: boolean }) {
  const liveMode = live ?? isLive();
  const setCampaignId = useGraphStore((s) => s.setCampaignId);
  const [events, setEvents] = useState<ProgressEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [mode, setMode] = useState<'idle' | 'live' | 'static' | 'done'>('idle');
  const reduced = usePrefersReducedMotion();

  /** 静态回退 / 离线加载：progress.jsonl → 事件列表。 */
  const loadStatic = useCallback(async () => {
    try {
      const list = await fetchStaticProgress();
      setEvents(list);
    } catch {
      setEvents([]);
    } finally {
      setLoading(false);
      setMode('static');
    }
  }, []);

  // live=1：EventSource 实时流；断开/错误帧回退静态
  useEffect(() => {
    if (!liveMode) return;
    let alive = true;
    let finished = false;
    const es = new EventSource(GENERATE_SSE_URL);

    const teardown = () => {
      finished = true;
      es.close();
    };

    es.onopen = () => {
      if (!alive || finished) return;
      setEvents([]);
      setLoading(false);
      setMode('live');
    };

    es.onmessage = (ev: MessageEvent) => {
      if (!alive || finished) return;
      let frame: SseFrame | null = null;
      try {
        frame = parseSSEEvent(ev.data);
      } catch {
        frame = null; // 坏帧丢弃（后端契约外的数据）
      }
      if (!frame) return;
      if (frame.kind === 'progress') {
        setEvents((prev) => [...prev, sseToProgressEvent(frame)]);
      } else if (frame.kind === 'done') {
        teardown();
        if (frame.campaign?.id) setCampaignId(frame.campaign.id);
        setLoading(false);
        setMode('done');
      } else if (frame.kind === 'error') {
        teardown();
        void loadStatic(); // 错误帧 → 回退静态
      }
    };

    es.onerror = () => {
      if (!alive || finished) return;
      teardown();
      void loadStatic(); // 断开 → 回退静态
    };

    return () => {
      alive = false;
      es.close();
    };
  }, [liveMode, loadStatic, setCampaignId]);

  // 离线：直接静态加载
  useEffect(() => {
    if (liveMode) return;
    void loadStatic();
  }, [liveMode, loadStatic]);

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
        {mode === 'live' && (
          <span className="tn-progress__live" role="status">
            实时生成
          </span>
        )}
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
