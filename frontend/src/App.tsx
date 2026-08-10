import { useCallback, useEffect, useMemo, useState, type CSSProperties } from 'react';
import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
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
import type { CampaignView, EdgeKind, GraphEdge, GraphNode } from './lib/types';
import { useGraphStore } from './store/useGraphStore';
import { nodeTypes } from './components/nodes';
import { NodeDrawer } from './components/NodeDrawer';
import { Legend } from './components/Legend';
import { ProgressBand } from './components/ProgressBand';

type Theme = 'light' | 'dark';

function initialTheme(): Theme {
  const saved = localStorage.getItem('tindalos-theme');
  if (saved === 'light' || saved === 'dark') return saved;
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
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

export default function App() {
  const nodes = useGraphStore((s) => s.nodes);
  const edges = useGraphStore((s) => s.edges);
  const selectedId = useGraphStore((s) => s.selectedId);
  const loadGraph = useGraphStore((s) => s.loadGraph);
  const onNodesChange = useGraphStore((s) => s.onNodesChange);
  const selectNode = useGraphStore((s) => s.selectNode);
  const undo = useGraphStore((s) => s.undo);
  const canUndo = useGraphStore((s) => s.canUndo);

  const [theme, setTheme] = useState<Theme>(initialTheme);
  const [loadError, setLoadError] = useState<string | null>(null);

  // 主题 ↔ 根元素 data-theme（theme.css 的 [data-theme=dark] 暖墨板生效）
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem('tindalos-theme', theme);
  }, [theme]);

  // 载入 public/campaign.json → 映射 → dagre 布局 → 入 store
  useEffect(() => {
    let alive = true;
    fetch(`${import.meta.env.BASE_URL}campaign.json`)
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then((json) => {
        const campaign = (json.campaign ?? json) as CampaignView;
        const { nodes: mapped, edges } = buildScriptGraph(campaign);
        const laid = layoutGraph(mapped, edges);
        if (alive) loadGraph(laid, edges);
      })
      .catch((err: unknown) => {
        if (alive) setLoadError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      alive = false;
    };
  }, [loadGraph]);

  const onNodesChangeHandler = useCallback(
    (changes: NodeChange[]) => onNodesChange(changes as never),
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
    (node: GraphNode) => NODE_TYPE_COLORS[node.type] ?? '#8A7F6D',
    [],
  );

  const layoutOk = positionsAreFinite(nodes);

  return (
    <div className="tn-app">
      <header className="tn-topbar">
        <div className="tn-topbar__brand">
          <span className="tn-topbar__mark" aria-hidden="true">雾</span>
          <h1 className="tn-topbar__title">Tindalos · 剧本节点图</h1>
        </div>
        <div className="tn-topbar__actions">
          <button type="button" className="tn-btn tn-btn--ghost" onClick={() => undo()} disabled={!canUndo()}>
            撤销
          </button>
          <button
            type="button"
            className="tn-btn tn-btn--ghost"
            onClick={() => setTheme((t) => (t === 'light' ? 'dark' : 'light'))}
          >
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
          minZoom={0.4}
          maxZoom={1.6}
          proOptions={{ hideAttribution: false }}
          onNodesChange={onNodesChangeHandler}
          onEdgesChange={onEdgesChangeHandler}
          onNodeClick={(_event, node) => selectNode(node.id)}
          onPaneClick={() => selectNode(null)}
          deleteKeyCode={null}
        >
          <Background variant={BackgroundVariant.Dots} gap={22} size={1.2} />
          <MiniMap
            nodeColor={miniMapColor}
            nodeStrokeColor="transparent"
            nodeBorderRadius={4}
            maskColor="rgba(43,38,32,0.06)"
          />
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
            载入失败：{loadError}（请确认 public/campaign.json 存在）
          </div>
        )}

        <NodeDrawer />
        <ProgressBand />
      </div>
    </div>
  );
}
