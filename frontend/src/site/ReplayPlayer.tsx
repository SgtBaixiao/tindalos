/**
 * site/ReplayPlayer.tsx —— 剧本重放器：逐幕 → 场景 → 事件 分步播放。
 * 每步 2.5s 自动前进；点时间线圆点可跳步；到最后一步显示「重放完毕」。
 */

import { useEffect, useMemo, useState } from 'react';
import type { Campaign, EventView } from './types';

const STEP_MS = 2500;

export type ReplayStep =
  | { kind: 'act'; id: string; title: string; description: string }
  | { kind: 'scene'; id: string; title: string; description: string }
  | {
      kind: 'event';
      id: string;
      title: string;
      description: string;
      conditions: string[];
    };

/** 把 campaign 压平成 幕 → 场景 → 事件 的线性步骤序列。 */
export function buildReplaySteps(campaign: Campaign): ReplayStep[] {
  const steps: ReplayStep[] = [];
  for (const act of campaign.acts ?? []) {
    steps.push({
      kind: 'act',
      id: act.id,
      title: act.title,
      description: act.summary || `第 ${act.roman ?? '?'} 幕`,
    });
    for (const scene of act.scenes ?? []) {
      const setting = scene.setting ?? {};
      const desc = [setting.time, setting.place].filter(Boolean).join(' · ');
      steps.push({
        kind: 'scene',
        id: scene.id,
        title: scene.title,
        description: desc || '（场景）',
      });
      for (const event of scene.events ?? []) {
        steps.push({
          kind: 'event',
          id: event.id,
          title: event.title,
          description: event.description || '',
          conditions: event.conditions ?? [],
        });
      }
    }
  }
  return steps;
}

type ReplayPlayerProps = {
  campaign: Campaign;
};

export function ReplayPlayer({ campaign }: ReplayPlayerProps) {
  const steps = useMemo(() => buildReplaySteps(campaign), [campaign]);
  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(true);
  const done = steps.length > 0 && index >= steps.length - 1;

  // 自动播放：到达末步停；done 后不再前进
  useEffect(() => {
    if (!playing || steps.length === 0 || done) return;
    const id = window.setTimeout(() => {
      setIndex((i) => Math.min(i + 1, steps.length - 1));
    }, STEP_MS);
    return () => window.clearTimeout(id);
  }, [playing, steps.length, done, index]);

  if (steps.length === 0) {
    return <p className="sx-empty">该剧本没有可重放的内容</p>;
  }

  const step = steps[index];
  const progress = ((index + 1) / steps.length) * 100;

  const goTo = (i: number) => {
    setIndex(Math.max(0, Math.min(i, steps.length - 1)));
    setPlaying(true);
  };

  return (
    <div className="sx-replay-player" data-testid="sx-replay-player">
      <div className="sx-replay-player__head">
        <span className={`sx-badge sx-badge--${step.kind}`}>
          {step.kind === 'act' ? '幕' : step.kind === 'scene' ? '场景' : '事件'}
        </span>
        <h2 className="sx-replay-player__title">{step.title}</h2>
      </div>
      <p className="sx-replay-player__desc">
        {step.description || '（无描述）'}
      </p>
      {step.kind === 'event' && step.conditions.length > 0 && (
        <ul className="sx-replay-player__conditions">
          {step.conditions.map((c, i) => (
            <li key={i}>· {c}</li>
          ))}
        </ul>
      )}

      <div className="sx-replay-player__timeline" role="tablist" aria-label="步骤">
        {steps.map((s, i) => (
          <button
            key={`${s.kind}-${s.id}`}
            type="button"
            className={`sx-replay-player__dot${
              i === index ? ' is-active' : i < index ? ' is-past' : ''
            }`}
            onClick={() => goTo(i)}
            aria-label={`第 ${i + 1} 步：${s.title}`}
          />
        ))}
      </div>
      <div className="sx-replay-player__progress">
        <span
          className="sx-replay-player__bar"
          style={{ width: `${progress}%` }}
          data-testid="progress-bar"
        />
      </div>
      <p className="sx-replay-player__count">
        第 {index + 1} / {steps.length} 步
      </p>

      {done && (
        <div className="sx-replay-player__done" data-testid="replay-done">
          重放完毕
        </div>
      )}

      <div className="sx-replay-player__controls">
        <button
          type="button"
          className="sx-btn sx-btn--ghost"
          onClick={() => goTo(index - 1)}
          disabled={index === 0}
        >
          上一步
        </button>
        <button
          type="button"
          className="sx-btn sx-btn--ink"
          onClick={() => setPlaying((p) => !p)}
        >
          {playing ? '暂停' : '播放'}
        </button>
        <button
          type="button"
          className="sx-btn sx-btn--ghost"
          onClick={() => goTo(index + 1)}
          disabled={done}
        >
          下一步
        </button>
      </div>
    </div>
  );
}
