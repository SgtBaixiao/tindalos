"""历史记录模块：SQLite 持久化上传模组与生成剧本（可重放）。

wayfinder ticket 07（历史记录与可重放）。web.py 启动时调用 ``init_db()`` 建库，
各函数自开自关连接（每调用一个 sqlite3 连接，``with closing(_connect())`` 打开即关），
仅依赖标准库（sqlite3 / json / datetime / pathlib / os），零新增依赖。

存储位置：``data/site.db``（可用环境变量 ``TINDALOS_SITE_DB`` 覆盖，可绝对可相对）。

行 → dict 约定：返回 dict 的键为列名；``meta_json`` / ``snapshot_json`` 用
``json.dumps(ensure_ascii=False)`` 落库、读取时 ``json.loads`` 回对象。
``created_at`` 用 ``datetime.now().isoformat(timespec="seconds")``。
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

# update_module 可更新的白名单字段（其余入参一律忽略）
_MODULE_UPDATE_FIELDS = ("status", "rules", "meta_json")

# JSON 列：读取时自动 json.loads 回对象
_JSON_COLUMNS = ("meta_json", "snapshot_json")


def db_path() -> Path:
    """返回 SQLite 库路径。

    优先 ``TINDALOS_SITE_DB`` 环境变量（可绝对可相对）；缺省 ``data/site.db``
    （相对当前工作目录，与 web/rag/store 共用 ``TINDALOS_DATA_DIR`` 语义——
    本地编辑安装 CWD=仓库根、容器内 CWD=/app 时都落在统一数据目录，卷挂载即持久化）。
    每次调用现读环境，便于测试 monkeypatch 覆盖。
    """
    env = os.environ.get("TINDALOS_SITE_DB")
    if env:
        return Path(env)
    return Path(os.environ.get("TINDALOS_DATA_DIR", "data")) / "site.db"


def _connect() -> sqlite3.Connection:
    """新建一条到 site.db 的连接（row_factory=Row）；调用方负责 with/closing 关闭。"""
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    return conn


def _row_to_dict(row: sqlite3.Row) -> dict:
    """把 sqlite3.Row 转为 dict；JSON 列（meta_json/snapshot_json）解析回对象。"""
    d = dict(row)
    for key in _JSON_COLUMNS:
        val = d.get(key)
        if isinstance(val, str):
            d[key] = json.loads(val)
    return d


def _campaign_row_to_dict(row: sqlite3.Row) -> dict:
    """campaigns 行 → {id,title,rules,created_at,event_count,size,snapshot}。

    snapshot 为解析后的 dict（由 snapshot_json json.loads 回来）。
    """
    snapshot = row["snapshot_json"]
    return {
        "id": row["id"],
        "title": row["title"],
        "rules": row["rules"],
        "created_at": row["created_at"],
        "event_count": row["event_count"],
        "size": row["size"],
        "snapshot": json.loads(snapshot) if snapshot else None,
    }


def init_db() -> None:
    """幂等建表：modules + campaigns（模块 import 时不自动调，由 web 启动调用）。"""
    db = db_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS modules (
                id TEXT PRIMARY KEY,
                filename TEXT,
                sha256 TEXT UNIQUE,
                pages INTEGER,
                chars INTEGER,
                rules TEXT DEFAULT 'COC7',
                status TEXT DEFAULT 'uploaded',
                created_at TEXT,
                meta_json TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS campaigns (
                id TEXT PRIMARY KEY,
                title TEXT,
                rules TEXT DEFAULT 'COC7',
                created_at TEXT,
                event_count INTEGER DEFAULT 0,
                size INTEGER DEFAULT 0,
                snapshot_json TEXT
            )
            """
        )
        conn.commit()


def register_module(
    module_id,
    filename,
    sha256,
    pages,
    chars,
    *,
    rules="COC7",
    status="uploaded",
    meta=None,
) -> dict:
    """注册一个上传模组，返回该行 dict。

    sha256 冲突（同一文件重复上传）时：更新已有行（保持原 id / created_at，
    刷新 filename/pages/chars/rules/status/meta_json）并返回已有行。meta 为可
    JSON 序列化对象，以 ensure_ascii=False 存入 meta_json。
    """
    meta_json = json.dumps(meta, ensure_ascii=False) if meta is not None else None
    now = datetime.now().isoformat(timespec="seconds")
    with closing(_connect()) as conn:
        existing = conn.execute(
            "SELECT * FROM modules WHERE sha256 = ?", (sha256,)
        ).fetchone()
        if existing is not None:
            conn.execute(
                """
                UPDATE modules
                SET filename = ?, pages = ?, chars = ?, rules = ?, status = ?, meta_json = ?
                WHERE id = ?
                """,
                (filename, pages, chars, rules, status, meta_json, existing["id"]),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM modules WHERE id = ?", (existing["id"],)
            ).fetchone()
            return _row_to_dict(row)
        conn.execute(
            """
            INSERT INTO modules
                (id, filename, sha256, pages, chars, rules, status, created_at, meta_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (module_id, filename, sha256, pages, chars, rules, status, now, meta_json),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM modules WHERE id = ?", (module_id,)
        ).fetchone()
        return _row_to_dict(row)


def get_module(module_id) -> dict | None:
    """按 id 查询模组；不存在返回 None（meta_json 已解析回对象）。"""
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT * FROM modules WHERE id = ?", (module_id,)
        ).fetchone()
    return _row_to_dict(row) if row is not None else None


def list_modules() -> list[dict]:
    """按 created_at 倒序列出全部模组（含解析后的 meta_json）。"""
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT * FROM modules ORDER BY created_at DESC"
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def update_module(module_id, **fields) -> dict | None:
    """更新模组白名单字段（status/rules/meta_json），返回更新后的行 dict；不存在返回 None。

    meta_json 入参为可 JSON 序列化对象（与 register_module 的 meta 一致，内部 dumps 落库）；
    其它入参（filename/pages 等）不在白名单，一律忽略。
    """
    allowed = {k: v for k, v in fields.items() if k in _MODULE_UPDATE_FIELDS}
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT * FROM modules WHERE id = ?", (module_id,)
        ).fetchone()
        if row is None:
            return None
        if "meta_json" in allowed and not isinstance(allowed["meta_json"], str):
            allowed["meta_json"] = json.dumps(allowed["meta_json"], ensure_ascii=False)
        if allowed:
            sets = ", ".join(f"{k} = ?" for k in allowed)
            conn.execute(
                f"UPDATE modules SET {sets} WHERE id = ?",
                (*allowed.values(), module_id),
            )
            conn.commit()
        row = conn.execute(
            "SELECT * FROM modules WHERE id = ?", (module_id,)
        ).fetchone()
        return _row_to_dict(row)


def register_campaign(campaign_id, title, rules, campaign_dict) -> dict:
    """持久化一份生成剧本；返回 {id,title,rules,created_at,event_count,size,snapshot}。

    event_count = 全部场景事件总数（跨所有 acts/scenes 的 events 长度和）；
    size = snapshot JSON 的 UTF-8 字节数；snapshot_json 存整份 campaign dict
    （ensure_ascii=False）。campaign_dict 可为 dict 或 pydantic Campaign
    （自动 model_dump）。同一 id 再次注册为覆盖式更新（INSERT OR REPLACE）。
    """
    if hasattr(campaign_dict, "model_dump"):
        campaign_dict = campaign_dict.model_dump()
    event_count = sum(
        len(scene.get("events") or [])
        for act in campaign_dict.get("acts", []) or []
        for scene in act.get("scenes", []) or []
    )
    snapshot = json.dumps(campaign_dict, ensure_ascii=False)
    size = len(snapshot.encode("utf-8"))
    now = datetime.now().isoformat(timespec="seconds")
    with closing(_connect()) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO campaigns
                (id, title, rules, created_at, event_count, size, snapshot_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (campaign_id, title, rules, now, event_count, size, snapshot),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM campaigns WHERE id = ?", (campaign_id,)
        ).fetchone()
    return _campaign_row_to_dict(row)


def get_campaign(campaign_id) -> dict | None:
    """按 id 查询剧本；返回 {id,title,rules,created_at,event_count,size,snapshot}，不存在返回 None。

    snapshot 为解析后的 dict（可重放的完整剧本快照）。
    """
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT * FROM campaigns WHERE id = ?", (campaign_id,)
        ).fetchone()
    return _campaign_row_to_dict(row) if row is not None else None


def list_campaigns() -> list[dict]:
    """按 created_at 倒序列出剧本（不含 snapshot，仅元数据）。

    每项 {id,title,rules,created_at,event_count,size}——列表页不需要整份快照。
    """
    with closing(_connect()) as conn:
        rows = conn.execute(
            """
            SELECT id, title, rules, created_at, event_count, size
            FROM campaigns ORDER BY created_at DESC
            """
        ).fetchall()
    return [dict(r) for r in rows]


def delete_campaign(campaign_id) -> bool:
    """删除剧本；删除成功返回 True，不存在返回 False。"""
    with closing(_connect()) as conn:
        cur = conn.execute("DELETE FROM campaigns WHERE id = ?", (campaign_id,))
        conn.commit()
        return cur.rowcount > 0


__all__ = [
    "db_path",
    "init_db",
    "register_module",
    "get_module",
    "list_modules",
    "update_module",
    "register_campaign",
    "get_campaign",
    "list_campaigns",
    "delete_campaign",
]
