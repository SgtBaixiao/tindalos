/**
 * site/Loading.tsx —— 启动屏：品牌词逐词升起，随后坠落淡出。
 * 提供 onDone 回调，或传入 onDone 后由父组件决定是否卸载。
 *
 * 时序：hide(开始坠落) → fade(淡出) → done(通知父组件)。具体毫秒见 LOADING_MS。
 * 尊重 prefers-reduced-motion：直接触发 onDone。
 */

import { useEffect, useRef, useState } from 'react';
import { sxVars } from './css';

export const LOADING_MS = { hide: 1500, fade: 2100, done: 2600 } as const;

const BRAND_WORDS = ['SgtX', 'Lonely', 'Hearts', 'Club'];

type LoadingProps = {
  onDone?: () => void;
};

export function Loading({ onDone }: LoadingProps) {
  const [hiding, setHiding] = useState(false);
  const [fading, setFading] = useState(false);
  const doneRef = useRef(onDone);
  doneRef.current = onDone;

  const reduced = useRef(
    typeof window !== 'undefined' &&
      typeof window.matchMedia === 'function' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches,
  ).current;

  useEffect(() => {
    if (reduced) {
      const id = window.setTimeout(() => doneRef.current?.(), 0);
      return () => window.clearTimeout(id);
    }
    const tHide = window.setTimeout(() => setHiding(true), LOADING_MS.hide);
    const tFade = window.setTimeout(() => setFading(true), LOADING_MS.fade);
    const tDone = window.setTimeout(() => doneRef.current?.(), LOADING_MS.done);
    return () => {
      window.clearTimeout(tHide);
      window.clearTimeout(tFade);
      window.clearTimeout(tDone);
    };
  }, [reduced]);

  return (
    <div
      className="sx-loading"
      data-hiding={hiding}
      data-fading={fading}
      role="status"
      aria-label="SgtXLonelyHeartsClub 加载中"
    >
      <h1 className="sx-loading__title" aria-label="SgtXLonelyHeartsClub">
        {BRAND_WORDS.map((word, i) => (
          <span className="sx-loading__word" key={word}>
            <span
              className="sx-loading__wordInner"
              style={sxVars({ '--sx-i': i })}
            >
              {word}
            </span>
          </span>
        ))}
      </h1>
      <div className="sx-loading__bar" aria-hidden="true">
        <span className="sx-loading__fill" />
      </div>
      <div className="sx-loading__credit">随时可访问的 TRPG 备团工作台</div>
    </div>
  );
}
