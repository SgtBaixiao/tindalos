import { Panel } from '@xyflow/react';
import { EDGE_KIND_META, NODE_TYPE_META } from '../lib/scriptGraph';

/**
 * Legend：画布左下角图例面板。
 * 节点 = 顶条色 + 类型名；边 = 线型说明（实线=顺序流 / 虚线=分支 / 点划线=引用）。
 */
export function Legend() {
  return (
    <Panel position="bottom-left" className="tn-legend">
      <div className="tn-legend__title">图例</div>
      <ul className="tn-legend__list">
        {NODE_TYPE_META.map((meta) => (
          <li key={meta.type} className="tn-legend__item">
            <span
              className={`tn-legend__swatch ${meta.cssClass}`}
              style={{ backgroundColor: meta.color }}
              aria-hidden="true"
            />
            <span>{meta.label}</span>
          </li>
        ))}
      </ul>
      <div className="tn-legend__rule" aria-hidden="true" />
      <ul className="tn-legend__list">
        {EDGE_KIND_META.map((meta) => (
          <li key={meta.kind} className="tn-legend__item">
            <span
              className={`tn-legend__line tn-legend__line--${meta.kind}`}
              aria-hidden="true"
            />
            <span>{meta.label}</span>
          </li>
        ))}
      </ul>
    </Panel>
  );
}
