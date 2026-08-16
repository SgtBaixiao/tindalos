import { memo } from 'react';
import { Handle, Position, type NodeProps } from '@xyflow/react';
import type { EventData } from '../../lib/types';

/**
 * 事件节点：铜锈绿强调条（--t-verdigris）· 虚线边框（=可触发/条件）· kind 徽章。
 */
const EventNode = memo(function EventNode({ data, selected }: NodeProps) {
  const d = data as unknown as EventData;
  return (
    <div className={`tn-node tn-node--event${selected ? ' is-selected' : ''}`}>
      <Handle type="target" position={Position.Left} className="tn-handle" />
      <div className="tn-node__bar" aria-hidden="true" />
      <div className="tn-node__body">
        <div className="tn-node__title">{d.title}</div>
        <div className="tn-node__meta">{d.description}</div>
        <span className="tn-badge tn-badge--kind">{d.kind}</span>
        {d.conditions.length > 0 && (
          <div className="tn-node__conditions">
            {d.conditions.map((c, i) => (
              <span key={i} className="tn-chip">{c}</span>
            ))}
          </div>
        )}
      </div>
      <Handle type="source" position={Position.Right} className="tn-handle" />
    </div>
  );
});

export default EventNode;
