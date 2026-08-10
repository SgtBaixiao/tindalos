import { useEffect, useState } from 'react';

/**
 * prefers-reduced-motion 探测：reduced → 直接静止（house-style 动效铁律）。
 */
export function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);

  useEffect(() => {
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
    setReduced(mq.matches);
    const onChange = (e: MediaQueryListEvent) => setReduced(e.matches);
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, []);

  return reduced;
}

/**
 * 打字机滚动：按 speed(ms) 逐字 reveal fullText。
 * enabled=false（reduced-motion 或暂停）时直接返回全文。
 */
export function useTypewriter(fullText: string, speed = 28, enabled = true): string {
  const [count, setCount] = useState(enabled ? 0 : fullText.length);

  useEffect(() => {
    if (!enabled) {
      setCount(fullText.length);
      return;
    }
    setCount(0);
    let i = 0;
    const timer = window.setInterval(() => {
      i += 1;
      setCount(i);
      if (i >= fullText.length) window.clearInterval(timer);
    }, speed);
    return () => window.clearInterval(timer);
  }, [fullText, speed, enabled]);

  return fullText.slice(0, count);
}
