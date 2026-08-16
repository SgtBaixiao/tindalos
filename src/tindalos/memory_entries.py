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

CREATE TABLE IF NOT EXISTS play_sessions (
  id             TEXT PRIMARY KEY,        -- 'sess:<campaign_id>:<index>'
  campaign_id    TEXT NOT NULL,
  session_index  INTEGER NOT NULL,
  summary        TEXT NOT NULL,
  play_status    TEXT,                    -- 最近一次游玩状态（结局/进行中/...）
  conflicts      TEXT,                    -- JSON：分歧/规则裁定记录
  created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ps_campaign ON play_sessions(campaign_id);
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


# ---------------------------------------------------------------- P1 整合维护层

# longterm 三键（设计文档 §3.4 / P1：consolidate 产出长期记忆的固定 subject_key）
_LLM_LONGTERM_KEYS: tuple[str, ...] = ("synopsis", "plotline", "npc_arcs")


def _active_rows(conn: sqlite3.Connection, campaign_id: str, memory_type: str) -> list[dict[str, Any]]:
    """某 campaign 该类型的 active 条目（created_at + rowid 稳定排序，供「最旧一批」判定）。

    rowid 作同秒时间戳的稳定 tiebreaker，保证确定性：同样输入两次调用取同一批。
    """
    rows = conn.execute(
        "SELECT * FROM memory_entries WHERE campaign_id = ? AND memory_type = ? AND status = 'active' "
        "ORDER BY created_at ASC, rowid ASC",
        (campaign_id, memory_type),
    ).fetchall()
    return [dict(r) for r in rows]


def _mark_superseded(conn: sqlite3.Connection, entry_id: str, supersedes_id: str | None) -> None:
    """把某条目置 superseded（supersedes_id 链；绝不物理删除）。"""
    conn.execute(
        "UPDATE memory_entries SET status = 'superseded', supersedes_id = ?, updated_at = ? WHERE id = ?",
        (supersedes_id, _now(), entry_id),
    )


def _mark_oldest_episodic_consolidated(
    conn: sqlite3.Connection, campaign_id: str, min_episodic: int
) -> tuple[list[str], list[dict[str, Any]]]:
    """情景上限：active episodic 超出 min_episodic 时，把最旧一批置 consolidated。

    返回 (被整合的条目 id 列表, 对应行)。幂等：未超限 → 空，不写任何行。
    """
    active = _active_rows(conn, campaign_id, "episodic")
    overflow = len(active) - min_episodic
    if overflow <= 0:
        return [], []
    batch = active[:overflow]
    ids = [r["id"] for r in batch]
    now = _now()
    for eid in ids:
        conn.execute(
            "UPDATE memory_entries SET status = 'consolidated', updated_at = ? WHERE id = ? AND status = 'active'",
            (now, eid),
        )
    return ids, batch


def _backfill_consolidated_into(
    conn: sqlite3.Connection, consolidated_ids: Sequence[str], target_id: str | None
) -> None:
    """整合回填：consolidated 条目指向它们被整合进的长条目 id。"""
    if not consolidated_ids:
        return
    now = _now()
    for eid in consolidated_ids:
        conn.execute(
            "UPDATE memory_entries SET consolidated_into = ?, updated_at = ? WHERE id = ?",
            (target_id, now, eid),
        )


def _write_longterm(
    conn: sqlite3.Connection,
    campaign_id: str,
    subject_key: str,
    content: str,
    ref_ids: Sequence[str] | None = None,
    source_episode: str | None = None,
    importance: float = 0.8,
) -> tuple[str, bool]:
    """写一条 longterm（subject_key ∈ synopsis/plotline/npc_arcs）。

    同键旧 active 条目置 superseded（supersedes_id 指向新版）；同键同内容 → 幂等跳过。
    返回 (条目 id, 是否实际写入)。
    """
    content = _clip(content)
    c_hash = _content_hash(content)
    now = _now()
    existing = conn.execute(
        "SELECT id, content_hash FROM memory_entries "
        "WHERE campaign_id = ? AND memory_type = 'longterm' AND subject_key = ? AND status = 'active' "
        "ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (campaign_id, subject_key),
    ).fetchone()
    if existing is not None and existing["content_hash"] == c_hash:
        return existing["id"], False
    new_id = f"ltm:{campaign_id}:{subject_key}:{c_hash[:12]}"
    if existing is not None:
        _mark_superseded(conn, existing["id"], new_id)
    row = {
        "id": new_id,
        "campaign_id": campaign_id,
        "memory_type": "longterm",
        "content": content,
        "importance": importance,
        "source_episode": source_episode,
        "ref_ids": json.dumps(list(ref_ids or []), ensure_ascii=False),
        "subject_key": subject_key,
        "status": _ACTIVE,
        "valid_from": None,
        "valid_to": None,
        "supersedes_id": existing["id"] if existing is not None else None,
        "consolidated_into": None,
        "content_hash": c_hash,
        "embedding": None,
        "created_at": now,
        "updated_at": now,
    }
    _upsert(conn, row)
    return new_id, True


def _insert_entry(
    conn: sqlite3.Connection,
    campaign_id: str,
    memory_type: str,
    entry_id: str,
    content: str,
    subject_key: str | None = None,
    ref_ids: Sequence[str] | None = None,
    source_episode: str | None = None,
    importance: float = 0.6,
) -> None:
    """追加式写入一条非 longterm 条目（LLM 路径 ADD/UPDATE 的落地）。"""
    content = _clip(content)
    now = _now()
    row = {
        "id": entry_id,
        "campaign_id": campaign_id,
        "memory_type": memory_type,
        "content": content,
        "importance": importance,
        "source_episode": source_episode,
        "ref_ids": json.dumps(list(ref_ids or []), ensure_ascii=False),
        "subject_key": subject_key,
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
    _upsert(conn, row)


def _active_by_subject(
    conn: sqlite3.Connection, campaign_id: str, memory_type: str, subject_key: str
) -> dict | None:
    """定位 active 条目（kind + subject_key）；无 → None。"""
    row = conn.execute(
        "SELECT id FROM memory_entries WHERE campaign_id = ? AND memory_type = ? AND subject_key = ? "
        "AND status = 'active' ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (campaign_id, memory_type, subject_key),
    ).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------- LLM 两段式


def _build_prompt(campaign_id: str, episodic: Sequence[dict], semantic: Sequence[dict]) -> str:
    """第一遍 prompt：把 active episodic + semantic 摘要喂给 LLM 提议操作。"""
    lines = [
        "你是记忆整合助手。请把给定战役（campaign）的情景记忆与语义事实整合为长期记忆。",
        "只返回 JSON 操作列表，不要解释。每项形如：",
        '{"op": "ADD|UPDATE|DELETE", "kind": "episodic|semantic|longterm", "subject_key": "...", "content": "...", "ref_ids": [...]}',
        "规则：",
        "- ADD：新增一条记忆；UPDATE：按 subject_key 定位旧条目并写新内容；DELETE：把 subject_key 条目标记为删除。",
        "- longterm 的 subject_key 只能是 synopsis / plotline / npc_arcs 之一。",
        "- content 单条不超过 200 字。",
        "",
        f"campaign_id: {campaign_id}",
        "",
        "## 情景记忆（episodic）",
    ]
    for e in episodic:
        lines.append(f"- [{e.get('source_episode') or e.get('id')}] {e.get('content', '')}")
    lines += ["", "## 语义事实（semantic）"]
    for s in semantic:
        lines.append(f"- ({s.get('subject_key') or s.get('id')}) {s.get('content', '')}")
    return "\n".join(lines)


def _extract_json_list(text: str) -> list | None:
    """容错 JSON 数组提取：裸 JSON、```json ``` 包裹、或前后带说明文字。失败 → None。"""
    text = text.strip()
    if not text:
        return None
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        pass
    import re

    m = re.search(r"\[[\s\S]*\]", text)
    if m is not None:
        try:
            data = json.loads(m.group(0))
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            return None
    return None


def _parse_ops(text: str) -> list[dict[str, Any]]:
    """LLM 返回的 JSON 文本 → 规范化操作列表；任何结构不合法 → []（确定性降级，不抛异常）。"""
    data = _extract_json_list(text)
    if data is None:
        return []
    ops: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        op = str(item.get("op", "")).strip().upper()
        kind = str(item.get("kind", "")).strip().lower()
        if op not in ("ADD", "UPDATE", "DELETE") or kind not in ("episodic", "semantic", "longterm"):
            continue
        content = item.get("content")
        subject_key = item.get("subject_key")
        ref_ids = item.get("ref_ids")
        if content is not None and not isinstance(content, str):
            continue
        if subject_key is not None and not isinstance(subject_key, str):
            continue
        if ref_ids is not None and not isinstance(ref_ids, list):
            continue
        content = content.strip() if content is not None else None
        if subject_key is not None:
            subject_key = subject_key.strip()
        if op in ("ADD", "UPDATE") and not content:
            continue
        if op in ("UPDATE", "DELETE") and not subject_key:
            continue
        if kind == "longterm" and subject_key not in _LLM_LONGTERM_KEYS:
            continue
        ops.append(
            {
                "op": op,
                "kind": kind,
                "subject_key": subject_key,
                "content": content,
                "ref_ids": [r for r in ref_ids if isinstance(r, str)] if ref_ids else None,
            }
        )
    return ops


def _apply_ops(
    conn: sqlite3.Connection, campaign_id: str, ops: list[dict[str, Any]]
) -> tuple[int, list[str]]:
    """第二遍执行：ADD 新增、UPDATE 旧条目置 superseded 再写新版、DELETE 置 superseded。

    绝不物理删除。返回 (成功应用数, 操作写过的 longterm subject_key 列表)。
    """
    applied = 0
    longterm_keys: list[str] = []
    for op in ops:
        kind = op["kind"]
        subject_key = op.get("subject_key")
        content = op.get("content")
        ref_ids = op.get("ref_ids")
        if kind == "longterm":
            _write_longterm(conn, campaign_id, subject_key, content, ref_ids=ref_ids)
            longterm_keys.append(subject_key)
            applied += 1
            continue
        if op["op"] == "ADD":
            c_hash = _content_hash(content)[:12]
            if kind == "semantic":
                suffix = subject_key or "add"
                new_id = f"sem:{campaign_id}:{suffix}:{c_hash}"
            else:
                new_id = f"evm:{campaign_id}:add:{c_hash}"
            _insert_entry(conn, campaign_id, kind, new_id, content, subject_key=subject_key, ref_ids=ref_ids)
            applied += 1
        elif op["op"] == "UPDATE":
            target = _active_by_subject(conn, campaign_id, kind, subject_key)
            if target is None:
                continue
            c_hash = _content_hash(content)[:12] if content else "update"
            if kind == "semantic":
                new_id = f"sem:{campaign_id}:{subject_key}:{c_hash}"
            else:
                new_id = f"evm:{campaign_id}:{subject_key}:{c_hash}"
            _insert_entry(conn, campaign_id, kind, new_id, content, subject_key=subject_key, ref_ids=ref_ids)
            _mark_superseded(conn, target["id"], new_id)
            applied += 1
        else:  # DELETE
            target = _active_by_subject(conn, campaign_id, kind, subject_key)
            if target is None:
                continue
            _mark_superseded(conn, target["id"], None)
            applied += 1
    return applied, longterm_keys


def _safe_parse_ops(conn: sqlite3.Connection, campaign_id: str, llm: Any) -> list[dict[str, Any]]:
    """第一遍：把摘要喂给 llm，解析操作列表；LLM 抛异常 / 返回非法结构 → []。"""
    try:
        episodic = _active_rows(conn, campaign_id, "episodic")
        semantic = _active_rows(conn, campaign_id, "semantic")
        prompt = _build_prompt(campaign_id, episodic, semantic)
        text = llm(prompt)
        return _parse_ops(text)
    except Exception:  # noqa: BLE001 - LLM 抛异常 → 确定性降级
        return []


def _event_title(row: dict) -> str:
    """情景条目 → 事件标题：content 形如 '[Act·Scene] title：desc'。"""
    content = row.get("content") or ""
    if "]" in content:
        content = content.split("]", 1)[-1]
    return content.split("：", 1)[0].strip()


def _npc_name(row: dict) -> str:
    """NPC 语义条目 → 名字：content 形如 '老吴（富商）：…'。"""
    content = row.get("content") or ""
    if "（" in content:
        return content.split("（", 1)[0].strip()
    return content.split("：", 1)[0].strip()


def _deterministic_longterm(campaign_id: str, key: str, rows: Sequence[dict]) -> str:
    """确定性 longterm 内容（零 LLM 兜底）：从源条目拼一段 ≤200 字文本。"""
    if key == "synopsis":
        titles = [_event_title(r) for r in rows]
        uniq = list(dict.fromkeys(t for t in titles if t))
        head = "、".join(uniq)[:120]
        return _clip(f"剧情概要：整合了 {len(rows)} 条情景记忆，事件序列：{head or '（空）'}。")
    if key == "plotline":
        acts = sorted({str(r.get("source_episode") or "").split("/", 1)[0] for r in rows if r.get("source_episode")})
        return _clip(f"主线脉络：覆盖 {len(acts)} 个幕（{'、'.join(acts) or '—'}），共 {len(rows)} 条情景记忆。")
    if key == "npc_arcs":
        names = sorted({_npc_name(r) for r in rows if str(r.get("subject_key") or "").startswith("npc:")})
        return _clip(f"NPC 弧光：{'、'.join(names) or '（暂无可追踪 NPC）'}。")
    return _clip(f"{key}：已整合 {len(rows)} 条记忆。")


def _ensure_longterm_keys(
    conn: sqlite3.Connection, campaign_id: str, source_rows: Sequence[dict]
) -> list[str]:
    """LLM 路径兜底：保证 longterm 三键都有 active 条目，缺则确定性生成。

    返回本次补齐的键列表。
    """
    written: list[str] = []
    for key in _LLM_LONGTERM_KEYS:
        exists = conn.execute(
            "SELECT id FROM memory_entries WHERE campaign_id = ? AND memory_type = 'longterm' "
            "AND subject_key = ? AND status = 'active'",
            (campaign_id, key),
        ).fetchone()
        if exists is not None:
            continue
        content = _deterministic_longterm(campaign_id, key, source_rows)
        _write_longterm(conn, campaign_id, key, content)
        written.append(key)
    return written


def _consolidate_with_ops(
    conn: sqlite3.Connection, campaign_id: str, ops: list[dict[str, Any]], min_episodic: int
) -> dict:
    """LLM 两段式第二遍：执行操作 → 保证 longterm 三键 → episodic 上限置 consolidated。"""
    episodic_before = _active_rows(conn, campaign_id, "episodic")
    semantic_before = _active_rows(conn, campaign_id, "semantic")
    ops_applied, ops_longterm = _apply_ops(conn, campaign_id, ops)
    written = list(ops_longterm)
    for k in _ensure_longterm_keys(conn, campaign_id, episodic_before + semantic_before):
        if k not in written:
            written.append(k)
    consolidated_ids, _ = _mark_oldest_episodic_consolidated(conn, campaign_id, min_episodic)
    if consolidated_ids and "synopsis" not in written:
        rows = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM memory_entries WHERE id IN ({})".format(",".join("?" * len(consolidated_ids))),
                consolidated_ids,
            ).fetchall()
        ]
        content = _deterministic_longterm(campaign_id, "synopsis", rows)
        new_id, _ = _write_longterm(conn, campaign_id, "synopsis", content, ref_ids=consolidated_ids)
        _backfill_consolidated_into(conn, consolidated_ids, new_id)
        written.append("synopsis")
    elif consolidated_ids:
        # 已有 synopsis（LLM 或兜底写入）→ consolidated 指向它
        syn = conn.execute(
            "SELECT id FROM memory_entries WHERE campaign_id = ? AND memory_type = 'longterm' "
            "AND subject_key = 'synopsis' AND status = 'active' LIMIT 1",
            (campaign_id,),
        ).fetchone()
        _backfill_consolidated_into(conn, consolidated_ids, syn["id"] if syn else None)
    return {
        "campaign_id": campaign_id,
        "llm": True,
        "degraded": False,
        "ops_applied": ops_applied,
        "episodic_consolidated": len(consolidated_ids),
        "longterm_written": sorted(set(written)),
    }


def _consolidate_deterministic(
    conn: sqlite3.Connection, campaign_id: str, min_episodic: int, degraded: bool = False
) -> dict:
    """确定性路径：episodic 超限置 consolidated + 确定性拼 synopsis。幂等。"""
    consolidated_ids, consolidated_rows = _mark_oldest_episodic_consolidated(conn, campaign_id, min_episodic)
    written: list[str] = []
    if consolidated_ids:
        content = _deterministic_longterm(campaign_id, "synopsis", consolidated_rows)
        new_id, _ = _write_longterm(conn, campaign_id, "synopsis", content, ref_ids=consolidated_ids)
        _backfill_consolidated_into(conn, consolidated_ids, new_id)
        written = ["synopsis"]
    return {
        "campaign_id": campaign_id,
        "llm": False,
        "degraded": degraded,
        "ops_applied": 0,
        "episodic_consolidated": len(consolidated_ids),
        "longterm_written": written,
    }


def consolidate(
    campaign_id: str,
    db_path: Path | None = None,
    llm: Any | None = None,
    min_episodic: int = 20,
) -> dict:
    """记忆整合维护层（设计文档 §3.4 / P1 ticket 01）。

    - LLM 两段式（llm 为最小可调用 `llm(prompt: str) -> str`，返回 JSON 文本）：
      第一遍把 active episodic+semantic 摘要喂给 llm 提议 ADD/UPDATE/DELETE；
      第二遍执行，并保证 longterm 三键（synopsis/plotline/npc_arcs）就位；
      被整合的 episodic 置 consolidated（不物理删除，consolidated_into 连回 synopsis）。
    - 确定性降级（llm=None，或 LLM 返回结构非法/抛异常）：episodic 超 min_episodic
      时把最旧一批置 consolidated + 确定性拼 synopsis。幂等：同输入两次结果一致
      （content_hash / status 判重，二次运行无新增写入）。
    """
    conn = _connect(db_path)
    try:
        if llm is not None:
            ops = _safe_parse_ops(conn, campaign_id, llm)
            if ops:
                return _consolidate_with_ops(conn, campaign_id, ops, min_episodic)
            return _consolidate_deterministic(conn, campaign_id, min_episodic, degraded=True)
        return _consolidate_deterministic(conn, campaign_id, min_episodic)
    finally:
        conn.close()


# ---------------------------------------------------------------- 游玩会话（play_sessions）


def record_session(
    campaign_id: str,
    session_summary: str,
    db_path: Path | None = None,
    llm: Any | None = None,
    play_status: str | None = None,
    conflicts: Any | None = None,
) -> dict:
    """游玩会话记录（设计文档 §3.3 / P1 ticket 01）。

    KP 回叙 → 新增 play_sessions 行（session_index = 现有计数 + 1）→ 触发轻量
    consolidate。确定性路径零 LLM（传 llm 时走 LLM 两段式）。
    """
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT COALESCE(MAX(session_index), 0) AS n FROM play_sessions WHERE campaign_id = ?",
            (campaign_id,),
        ).fetchone()
        session_index = int(row["n"]) + 1
        session_id = f"sess:{campaign_id}:{session_index}"
        now = _now()
        conn.execute(
            "INSERT INTO play_sessions (id, campaign_id, session_index, summary, play_status, conflicts, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                session_id,
                campaign_id,
                session_index,
                session_summary,
                play_status,
                json.dumps(conflicts, ensure_ascii=False) if conflicts is not None else None,
                now,
            ),
        )
    finally:
        conn.close()
    result = consolidate(campaign_id, db_path, llm=llm)
    return {
        "session_id": session_id,
        "session_index": session_index,
        "play_status": play_status,
        "consolidate": result,
    }


def current_play_status(campaign_id: str, db_path: Path | None = None) -> str | None:
    """最近一次游玩会话的 play_status（无会话 → None）。零 LLM。"""
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT play_status FROM play_sessions WHERE campaign_id = ? "
            "ORDER BY session_index DESC, created_at DESC LIMIT 1",
            (campaign_id,),
        ).fetchone()
        return row["play_status"] if row is not None else None
    finally:
        conn.close()


def list_play_sessions(campaign_id: str, db_path: Path | None = None) -> list[dict]:
    """该 campaign 的全部游玩会话（按 session_index 升序）。零 LLM。"""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM play_sessions WHERE campaign_id = ? ORDER BY session_index ASC",
            (campaign_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def supersede_entries(
    campaign_id: str,
    db_path: Path | None = None,
    ids: Sequence[str] | None = None,
    subject_keys: Sequence[str] | None = None,
) -> int:
    """把匹配的 active 条目置 superseded（更新 updated_at；绝不物理删除）。

    - ids：精确匹配条目 id；
    - subject_keys：匹配该 campaign 下 semantic/longterm 的 subject_key（episodic 无
      subject_key，不参与）；
    - ids 与 subject_keys 至少给一个，否则 ValueError；
    - 只影响 active 条目；返回受影响条数。确定性、幂等、零 LLM。
    """
    if not ids and not subject_keys:
        raise ValueError("ids 与 subject_keys 至少给一个")
    conn = _connect(db_path)
    try:
        affected = 0
        if ids:
            placeholders = ",".join("?" * len(ids))
            cur = conn.execute(
                f"UPDATE memory_entries SET status = ?, updated_at = ? "
                f"WHERE campaign_id = ? AND id IN ({placeholders}) AND status = ?",
                [_SUPERSEDED, _now(), campaign_id, *ids, _ACTIVE],
            )
            affected += int(cur.rowcount or 0)
        if subject_keys:
            placeholders = ",".join("?" * len(subject_keys))
            cur = conn.execute(
                f"UPDATE memory_entries SET status = ?, updated_at = ? "
                f"WHERE campaign_id = ? AND memory_type IN ('semantic', 'longterm') "
                f"AND subject_key IN ({placeholders}) AND status = ?",
                [_SUPERSEDED, _now(), campaign_id, *subject_keys, _ACTIVE],
            )
            affected += int(cur.rowcount or 0)
        return affected
    finally:
        conn.close()


# ---------------------------------------------------------------- P2 起步：post-session briefing + 向量检索


def briefing(campaign_id: str, db_path: Path | None = None) -> str:
    """生成「上次停在哪」回叙文本（设计文档 §3.5 P2 / ticket 05）。

    组成：最近游玩会话摘要 + 当前 play_status + longterm synopsis/plotline 概要。
    无任何会话与长期记忆 → 中文占位文案。确定性、零 LLM。
    """
    sessions = list_play_sessions(campaign_id, db_path)
    longterm = list_entries(campaign_id, "longterm", db_path, status=_ACTIVE)
    by_key = {e["subject_key"]: e["content"] for e in longterm}
    synopsis = by_key.get("synopsis")
    plotline = by_key.get("plotline")
    if not sessions and not longterm:
        return "该战役暂无游玩记录与长期记忆——还没有「上次停在哪」可回叙。"
    lines = ["【上次停在哪】", ""]
    if sessions:
        last = sessions[-1]
        lines.append(f"最近游玩（第 {last['session_index']} 场）：{last['summary']}")
        status = last.get("play_status") or current_play_status(campaign_id, db_path)
        if status:
            lines.append(f"当前状态：{status}")
    else:
        lines.append("（尚无已记录的游玩会话）")
    lines.append("")
    if synopsis:
        lines.append(f"剧情概要：{synopsis}")
    if plotline:
        lines.append(f"主线脉络：{plotline}")
    return "\n".join(lines).rstrip()


def _pack_vector(vec: Any) -> bytes:
    """向量 → BLOB（小端 float32 数组）。支持 list/tuple/np.ndarray。"""
    import struct

    vals = [float(v) for v in vec]
    return struct.pack(f"<{len(vals)}f", *vals)


def _unpack_vector(blob: bytes) -> list[float]:
    """BLOB → list[float]（小端 float32 数组，宽按字节数推断）。"""
    import struct

    return list(struct.unpack(f"<{len(blob) // 4}f", blob))


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """余弦相似度（纯 Python 嵌套列表，零新依赖）。零向量 → 0.0。"""
    import math

    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _query_vector(query: str, embedder: Any | None) -> list[float]:
    """query → list[float]。embedder 按 (text: str) -> list[float] 契约；
    embedder=None 复用 rag.get_embedder（批契约取第一行）。不可用 → []（走 BM25 降级）。"""
    if embedder is None:
        try:
            from tindalos import rag

            arr = rag.get_embedder()([query])
            if arr is not None and len(arr) > 0:
                row = arr[0]
                if hasattr(row, "tolist"):
                    row = row.tolist()
                if isinstance(row, (list, tuple)):
                    return [float(v) for v in row]
        except Exception:  # noqa: BLE001 - 查询向量不可用 → 降级 BM25
            return []
        return []
    out = embedder(query)
    if out is None:
        return []
    if hasattr(out, "tolist"):
        out = out.tolist()
    if isinstance(out, (list, tuple)) and out and (
        isinstance(out[0], (list, tuple)) or hasattr(out[0], "tolist")
    ):
        first = out[0]
        if hasattr(first, "tolist"):
            first = first.tolist()
        out = first
    if not isinstance(out, (list, tuple)):
        return []
    try:
        return [float(v) for v in out]
    except (TypeError, ValueError):
        return []


def _result_dict(row: dict, score: float) -> dict:
    """检索结果条目字典：id/memory_type/content/score + 溯源字段。"""
    return {
        "id": row["id"],
        "memory_type": row["memory_type"],
        "content": row["content"],
        "score": round(float(score), 6),
        "importance": row.get("importance"),
        "subject_key": row.get("subject_key"),
        "source_episode": row.get("source_episode"),
    }


def _bm25_retrieve(rows: Sequence[dict], query: str, k: int) -> list[dict]:
    """BM25 确定性降级：对该 campaign 的 active 条目做经典 BM25 评分取 top-k。"""
    idx = BM25Index().fit(
        [r["content"] for r in rows],
        doc_ids=[r["id"] for r in rows],
    )
    hits = idx.search(query, k)
    by_id = {r["id"]: r for r in rows}
    out: list[dict] = []
    for h in hits:
        row = by_id.get(h["doc_id"])
        if row is None or not h.get("score") or h["score"] <= 0:
            continue
        out.append(_result_dict(row, float(h["score"])))
    return out


def embed_entries(
    campaign_id: str,
    db_path: Path | None = None,
    embedder: Any | None = None,
) -> int:
    """给 memory_entries.embedding 列填充向量 BLOB（设计文档 §3.2 P2 / ticket 05）。

    - embedder 为 callable `(text: str) -> list[float]`；无 embedder → 不写并返回 0；
    - 某条调用抛错 → 停止并返回已处理数（诚实降级，不崩）；
    - 幂等：已 embedding（embedding IS NOT NULL）的条目跳过。
    返回本次实际写入 embedding 的条数。零 LLM。
    """
    if embedder is None:
        return 0
    conn = _connect(db_path)
    processed = 0
    try:
        rows = conn.execute(
            "SELECT id, content FROM memory_entries "
            "WHERE campaign_id = ? AND embedding IS NULL",
            (campaign_id,),
        ).fetchall()
        for row in rows:
            try:
                blob = _pack_vector(embedder(row["content"]))
            except Exception:  # noqa: BLE001 - embedder 抛错 → 诚实降级
                break
            if not blob:
                continue
            conn.execute(
                "UPDATE memory_entries SET embedding = ?, updated_at = ? WHERE id = ?",
                (blob, _now(), row["id"]),
            )
            processed += 1
    finally:
        conn.close()
    return processed


def retrieve_memory(
    campaign_id: str,
    query: str,
    db_path: Path | None = None,
    k: int = 5,
    embedder: Any | None = None,
) -> list[dict]:
    """记忆向量检索（设计文档 §3.5 P2 / ticket 05）。

    - 有 embedding 的条目 → 余弦（纯 Python 嵌套列表，零新依赖）取 top-k；
      查询向量由 embedder 计算（None 时复用 rag.get_embedder 批契约）。
    - 无任何 embedding 条目 / 查询向量不可用 → 确定性降级：对该 campaign 的
      active 条目做 BM25 评分取 top-k。
    返回条目字典：{id, memory_type, content, score, importance, subject_key, source_episode}。
    零 LLM。
    """
    if not query:
        return []
    rows = list_entries(campaign_id, db_path=db_path, status=_ACTIVE)
    if not rows:
        return []
    embedded = [r for r in rows if r.get("embedding")]
    if embedded:
        qvec = _query_vector(query, embedder)
        if qvec:
            scored: list[tuple[float, dict]] = []
            for r in embedded:
                try:
                    vec = _unpack_vector(r["embedding"])
                except Exception:  # noqa: BLE001 - 损坏 BLOB 跳过该条
                    continue
                scored.append((_cosine(qvec, vec), r))
            scored.sort(key=lambda x: x[0], reverse=True)
            return [_result_dict(r, s) for s, r in scored[:k]]
    return _bm25_retrieve(rows, query, k)


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
    "consolidate",
    "record_session",
    "current_play_status",
    "list_play_sessions",
    "supersede_entries",
    "briefing",
    "embed_entries",
    "retrieve_memory",
]
