import { memo } from 'react';
import { Handle, Position, type NodeProps } from '@xyflow/react';
import type { ClueData } from '../../lib/types';

/**
 * 线索节点：墨棕顶条（--t-sepia-ink）· 旧纸底（--t-oldpaper）· 典籍感。
 */
const ClueNode = memo(function ClueNode({ data, selected }: NodeProps) {
  const d = data as unknown as ClueData;
  return (
    <div className={`tn-node tn-node--clue${selected ? ' is-selected' : ''}`}>
      <Handle type="target" position={Position.Left} className="tn-handle" />
      <div className="tn-node__bar" aria-hidden="true" />
      <div className="tn-node__body">
        <div className="tn-node__title">{d.name}</div>
        <div className="tn-node__meta">{d.description}</div>
        <span className="tn-badge tn-badge--clue">线索</span>
      </div>
      <Handle type="source" position={Position.Right} className="tn-handle" />
    </div>
  );
});

export default ClueNode;
