import { useEffect, useMemo, useState } from 'react';
import { useGraphStore } from '../store/useGraphStore';
import { fetchRegenerate, isLive, patchGraphFromCampaign } from '../lib/live';

/**
 * NodeDrawer：右侧滑出详情面板（点击节点打开）。
 * - 标题/描述为本地 state 编辑，保存时写入 store（产生 undo 快照）
 * - 「重生成」按钮：live（?live=1）时 POST /api/regenerate → patch store 节点
 *   data + edges（单点真相：剧本 JSON → buildScriptGraph 重建）；离线置灰 + tooltip
 * - Esc 关闭；空选择时渲染 null（画布上下文不丢失）
 */
export function NodeDrawer() {
  const nodes = useGraphStore((s) => s.nodes);
  const selectedId = useGraphStore((s) => s.selectedId);
  const selectNode = useGraphStore((s) => s.selectNode);
  const updateNodeData = useGraphStore((s) => s.updateNodeData);
  const regenerateNode = useGraphStore((s) => s.regenerateNode);
  const setEdges = useGraphStore((s) => s.setEdges);
  const campaignId = useGraphStore((s) => s.campaignId);

  const liveMode = useMemo(() => isLive(), []);
  const [regenerating, setRegenerating] = useState(false);
  const [regenerateError, setRegenerateError] = useState<string | null>(null);

  const node = useMemo(
    () => nodes.find((n) => n.id === selectedId) ?? null,
    [nodes, selectedId],
  );

  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [time, setTime] = useState('');
  const [place, setPlace] = useState('');

  useEffect(() => {
    if (!node) return;
    const d = node.data as Record<string, unknown>;
    setTitle(String(d.title ?? d.name ?? ''));
    setDescription(String(d.description ?? d.summary ?? ''));
    setTime(String(d.time ?? ''));
    setPlace(String(d.place ?? ''));
  }, [node?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') selectNode(null);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [selectNode]);

  if (!node) return null;

  const data = node.data as Record<string, unknown>;
  const isNpc = node.type === 'npc';

  const save = () => {
    updateNodeData(node.id, isNpc ? { name: title, description } : { title, description, time, place });
  };

  /** 重生成：live → fetchRegenerate(campaignId, nodeId) → patch 节点 data + edges。 */
  const onRegenerate = async () => {
    if (!node || !liveMode || !campaignId) return;
    setRegenerating(true);
    setRegenerateError(null);
    regenerateNode(node.id); // 视觉态：生成中
    try {
      const { campaign } = await fetchRegenerate(campaignId, node.id);
      const { nodeData, edges } = patchGraphFromCampaign(campaign, node.id);
      updateNodeData(node.id, { ...nodeData, status: undefined }); // 清除「生成中」视觉态（G5 修正）
      setEdges(edges);
    } catch (err) {
      setRegenerateError(err instanceof Error ? err.message : String(err));
      updateNodeData(node.id, { status: '失败' });
    } finally {
      setRegenerating(false);
    }
  };

  const offline = !liveMode || !campaignId;

  return (
    <aside className={`tn-drawer${selectedId ? ' tn-drawer--open' : ''}`} aria-label="节点详情">
      <div className="tn-drawer__head">
        <span className={`tn-badge tn-badge--type tn-badge--${node.type}`}>{node.type}</span>
        <button type="button" className="tn-drawer__close" onClick={() => selectNode(null)} aria-label="关闭">
          ×
        </button>
      </div>
      <div className="tn-drawer__body">
        <label className="tn-field">
          <span className="tn-field__label">{isNpc ? '名字' : '标题'}</span>
          <input
            className="tn-field__input"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="未命名"
          />
        </label>

        {!isNpc && (
          <div className="tn-field__row">
            <label className="tn-field">
              <span className="tn-field__label">时间</span>
              <input className="tn-field__input" value={time} onChange={(e) => setTime(e.target.value)} />
            </label>
            <label className="tn-field">
              <span className="tn-field__label">地点</span>
              <input className="tn-field__input" value={place} onChange={(e) => setPlace(e.target.value)} />
            </label>
          </div>
        )}

        <label className="tn-field">
          <span className="tn-field__label">{isNpc ? '描述' : '描述 / 提要'}</span>
          <textarea
            className="tn-field__input tn-field__textarea"
            rows={5}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </label>

        {node.type === 'npc' && (
          <div className="tn-field">
            <span className="tn-field__label">人格</span>
            <div className="tn-chip-row">
              {((data.personality as string[]) ?? []).map((p, i) => (
                <span key={i} className="tn-chip">{p}</span>
              ))}
            </div>
          </div>
        )}

        {node.type === 'event' && (
          <div className="tn-field">
            <span className="tn-field__label">触发条件</span>
            <div className="tn-chip-row">
              {((data.conditions as string[]) ?? []).length === 0 && (
                <span className="tn-field__hint">无（无条件事件）</span>
              )}
              {((data.conditions as string[]) ?? []).map((c, i) => (
                <span key={i} className="tn-chip tn-chip--verdigris">{c}</span>
              ))}
            </div>
          </div>
        )}
      </div>

      <div className="tn-drawer__foot">
        <button type="button" className="tn-btn tn-btn--primary" onClick={save}>
          保存
        </button>
        <button
          type="button"
          className="tn-btn"
          onClick={() => void onRegenerate()}
          disabled={offline || regenerating}
          title={offline ? '需 tindalos serve' : regenerating ? '生成中…' : '重新生成此节点（POST /api/regenerate）'}
        >
          {regenerating ? '重生成中…' : '重生成'}
        </button>
      </div>
      {regenerateError && (
        <p className="tn-drawer__err" role="alert">
          重生成失败：{regenerateError}
        </p>
      )}
    </aside>
  );
}
