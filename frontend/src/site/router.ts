/**
 * site/router.ts —— 极简 hash 路由（不装 react-router）。
 * 路由表：#/ 首页、#/workbench 工作台、#/library 资料库、
 * #/qa 规则问答、#/history 历史记录、#/history/<campaignId> 重放。
 */

import { useCallback, useEffect, useState } from 'react';

/** 解析 location.hash → 路由段数组（'#/history/c1' → ['history','c1']）。 */
export function parseHash(hash: string): string[] {
  return hash
    .replace(/^#\/?/, '')
    .split('/')
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
}

export type HashRoute = {
  /** 路由段（首段为栏目名）。 */
  segments: string[];
  /** 首段（home 为缺省首页）。 */
  route: string;
  /** 跳转：赋 location.hash（触发 hashchange）。 */
  navigate: (to: string) => void;
};

export function useHashRoute(): HashRoute {
  const [segments, setSegments] = useState<string[]>(() => parseHash(window.location.hash));

  useEffect(() => {
    const onChange = () => setSegments(parseHash(window.location.hash));
    window.addEventListener('hashchange', onChange);
    return () => window.removeEventListener('hashchange', onChange);
  }, []);

  const navigate = useCallback((to: string) => {
    window.location.hash = to;
  }, []);

  return { segments, route: segments[0] ?? 'home', navigate };
}
