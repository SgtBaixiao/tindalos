import { useCallback, useEffect, useMemo, useState } from 'react';
import { type ProgressEvent } from '../lib/progress';
import { usePrefersReducedMotion, useTypewriter } from '../lib/hooks';
import {
  DEMO_MODULE_TEXT,
  fetchGenerateStream,
  fetchStaticProgress,
  isLive,
  sseToProgressEvent,
} from '../lib/live';
import { useGraphStore } from '../store/useGraphStore';

/**
 * ProgressBand：底部进度带 —— 多智能体工作流可视化（AI 产品差异点）。
 * - ?live=1：fetch POST /api/generate 读 SSE 流（data:{stage,message}
 *   → data:{done:true,campaign}）实时渲染；连接失败 / 非 2xx / 错误帧 / 流异常
 *   → 回退静态 public/progress.jsonl
 * - moduleText：生成用的模组文本（01 工作台传入）；缺省用 DEMO_MODULE_TEXT
 *   确定性生成，保证离线 / 无密钥也能实时看到进度流
 * - 离线（无 live 参数）：直接读静态 progress.jsonl（示例进度事件）
 * - KP 主控步骤时间线 + NPC 小胶囊列 + 打字机滚动（reduced-motion 静止）
 */
export function ProgressBand({
  live,
  moduleText,
}: {
  live?: boolean;
  moduleText?: string;
}) {
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

  // live=1：fetch POST /api/generate 流式 SSE；连接失败/非 2xx/错误帧/流异常 → 回退静态
  useEffect(() => {
    if (!liveMode) return;
    const controller = new AbortController();
    let alive = true;
    (async () => {
      try {
        const text = moduleText?.trim() || DEMO_MODULE_TEXT;
        setEvents([]);
        let started = false;
        for await (const frame of fetchGenerateStream(text, {
          signal: controller.signal,
        })) {
          if (!alive) return;
          if (!started) {
            started = true;
            setLoading(false);
            setMode('live');
          }
          if (frame.kind === 'progress') {
            setEvents((prev) => [...prev, sseToProgressEvent(frame)]);
          } else if (frame.kind === 'done') {
            if (frame.campaign?.id) setCampaignId(frame.campaign.id);
            setLoading(false);
            setMode('done');
            return;
          } else if (frame.kind === 'error') {
            void loadStatic(); // 错误帧 → 回退静态
            return;
          }
        }
        // 流结束但无 done 帧 → 回退静态
        if (alive) void loadStatic();
      } catch {
        // 连接失败 / 非 2xx / 坏流 → 回退静态
        if (alive) void loadStatic();
      }
    })();
    return () => {
      alive = false;
      controller.abort();
    };
  }, [liveMode, moduleText, loadStatic, setCampaignId]);

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
