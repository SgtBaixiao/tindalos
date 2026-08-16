import { memo } from 'react';
import { Handle, Position, type NodeProps } from '@xyflow/react';
import type { ActData } from '../../lib/types';

/**
 * 幕节点：墨强调条（--t-rule-red）· 圆角宽卡 · 罗马数字标题。
 * §2.3 卡片规范：左缘细强调条区分类型，整卡纸墨统一（克制，去纸纹噪点）。
 */
const ActNode = memo(function ActNode({ data, selected }: NodeProps) {
  const d = data as unknown as ActData;
  return (
    <div className={`tn-node tn-node--act${selected ? ' is-selected' : ''}`}>
      <Handle type="target" position={Position.Left} className="tn-handle" />
      <div className="tn-node__bar" aria-hidden="true" />
      <div className="tn-node__body">
        <div className="tn-node__roman">{d.roman}</div>
        <div className="tn-node__title">{d.title}</div>
        <div className="tn-node__meta">
          {d.sceneCount} 个场景 · {d.description ?? d.summary ?? ''}
        </div>
      </div>
      <Handle type="source" position={Position.Right} className="tn-handle" />
    </div>
  );
});

export default ActNode;
