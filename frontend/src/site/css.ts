import type { CSSProperties } from 'react';

/** 把 `--sx-*` 自定义属性对象转换为 React CSSProperties（TS 无法直接识别自定义键）。 */
export function sxVars(vars: Record<string, string | number>): CSSProperties {
  return vars as CSSProperties;
}
