import { memo } from 'react';
import { Handle, Position, type NodeProps } from '@xyflow/react';
import type { SceneData } from '../../lib/types';

/**
 * 场景节点：墨灰强调条（--t-orange）· 标准矩形 · 时间地点 + 状态徽章。
 */
const SceneNode = memo(function SceneNode({ data, selected }: NodeProps) {
  const d = data as unknown as SceneData;
  return (
    <div className={`tn-node tn-node--scene${selected ? ' is-selected' : ''}`}>
      <Handle type="target" position={Position.Left} className="tn-handle" />
      <div className="tn-node__bar" aria-hidden="true" />
      <div className="tn-node__body">
        <div className="tn-node__title">{d.title}</div>
        <div className="tn-node__meta">
          {d.time} · {d.place} · {d.eventCount} 事件
        </div>
        <span className={`tn-badge tn-badge--${d.status}`}>{d.status}</span>
      </div>
      <Handle type="source" position={Position.Right} className="tn-handle" />
    </div>
  );
});

export default SceneNode;
