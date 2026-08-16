"""四类记忆条目层（P0-a，零 LLM）。

设计文档《云数据库 + 记忆系统 + Eval 系统统一落地路线》§3/§5：
- 两轴 × 四类：内容类型（情景 episodic / 语义 semantic）× 保留时限（短期
  shortterm / 长期 longterm）。P0-a 只落 episodic + semantic 写入与检索，
  shortterm / longterm 由 P1 整合维护层填充，但 schema 与 list 已就位。
- 写入 = 追加式 upsert（event id 幂等、content_hash 判重），不主动物理删除
  （旧版本置 superseded / consolidated，P1）。
- 读取 = BM25 检索（复用 rag.tokenize / rag.BM25Index）+ 近因加权，注入
  write_act（历史记忆；首轮生成无历史即返回空，不影响确定性输出）。

与 memory.py（t14 聚合记忆）并存且互补：
- memory.py 写「快照式聚合事实」（campaign 整体 → NPC 印象/关键事件/世界状态），
  服务备团笔记 render_notes 与 CLI memories 的聚合视角；
- 本模块写「逐条可检索事件流 + 语义事实」，服务 write_act 记忆注入与 CLI 四类视角。
- 两者都在 pipeline.compose 收敛点写入（try/except，写失败不阻塞生成）。

落盘：独立 SQLite（settings.store_dir / "memory_entries.sqlite"），尊重
TINDALOS_DATA_DIR / store_dir 语义，与 memory.sqlite 分开，互不干扰。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from tindalos.config import Settings, get_settings
from tindalos.models import Campaign, Scene
from tindalos.rag import BM25Index

# 四类记忆（与设计文档 §3 词汇表一致；保留时限轴由 P1 写入）
MEMORY_TYPES: tuple[str, ...] = ("episodic", "semantic", "shortterm", "longterm")
_ACTIVE = "active"
_SUPERSEDED = "superseded"
_CONSOLIDATED = "consolidated"

# 内容单条 ≤ 200 字（克制原则：harness 是限制器，避免过度生成）
_MAX_CONTENT_CHARS = 200

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_entries (
  id               TEXT PRIMARY KEY,        -- 'evm:...' / 'sem:...' / 'stm:...' / 'ltm:...'
  campaign_id      TEXT NOT NULL,
  memory_type      TEXT NOT NULL,           -- episodic | semantic | shortterm | longterm
  content          TEXT NOT NULL,           -- 单条 ≤ 200 字（克制）
  importance       REAL NOT NULL DEFAULT 0.5,  -- 0~1
  source_episode   TEXT,                    -- 生成时的 act/scene/event 溯源
  ref_ids          TEXT,                    -- JSON：关联条目 id（整合链）
  subject_key      TEXT,                    -- 语义去重键（episodic 可空）
  status           TEXT NOT NULL DEFAULT 'active',  -- active | superseded | consolidated
  valid_from       TEXT,
  valid_to         TEXT,
  supersedes_id    TEXT,
  consolidated_into TEXT,
  content_hash     TEXT NOT NULL,           -- 幂等写入判重
  embedding        BLOB,                    -- 可选向量（P2）
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL,
  last_accessed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_mem_campaign ON memory_entries(campaign_id);
CREATE INDEX IF NOT EXISTS idx_mem_type ON memory_entries(campaign_id, memory_type);
CREATE INDEX IF NOT EXISTS idx_mem_status ON memory_entries(campaign_id, status);
"""


# ---------------------------------------------------------------- 路径与连接


def entries_db_path(settings: Settings | None = None) -> Path:
    """memory_entries 独立 SQLite 路径：<store_dir>/memory_entries.sqlite。"""
    settings = settings or get_settings()
    return settings.store_dir / "memory_entries.sqlite"


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    """打开连接并建表（幂等 DDL）。autocommit 模式，无事务嵌套问题。"""
    path = Path(db_path or entries_db_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _clip(content: str) -> str:
    """克制：截断到 _MAX_CONTENT_CHARS 字（设计文档 §3 单条上限）。"""
    return content if len(content) <= _MAX_CONTENT_CHARS else content[:_MAX_CONTENT_CHARS] + "…"


# ---------------------------------------------------------------- 幂等写入


def _upsert(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    """追加式 upsert：id 冲突且 content_hash 相同 → 不更新（幂等）；
    id 冲突且 hash 不同（regenerate 覆盖新内容）→ 更新并置回 active。"""
    conn.execute(
        """
        INSERT INTO memory_entries (
          id, campaign_id, memory_type, content, importance, source_episode,
          ref_ids, subject_key, status, valid_from, valid_to, supersedes_id,
          consolidated_into, content_hash, embedding, created_at, updated_at
        ) VALUES (
          :id, :campaign_id, :memory_type, :content, :importance, :source_episode,
          :ref_ids, :subject_key, :status, :valid_from, :valid_to, :supersedes_id,
          :consolidated_into, :content_hash, :embedding, :created_at, :updated_at
        )
        ON CONFLICT(id) DO UPDATE SET
          content            = excluded.content,
          importance         = excluded.importance,
          source_episode     = excluded.source_episode,
          ref_ids            = excluded.ref_ids,
          subject_key        = excluded.subject_key,
          status             = excluded.status,
          content_hash       = excluded.content_hash,
          updated_at         = excluded.updated_at
        WHERE excluded.content_hash != memory_entries.content_hash
        """,
        row,
    )


def _count(conn: sqlite3.Connection, campaign_id: str, memory_type: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM memory_entries WHERE campaign_id = ? AND memory_type = ?",
        (campaign_id, memory_type),
    ).fetchone()
    return int(row["n"]) if row else 0


# ---------------------------------------------------------------- 情景记忆


def _as_dict(obj: Any) -> dict:
    """宽松归一：pydantic 模型或普通 dict → dict（构造宽松 Campaign 时同样可用）。"""
    return obj.model_dump() if hasattr(obj, "model_dump") else dict(obj)


def _episodic_item(campaign: Campaign, scene: Scene, ev: dict, act_index: int, scene_index: int, event_index: int) -> dict[str, Any]:
    """单条情景记忆：event → 条目。event id 全局唯一（Campaign 跨层校验保证），
    直接作为条目 id 幂等键；content_hash 判重（regenerate 换内容时覆盖）。"""
    act = campaign.acts[act_index]
    event_id = ev["id"]
    kind = ev.get("kind", "entry")
    importance = {"outcome": 0.8, "trigger": 0.65}.get(kind, 0.5)
    description = (ev.get("description") or "").strip().replace("\n", " ")
    content = _clip(f"[{act.title}·{scene.title}] {ev.get('title', event_id)}：{description}")
    episode = f"{act.id}/{scene.id}/{event_id}"
    now = _now()
    return {
        "id": f"evm:{campaign.id}:{event_id}",
        "campaign_id": campaign.id,
        "memory_type": "episodic",
        "content": content,
        "importance": importance,
        "source_episode": episode,
        "ref_ids": json.dumps([act.id, scene.id, event_id], ensure_ascii=False),
        "subject_key": None,
        "status": _ACTIVE,
        "valid_from": None,
        "valid_to": None,
        "supersedes_id": None,
        "consolidated_into": None,
        "content_hash": _content_hash(content),
        "embedding": None,
        "created_at": now,
        "updated_at": now,
    }


def capture_episodic(campaign: Campaign, db_path: Path | None = None) -> dict[str, int]:
    """情景记忆：逐 act/scene/event 追加式 upsert（设计文档 §3.3 写入口）。

    幂等：同 campaign 两次捕获，id 相同 + content_hash 相同 → 不重复写入。
    返回 {"inserted": n, "updated": m, "total": t}（total = 该 campaign 情景条目总数）。
    """
    conn = _connect(db_path)
    before = _count(conn, campaign.id, "episodic")
    total_events = 0
    for a, act in enumerate(campaign.acts):
        for s, scene in enumerate(act.scenes):
            for e, ev in enumerate(scene.events):
                _upsert(conn, _episodic_item(campaign, scene, _as_dict(ev), a, s, e))
                total_events += 1
    after = _count(conn, campaign.id, "episodic")
    inserted = after - before
    updated = max(0, total_events - inserted)
    conn.close()
    return {"inserted": inserted, "updated": updated, "total": after}


# ---------------------------------------------------------------- 语义记忆


def _semantic_items(campaign: Campaign) -> list[dict[str, Any]]:
    """确定性抽取（零 LLM）：NPC 事实（subject_key=npc:<id>）+ 地点事实（place:<scene_id>）。

    复用 memory.npc_impression 的同一视角（身份 + 特质 + 本局角色 + 描述），
    保证两条记忆视图互相一致；subject_key 去重（同 NPC 两次捕获只留一份）。
    """
    from tindalos.memory import npc_impression  # 延迟导入避免循环依赖

    items: list[dict[str, Any]] = []
    now = _now()
    for npc_id, npc in campaign.npcs.items():
        content = _clip(npc_impression(npc))
        items.append(
            {
                "id": f"sem:{campaign.id}:npc:{npc_id}",
                "campaign_id": campaign.id,
                "memory_type": "semantic",
                "content": content,
                "importance": 0.8,
                "source_episode": npc_id,
                "ref_ids": json.dumps([npc_id], ensure_ascii=False),
                "subject_key": f"npc:{npc_id}",
                "status": _ACTIVE,
                "valid_from": None,
                "valid_to": None,
                "supersedes_id": None,
                "consolidated_into": None,
                "content_hash": _content_hash(content),
                "embedding": None,
                "created_at": now,
                "updated_at": now,
            }
        )
    for a, act in enumerate(campaign.acts):
        for s, scene in enumerate(act.scenes):
            setting = scene.setting or {}
            time_, place = setting.get("time", ""), setting.get("place", "")
            if not (time_ or place):
                continue
            content = _clip(f"[{act.title}·{scene.title}] 地点：{place or '未知'}，时间：{time_ or '未知'}")
            items.append(
                {
                    "id": f"sem:{campaign.id}:place:{scene.id}",
                    "campaign_id": campaign.id,
                    "memory_type": "semantic",
                    "content": content,
                    "importance": 0.5,
                    "source_episode": f"{act.id}/{scene.id}",
                    "ref_ids": json.dumps([act.id, scene.id], ensure_ascii=False),
                    "subject_key": f"place:{scene.id}",
                    "status": _ACTIVE,
                    "valid_from": None,
                    "valid_to": None,
                    "supersedes_id": None,
                    "consolidated_into": None,
                    "content_hash": _content_hash(content),
                    "embedding": None,
                    "created_at": now,
                    "updated_at": now,
                }
            )
    return items


def capture_semantic_initial(campaign: Campaign, db_path: Path | None = None) -> dict[str, int]:
    """语义记忆：确定性抽取 NPC/地点事实，subject_key 去重（设计文档 §3.3 写入口）。"""
    conn = _connect(db_path)
    before = _count(conn, campaign.id, "semantic")
    items = _semantic_items(campaign)
    for item in items:
        _upsert(conn, item)
    after = _count(conn, campaign.id, "semantic")
    inserted = after - before
    updated = max(0, len(items) - inserted)
    conn.close()
    return {"inserted": inserted, "updated": updated, "total": after}


def capture_memory_entries(campaign: Campaign, db_path: Path | None = None) -> dict[str, dict[str, int]]:
    """compose 收敛点单点：情景 + 语义一并写入（设计文档 §3.3）。"""
    return {
        "episodic": capture_episodic(campaign, db_path),
        "semantic": capture_semantic_initial(campaign, db_path),
    }


# ---------------------------------------------------------------- 读取


def list_entries(
    campaign_id: str,
    memory_type: str | None = None,
    db_path: Path | None = None,
    status: str = _ACTIVE,
) -> list[dict]:
    """读取某 campaign 的记忆条目（按 created_at 升序；P0-a 只回 active）。"""
    conn = _connect(db_path)
    sql = "SELECT * FROM memory_entries WHERE campaign_id = ? AND status = ?"
    params: list[Any] = [campaign_id, status]
    if memory_type:
        sql += " AND memory_type = ?"
        params.append(memory_type)
    sql += " ORDER BY created_at ASC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def count_entries(campaign_id: str, db_path: Path | None = None) -> dict[str, int]:
    """按四类统计（CLI 概览）。"""
    conn = _connect(db_path)
    counts = {
        mt: int(
            conn.execute(
                "SELECT COUNT(*) AS n FROM memory_entries WHERE campaign_id = ? AND memory_type = ? AND status = 'active'",
                (campaign_id, mt),
            ).fetchone()["n"]
        )
        for mt in MEMORY_TYPES
    }
    conn.close()
    return counts


_MEMORY_TYPE_LABELS = {
    "episodic": "情景记忆",
    "semantic": "语义记忆",
    "shortterm": "短期记忆",
    "longterm": "长期记忆",
}


def render_entries_doc(campaign_id: str, entries: Sequence[dict] | None = None, db_path: Path | None = None) -> str:
    """按四类渲染 markdown 记忆清单（CLI memories 追加节，与聚合视角并存）。"""
    if entries is None:
        entries = list_entries(campaign_id, db_path=db_path)
    if not entries:
        return ""
    lines = ["## 记忆条目（四类）", ""]
    for mt in MEMORY_TYPES:
        group = [e for e in entries if e["memory_type"] == mt]
        if not group:
            continue
        lines.append(f"### {_MEMORY_TYPE_LABELS[mt]}（{len(group)} 条）")
        for e in group:
            tag = f"（{e['importance']:.2f}）" if e.get("importance") is not None else ""
            src = f"〔{e['source_episode']}〕" if e.get("source_episode") else ""
            lines.append(f"- {e['content']} {tag}{src}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------- write_act 注入


def assemble_memory_context(
    campaign_id: str,
    query: str,
    top_k: int = 6,
    db_path: Path | None = None,
) -> str:
    """BM25 检索 + 近因加权 → markdown 记忆上下文（write_act 注入用）。

    - 复用 rag.BM25Index 中文分词（tokenize）；
    - 无历史 / 检索无命中 → 返回空串（首轮生成零影响，保持确定性）；
    - 排序分 = bm25 × decay^天数 × (0.5 + importance)，天然偏向近期 + 高重要条目；
    - 克制：top_k 上限 + 空命中即截断，不把无关记忆塞进生成。
    """
    rows = list_entries(campaign_id, db_path=db_path, status=_ACTIVE)
    if not rows:
        return ""
    idx = BM25Index().fit(
        [r["content"] for r in rows],
        doc_ids=[r["id"] for r in rows],
    )
    hits = idx.search(query, len(rows))
    if not hits:
        return ""
    now = datetime.now(timezone.utc)
    scored: list[tuple[float, dict]] = []
    for h in hits:
        if not h.get("score") or h["score"] <= 0:
            continue
        row = next((r for r in rows if r["id"] == h["doc_id"]), None)
        if row is None:
            continue
        try:
            created = datetime.fromisoformat(row["created_at"])
            age_days = max(0.0, (now - created).total_seconds() / 86400.0)
        except (ValueError, TypeError):
            age_days = 0.0
        decay = 0.85 ** age_days  # 近因加权：1 天 0.85，7 天 ≈ 0.32，30 天 ≈ 0.008
        importance = float(row.get("importance") or 0.5)
        final = float(h["score"]) * decay * (0.5 + importance)
        scored.append((final, row))
    if not scored:
        return ""
    scored.sort(key=lambda x: x[0], reverse=True)
    lines = ["【既有记忆】"]
    for _, row in scored[:top_k]:
        tag = _MEMORY_TYPE_LABELS.get(row["memory_type"], row["memory_type"])
        lines.append(f"- （{tag}）{row['content']}")
    return "\n".join(lines)


__all__ = [
    "MEMORY_TYPES",
    "entries_db_path",
    "capture_episodic",
    "capture_semantic_initial",
    "capture_memory_entries",
    "list_entries",
    "count_entries",
    "render_entries_doc",
    "assemble_memory_context",
]
