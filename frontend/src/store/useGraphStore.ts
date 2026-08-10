/**
 * useGraphStore：zustand 5 状态层 —— nodes/edges/selectedId + 命令栈式 undo。
 *
 * 设计（script-graph-frontend.md §4.4/§1.2）：结构操作（拖拽位移/选中）不进
 * 历史；只有「编辑节点数据」产生快照。快照栈上限 UNDO_LIMIT=10 步。
 */

import { create } from 'zustand';
import type { GraphEdge, GraphNode, NodeType } from '../lib/types';

export const UNDO_LIMIT = 10;

type Snapshot = { nodes: GraphNode[]; edges: GraphEdge[] };

/** 对 nodes 的浅快照（data 一层展开复制，足以还原文本编辑）。 */
function snapshot(nodes: GraphNode[], edges: GraphEdge[]): Snapshot {
  return {
    nodes: nodes.map((n) => ({ ...n, data: { ...n.data } })),
    edges: edges.map((e) => ({ ...e })),
  };
}

export type NodeChangeLike =
  | { type: 'position'; id: string; position?: { x: number; y: number } }
  | { type: 'remove'; id: string }
  | { type: 'select'; id: string; selected: boolean };

/** 最小化的变更应用（等价于 @xyflow/react 的 applyNodeChanges 子集，避免在
 *  纯测试环境拖入整个 React Flow 运行时）。 */
export function applyNodeChanges(changes: NodeChangeLike[], nodes: GraphNode[]): GraphNode[] {
  let next = nodes;
  for (const change of changes) {
    if (change.type === 'position' && change.position) {
      const pos = change.position; // 闭包内保窄（TS 属性收窄不穿透回调）
      next = next.map((n) =>
        n.id === change.id ? { ...n, position: { x: pos.x, y: pos.y } } : n,
      );
    } else if (change.type === 'remove') {
      next = next.filter((n) => n.id !== change.id);
    } else if (change.type === 'select') {
      next = next.map((n) =>
        n.id === change.id ? { ...n, selected: change.selected } : n,
      );
    }
  }
  return next;
}

export type GraphState = {
  nodes: GraphNode[];
  edges: GraphEdge[];
  selectedId: string | null;
  campaignId: string | null;
  past: Snapshot[];
  /** 载入整图（初次渲染 / 重新布局 / 后端全量重建）；campaignId 一并记录（重生成按钮用）。 */
  loadGraph: (nodes: GraphNode[], edges: GraphEdge[], campaignId?: string | null) => void;
  /** 设置/更新当前 campaign id（live SSE 结束帧写入，供 NodeDrawer 重生成）。 */
  setCampaignId: (id: string | null) => void;
  /** React Flow onNodesChange 入口（拖拽位移等，不进 undo 历史）。 */
  onNodesChange: (changes: NodeChangeLike[]) => void;
  setNodes: (nodes: GraphNode[]) => void;
  setEdges: (edges: GraphEdge[]) => void;
  selectNode: (id: string | null) => void;
  /** 编辑节点 data（产生 undo 快照）。 */
  updateNodeData: (id: string, patch: Record<string, unknown>) => void;
  /** 「重生成」占位：标记生成中（不落历史，占位语义）。 */
  regenerateNode: (id: string) => void;
  undo: () => void;
  canUndo: () => boolean;
  undoCount: () => number;
};

export const useGraphStore = create<GraphState>()((set, get) => ({
  nodes: [],
  edges: [],
  selectedId: null,
  campaignId: null,
  past: [],

  loadGraph: (nodes, edges, campaignId = null) =>
    set({ nodes, edges, selectedId: null, past: [], campaignId }),

  setCampaignId: (id) => set({ campaignId: id }),

  onNodesChange: (changes) =>
    set((state) => ({ nodes: applyNodeChanges(changes, state.nodes) })),

  setNodes: (nodes) => set({ nodes }),

  setEdges: (edges) => set({ edges }),

  selectNode: (id) => set({ selectedId: id }),

  updateNodeData: (id, patch) =>
    set((state) => {
      if (!state.nodes.some((n) => n.id === id)) return state;
      return {
        nodes: state.nodes.map((n) =>
          n.id === id ? { ...n, data: { ...n.data, ...patch } } : n,
        ),
        // 快照 = 编辑前的整图；栈上限 UNDO_LIMIT（最近 10 步）。
        past: [...state.past.slice(-(UNDO_LIMIT - 1)), snapshot(state.nodes, state.edges)],
      };
    }),

  regenerateNode: (id) =>
    set((state) => ({
      nodes: state.nodes.map((n) =>
        n.id === id ? { ...n, data: { ...n.data, status: '生成中' } } : n,
      ),
    })),

  undo: () =>
    set((state) => {
      if (state.past.length === 0) return state;
      const prev = state.past[state.past.length - 1];
      return {
        nodes: prev.nodes,
        edges: prev.edges,
        past: state.past.slice(0, -1),
      };
    }),

  canUndo: () => get().past.length > 0,

  undoCount: () => get().past.length,
}));

/** 便捷：按 id 取节点类型。 */
export function nodeTypeOf(nodes: GraphNode[], id: string | null): NodeType | null {
  if (!id) return null;
  return nodes.find((n) => n.id === id)?.type ?? null;
}
