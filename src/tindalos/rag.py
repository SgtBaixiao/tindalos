"""RAG 检索模块：模组资料库/规则问答后端（离线可跑 + 云端可选）。

与全仓"LLM 失败按设计降级"哲学一致：向量化与问答均分在线/离线两档，
云端（DashScope embedding / OpenAI 兼容 chat/completions）只是增强层，
缺 key、超时、网络失败一律诚实降级到确定性离线路径，绝不抛致命异常。

数据流：
  ingest_module()  → 父子分块（父=段落合并，子=滑动窗口）→ ChromaDB 入库
                     （子块 + 父块双份，子块参与检索，父块供 QA 取上下文）
                     → 同步重建 BM25Index 并落盘 data/rag/bm25.json
  search()         → 向量 top-20 + BM25 top-20 → RRF(k=60) 合并 → top_k
  qa()             → search top-6 子块 + 对应父块 → 拼上下文
                     有 LLM key 且 TINDALOS_LLM_ENABLED==1 → 云端回答
                     否则 → 本地关键词句子拼接（诚实标注"无 LLM"）

零新增依赖：标准库 + numpy + chromadb（均已装）。
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import time
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np

from tindalos.config import get_settings
from tindalos.llm import LLMClient

try:  # chromadb 为可选；缺失时库导入不失败，调用入库/检索时报错
    import chromadb
    from chromadb.api.types import Documents, Embeddings, EmbeddingFunction
except ImportError:  # pragma: no cover - 依赖探测已确认安装，此分支仅防御
    chromadb = None  # type: ignore[assignment]
    EmbeddingFunction = object  # type: ignore[misc, assignment]

# --------------------------------------------------------------------------
# 1. 分块：字符级滑动窗口（父=页/整段，子=窗口块）
# --------------------------------------------------------------------------


def chunk_text(text: str, chunk_chars: int = 400, overlap: int = 80) -> list[str]:
    """字符级滑动窗口分块（中文友好，按字符切即可）。

    chunk_chars 为窗口宽，overlap 为相邻窗口重叠字符数；步长 = chunk_chars - overlap。
    返回非空块列表；文本不超过窗口时原样返回单块。
    """
    if not text:
        return []
    text = text.strip()
    if not text:
        return []
    n = len(text)
    if n <= chunk_chars:
        return [text]
    step = max(chunk_chars - overlap, 1)
    chunks: list[str] = []
    i = 0
    while i < n:
        chunk = text[i : i + chunk_chars]
        if chunk:
            chunks.append(chunk)
        i += step
    return chunks


# --------------------------------------------------------------------------
# 2. 分词 + BM25（纯 Python，无 jieba）
# --------------------------------------------------------------------------


def _is_cjk(ch: str) -> bool:
    """基础 CJK 统一表意区（U+4E00–U+9FFF）。"""
    return "一" <= ch <= "鿿"


def tokenize(text: str) -> list[str]:
    """中文 → 字符二元组 + 单字；拉丁 → 小写词；数字视为词；其余跳过。

    例：tokenize("克苏鲁神话") → ["克","苏","鲁","神","话","克苏","苏鲁","鲁神","神话"]
    例：tokenize("Drogheda cult") → ["drogheda", "cult"]
    """
    tokens: list[str] = []
    latin_buf: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if _is_cjk(ch):
            if latin_buf:
                tokens.append("".join(latin_buf).lower())
                latin_buf = []
            j = i
            while j < n and _is_cjk(text[j]):
                j += 1
            run = text[i:j]
            for k in range(len(run)):
                tokens.append(run[k])  # 单字
                if k + 1 < len(run):
                    tokens.append(run[k : k + 2])  # 二元组
            i = j
        elif ch.isalnum():
            latin_buf.append(ch)
            i += 1
        else:
            if latin_buf:
                tokens.append("".join(latin_buf).lower())
                latin_buf = []
            i += 1
    if latin_buf:
        tokens.append("".join(latin_buf).lower())
    return tokens


class BM25Index:
    """经典 BM25 索引（k1=1.5, b=0.75）。无 IDF 的查询词做平滑（贡献 0，不崩）。

    fit(docs) 建索引；score(query) 返回逐文档分数；search(query, top_k, doc_ids)
    在允许的 doc_ids（模块过滤）内取 top_k。索引可 to_dict/from_dict 持久化。
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.docs: list[str] = []  # 原始文本（检索结果回填）
        self.doc_ids: list[str] = []  # 并行 doc_id（= chroma id）
        self.metas: list[dict] = []  # 并行元数据（module_id 等）
        self.term_freqs: list[dict[str, int]] = []  # 每文档词频
        self.doc_lens: list[int] = []  # 每文档 token 数
        self.df: dict[str, int] = {}  # 词 → 含该词的文档数
        self.N = 0
        self.avgdl = 0.0

    # -- 建索引 -----------------------------------------------------------
    def fit(
        self,
        docs: Sequence[str],
        doc_ids: Sequence[str] | None = None,
        metas: Sequence[dict] | None = None,
    ) -> "BM25Index":
        self.docs = list(docs)
        self.doc_ids = (
            list(doc_ids) if doc_ids is not None else [str(i) for i in range(len(docs))]
        )
        self.metas = list(metas) if metas is not None else [{} for _ in docs]
        self.term_freqs = []
        self.doc_lens = []
        self.df = {}
        self.N = len(docs)
        for text in docs:
            toks = tokenize(text)
            self.doc_lens.append(len(toks))
            tf: dict[str, int] = {}
            for t in toks:
                tf[t] = tf.get(t, 0) + 1
            self.term_freqs.append(tf)
            for t in tf:
                self.df[t] = self.df.get(t, 0) + 1
        self.avgdl = (sum(self.doc_lens) / self.N) if self.N else 0.0
        return self

    def _idf(self, term: str) -> float:
        n = self.df.get(term, 0)
        if n == 0:  # 平滑：语料中不存在的词贡献 0
            return 0.0
        return math.log(1.0 + (self.N - n + 0.5) / (n + 0.5))

    def score(self, query: str) -> list[float]:
        """返回逐文档 BM25 分数（顺序与 fit 时 docs 一致）。"""
        scores = [0.0] * self.N
        if self.N == 0 or not query:
            return scores
        for term in set(tokenize(query)):
            idf = self._idf(term)
            if idf == 0.0:
                continue
            for i in range(self.N):
                tf = self.term_freqs[i].get(term, 0)
                if tf == 0:
                    continue
                dl = self.doc_lens[i]
                if self.avgdl > 0:
                    denom = tf + self.k1 * (1 - self.b + self.b * dl / self.avgdl)
                else:
                    denom = tf + self.k1
                scores[i] += idf * (tf * (self.k1 + 1)) / denom
        return scores

    def search(
        self,
        query: str,
        top_k: int,
        doc_ids: Sequence[str] | None = None,
    ) -> list[dict]:
        """在允许的 doc_ids 内取 BM25 top_k。doc_ids=None 表示不限模块。

        返回含 {doc_id, score, index, text, module_id, module_name, chunk_index, kind}。
        """
        if self.N == 0:
            return []
        allowed = set(doc_ids) if doc_ids is not None else None
        scores = self.score(query)
        cands = [
            (scores[i], i)
            for i in range(self.N)
            if allowed is None or self.doc_ids[i] in allowed
        ]
        cands.sort(key=lambda x: x[0], reverse=True)
        out: list[dict] = []
        for s, i in cands[:top_k]:
            m = self.metas[i] if i < len(self.metas) else {}
            out.append(
                {
                    "doc_id": self.doc_ids[i],
                    "score": float(s),
                    "index": i,
                    "text": self.docs[i],
                    "module_id": m.get("module_id"),
                    "module_name": m.get("module_name"),
                    "chunk_index": m.get("chunk_index"),
                    "kind": m.get("kind"),
                    "parent_index": m.get("parent_index"),
                }
            )
        return out

    def doc_ids_by_module(self, module_id: str) -> list[str]:
        """按模块分组过滤：返回该模块所有子块 doc_id（供 search 限定 BM25）。"""
        return [d for d, m in zip(self.doc_ids, self.metas) if m.get("module_id") == module_id]

    # -- 持久化 -----------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "version": 1,
            "k1": self.k1,
            "b": self.b,
            "docs": self.docs,
            "doc_ids": self.doc_ids,
            "metas": self.metas,
            "term_freqs": self.term_freqs,
            "doc_lens": self.doc_lens,
            "df": self.df,
            "N": self.N,
            "avgdl": self.avgdl,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "BM25Index":
        idx = cls(k1=d.get("k1", 1.5), b=d.get("b", 0.75))
        idx.docs = d.get("docs", [])
        idx.doc_ids = d.get("doc_ids", [])
        idx.metas = d.get("metas", [])
        idx.term_freqs = d.get("term_freqs", [])
        idx.doc_lens = d.get("doc_lens", [])
        idx.df = d.get("df", {})
        idx.N = d.get("N", 0)
        idx.avgdl = d.get("avgdl", 0.0)
        return idx


# --------------------------------------------------------------------------
# 3. Embedding（可插拔：在线 DashScope / 离线确定性哈希）
# --------------------------------------------------------------------------

_EMBED_STATE: dict[str, Any] = {"online_ok": None, "mode": "offline"}
_embedder_cache: dict[str, Any] = {"fn": None}

_OFFLINE_DIM = 256


def _online_allowed() -> bool:
    """熔断冷却判定：未失败（online_ok 非 False）或冷却窗口已过 → 允许再试在线。"""
    if _EMBED_STATE.get("online_ok") is not False:
        return True
    return time.time() >= _EMBED_STATE.get("offline_until", 0)


def _offline_embed(texts: Sequence[str]) -> np.ndarray:
    """确定性离线向量化：对 tokenize 结果做特征哈希。

    每个 token → blake2b(token, digest_size=8).digest() 前 4 字节 → 有符号 32 位整数，
    映射到 256 维桶（val % 256）带符号累加，L2 归一化。纯函数、无随机、跨进程可复现。
    """
    vecs: list[np.ndarray] = []
    for text in texts:
        vec = np.zeros(_OFFLINE_DIM, dtype=np.float64)
        for tok in tokenize(text):
            digest = hashlib.blake2b(tok.encode("utf-8"), digest_size=8).digest()
            val = int.from_bytes(digest[:4], byteorder="little", signed=True)
            bucket = val % _OFFLINE_DIM
            vec[bucket] += 1.0 if val >= 0 else -1.0
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm
        vecs.append(vec)
    return np.asarray(vecs, dtype=np.float32)


def _online_embed(
    texts: Sequence[str],
    key: str,
    model: str,
    base: str,
    timeout: float = 30.0,
) -> np.ndarray:
    """在线向量化：经统一 LLMClient.embed（OpenAI 兼容 /embeddings，text-embedding-v4 1024 维）。"""
    settings = get_settings()
    vecs = LLMClient(settings).embed(
        texts, timeout=timeout, base_url=base, model=model, api_key=key
    )
    arr = np.asarray(vecs, dtype=np.float32)
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    arr = arr / norms
    return arr


def make_embedder() -> Callable[[list[str]], np.ndarray]:
    """返回 embed(texts) 可调用对象：在线优先，失败/无 key 自动降级离线。

    在线失败记入 _EMBED_STATE 并进入 60s 冷却窗口（offline_until），冷却过后恢复
    重试在线，避免反复网络超时（2026-08-16 熔断恢复：不再首次失败即永久 offline）。
    """

    def embed(texts: Sequence[str]) -> np.ndarray:
        settings = get_settings()
        key = settings.embed_key
        if key and _online_allowed():
            try:
                vecs = LLMClient(settings).embed(texts, timeout=30.0)
                arr = np.asarray(vecs, dtype=np.float32)
                norms = np.linalg.norm(arr, axis=1, keepdims=True)
                norms[norms == 0] = 1.0
                arr = arr / norms
                _EMBED_STATE["online_ok"] = True
                _EMBED_STATE["mode"] = "online"
                return arr
            except Exception as e:  # noqa: BLE001 - 在线失败诚实降级离线
                _EMBED_STATE["online_ok"] = False
                _EMBED_STATE["mode"] = "offline"
                _EMBED_STATE["degraded_reason"] = str(e)[:120]
                _EMBED_STATE["offline_until"] = time.time() + 60
        return _offline_embed(texts)

    return embed


def get_embedder() -> Callable[[list[str]], np.ndarray]:
    """进程级单例 embedder（stats/入库/检索共用同一状态）。"""
    if _embedder_cache["fn"] is None:
        _embedder_cache["fn"] = make_embedder()
    return _embedder_cache["fn"]


def _embed_mode() -> str:
    """当前向量化模式：有 key 且未失败即 online，否则 offline。"""
    if not get_settings().embed_key:
        return "offline"
    if _EMBED_STATE.get("online_ok") is False:
        return "offline"
    return "online"  # 有 key 尚未尝试或在线可用


class _ChromaEF(EmbeddingFunction):  # type: ignore[misc, valid-type]
    """把我们的 embedder 包装成 chromadb 要求的 EmbeddingFunction。

    chromadb 要求继承 EmbeddingFunction 并实现 __call__(input: Documents) -> Embeddings；
    name/build_from_config 须为 staticmethod（chroma 会以 cls.name() / cls.build_from_config()
    形式调用，见 embedding_functions.config_to_embedding_function）。
    """

    def __init__(self, embed: Callable[[list[str]], np.ndarray]):
        self._embed = embed

    @staticmethod
    def name() -> str:
        return "tindalos-rag-embedder"

    @staticmethod
    def build_from_config(cfg: dict) -> "_ChromaEF":
        return _ChromaEF(get_embedder())

    def get_config(self) -> dict:
        return {}

    def __call__(self, input: Documents) -> Embeddings:
        arr = self._embed(list(input))
        return arr.tolist()


# --------------------------------------------------------------------------
# 4. ChromaDB 入库
# --------------------------------------------------------------------------

_client = None
_collection = None
_bm25: BM25Index | None = None


def _rag_dir() -> Path:
    """数据目录：env TINDALOS_RAG_DIR 可覆盖，默认 data/rag/。"""
    return Path(os.environ.get("TINDALOS_RAG_DIR", "data/rag"))


def _get_client():
    """进程级 PersistentClient（惰性创建；anonymized_telemetry 关闭防外联）。"""
    global _client
    if _client is None:
        if chromadb is None:
            raise RuntimeError("chromadb 未安装，无法入库/检索")
        from chromadb.config import Settings as _ChromaSettings

        rag_dir = _rag_dir()
        rag_dir.mkdir(parents=True, exist_ok=True)
        _client = chromadb.PersistentClient(
            path=str(rag_dir),
            settings=_ChromaSettings(anonymized_telemetry=False, allow_reset=True),
        )
    return _client


def _get_collection():
    """进程级 collection（cosine 空间；自定义 EF 包装我们的 embedder）。"""
    global _collection
    if _collection is None:
        col = _get_client().get_or_create_collection(
            "modules",
            embedding_function=_ChromaEF(get_embedder()),
            metadata={"hnsw:space": "cosine"},
        )
        _collection = col
    return _collection


def _record_meta(reserved: dict, extra: dict | None) -> dict:
    """合并调用方 meta 与保留字段（保留字段优先，防覆盖）。"""
    m = dict(extra or {})
    m.update(reserved)
    return m


def _split_parent_child(
    full_text: str,
    chunk_chars: int = 400,
    overlap: int = 80,
    max_parent_chars: int = 800,
) -> tuple[list[dict], list[dict]]:
    """父子分块：父=按 \\n\\n 分段（逐段合并到 ≤max_parent_chars），子=父内滑动窗口。

    返回 (parents, children)：
      parents  = [{"index": p, "text": str}]
      children = [{"text": str, "chunk_index": c, "parent_index": p}]
    """
    segments = [s.strip() for s in full_text.split("\n\n") if s.strip()]
    parents: list[str] = []
    cur = ""
    for seg in segments:
        if cur and len(cur) + len(seg) + 2 > max_parent_chars:
            parents.append(cur)
            cur = ""
        while len(seg) > max_parent_chars:
            parents.append(seg[:max_parent_chars])
            seg = seg[max_parent_chars:]
        if seg:
            cur = (cur + "\n\n" + seg) if cur else seg
    if cur:
        parents.append(cur)

    children: list[dict] = []
    ci = 0
    for pi, ptext in enumerate(parents):
        for win in chunk_text(ptext, chunk_chars=chunk_chars, overlap=overlap):
            children.append({"text": win, "chunk_index": ci, "parent_index": pi})
            ci += 1
    return [{"index": i, "text": t} for i, t in enumerate(parents)], children


def ingest_module(
    module_id: str,
    module_name: str,
    full_text: str,
    *,
    meta: dict | None = None,
) -> int:
    """入库一个模组：子块 + 父块双份写入 ChromaDB，并同步重建/持久化 BM25。

    幂等：先删除同 module_id 旧块再插入。返回子块数。
    """
    col = _get_collection()
    embed = get_embedder()

    # 先删旧块（幂等）
    try:
        col.delete(where={"module_id": module_id})
    except Exception:  # noqa: BLE001 - 删除失败不阻塞后续插入
        pass

    parents, children = _split_parent_child(full_text or "")

    if not children:
        _rebuild_bm25()
        return 0

    # -- 子块入库（参与检索） ----------------------------------------------
    child_ids = [f"{module_id}:c{i}" for i in range(len(children))]
    child_docs = [c["text"] for c in children]
    child_metas = [
        _record_meta(
            {
                "module_id": module_id,
                "module_name": module_name,
                "chunk_index": c["chunk_index"],
                "kind": "child",
                "parent_index": c["parent_index"],
            },
            meta,
        )
        for c in children
    ]

    # -- 父块入库（供 QA 取上下文） -----------------------------------------
    parent_ids = [f"{module_id}:p{p['index']}" for p in parents]
    parent_docs = [p["text"] for p in parents]
    parent_metas = [
        _record_meta(
            {
                "module_id": module_id,
                "module_name": module_name,
                "chunk_index": p["index"],
                "kind": "parent",
            },
            meta,
        )
        for p in parents
    ]

    # 子块 + 父块合并一次 embed（保证单次 ingest 内维度一致：此前分两次调用，
    # 一次在线成功（1024 维）+ 一次失败降级（256 维）→ ChromaDB 入库崩溃/检索全空）
    n = len(child_docs)
    all_emb = embed(child_docs + parent_docs)
    child_emb = all_emb[:n]
    parent_emb = all_emb[n:]

    col.add(
        ids=child_ids,
        documents=child_docs,
        embeddings=child_emb.tolist(),
        metadatas=child_metas,
    )
    col.add(
        ids=parent_ids,
        documents=parent_docs,
        embeddings=parent_emb.tolist(),
        metadatas=parent_metas,
    )

    # 同步维护 BM25 并持久化
    _rebuild_bm25()
    return len(children)


# --------------------------------------------------------------------------
# BM25 持久化
# --------------------------------------------------------------------------


def _save_bm25(clear: bool = False) -> None:
    """写 data/rag/bm25.json（clear=True 或索引为空时删除陈旧文件）。失败静默。"""
    rag = _rag_dir()
    try:
        rag.mkdir(parents=True, exist_ok=True)
        path = rag / "bm25.json"
        if clear or _bm25 is None:
            if path.exists():
                path.unlink()
            return
        with open(path, "w", encoding="utf-8") as f:
            json.dump(_bm25.to_dict(), f, ensure_ascii=False)
    except Exception:  # noqa: BLE001 - 持久化失败不影响内存检索
        pass


def _load_bm25_from_disk() -> BM25Index | None:
    path = _rag_dir() / "bm25.json"
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        if d.get("version") != 1:
            return None
        return BM25Index.from_dict(d)
    except Exception:  # noqa: BLE001 - 损坏文件视为无索引
        return None


def _rebuild_bm25() -> BM25Index | None:
    """从 ChromaDB 全量子块重建 BM25 索引并落盘。返回索引（空库返回 None）。"""
    global _bm25
    try:
        col = _get_collection()
        g = col.get(include=["documents", "metadatas"])
        docs: list[str] = []
        ids: list[str] = []
        metas: list[dict] = []
        for i, mid in enumerate(g["ids"]):
            m = g["metadatas"][i] or {}
            if m.get("kind") != "child":
                continue
            docs.append(g["documents"][i])
            ids.append(mid)
            metas.append(m)
        if not docs:
            _bm25 = None
            _save_bm25(clear=True)
            return None
        index = BM25Index()
        index.fit(docs, doc_ids=ids, metas=metas)
        _bm25 = index
        _save_bm25()
        return index
    except Exception:  # noqa: BLE001 - 检索降级：BM25 不可用仅剩向量
        _bm25 = None
        return None


def _get_bm25_index() -> BM25Index | None:
    """进程内索引：优先内存，其次磁盘，最后从 chroma 重建。"""
    global _bm25
    if _bm25 is None:
        _bm25 = _load_bm25_from_disk()
    if _bm25 is None:
        _bm25 = _rebuild_bm25()
    return _bm25


# --------------------------------------------------------------------------
# 5. 混合检索 + RRF
# --------------------------------------------------------------------------


def _vector_results(res: dict) -> list[dict]:
    """把 chroma query 响应转成统一结果 dict（score = 1 - cosine distance）。"""
    out: list[dict] = []
    if not res or not res.get("ids") or not res["ids"][0]:
        return out
    ids = res["ids"][0]
    docs = res["documents"][0]
    dists = res["distances"][0]
    metas = res["metadatas"][0]
    for i, did in enumerate(ids):
        m = metas[i] or {}
        out.append(
            {
                "doc_id": did,
                "text": docs[i],
                "score": float(max(0.0, 1.0 - dists[i])),
                "module_id": m.get("module_id"),
                "module_name": m.get("module_name"),
                "chunk_index": m.get("chunk_index"),
                "kind": m.get("kind"),
                "parent_index": m.get("parent_index"),
            }
        )
    return out


def _rrf_merge(result_lists: list[list[dict]], *, k: int = 60, top_k: int) -> list[dict]:
    """Reciprocal Rank Fusion：多路检索结果按 doc_id 合并去重，分数 = Σ 1/(k+rank)。"""
    score_map: dict[str, float] = {}
    holder: dict[str, dict] = {}
    for rl in result_lists:
        for rank, r in enumerate(rl):
            did = r.get("doc_id")
            if not did:
                continue
            score_map[did] = score_map.get(did, 0.0) + 1.0 / (k + rank + 1)
            holder.setdefault(did, r)
    ranked = sorted(score_map.items(), key=lambda kv: kv[1], reverse=True)
    out: list[dict] = []
    for did, sc in ranked[:top_k]:
        r = dict(holder[did])
        r["score"] = round(float(sc), 6)
        out.append(r)
    return out


def search(
    query: str,
    *,
    module_id: str | None = None,
    top_k: int = 6,
) -> list[dict]:
    """混合检索：向量 top-20 + BM25 top-20 → RRF(k=60) 合并 → top_k。

    module_id 过滤：向量用 chroma where，BM25 在索引内按模块分组过滤。
    空索引 → 返回 []。结果 dict：{text, score, module_id, module_name, chunk_index, kind}。
    """
    if not query:
        return []
    try:
        col = _get_collection()
        if col.count() == 0:
            return []
        embed = get_embedder()
        where: dict[str, Any] = {"kind": "child"}
        if module_id:
            where = {"$and": [{"module_id": module_id}, {"kind": "child"}]}
        res = col.query(
            query_embeddings=embed([query]).tolist(),
            n_results=20,
            where=where,
            include=["documents", "metadatas", "distances"],
        )
    except Exception:  # noqa: BLE001 - 检索失败按空结果降级，不抛致命
        return []
    vec_results = _vector_results(res)

    bm_results: list[dict] = []
    bm = _get_bm25_index()
    if bm is not None and bm.N > 0:
        allowed = bm.doc_ids_by_module(module_id) if module_id else None
        bm_results = bm.search(query, top_k=20, doc_ids=allowed)

    return _rrf_merge([vec_results, bm_results], k=60, top_k=top_k)


# --------------------------------------------------------------------------
# 6. RAG 问答
# --------------------------------------------------------------------------


def _split_sentences(text: str) -> list[str]:
    """按中文句末标点/换行切句，去空。"""
    import re

    parts = re.split(r"[。！？\n；;]+", text)
    return [p.strip() for p in parts if p.strip()]


def _local_answer(question: str, results: list[dict]) -> str:
    """本地降级答案：取 top 块中含问题关键词的句子拼接 + 诚实提示。"""
    keywords = [t for t in tokenize(question) if len(t) >= 2]
    seen: set[str] = set()
    kws: list[str] = []
    for t in keywords:
        if t not in seen:
            seen.add(t)
            kws.append(t)

    sentences: list[str] = []
    for r in results:
        for s in _split_sentences(r.get("text", "")):
            if any(k in s for k in kws):
                sentences.append(s)
    # 去重保序
    dedup: list[str] = []
    sseen: set[str] = set()
    for s in sentences:
        if s not in sseen:
            sseen.add(s)
            dedup.append(s)
    sentences = dedup[:8]

    if not sentences and results:
        sentences = _split_sentences(results[0].get("text", ""))[:2]

    if not sentences:
        return "本地检索未找到与问题直接相关的内容。（本地检索，无 LLM）"
    return "；".join(sentences) + "（本地检索，无 LLM）"


def _build_context(sources: list[dict], limit_chars: int = 6000) -> str:
    """拼接 LLM 参考上下文（带来源标注）。"""
    parts: list[str] = []
    for i, s in enumerate(sources, 1):
        head = f"[来源{i} 模块:{s.get('module_name', '')} 块:{s.get('chunk_index', '')}]"
        parts.append(f"{head}\n{s['text']}")
    ctx = "\n\n".join(parts)
    return ctx[:limit_chars] if limit_chars else ctx


def _llm_answer(question: str, context: str, rules: str | None, sources: list[dict]) -> str:
    """在线 LLM 问答（统一 LLMClient.chat）。

    端点/模型/key/超时自动取 Settings（本地 Ollama 与云端 OpenAI 兼容端点通用），
    失败抛 LLMError → 上层 qa() try/except 兜底降级本地。
    """
    settings = get_settings()
    system = (
        "你是 TRPG 规则问答助手（Tindalos）。规则体系无关（COC/DND 皆可回答）；用中文回答。\n"
        "作答要求（按优先级）：\n"
        "1. 直接回答用户的问题，先给结论，再分点补充规则细节；用你自己的话组织语言，"
        "不要照抄参考来源原文。\n"
        "2. 只依据下面给出的参考来源作答，不得编造来源之外的信息；"
        "若用户问的细节在来源中找不到，明确回答『来源未提及』。\n"
        "3. 引用来源时，在相应句子末尾用 [来源N] 标注（N 对应参考来源编号）。\n"
        "4. 答案要简洁克制：只回答问到的，不展开无关背景，避免重复与冗余。"
    )
    if rules:
        system += f"\n本次问答适用的规则体系：{rules}。"

    return LLMClient(settings).chat(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": "问题：" + question + "\n\n参考来源：\n" + context},
        ],
        temperature=0.3,
    ).strip()


def _fetch_parent_records(child_results: list[dict]) -> list[dict]:
    """按子块的 parent_index 取对应父块记录（供 QA 上下文）。"""
    col = _get_collection()
    parent_ids: set[str] = set()
    for r in child_results:
        pi = r.get("parent_index")
        mid = r.get("module_id")
        if pi is not None and mid:
            parent_ids.add(f"{mid}:p{pi}")
    if not parent_ids:
        return []
    try:
        got = col.get(ids=list(parent_ids), include=["documents", "metadatas"])
    except Exception:  # noqa: BLE001 - 取父块失败不致命
        return []
    out: list[dict] = []
    for i, pid in enumerate(got["ids"]):
        m = got["metadatas"][i] or {}
        out.append(
            {
                "doc_id": pid,
                "text": got["documents"][i],
                "score": 0.0,
                "module_id": m.get("module_id"),
                "module_name": m.get("module_name"),
                "chunk_index": m.get("chunk_index"),
                "kind": "parent",
            }
        )
    return out


def _merge_sources(child_results: list[dict], parent_results: list[dict]) -> list[dict]:
    """子块 + 对应父块拼成 sources（父块分数取其子块最大 RRF 分数）。"""
    parent_score: dict[tuple[str, int], float] = {}
    for r in child_results:
        pi = r.get("parent_index")
        mid = r.get("module_id")
        if pi is not None and mid:
            key = (str(mid), int(pi))
            parent_score[key] = max(parent_score.get(key, 0.0), float(r.get("score", 0.0)))
    out: list[dict] = []
    for r in child_results:
        out.append(
            {
                "text": r.get("text", ""),
                "module_id": r.get("module_id"),
                "module_name": r.get("module_name"),
                "score": r.get("score", 0.0),
                "kind": "child",
                "chunk_index": r.get("chunk_index"),
            }
        )
    for p in parent_results:
        pi = p.get("chunk_index")
        mid = p.get("module_id")
        key = (str(mid), int(pi)) if (mid is not None and pi is not None) else None
        out.append(
            {
                "text": p.get("text", ""),
                "module_id": mid,
                "module_name": p.get("module_name"),
                "score": parent_score.get(key, 0.0) if key else 0.0,
                "kind": "parent",
                "chunk_index": pi,
            }
        )
    return out


def _dedup_sources(sources: list[dict]) -> list[dict]:
    """按文本内容去重（同一 PDF 被多次上传会索引出重复块；父块与子块也可能重叠）。

    取每条来源的空白归一文本作 key，同 key 只保留 score 最高的一条；
    返回仍按 score 降序（检索信号优先）。
    """
    seen: set[str] = set()
    out: list[dict] = []
    for s in sorted(sources, key=lambda x: float(x.get("score") or 0.0), reverse=True):
        norm = "".join((s.get("text") or "").split())
        if not norm or norm in seen:
            continue
        seen.add(norm)
        out.append(s)
    return out


def qa(
    question: str,
    *,
    module_id: str | None = None,
    rules: str | None = None,
) -> dict:
    """RAG 问答。LLM 模式需 TINDALOS_API_KEY + TINDALOS_LLM_ENABLED==1；
    否则/失败 → 本地降级（mode="local"，诚实标注"无 LLM"）。

    返回 {"answer", "sources": [{text, module_id, module_name, score}], "mode", "rules"}。
    """
    results = search(question, module_id=module_id, top_k=6)
    parent_results = _fetch_parent_records(results)
    sources = _dedup_sources(_merge_sources(results, parent_results))

    settings = get_settings()
    llm_key = settings.api_key
    llm_enabled = settings.llm_enabled

    degraded_reason = ""
    if llm_key and llm_enabled and results:
        context = _build_context(sources)
        try:
            answer = _llm_answer(question, context, rules, sources)
            return {
                "answer": answer,
                "sources": sources,
                "mode": "llm",
                "rules": rules,
            }
        except Exception as e:  # noqa: BLE001 - 在线失败诚实降级本地
            degraded_reason = str(e)[:200]
    elif not results:
        answer = "本地检索无结果（资料库为空或未找到相关内容）。（本地检索，无 LLM）"
    else:
        degraded_reason = "no-key-or-llm-disabled"

    answer = _local_answer(question, results)
    out: dict[str, Any] = {
        "answer": answer,
        "sources": sources,
        "mode": "local",
        "rules": rules,
    }
    if degraded_reason:
        out["degraded_reason"] = degraded_reason
    return out


# --------------------------------------------------------------------------
# 7. 辅助
# --------------------------------------------------------------------------


def stats() -> dict:
    """返回 {chunks, modules, dir, embed_mode}。空库/未初始化均安全返回默认值。"""
    data: dict[str, Any] = {
        "chunks": 0,
        "modules": 0,
        "dir": str(_rag_dir()),
        "embed_mode": _embed_mode(),
    }
    try:
        col = _get_collection()
        g = col.get(include=["metadatas"])
        metas = g["metadatas"] or []
        if metas:
            data["chunks"] = sum(1 for m in metas if m.get("kind") == "child")
            data["modules"] = len({m.get("module_id") for m in metas if m.get("module_id")})
    except Exception:  # noqa: BLE001 - 统计失败按空返回
        pass
    return data


def reset() -> None:
    """清空数据目录与内存态（测试用）。释放 chroma 客户端后删除 rag 目录。"""
    global _client, _collection, _bm25
    _bm25 = None
    if _client is not None:
        try:
            _client.reset()
        except Exception:  # noqa: BLE001
            pass
        try:
            _client.close()
        except Exception:  # noqa: BLE001
            pass
        _client = None
    _collection = None
    rag = _rag_dir()
    if rag.exists():
        shutil.rmtree(rag, ignore_errors=True)
    _EMBED_STATE.clear()
    _EMBED_STATE.update({"online_ok": None, "mode": "offline"})


__all__ = [
    "chunk_text",
    "tokenize",
    "BM25Index",
    "make_embedder",
    "get_embedder",
    "ingest_module",
    "search",
    "qa",
    "stats",
    "reset",
]
