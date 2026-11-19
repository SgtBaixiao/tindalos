import { memo, useCallback, useEffect, useMemo, useState, type CSSProperties } from 'react';
import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
  useNodesInitialized,
  useReactFlow,
  type EdgeChange,
  type NodeChange,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import './theme.css';
import './styles/nodes.css';
import './styles/panels.css';
import {
  buildScriptGraph,
  layoutGraph,
  NODE_TYPE_COLORS,
  positionsAreFinite,
} from './lib/scriptGraph';
import { getCampaignIdFromQuery, isLive, loadCampaign } from './lib/live';
import type { CampaignView, EdgeKind, GraphEdge, GraphNode } from './lib/types';
import { useGraphStore } from './store/useGraphStore';
import { nodeTypes } from './components/nodes';
import { NodeDrawer } from './components/NodeDrawer';
import { Legend } from './components/Legend';
import { ProgressBand } from './components/ProgressBand';
import { useTheme } from './ui/theme';

/** Fit after measurement, once for each adopted structure; edits preserve the viewport. */
function FitCampaign({ structure }: { structure: string }) {
  const ready = useNodesInitialized();
  const { fitView } = useReactFlow();
  useEffect(() => {
    if (ready && structure) void fitView({ padding: 0.15, minZoom: 0.1 });
  }, [ready, structure, fitView]);
  return null;
}

/** 三类边的样式：flow 实线 / branch 虚线 / reference 点划线（§3.4）。 */
function edgeStyleFor(kind: EdgeKind): CSSProperties {
  switch (kind) {
    case 'flow':
      return { stroke: 'var(--t-ink-faint)', strokeWidth: 1.6 };
    case 'branch':
      return { stroke: 'var(--t-verdigris)', strokeWidth: 1.4, strokeDasharray: '7 5' };
    case 'reference':
      return { stroke: 'var(--t-inkblue)', strokeWidth: 1.2, strokeDasharray: '2 4' };
  }
}

function App({ workspace = false }: { workspace?: boolean }) {
  const nodes = useGraphStore((s) => s.nodes);
  const edges = useGraphStore((s) => s.edges);
  const loadGraph = useGraphStore((s) => s.loadGraph);
  const adoptCampaign = useGraphStore((s) => s.adoptCampaign);
  const onNodesChange = useGraphStore((s) => s.onNodesChange);
  const selectNode = useGraphStore((s) => s.selectNode);
  const undo = useGraphStore((s) => s.undo);
  const undoAvailable = useGraphStore((s) => s.past.length > 0);
  const structure = useGraphStore((s) => s.structureKey);

  const [theme, toggleTheme] = useTheme();
  const [loadError, setLoadError] = useState<string | null>(null);

  // 载入 campaign → 映射 → dagre 布局 → 入 store
  // Workspace reads authoritative revisions. Standalone mode explicitly loads a demo.
  useEffect(() => {
    let alive = true;
    const liveMode = isLive();
    const campaignId = getCampaignIdFromQuery();
    if (workspace && !campaignId) return;
    if (workspace && campaignId) {
      fetch(`/api/campaigns/${encodeURIComponent(campaignId)}`)
        .then(async (res) => {
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          return res.json();
        })
        .then((doc) => {
          if (alive) adoptCampaign(doc.campaign, doc.meta.revision, false);
        })
        .catch((err) => {
          if (alive) setLoadError(String(err));
        });
      return () => {
        alive = false;
      };
    }
    loadCampaign(liveMode, campaignId)
      .then((campaign) => {
        const { nodes: mapped, edges } = buildScriptGraph(campaign);
        const laid = layoutGraph(mapped, edges);
        if (alive) loadGraph(laid, edges, campaign.id);
      })
      .catch((err: unknown) => {
        if (alive) setLoadError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      alive = false;
    };
  }, [loadGraph, adoptCampaign, workspace]);

  const onNodesChangeHandler = useCallback(
    (changes: NodeChange[]) =>
      onNodesChange(
        changes.filter(
          (change) =>
            change.type === 'position' ||
            change.type === 'remove' ||
            change.type === 'select' ||
            change.type === 'dimensions',
        ),
      ),
    [onNodesChange],
  );

  const onEdgesChangeHandler = useCallback((_changes: EdgeChange[]) => {
    // 边当前为派生展示（kind 驱动样式），删除/选中由 store 承载即可；此处不消费。
  }, []);

  // 展示边：统一 smoothstep + 按 kind 上样式
  const displayEdges: GraphEdge[] = useMemo(
    () =>
      edges.map((e) => ({
        ...e,
        type: 'smoothstep' as const,
        style: edgeStyleFor(e.kind),
        label: e.kind === 'flow' ? undefined : e.label,
      })),
    [edges],
  );

  const miniMapColor = useCallback(
    (node: GraphNode) => NODE_TYPE_COLORS[node.type] ?? 'var(--t-ink-faint)',
    [],
  );

  const layoutOk = useMemo(() => positionsAreFinite(nodes), [nodes]);
  const onNodeClick = useCallback(
    (_event: unknown, node: GraphNode) => selectNode(node.id),
    [selectNode],
  );
  const onPaneClick = useCallback(() => selectNode(null), [selectNode]);

  return (
    <div className="tn-app">
      <header className="tn-topbar">
        <div className="tn-topbar__brand">
          <span className="tn-topbar__mark" aria-hidden="true">
            雾
          </span>
          <h1 className="tn-topbar__title">Tindalos · 剧本节点图</h1>
        </div>
        <div className="tn-topbar__actions">
          <button
            type="button"
            className="tn-btn tn-btn--ghost"
            onClick={() => undo()}
            disabled={!undoAvailable || workspace}
          >
            撤销
          </button>
          <button type="button" className="tn-btn tn-btn--ghost" onClick={toggleTheme}>
            {theme === 'light' ? '暖墨' : '纸白'}
          </button>
        </div>
      </header>

      <div className="tn-canvas">
        <ReactFlow
          nodes={nodes}
          edges={displayEdges}
          nodeTypes={nodeTypes}
          colorMode={theme}
          fitView
          onlyRenderVisibleElements
          minZoom={0.1}
          maxZoom={1.6}
          proOptions={{ hideAttribution: false }}
          onNodesChange={onNodesChangeHandler}
          onEdgesChange={onEdgesChangeHandler}
          onNodeClick={onNodeClick}
          onPaneClick={onPaneClick}
          deleteKeyCode={null}
        >
          <FitCampaign structure={structure} />
          <Background variant={BackgroundVariant.Dots} gap={22} size={1.2} />
          <MiniMap nodeColor={miniMapColor} nodeStrokeColor="transparent" nodeBorderRadius={4} />
          <Controls />
          <Legend />
        </ReactFlow>

        {!layoutOk && (
          <div className="tn-canvas__warn" role="status">
            布局坐标异常（dagre 输出非有限值）
          </div>
        )}
        {loadError && (
          <div className="tn-canvas__warn" role="status">
            载入失败：{loadError}（离线请确认 public/campaign.json 存在；live 请确认 tindalos serve
            已起）
          </div>
        )}

        <NodeDrawer />
        {!workspace && <ProgressBand live={false} />}
      </div>
    </div>
  );
}

export default memo(App);
