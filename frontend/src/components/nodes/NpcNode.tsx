import { memo } from 'react';
import { Handle, Position, type NodeProps } from '@xyflow/react';
import type { NpcData } from '../../lib/types';

function initials(name: string): string {
  return name.trim().slice(0, 1) || '?';
}

/**
 * NPC 节点：墨蓝胶囊（--t-inkblue）· 圆角胶囊（区别于剧情节点矩形）·
 * 头像圈 + 名字（展示字体）+ 人格词。
 */
const NpcNode = memo(function NpcNode({ data, selected }: NodeProps) {
  const d = data as unknown as NpcData;
  return (
    <div className={`tn-node tn-node--npc${selected ? ' is-selected' : ''}`}>
      <Handle type="target" position={Position.Left} className="tn-handle" />
      <div className="tn-node__avatar">{initials(d.name)}</div>
      <div className="tn-node__body">
        <div className="tn-node__title">{d.name}</div>
        <div className="tn-node__meta">
          {d.archetype}
          {d.personality.length > 0 && ` · ${d.personality.join('、')}`}
        </div>
      </div>
      <Handle type="source" position={Position.Right} className="tn-handle" />
    </div>
  );
});

export default NpcNode;
