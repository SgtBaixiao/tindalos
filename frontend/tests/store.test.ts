/**
 * store.test.ts —— useGraphStore：选中节点 / 编辑 / 命令栈 undo（上限 10 步）。
 */
import { beforeEach, describe, expect, it } from 'vitest';
import { UNDO_LIMIT, useGraphStore } from '../src/store/useGraphStore';
import type { GraphEdge, GraphNode } from '../src/lib/types';

function makeNode(id: string, title: string, type: GraphNode['type'] = 'event'): GraphNode {
  return { id, type, position: { x: 0, y: 0 }, data: { title } };
}

function freshNodes(): GraphNode[] {
  return [makeNode('evt-1', '抵达现场'), makeNode('evt-2', '发现线索')];
}

const freshEdges: GraphEdge[] = [{ id: 'e1', source: 'evt-1', target: 'evt-2', kind: 'flow' }];

beforeEach(() => {
  useGraphStore.setState({ nodes: [], edges: [], selectedId: null, past: [] });
});

describe('选中（selectNode）', () => {
  it('selectNode 设置并清除 selectedId', () => {
    useGraphStore.getState().loadGraph(freshNodes(), freshEdges);
    useGraphStore.getState().selectNode('evt-1');
    expect(useGraphStore.getState().selectedId).toBe('evt-1');
    useGraphStore.getState().selectNode(null);
    expect(useGraphStore.getState().selectedId).toBeNull();
  });

  it('选中不产生 undo 历史（选择不进命令栈）', () => {
    useGraphStore.getState().loadGraph(freshNodes(), freshEdges);
    useGraphStore.getState().selectNode('evt-1');
    useGraphStore.getState().selectNode('evt-2');
    useGraphStore.getState().selectNode(null);
    expect(useGraphStore.getState().canUndo()).toBe(false);
  });
});

describe('编辑与 undo', () => {
  it('updateNodeData 修改节点 data 并入栈', () => {
    useGraphStore.getState().loadGraph(freshNodes(), freshEdges);
    useGraphStore.getState().updateNodeData('evt-1', { title: '密道封条' });
    const n = useGraphStore.getState().nodes.find((x) => x.id === 'evt-1')!;
    expect(n.data.title).toBe('密道封条');
    expect(useGraphStore.getState().undoCount()).toBe(1);
  });

  it('undo 恢复编辑前的节点数据', () => {
    useGraphStore.getState().loadGraph(freshNodes(), freshEdges);
    useGraphStore.getState().updateNodeData('evt-1', { title: '密道封条' });
    useGraphStore.getState().undo();
    const n = useGraphStore.getState().nodes.find((x) => x.id === 'evt-1')!;
    expect(n.data.title).toBe('抵达现场');
    expect(useGraphStore.getState().canUndo()).toBe(false);
  });

  it('undo 空栈时无操作且不报错', () => {
    useGraphStore.getState().loadGraph(freshNodes(), freshEdges);
    expect(useGraphStore.getState().canUndo()).toBe(false);
    useGraphStore.getState().undo();
    expect(useGraphStore.getState().nodes).toHaveLength(2);
  });

  it('undo 栈上限为最近 10 步', () => {
    useGraphStore.getState().loadGraph(freshNodes(), freshEdges);
    for (let i = 0; i < UNDO_LIMIT + 5; i += 1) {
      useGraphStore.getState().updateNodeData('evt-1', { title: `v${i}` });
    }
    expect(useGraphStore.getState().undoCount()).toBe(UNDO_LIMIT);
    // 连撤 10 次后回到第 5 次编辑后的状态（最早 5 次快照已被挤出）
    for (let i = 0; i < UNDO_LIMIT; i += 1) {
      useGraphStore.getState().undo();
    }
    expect(useGraphStore.getState().canUndo()).toBe(false);
    const n = useGraphStore.getState().nodes.find((x) => x.id === 'evt-1')!;
    expect(n.data.title).toBe('v4');
  });

  it('loadGraph 重置历史', () => {
    useGraphStore.getState().loadGraph(freshNodes(), freshEdges);
    useGraphStore.getState().updateNodeData('evt-1', { title: 'x' });
    useGraphStore.getState().loadGraph(freshNodes(), freshEdges);
    expect(useGraphStore.getState().canUndo()).toBe(false);
    expect(useGraphStore.getState().selectedId).toBeNull();
  });

  it('regenerateNode 占位：标记生成中（不落历史）', () => {
    useGraphStore.getState().loadGraph(freshNodes(), freshEdges);
    useGraphStore.getState().regenerateNode('evt-1');
    const n = useGraphStore.getState().nodes.find((x) => x.id === 'evt-1')!;
    expect(n.data.status).toBe('生成中');
    expect(useGraphStore.getState().canUndo()).toBe(false);
  });
});
