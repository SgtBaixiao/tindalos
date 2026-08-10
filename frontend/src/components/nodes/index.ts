import type { NodeTypes } from '@xyflow/react';
import ActNode from './ActNode';
import SceneNode from './SceneNode';
import EventNode from './EventNode';
import NpcNode from './NpcNode';
import ClueNode from './ClueNode';

/** 五类自定义节点注册表（React Flow nodeTypes）。 */
export const nodeTypes: NodeTypes = {
  act: ActNode,
  scene: SceneNode,
  event: EventNode,
  npc: NpcNode,
  clue: ClueNode,
};
