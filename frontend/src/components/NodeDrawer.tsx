import { useEffect, useState } from 'react';
import { useGraphStore } from '../store/useGraphStore';
import { fetchRegenerate } from '../lib/live';
import { loadRevision, saveNode } from '../lib/workspace';
import { useShallow } from 'zustand/react/shallow';

/**
 * NodeDrawer：右侧滑出详情面板（点击节点打开）。
 * - Dirty drafts retain their baseline revision until the server confirms saving.
 * - Save/regenerate adopt a complete persisted revision; conflicts retain drafts.
 * - Esc 关闭；空选择时渲染 null（画布上下文不丢失）
 */
export function NodeDrawer() {
  const selectedId = useGraphStore((s) => s.selectedId);
  const node = useGraphStore(
    useShallow((s) => {
      const selected = s.nodes.find((n) => n.id === s.selectedId);
      return selected ? { id: selected.id, type: selected.type, data: selected.data } : null;
    }),
  );
  const selectNode = useGraphStore((s) => s.selectNode);
  const updateNodeData = useGraphStore((s) => s.updateNodeData);
  const adopt = useGraphStore((s) => s.adoptCampaign);
  const campaignId = useGraphStore((s) => s.campaignId);
  const revision = useGraphStore((s) => s.revision);
  const [baseRevision, setBaseRevision] = useState<number | null>(null);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState('');
  const [regenerating, setRegenerating] = useState(false);
  const [regenerateError, setRegenerateError] = useState<string | null>(null);
  const [extra, setExtra] = useState<Record<string, string>>({});

  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [time, setTime] = useState('');
  const [place, setPlace] = useState('');

  useEffect(() => {
    setDirty(false);
    setSaved('');
    setRegenerateError(null);
  }, [node?.id]);
  useEffect(() => {
    if (!node || dirty) return;
    const d = node.data;
    setTitle(String(d.title ?? d.name ?? ''));
    setDescription(String(d.description ?? d.summary ?? ''));
    setTime(String(d.time ?? ''));
    setPlace(String(d.place ?? ''));
    setBaseRevision(revision);
    const fields =
      node.type === 'clue'
        ? ['conclusions', 'acquisition_conditions', 'alternatives', 'source_refs']
        : node.type === 'npc'
          ? ['goals', 'knowledge', 'resources']
          : [];
    setExtra(Object.fromEntries(fields.map((k) => [k, ((d[k] as string[]) ?? []).join('\n')])));
  }, [node?.id, node?.data, dirty, revision]);

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

  const save = async () => {
    const listFields = Object.fromEntries(
      Object.entries(extra).map(([k, v]) => [k, v.split('\n').filter((t) => t.trim())]),
    );
    const patch =
      node.type === 'npc' || node.type === 'clue'
        ? { name: title, description, ...listFields }
        : node.type === 'act'
          ? { title, summary: description }
          : node.type === 'scene'
            ? { title, setting: { time, place } }
            : { title, description };
    setSaving(true);
    setRegenerateError(null);
    try {
      if (campaignId && baseRevision) {
        const doc = await saveNode(campaignId, node.id, baseRevision, patch);
        setDirty(false);
        adopt(doc.campaign, doc.revision);
        setSaved(`已保存版本 ${doc.revision}`);
      } else {
        updateNodeData(node.id, patch);
        setDirty(false);
        setSaved('演示草稿已保存在本页');
      }
    } catch (err) {
      setRegenerateError(String(err));
    } finally {
      setSaving(false);
    }
  };
  const onRegenerate = async () => {
    if (!node || !campaignId || !revision) return;
    setRegenerating(true);
    setRegenerateError(null);
    try {
      const doc = await fetchRegenerate(campaignId, node.id, { revision });
      setDirty(false);
      adopt(doc.campaign, doc.revision ?? null);
      setSaved(`已保存版本 ${doc.revision}`);
    } catch (err) {
      setRegenerateError(String(err));
    } finally {
      setRegenerating(false);
    }
  };
  const offline = !campaignId || !revision;
  const change = () => {
    setDirty(true);
    setSaved('');
  };

  return (
    <aside className={`tn-drawer${selectedId ? ' tn-drawer--open' : ''}`} aria-label="节点详情">
      <div className="tn-drawer__head">
        <span className={`tn-badge tn-badge--type tn-badge--${node.type}`}>{node.type}</span>
        <button
          type="button"
          className="tn-drawer__close"
          onClick={() => selectNode(null)}
          aria-label="关闭"
        >
          ×
        </button>
      </div>
      <div className="tn-drawer__body">
        <label className="tn-field">
          <span className="tn-field__label">{isNpc || node.type === 'clue' ? '名字' : '标题'}</span>
          <input
            className="tn-field__input"
            value={title}
            onChange={(e) => {
              change();
              setTitle(e.target.value);
            }}
            placeholder="未命名"
          />
        </label>

        {node.type === 'scene' && (
          <div className="tn-field__row">
            <label className="tn-field">
              <span className="tn-field__label">时间</span>
              <input
                className="tn-field__input"
                value={time}
                onChange={(e) => {
                  change();
                  setTime(e.target.value);
                }}
              />
            </label>
            <label className="tn-field">
              <span className="tn-field__label">地点</span>
              <input
                className="tn-field__input"
                value={place}
                onChange={(e) => {
                  change();
                  setPlace(e.target.value);
                }}
              />
            </label>
          </div>
        )}

        <label className="tn-field">
          <span className="tn-field__label">{isNpc ? '描述' : '描述 / 提要'}</span>
          <textarea
            className="tn-field__input tn-field__textarea"
            rows={5}
            value={description}
            onChange={(e) => {
              change();
              setDescription(e.target.value);
            }}
          />
        </label>

        {node.type === 'npc' && (
          <div className="tn-field">
            <span className="tn-field__label">人格</span>
            <div className="tn-chip-row">
              {((data.personality as string[]) ?? []).map((p, i) => (
                <span key={i} className="tn-chip">
                  {p}
                </span>
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
                <span key={i} className="tn-chip tn-chip--verdigris">
                  {c}
                </span>
              ))}
            </div>
          </div>
        )}
        {Object.entries(extra).map(([key, value]) => (
          <label className="tn-field" key={key}>
            <span className="tn-field__label">
              {
                (
                  {
                    goals: '目标',
                    knowledge: '已知信息',
                    resources: '资源',
                    conclusions: '能推出的结论',
                    acquisition_conditions: '取得条件',
                    alternatives: '替代取得路径',
                    source_refs: '来源引用',
                  } as Record<string, string>
                )[key]
              }
              （每行一项）
            </span>
            <textarea
              className="tn-field__input tn-field__textarea"
              value={value}
              onChange={(e) => {
                change();
                setExtra({ ...extra, [key]: e.target.value });
              }}
            />
          </label>
        ))}
      </div>
      <div className="tn-drawer__foot">
        <button
          type="button"
          className="tn-btn tn-btn--primary"
          onClick={() => void save()}
          disabled={saving || regenerating}
        >
          {saving ? '保存中…' : '保存'}
        </button>
        <button
          type="button"
          className="tn-btn"
          onClick={() => void onRegenerate()}
          disabled={offline || regenerating || saving || dirty || node.type === 'act'}
          title={
            offline
              ? '需 tindalos serve'
              : regenerating
                ? '生成中…'
                : '重新生成此节点（POST /api/regenerate）'
          }
        >
          {regenerating ? '重生成中…' : '重生成'}
        </button>
      </div>
      {saved && <p role="status">{saved}</p>}
      {dirty && revision !== baseRevision && <p role="alert">当前版本已变化，草稿仍保留。</p>}
      {regenerateError && !offline && (
        <button
          onClick={async () => {
            try {
              const doc = await loadRevision(campaignId!);
              setDirty(false);
              adopt(doc.campaign, doc.revision);
              setRegenerateError(null);
            } catch (e) {
              setRegenerateError(String(e));
            }
          }}
        >
          放弃草稿并重载
        </button>
      )}
      {regenerateError && (
        <p className="tn-drawer__err" role="alert">
          操作失败：{regenerateError}。草稿仍在；若版本冲突，可放弃草稿并重载。
        </p>
      )}
    </aside>
  );
}
