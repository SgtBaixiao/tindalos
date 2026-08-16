/**
 * site/LibraryView.tsx —— 模组资料库：上传规则书 PDF → 入库 + 建立 RAG 索引；
 * 已入库模组列表 → 点开右侧抽屉（桌面 1/3 面板 / 移动 90dvh 底部面板）：
 * 文本预览 + 多模态识图结果。低置信项提供人工确认（选 kind + 名字 → confirmImage）。
 */

import { useCallback, useEffect, useState, type ChangeEvent } from 'react';
import {
  confirmImage,
  getModule,
  ingestModule,
  listModules,
  uploadModule,
} from './api';
import {
  VISION_KIND_LABELS,
  VISION_KINDS,
  type Module,
  type ModuleDetail,
  type VisionKind,
  type VisionResult,
} from './types';
import { sxVars } from './css';

type ConfirmControlProps = {
  moduleId: string;
  img: VisionResult;
  onConfirmed: () => void;
};

/** 低置信视觉项的人工确认控件：选 kind + 可选名字 → confirmImage。 */
function ConfirmControl({ moduleId, img, onConfirmed }: ConfirmControlProps) {
  const [kind, setKind] = useState<VisionKind | ''>('');
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  const submit = async () => {
    if (!kind) {
      setError('请选择类型');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await confirmImage(moduleId, {
        image_path: img.image_path,
        kind,
        name: name.trim() || undefined,
      });
      setDone(true);
      onConfirmed();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  if (done) {
    return <p className="sx-note sx-note--ok">已确认</p>;
  }

  return (
    <div className="sx-confirm">
      <span className="sx-note">需人工确认</span>
      <div className="sx-confirm__row" role="group" aria-label="选择类型">
        {VISION_KINDS.map((k) => (
          <button
            key={k}
            type="button"
            className={`sx-confirm__kind${kind === k ? ' is-selected' : ''}`}
            onClick={() => setKind(k)}
          >
            {VISION_KIND_LABELS[k]}
          </button>
        ))}
      </div>
      <input
        className="sx-confirm__name"
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder="名字 / 说明（可选）"
        aria-label="名字"
      />
      <div className="sx-confirm__row">
        <button
          type="button"
          className="sx-btn sx-btn--ink"
          onClick={() => void submit()}
          disabled={busy}
        >
          {busy ? '确认中…' : '确认'}
        </button>
        {error && <span className="sx-error">{error}</span>}
      </div>
    </div>
  );
}

type GalleryProps = {
  moduleId: string;
  images: VisionResult[];
  onConfirmed: () => void;
};

function Gallery({ moduleId, images, onConfirmed }: GalleryProps) {
  if (images.length === 0) {
    return <p className="sx-empty">暂无视觉识别结果</p>;
  }
  return (
    <ul className="sx-gallery">
      {images.map((img) => (
        <li key={img.image_path} className="sx-gallery__item">
          <img
            className="sx-gallery__img"
            src={img.image_path}
            alt={img.name ?? VISION_KIND_LABELS[img.kind]}
            loading="lazy"
          />
          <div className="sx-gallery__meta">
            <span className={`sx-badge sx-badge--${img.kind}`}>
              {VISION_KIND_LABELS[img.kind]}
            </span>
            <span className="sx-gallery__conf">
              {Math.round(img.confidence * 100)}%
            </span>
            {img.needs_confirmation && (
              <span className="sx-gallery__flag">待确认</span>
            )}
          </div>
          {img.name && <p className="sx-gallery__name">{img.name}</p>}
          {img.caption && <p className="sx-gallery__caption">{img.caption}</p>}
          {img.needs_confirmation && (
            <ConfirmControl moduleId={moduleId} img={img} onConfirmed={onConfirmed} />
          )}
        </li>
      ))}
    </ul>
  );
}

export function LibraryView() {
  const [modules, setModules] = useState<Module[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [ingesting, setIngesting] = useState(false);
  const [detailId, setDetailId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ModuleDetail | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);

  const loadList = useCallback(async () => {
    try {
      const { modules: list } = await listModules();
      setModules(list);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setModules([]);
    }
  }, []);

  useEffect(() => {
    void loadList();
  }, [loadList]);

  const loadDetail = useCallback(async (id: string) => {
    setDetailError(null);
    try {
      const { module } = await getModule(id);
      setDetail(module);
    } catch (err) {
      setDetailError(err instanceof Error ? err.message : String(err));
      setDetail(null);
    }
  }, []);

  const openDetail = async (id: string) => {
    setDetailId(id);
    setDetail(null);
    await loadDetail(id);
  };

  const onUpload = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    setUploading(true);
    setError(null);
    try {
      const { module } = await uploadModule(file);
      await loadList();
      await openDetail(module.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setUploading(false);
    }
  };

  const onIngest = async () => {
    if (!detailId) return;
    setIngesting(true);
    setDetailError(null);
    try {
      const res = await ingestModule(detailId);
      setDetailError(
        res.indexed ? `已建立索引（${res.chunks} 块）` : '索引未建立',
      );
    } catch (err) {
      setDetailError(err instanceof Error ? err.message : String(err));
    } finally {
      setIngesting(false);
    }
  };

  return (
    <section className="sx-library" data-testid="sx-library">
      <div className="sx-library__upload">
        <label className="sx-upload">
          <input
            type="file"
            accept="application/pdf"
            onChange={(e) => void onUpload(e)}
            disabled={uploading}
            aria-label="上传模组 PDF"
          />
          {uploading ? '上传中…' : '＋ 上传模组 PDF'}
        </label>
        {error && <p className="sx-error">{error}</p>}
      </div>

      <h2>已入库模组</h2>
      {modules === null ? (
        <p className="sx-empty">载入中…</p>
      ) : modules.length === 0 ? (
        <p className="sx-empty">还没有已入库的模组</p>
      ) : (
        <ul className="sx-module-list">
          {modules.map((m) => (
            <li key={m.id}>
              <button
                type="button"
                className="sx-module-card"
                onClick={() => void openDetail(m.id)}
              >
                <span className="sx-module-card__title">{m.title}</span>
                <span className="sx-module-card__meta">
                  {m.filename ?? m.id}
                  {m.chunk_count != null ? ` · ${m.chunk_count} 块` : ''}
                  {m.status ? ` · ${m.status}` : ''}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}

      <aside className="sx-drawer" data-open={detailId !== null}>
        <div className="sx-drawer__head">
          <h3>{detail?.title ?? '模组详情'}</h3>
          <button
            type="button"
            className="sx-drawer__close"
            onClick={() => setDetailId(null)}
            aria-label="关闭"
          >
            ×
          </button>
        </div>
        <div className="sx-drawer__body">
          {detailError && <p className="sx-error">{detailError}</p>}
          {detail ? (
            <>
              <div className="sx-detail__text">
                <h4>文本预览</h4>
                <p>
                  {detail.text_preview ||
                    '（无文本预览；可在资料库列表中重新入库生成索引）'}
                </p>
              </div>
              <div className="sx-detail__actions" style={sxVars({})}>
                <button
                  type="button"
                  className="sx-btn"
                  onClick={() => void onIngest()}
                  disabled={ingesting}
                >
                  {ingesting ? '建立索引中…' : '建立 RAG 索引'}
                </button>
              </div>
              <div className="sx-detail__images">
                <h4>图像识别</h4>
                <Gallery
                  moduleId={detail.id}
                  images={detail.images}
                  onConfirmed={() => void loadDetail(detail.id)}
                />
              </div>
            </>
          ) : detailId ? (
            <p className="sx-empty">载入中…</p>
          ) : null}
        </div>
      </aside>
    </section>
  );
}
