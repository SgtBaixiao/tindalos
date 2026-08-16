"""sleep-time 离线整合（P3-1）。

设计文档 §3.4 / §5 P3：离线把超限 episodic 整合进 longterm。零第三方依赖，
import 即测（不触网、不触 LLM，consolidate 走确定性路径）。

- list_campaign_ids：枚举 memory_entries 表 DISTINCT campaign_id（无表/无行 → []）；
- run_consolidation：跑一轮离线整合（单次模式 / 循环单轮），逐 campaign 独立容错，
  出错记录进 errors 不整体失败；幂等（consolidate 靠 content_hash/status 判重）；
- ConsolidationLoop：循环整合（serve 后台线程用）：interval_seconds 轮询 +
  线程安全停止事件；stop() 即 serve 的停止钩子。
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any, Sequence

from tindalos.memory_entries import consolidate, entries_db_path


def list_campaign_ids(db_path: Path | None = None) -> list[str]:
    """枚举 memory_entries 表 DISTINCT campaign_id（无该表/无行 → []）。

    文件不存在、表缺失、SQLite 损坏 → 一律返回空（离线检查不建文件、不抛异常）。
    """
    path = Path(db_path) if db_path else entries_db_path()
    if not path.exists():
        return []
    try:
        conn = sqlite3.connect(str(path))
        try:
            has_table = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'memory_entries'"
            ).fetchone()
            if has_table is None:
                return []
            rows = conn.execute(
                "SELECT DISTINCT campaign_id FROM memory_entries ORDER BY campaign_id"
            ).fetchall()
            return [row[0] for row in rows]
        finally:
            conn.close()
    except sqlite3.Error:
        return []


def consolidate_campaign(
    campaign_id: str,
    db_path: Path | None = None,
    llm: Any | None = None,
    min_episodic: int = 20,
) -> dict:
    """单 campaign 一轮整合（异常兜底）：成功/失败都返回统计 dict。

    成功：consolidate 结果 + {"ok": True, "error": None}；
    失败：{"ok": False, "error": str(exc)} + 空统计（不向上抛，调用方逐条收集）。
    """
    try:
        res = consolidate(campaign_id, db_path, llm=llm, min_episodic=min_episodic)
        res["ok"] = True
        res["error"] = None
        return res
    except Exception as exc:  # noqa: BLE001 - 单 campaign 失败不拖垮整轮
        return {
            "campaign_id": campaign_id,
            "ok": False,
            "error": str(exc),
            "llm": False,
            "degraded": True,
            "ops_applied": 0,
            "episodic_consolidated": 0,
            "longterm_written": [],
        }


def run_consolidation(
    db_path: Path | None = None,
    campaign_ids: Sequence[str] | None = None,
    llm: Any | None = None,
    min_episodic: int = 20,
) -> dict:
    """跑一轮离线整合（单次模式 / 循环的单轮）。

    - campaign_ids=None → 枚举全部 campaign；给定 → 只整合指定 campaign；
    - 每个 campaign 独立 try/except，出错记录进 errors，不整体失败；
    - 幂等：consolidate 靠 content_hash/status 判重，重复跑结果一致。

    返回 {"campaigns": [每 campaign 统计], "total_consolidated": n, "errors": [...]}。
    """
    targets = list(campaign_ids) if campaign_ids else list_campaign_ids(db_path)
    results: list[dict] = []
    errors: list[dict] = []
    total = 0
    for cid in targets:
        res = consolidate_campaign(cid, db_path, llm=llm, min_episodic=min_episodic)
        results.append(res)
        if res["ok"]:
            total += int(res.get("episodic_consolidated") or 0)
        else:
            errors.append({"campaign_id": cid, "error": res["error"]})
    return {"campaigns": results, "total_consolidated": total, "errors": errors}


class ConsolidationLoop:
    """循环离线整合（serve 后台线程用）：interval_seconds 轮询 + 线程安全停止事件。

    - start()：启动 daemon 线程（已在跑则 no-op）；stop()：置停止事件并 join；
    - run_once()：手动跑一轮（单次模式 / 测试驱动），结果存 last_result；
    - 单轮异常不 kill 循环（last_result=None 后继续轮询）。
    """

    def __init__(
        self,
        interval_seconds: float,
        db_path: Path | None = None,
        campaign_ids: Sequence[str] | None = None,
        llm: Any | None = None,
        min_episodic: int = 20,
    ) -> None:
        self.interval_seconds = float(interval_seconds)
        self.db_path = db_path
        self.campaign_ids = list(campaign_ids) if campaign_ids else None
        self.llm = llm
        self.min_episodic = min_episodic
        self.last_result: dict | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def stopped(self) -> bool:
        """停止事件是否已置位（线程安全只读）。"""
        return self._stop.is_set()

    # -- 单轮 ------------------------------------------------

    def run_once(self) -> dict:
        """跑一轮离线整合（手动单次 / 循环单轮共用），结果存 last_result。"""
        self.last_result = run_consolidation(
            self.db_path, self.campaign_ids, self.llm, self.min_episodic
        )
        return self.last_result

    # -- 循环线程 ----------------------------------------------

    def start(self) -> None:
        """启动 daemon 后台线程；已在跑则 no-op。"""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        thread = threading.Thread(
            target=self._loop, name="tindalos-consolidate", daemon=True
        )
        self._thread = thread
        thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """置停止事件并 join 等待线程退出（serve 停止钩子；未启动时 no-op）。"""
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:  # noqa: BLE001 - 单轮异常不 kill 循环
                self.last_result = None
            self._stop.wait(self.interval_seconds)


__all__ = [
    "list_campaign_ids",
    "consolidate_campaign",
    "run_consolidation",
    "ConsolidationLoop",
]
