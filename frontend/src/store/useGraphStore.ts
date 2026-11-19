/**
 * useGraphStore：zustand 5 状态层 —— nodes/edges/selectedId + 命令栈式 undo。
 *
 * 设计（script-graph-frontend.md §4.4/§1.2）：结构操作（拖拽位移/选中）不进
 * 历史；只有「编辑节点数据」产生快照。快照栈上限 UNDO_LIMIT=10 步。
 */

import { create } from 'zustand';
import { buildScriptGraph, layoutGraph } from '../lib/scriptGraph';
import type { CampaignView, GraphEdge, GraphNode, NodeType } from '../lib/types';

export const UNDO_LIMIT = 10;

type Snapshot = { id: string; data: Record<string, unknown> };

export type NodeChangeLike =
  | { type: 'position'; id: string; position?: { x: number; y: number } }
  | { type: 'remove'; id: string }
  | { type: 'select'; id: string; selected: boolean }
  | { type: 'dimensions'; id: string; dimensions?: { width: number; height: number } };

/** 最小化的变更应用（等价于 @xyflow/react 的 applyNodeChanges 子集，避免在
 *  纯测试环境拖入整个 React Flow 运行时）。 */
export function applyNodeChanges(changes: NodeChangeLike[], nodes: GraphNode[]): GraphNode[] {
  const byId = new Map<string, NodeChangeLike[]>();
  for (const change of changes) {
    const group = byId.get(change.id);
    if (group) group.push(change);
    else byId.set(change.id, [change]);
  }
  let changed = false;
  const result = nodes.flatMap((node) => {
    let next = node;
    for (const change of byId.get(node.id) ?? []) {
      if (change.type === 'remove') {
        changed = true;
        return [];
      }
      if (
        change.type === 'position' &&
        change.position &&
        (next.position.x !== change.position.x || next.position.y !== change.position.y)
      )
        next = { ...next, position: change.position };
      if (change.type === 'select' && Boolean(next.selected) !== change.selected)
        next = { ...next, selected: change.selected };
      if (
        change.type === 'dimensions' &&
        change.dimensions &&
        (next.measured?.width !== change.dimensions.width ||
          next.measured?.height !== change.dimensions.height)
      )
        next = { ...next, measured: change.dimensions };
    }
    if (next !== node) changed = true;
    return [next];
  });
  return changed ? result : nodes;
}

export type GraphState = {
  campaign: CampaignView | null;
  revision: number | null;
  structureKey: string;
  adoptCampaign: (
    campaign: CampaignView,
    revision: number | null,
    preservePositions?: boolean,
  ) => void;
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
  campaign: null,
  revision: null,
  structureKey: '',
  adoptCampaign: (campaign, revision, preservePositions = true) =>
    set((state) => {
      const mapped = buildScriptGraph(campaign);
      const old = new Map(state.nodes.map((n) => [n.id, n]));
      const sameStructure =
        mapped.nodes.length === state.nodes.length && mapped.nodes.every((n) => old.has(n.id));
      const laid =
        preservePositions && sameStructure ? mapped.nodes : layoutGraph(mapped.nodes, mapped.edges);
      return {
        campaign,
        revision,
        campaignId: campaign.id,
        edges: mapped.edges,
        structureKey: campaign.id + ':' + mapped.nodes.map((n) => n.id).join('|'),
        nodes: laid.map((n) => {
          const previous = old.get(n.id);
          return preservePositions && previous
            ? { ...n, position: previous.position, measured: previous.measured }
            : n;
        }),
        selectedId: mapped.nodes.some((n) => n.id === state.selectedId) ? state.selectedId : null,
        past: [],
      };
    }),
  nodes: [],
  edges: [],
  selectedId: null,
  campaignId: null,
  past: [],

  loadGraph: (nodes, edges, campaignId = null) =>
    set({
      nodes,
      edges,
      selectedId: null,
      past: [],
      campaignId,
      campaign: null,
      revision: null,
      structureKey: nodes.map((n) => n.id).join('|'),
    }),

  setCampaignId: (id) => set({ campaignId: id }),

  onNodesChange: (changes) =>
    set((state) => {
      const nodes = applyNodeChanges(changes, state.nodes);
      return nodes === state.nodes
        ? state
        : {
            nodes,
            structureKey:
              nodes.length !== state.nodes.length
                ? nodes.map((n) => n.id).join('|')
                : state.structureKey,
          };
    }),

  setNodes: (nodes) => set({ nodes, structureKey: nodes.map((n) => n.id).join('|') }),

  setEdges: (edges) => set({ edges }),

  selectNode: (id) => set({ selectedId: id }),

  updateNodeData: (id, patch) =>
    set((state) => {
      if (!state.nodes.some((n) => n.id === id)) return state;
      return {
        nodes: state.nodes.map((n) => (n.id === id ? { ...n, data: { ...n.data, ...patch } } : n)),
        // 快照 = 编辑前的整图；栈上限 UNDO_LIMIT（最近 10 步）。
        past: [
          ...state.past.slice(-(UNDO_LIMIT - 1)),
          { id, data: state.nodes.find((n) => n.id === id)!.data },
        ],
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
        nodes: state.nodes.map((n) => (n.id === prev.id ? { ...n, data: prev.data } : n)),
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
