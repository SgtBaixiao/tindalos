"""Eval trace 存储层（P0-b，零 LLM）。

设计文档《云数据库 + 记忆系统 + Eval 系统统一落地路线》§4.1：
- eval_runs：一次评测运行的不可变 trace（被测对象、生成参数、各层结果 JSON、
  总 verdict、预算花费），append-only，可回放、可对比、可归因。
- eval_annotations：逐条人类可复核的标注（score / explanation / evidence_refs）。
- 不可变契约：零 DELETE；annotations 写入后不再修改；finalize_run 是唯一
  允许的 UPDATE（运行生命周期 running → completed / short_circuited / error）。

落盘：独立 SQLite（settings.store_dir / "eval.sqlite"），沿用
TINDALOS_DATA_DIR / store_dir 语义，与 memory.sqlite / memory_entries.sqlite
分开，互不干扰。代码风格与 memory_entries.py 保持一致。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tindalos.config import Settings, get_settings

# 运行生命周期：append-only 契约中 finalize_run 允许的目标态
VALID_STATUSES = frozenset({"running", "completed", "short_circuited", "error"})
_TERMINAL_STATUSES = frozenset({"completed", "short_circuited", "error"})

_SCHEMA = """
CREATE TABLE IF NOT EXISTS eval_runs (
  run_id           TEXT PRIMARY KEY,        -- uuid（可回放键）
  campaign_id      TEXT,                    -- 被测 campaign id
  campaign_title   TEXT,                    -- 展示用标题
  subject_type     TEXT NOT NULL DEFAULT 'campaign',  -- 被测对象类型（P1: module/clip）
  subject_ref      TEXT,                    -- 被测对象引用（campaign id / 资源路径）
  params           TEXT NOT NULL DEFAULT '{}',   -- JSON：生成参数（被测配置）
  layers           TEXT NOT NULL DEFAULT '{}',   -- JSON：L1..L6 各层结果
  verdict          TEXT,                    -- pass | warning | fail
  status           TEXT NOT NULL DEFAULT 'running',
  budget_spent_usd REAL NOT NULL DEFAULT 0,
  duration_ms      INTEGER NOT NULL DEFAULT 0,
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_eval_runs_campaign ON eval_runs(campaign_id);
CREATE INDEX IF NOT EXISTS idx_eval_runs_created ON eval_runs(created_at DESC);

CREATE TABLE IF NOT EXISTS eval_annotations (
  annotation_id  TEXT PRIMARY KEY,          -- uuid
  run_id         TEXT NOT NULL,             -- 归属运行
  layer          TEXT NOT NULL,             -- 'L4' 等（哪个层产生）
  subject_ref    TEXT NOT NULL,             -- 被标注对象引用（claim / clue 等）
  score          REAL NOT NULL,             -- 0~1（支持度等）
  explanation    TEXT,                      -- 人工复核上下文
  evidence_refs  TEXT,                      -- JSON：证据引用 [{module_id, chunk_index, score}]
  created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_eval_ann_run ON eval_annotations(run_id);
"""


# ---------------------------------------------------------------- 路径与连接


def eval_db_path(settings: Settings | None = None) -> Path:
    """eval 独立 SQLite 路径：<store_dir>/eval.sqlite。"""
    settings = settings or get_settings()
    return settings.store_dir / "eval.sqlite"


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    """打开连接并建表（幂等 DDL）。autocommit 模式，无事务嵌套问题。"""
    path = Path(db_path or eval_db_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id() -> str:
    return uuid.uuid4().hex


# ---------------------------------------------------------------- 写入（append-only）


def create_run(
    *,
    campaign_id: str,
    campaign_title: str,
    subject_type: str = "campaign",
    subject_ref: str | None = None,
    params: dict[str, Any] | None = None,
    run_id: str | None = None,
    db_path: Path | None = None,
) -> str:
    """创建一次评测运行（INSERT）。返回 run_id。"""
    run_id = run_id or _new_id()
    now = _now()
    conn = _connect(db_path)
    conn.execute(
        """
        INSERT INTO eval_runs (
          run_id, campaign_id, campaign_title, subject_type, subject_ref,
          params, layers, verdict, status, budget_spent_usd, duration_ms,
          created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            campaign_id,
            campaign_title,
            subject_type,
            subject_ref or campaign_id,
            json.dumps(params or {}, ensure_ascii=False),
            "{}",
            None,
            "running",
            0.0,
            0,
            now,
            now,
        ),
    )
    conn.close()
    return run_id


def append_annotations(
    run_id: str,
    annotations: list[dict[str, Any]],
    db_path: Path | None = None,
) -> int:
    """追加标注（INSERT，返回写入条数）。annotations 写入后不可变。"""
    if not annotations:
        return 0
    now = _now()
    conn = _connect(db_path)
    for ann in annotations:
        conn.execute(
            """
            INSERT INTO eval_annotations (
              annotation_id, run_id, layer, subject_ref, score,
              explanation, evidence_refs, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ann.get("annotation_id") or _new_id(),
                run_id,
                ann.get("layer", ""),
                ann.get("subject_ref", ""),
                float(ann.get("score", 0.0)),
                ann.get("explanation"),
                json.dumps(ann.get("evidence_refs") or [], ensure_ascii=False),
                now,
            ),
        )
    conn.close()
    return len(annotations)


def finalize_run(
    run_id: str,
    *,
    status: str,
    verdict: str | None = None,
    layers: dict[str, Any] | None = None,
    budget_spent_usd: float | None = None,
    duration_ms: int | None = None,
    db_path: Path | None = None,
) -> bool:
    """完成一次运行（唯一允许的 UPDATE：running → 终态）。

    终态已设置时再次调用是幂等 no-op（返回 False）；目标态非法返回 False。
    这是 append-only 契约的唯一写口，保持 trace 可回放、不被事后篡改。
    """
    if status not in _TERMINAL_STATUSES:
        return False
    conn = _connect(db_path)
    row = conn.execute(
        "SELECT status FROM eval_runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    if row is None or row["status"] in _TERMINAL_STATUSES:
        conn.close()
        return False
    fields = ["status = ?", "updated_at = ?"]
    params: list[Any] = [status, _now()]
    if verdict is not None:
        fields.append("verdict = ?")
        params.append(verdict)
    if layers is not None:
        fields.append("layers = ?")
        params.append(json.dumps(layers, ensure_ascii=False))
    if budget_spent_usd is not None:
        fields.append("budget_spent_usd = ?")
        params.append(float(budget_spent_usd))
    if duration_ms is not None:
        fields.append("duration_ms = ?")
        params.append(int(duration_ms))
    params.append(run_id)
    conn.execute(
        f"UPDATE eval_runs SET {', '.join(fields)} WHERE run_id = ?", params
    )
    conn.close()
    return True


# ---------------------------------------------------------------- 读取


def _row_to_run(row: sqlite3.Row) -> dict:
    run = dict(row)
    for key in ("params", "layers"):
        try:
            run[key] = json.loads(run[key] or "{}")
        except (ValueError, TypeError):
            run[key] = {}
    return run


def get_run(run_id: str, db_path: Path | None = None) -> dict | None:
    """按 run_id 取完整 trace（含 layers 解析为 dict）。"""
    conn = _connect(db_path)
    row = conn.execute(
        "SELECT * FROM eval_runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    conn.close()
    return _row_to_run(row) if row else None


def list_runs(
    limit: int = 20,
    campaign_id: str | None = None,
    db_path: Path | None = None,
) -> list[dict]:
    """列出运行（新→旧），可选按 campaign 过滤。"""
    conn = _connect(db_path)
    sql = "SELECT * FROM eval_runs"
    params: list[Any] = []
    if campaign_id:
        sql += " WHERE campaign_id = ?"
        params.append(campaign_id)
    sql += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
    params.append(int(limit))
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [_row_to_run(r) for r in rows]


def list_annotations(run_id: str, db_path: Path | None = None) -> list[dict]:
    """某运行的全部标注（按 created_at 升序，保持追加顺序）。"""
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT * FROM eval_annotations WHERE run_id = ? ORDER BY created_at ASC, rowid ASC",
        (run_id,),
    ).fetchall()
    conn.close()
    result = []
    for r in rows:
        row = dict(r)
        try:
            row["evidence_refs"] = json.loads(row.get("evidence_refs") or "[]")
        except (ValueError, TypeError):
            row["evidence_refs"] = []
        result.append(row)
    return result


__all__ = [
    "VALID_STATUSES",
    "eval_db_path",
    "create_run",
    "append_annotations",
    "finalize_run",
    "get_run",
    "list_runs",
    "list_annotations",
]
