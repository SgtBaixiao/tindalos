"""P3-1 sleep-time 离线整合 + CLI 回叙（ticket 02）测试。

覆盖（对齐验收）：
1. list_campaign_ids：无文件/无表 → 空；有数据 → DISTINCT campaign_id；
2. run_consolidation 单轮：25 条 episodic → 最旧 5 条 consolidated + synopsis 写入；
3. 幂等：二跑整表快照一致（content_hash/status 判重）；
4. campaign 过滤：只整合指定 campaign，其余不受影响；
5. 单 campaign 出错 → 记录进 errors 不整体失败；
6. ConsolidationLoop：构造不启线程；start/stop 生命周期（serve 停止钩子）；
7. serve 无 --consolidate-interval 不新增后台线程；有 flag 时启动且停止钩子 join；
8. CLI consolidate / session 命令退出码与输出正常。

全程零网络零 LLM（consolidate 确定性路径，临时 SQLite）。
"""
from __future__ import annotations

import sqlite3
import sys
import threading
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import pytest
from typer.testing import CliRunner

from tindalos.cli import app
from tindalos.config import Settings
from tindalos.memory_entries import (
    capture_episodic,
    entries_db_path,
    list_entries,
    list_play_sessions,
)
from tindalos.models import construct_loose_campaign
from tindalos.sleep import ConsolidationLoop, list_campaign_ids, run_consolidation

runner = CliRunner()


def make_settings(tmp_path) -> Settings:
    return Settings(
        llm_enabled=False,
        checkpoint_dir=tmp_path / "checkpoints",
        store_dir=tmp_path / "store",
    )


def campaign_with_events(cid: str, n_events: int = 25):
    """构造一个战役：1 幕 · 1 场景 · n_events 个事件（纯 episodic，无 NPC/语义）。"""
    scenes = [
        {
            "id": f"scene-{i}",
            "title": f"场景{i}",
            "setting": {"time": "夜", "place": "雾镇"},
            "events": [
                {"id": f"ev-{i}", "title": f"事件{i}", "kind": "outcome", "description": f"描述{i}"}
            ],
            "npc_ids": [],
        }
        for i in range(n_events)
    ]
    acts = [{"id": "act-1", "title": "第一幕", "roman": "I", "scenes": scenes, "npc_ids": []}]
    return construct_loose_campaign(
        {"id": cid, "title": "雾镇疑云", "premise": "测试模组", "acts": acts, "npcs": {}}
    )


def _all_memory_rows(db) -> list[dict]:
    """整表快照（含非 active）——幂等断言用：两次运行前后行级完全一致。"""
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT * FROM memory_entries ORDER BY id").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------- list_campaign_ids


def test_list_campaign_ids_empty_and_populated(tmp_path):
    db = entries_db_path(make_settings(tmp_path))
    assert list_campaign_ids(db) == []  # 文件不存在 → 空
    assert run_consolidation(db) == {"campaigns": [], "total_consolidated": 0, "errors": []}

    # 文件存在但无 memory_entries 表 → 空
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db))
    conn.close()
    assert list_campaign_ids(db) == []

    capture_episodic(campaign_with_events("c-a", 3), db)
    capture_episodic(campaign_with_events("c-b", 3), db)
    assert sorted(list_campaign_ids(db)) == ["c-a", "c-b"]


# ---------------------------------------------------------------- run_consolidation 单轮


def test_run_consolidation_consolidates_oldest_and_writes_synopsis(tmp_path):
    db = entries_db_path(make_settings(tmp_path))
    capture_episodic(campaign_with_events("c-sleep", 25), db)

    res = run_consolidation(db)
    assert res["total_consolidated"] == 5  # 25 - min_episodic(20) = 5 最旧置 consolidated
    assert res["errors"] == []
    assert len(res["campaigns"]) == 1
    r = res["campaigns"][0]
    assert r["ok"] is True and r["campaign_id"] == "c-sleep"
    assert r["llm"] is False and r["degraded"] is False
    assert r["episodic_consolidated"] == 5
    assert r["longterm_written"] == ["synopsis"]

    active = list_entries("c-sleep", "episodic", db, status="active")
    consolidated = list_entries("c-sleep", "episodic", db, status="consolidated")
    assert len(active) == 20 and len(consolidated) == 5
    syn = list_entries("c-sleep", "longterm", db, status="active")
    assert len(syn) == 1 and syn[0]["subject_key"] == "synopsis"
    # consolidated 条目指向 synopsis（整合链）
    assert all(e["consolidated_into"] == syn[0]["id"] for e in consolidated)
    assert syn[0]["ref_ids"] and "事件" in syn[0]["content"]


def test_run_consolidation_idempotent(tmp_path):
    db = entries_db_path(make_settings(tmp_path))
    capture_episodic(campaign_with_events("c-idem", 25), db)

    run_consolidation(db)
    before = _all_memory_rows(db)
    res2 = run_consolidation(db)
    assert res2["total_consolidated"] == 0
    assert res2["campaigns"][0]["episodic_consolidated"] == 0
    assert res2["campaigns"][0]["longterm_written"] == []
    assert _all_memory_rows(db) == before, "二跑零写入（content_hash/status 幂等）"


# ---------------------------------------------------------------- campaign 过滤 / 出错容错


def test_run_consolidation_campaign_filter(tmp_path):
    db = entries_db_path(make_settings(tmp_path))
    capture_episodic(campaign_with_events("c-keep", 25), db)
    capture_episodic(campaign_with_events("c-skip", 25), db)

    res = run_consolidation(db, campaign_ids=["c-keep"])
    assert res["total_consolidated"] == 5
    assert [c["campaign_id"] for c in res["campaigns"]] == ["c-keep"]
    # c-skip 未受影响：全部仍 active；c-keep 已回落 min_episodic
    assert len(list_entries("c-skip", "episodic", db, status="active")) == 25
    assert len(list_entries("c-keep", "episodic", db, status="active")) == 20


def test_run_consolidation_campaign_error_recorded_not_abort(tmp_path):
    """目录当 db_path → sqlite 打不开 → consolidate 抛异常 → 记录 errors，不整体失败。"""
    bad_db = tmp_path / "store"
    bad_db.mkdir()
    res = run_consolidation(bad_db, campaign_ids=["c-x", "c-y"])
    assert len(res["errors"]) == 2
    assert {e["campaign_id"] for e in res["errors"]} == {"c-x", "c-y"}
    assert all(c["ok"] is False and c["error"] for c in res["campaigns"])
    assert res["total_consolidated"] == 0


# ---------------------------------------------------------------- ConsolidationLoop（serve 线程开关）


def test_loop_does_not_start_thread_until_start(tmp_path):
    db = entries_db_path(make_settings(tmp_path))
    loop = ConsolidationLoop(interval_seconds=0.05, db_path=db)
    assert loop._thread is None  # 构造时不启动线程（serve 无 flag 行为不变）
    assert not loop.stopped
    loop.stop()  # 未启动时 stop 只置停止事件，不创建线程、不崩
    assert loop.stopped
    assert loop._thread is None


def test_loop_run_once_manually(tmp_path):
    db = entries_db_path(make_settings(tmp_path))
    capture_episodic(campaign_with_events("c-loop", 25), db)
    loop = ConsolidationLoop(interval_seconds=0.05, db_path=db)
    res = loop.run_once()  # 单次模式：跑一轮即返回
    assert res["total_consolidated"] == 5
    assert loop.last_result is res


def test_loop_start_stop_lifecycle(tmp_path):
    db = entries_db_path(make_settings(tmp_path))
    loop = ConsolidationLoop(interval_seconds=0.05, db_path=db)
    loop.start()
    try:
        assert loop.stopped is False
        assert loop._thread is not None and loop._thread.is_alive()
    finally:
        loop.stop()  # serve 停止钩子：置停止事件并 join
    assert loop.stopped
    assert loop._thread is None


def test_serve_without_flag_no_background_thread(monkeypatch):
    calls: list[dict] = []

    def fake_serve(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr("tindalos.serve.serve", fake_serve)
    before = len(threading.enumerate())
    r = runner.invoke(app, ["serve"])
    assert r.exit_code == 0, r.stdout
    assert calls, "serve 被调用"
    assert len(threading.enumerate()) == before, "无 flag 不新增后台线程"


def test_serve_with_consolidate_interval_starts_and_stops(monkeypatch):
    seen: dict[str, bool] = {}

    def fake_serve(**kwargs):
        seen["thread_running"] = any(
            t.name == "tindalos-consolidate" and t.is_alive() for t in threading.enumerate()
        )

    monkeypatch.setattr("tindalos.serve.serve", fake_serve)
    before = len(threading.enumerate())
    r = runner.invoke(app, ["serve", "--consolidate-interval", "0.05"])
    assert r.exit_code == 0, r.stdout
    assert seen.get("thread_running") is True, "serve 运行期间后台整合线程存活"
    assert len(threading.enumerate()) == before, "停止钩子已 join 线程"


# ---------------------------------------------------------------- CLI consolidate / session


def test_cli_consolidate_command(tmp_path):
    db = entries_db_path(make_settings(tmp_path))
    capture_episodic(campaign_with_events("c-cli", 25), db)

    r = runner.invoke(app, ["consolidate", "--db", str(db)])
    assert r.exit_code == 0, r.stdout
    assert "c-cli" in r.stdout and "整合 5 条" in r.stdout and "synopsis" in r.stdout
    assert len(list_entries("c-cli", "episodic", db, status="active")) == 20

    # 再跑一次幂等：0 条新增整合，退出码仍 0
    r2 = runner.invoke(app, ["consolidate", "--db", str(db)])
    assert r2.exit_code == 0, r2.stdout
    assert "整合 0 条" in r2.stdout


def test_cli_consolidate_empty_db_message(tmp_path):
    db = tmp_path / "no-such" / "memory_entries.sqlite"  # 不存在 → 空
    r = runner.invoke(app, ["consolidate", "--db", str(db)])
    assert r.exit_code == 0, r.stdout
    assert "暂无待整合的 campaign" in r.stdout


def test_cli_session_records_and_prints_index(tmp_path):
    db = entries_db_path(make_settings(tmp_path))
    r = runner.invoke(
        app,
        ["session", "c-sess", "--summary", "调查员进入雾镇", "--play-status", "进行中", "--db", str(db)],
    )
    assert r.exit_code == 0, r.stdout
    assert "已记录第 1 场会话" in r.stdout
    assert "sess:c-sess:1" in r.stdout

    sessions = list_play_sessions("c-sess", db)
    assert len(sessions) == 1
    assert sessions[0]["summary"] == "调查员进入雾镇"
    assert sessions[0]["play_status"] == "进行中"
    assert sessions[0]["session_index"] == 1

    # 第二场：index 递增
    r2 = runner.invoke(app, ["session", "c-sess", "--summary", "第二次游玩", "--db", str(db)])
    assert r2.exit_code == 0, r2.stdout
    assert "已记录第 2 场会话" in r2.stdout
    assert len(list_play_sessions("c-sess", db)) == 2
