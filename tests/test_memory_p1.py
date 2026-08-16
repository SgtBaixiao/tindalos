"""P1 记忆核心（ticket 01）测试：consolidate / longterm / record_session / /api/memories。

覆盖（对齐验收）：
1. consolidate 确定性降级：seed 25 条 episodic → 最旧 5 条置 consolidated +
   synopsis longterm 存在 + 二跑一致（幂等，无新增写入）；
2. consolidate + FakeLLM（stub 返回精心构造的 ADD/UPDATE/DELETE JSON）：
   操作生效、supersede 链正确、longterm 三键写入；
3. consolidate + FakeLLM 返回坏 JSON → 确定性降级，不抛异常；
4. record_session：play_status 更新 + 最近会话可读 + conflicts JSON 往返 +
   session_index 递增；
5. /api/memories 端点 FastAPI TestClient：四类 + play_status。

全程零网络零真实 LLM：LLM 路径用 FakeLLM stub 驱动，其余全确定性。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import pytest
from fastapi.testclient import TestClient

from tindalos import web as web_mod
from tindalos.config import Settings
from tindalos.memory_entries import (
    capture_episodic,
    capture_memory_entries,
    consolidate,
    count_entries,
    current_play_status,
    entries_db_path,
    list_entries,
    list_play_sessions,
    record_session,
)
from tindalos.models import construct_loose_campaign


def make_settings(tmp_path) -> Settings:
    return Settings(
        llm_enabled=False,
        checkpoint_dir=tmp_path / "checkpoints",
        store_dir=tmp_path / "store",
    )


def campaign_with_events(cid: str, n_events: int = 25, n_npcs: int = 2):
    """构造一个战役：1 幕 · 1 场景 · n_events 个事件 · n_npcs 个 NPC（含场景 setting → place 语义事实）。"""
    scenes = [
        {
            "id": f"scene-{i}",
            "title": f"场景{i}",
            "setting": {"time": "夜", "place": "雾镇"},
            "events": [
                {
                    "id": f"ev-{i}",
                    "title": f"事件{i}",
                    "kind": "outcome",
                    "description": f"描述{i}",
                }
            ],
            "npc_ids": [],
        }
        for i in range(n_events)
    ]
    acts = [{"id": "act-1", "title": "第一幕", "roman": "I", "scenes": scenes, "npc_ids": []}]
    npcs = {
        f"npc-{j}": {"id": f"npc-{j}", "name": f"NPC{j}", "archetype": "居民"}
        for j in range(1, n_npcs + 1)
    }
    return construct_loose_campaign(
        {"id": cid, "title": "雾镇疑云", "premise": "测试模组", "acts": acts, "npcs": npcs}
    )


class FakeLLM:
    """最小 LLM stub：`llm(prompt) -> str`，记录 prompt 供断言。"""

    def __init__(self, response: str):
        self.response = response
        self.prompt: str | None = None

    def __call__(self, prompt: str) -> str:
        self.prompt = prompt
        return self.response


def _all_memory_rows(db) -> list[dict]:
    """整表快照（含非 active）——幂等断言用：两次运行前后行级完全一致。"""
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT * FROM memory_entries ORDER BY id").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------- 1. consolidate 确定性降级


def test_consolidate_deterministic_oldest_batch_and_synopsis(tmp_path):
    campaign = campaign_with_events("c-det", 25)
    db = entries_db_path(make_settings(tmp_path))
    stats = capture_episodic(campaign, db)
    assert stats["total"] == 25
    assert count_entries(campaign.id, db)["episodic"] == 25

    res = consolidate(campaign.id, db)
    assert res["llm"] is False and res["degraded"] is False
    assert res["episodic_consolidated"] == 5  # 25 - 20 = 5 最旧置 consolidated
    assert res["longterm_written"] == ["synopsis"]

    active = list_entries(campaign.id, "episodic", db, status="active")
    consolidated = list_entries(campaign.id, "episodic", db, status="consolidated")
    assert len(active) == 20
    assert len(consolidated) == 5

    # synopsis longterm 存在，且 consolidated 条目指向它
    longterm = list_entries(campaign.id, "longterm", db, status="active")
    syn = [e for e in longterm if e["subject_key"] == "synopsis"]
    assert len(syn) == 1
    assert all(e["consolidated_into"] == syn[0]["id"] for e in consolidated)
    assert syn[0]["ref_ids"] and "事件" in syn[0]["content"]


def test_consolidate_deterministic_idempotent(tmp_path):
    campaign = campaign_with_events("c-idem", 25)
    db = entries_db_path(make_settings(tmp_path))
    capture_episodic(campaign, db)

    consolidate(campaign.id, db)
    snapshot_before = _all_memory_rows(db)
    syn_before = list_entries(campaign.id, "longterm", db, status="active")

    res2 = consolidate(campaign.id, db)
    assert res2["episodic_consolidated"] == 0  # 已回到 min_episodic，不再整合
    assert res2["longterm_written"] == []

    snapshot_after = _all_memory_rows(db)
    assert snapshot_before == snapshot_after, "二跑零写入（content_hash / status 判重幂等）"
    syn_after = list_entries(campaign.id, "longterm", db, status="active")
    assert syn_before == syn_after


# ---------------------------------------------------------------- 2. consolidate + FakeLLM（两段式协议）


def test_consolidate_llm_ops_apply_supersede_chain_and_three_longterm(tmp_path):
    campaign = campaign_with_events("c-llm", 3, n_npcs=2)
    db = entries_db_path(make_settings(tmp_path))
    capture_memory_entries(campaign, db)
    assert count_entries(campaign.id, db)["episodic"] == 3
    assert any(e["subject_key"] == "npc:npc-1" for e in list_entries(campaign.id, "semantic", db))
    assert any(e["subject_key"] == "place:scene-0" for e in list_entries(campaign.id, "semantic", db))

    fake = FakeLLM(
        json.dumps(
            [
                {"op": "UPDATE", "kind": "semantic", "subject_key": "npc:npc-1", "content": "老吴（富商）：已揭示古宅秘密。"},
                {"op": "DELETE", "kind": "semantic", "subject_key": "place:scene-0", "content": None},
                {"op": "ADD", "kind": "longterm", "subject_key": "synopsis", "content": "剧情概要：调查员在雾镇揭开古宅之谜。"},
                {"op": "ADD", "kind": "longterm", "subject_key": "plotline", "content": "主线：守夜人与失踪案。"},
                {"op": "ADD", "kind": "longterm", "subject_key": "npc_arcs", "content": "NPC 弧光：老吴由富商转为关键证人。"},
            ],
            ensure_ascii=False,
        )
    )
    res = consolidate(campaign.id, db, llm=fake)
    assert res["llm"] is True and res["degraded"] is False
    assert res["ops_applied"] == 5
    assert set(res["longterm_written"]) == {"synopsis", "plotline", "npc_arcs"}
    assert fake.prompt is not None and "情景记忆" in fake.prompt

    # UPDATE：旧 npc-1 语义条目 superseded，新版 active，supersedes_id 链正确
    superseded_npc = [
        e for e in list_entries(campaign.id, "semantic", db, status="superseded")
        if e["subject_key"] == "npc:npc-1"
    ]
    active_npc = [
        e for e in list_entries(campaign.id, "semantic", db, status="active")
        if e["subject_key"] == "npc:npc-1"
    ]
    assert len(superseded_npc) == 1
    assert len(active_npc) == 1
    assert "古宅" in active_npc[0]["content"]
    assert superseded_npc[0]["supersedes_id"] == active_npc[0]["id"]
    # 旧版仍保留（非物理删除）
    assert superseded_npc[0]["id"].startswith("sem:")

    # DELETE：place:scene-0 置 superseded，行仍在
    place_sup = [
        e for e in list_entries(campaign.id, "semantic", db, status="superseded")
        if e["subject_key"] == "place:scene-0"
    ]
    assert len(place_sup) == 1
    assert place_sup[0]["id"] == "sem:c-llm:place:scene-0"
    assert not any(e["subject_key"] == "place:scene-0" for e in list_entries(campaign.id, "semantic", db))

    # longterm 三键 active
    keys = {e["subject_key"] for e in list_entries(campaign.id, "longterm", db, status="active")}
    assert keys == {"synopsis", "plotline", "npc_arcs"}


# ---------------------------------------------------------------- 3. consolidate + FakeLLM 坏 JSON → 降级


def test_consolidate_bad_json_degrades_deterministically(tmp_path):
    bad_responses = ("这不是 JSON", "", "[]", "[1, 2]", '{"op": "ADD"}')
    for i, bad in enumerate(bad_responses):
        # 每种坏输入用独立 store 目录，避免第一次降级后状态已被整合
        store = tmp_path / f"bad-{i}"
        db = entries_db_path(make_settings(store))
        campaign = campaign_with_events(f"c-bad-{i}", 25)
        capture_episodic(campaign, db)

        res = consolidate(campaign.id, db, llm=FakeLLM(bad))
        assert res["llm"] is False and res["degraded"] is True
        assert res["ops_applied"] == 0
        assert res["episodic_consolidated"] == 5
        # 与 llm=None 的确定性降级一致：synopsis 存在
        assert any(
            e["subject_key"] == "synopsis"
            for e in list_entries(campaign.id, "longterm", db, status="active")
        )
        active = len(list_entries(campaign.id, "episodic", db, status="active"))
        assert active == 20


def test_consolidate_llm_raising_degrades_without_exception(tmp_path):
    campaign = campaign_with_events("c-raise", 25)
    db = entries_db_path(make_settings(tmp_path))
    capture_episodic(campaign, db)

    def exploding(prompt: str) -> str:  # noqa: ARG001 - 模拟 LLM 抛异常
        raise RuntimeError("network down")

    res = consolidate(campaign.id, db, llm=exploding)
    assert res["llm"] is False and res["degraded"] is True
    assert res["episodic_consolidated"] == 5


# ---------------------------------------------------------------- 4. record_session / play_sessions


def test_record_session_play_status_conflicts_and_index(tmp_path):
    db = entries_db_path(make_settings(tmp_path))
    r1 = record_session("c-sess", "第一次游玩：调查员进入雾镇", db, play_status="进行中", conflicts={"scene-1": "分歧"})
    r2 = record_session("c-sess", "第二次游玩：发现古宅地窖", db, play_status="结局", conflicts=None)

    assert r1["session_index"] == 1 and r1["session_id"] == "sess:c-sess:1"
    assert r2["session_index"] == 2 and r2["session_id"] == "sess:c-sess:2"
    assert r1["play_status"] == "进行中" and r2["play_status"] == "结局"

    # 最近会话可读
    assert current_play_status("c-sess", db) == "结局"
    assert current_play_status("c-ghost", db) is None

    sessions = list_play_sessions("c-sess", db)
    assert len(sessions) == 2
    assert sessions[0]["summary"] == "第一次游玩：调查员进入雾镇"
    assert sessions[1]["session_index"] == 2
    # conflicts JSON 往返
    assert json.loads(sessions[0]["conflicts"]) == {"scene-1": "分歧"}
    assert sessions[1]["conflicts"] is None
    assert sessions[0]["created_at"]


# ---------------------------------------------------------------- 5. /api/memories 端点


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("TINDALOS_SITE_DB", str(tmp_path / "site.db"))
    monkeypatch.setenv("TINDALOS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TINDALOS_FRONTEND_DIST", str(tmp_path / "no-such-dist"))
    with TestClient(web_mod.create_app()) as c:
        yield c


def test_api_memories_four_types_and_play_status(client, tmp_path):
    cid = "c-mem"
    db = tmp_path / "data" / "store" / "memory_entries.sqlite"
    campaign = campaign_with_events(cid, 3, n_npcs=2)
    capture_memory_entries(campaign, db)
    record_session(cid, "首场游玩", db, play_status="进行中")

    r = client.get(f"/api/memories/{cid}")
    assert r.status_code == 200
    body = r.json()
    assert body["campaign_id"] == cid
    assert body["status"] == "ok"
    assert body["play_status"] == "进行中"

    mem = body["memories"]
    assert isinstance(mem, dict)
    assert len(mem["episodic"]) == 3
    assert all(e["id"].startswith("evm:") and e["content"] for e in mem["episodic"])
    npc_keys = {e["subject_key"] for e in mem["semantic"]}
    assert "npc:npc-1" in npc_keys and any(k.startswith("place:") for k in npc_keys)
    assert mem["shortterm"] == []
    assert mem["longterm"] == []
    # ref_ids 已解析为列表
    assert isinstance(mem["episodic"][0]["ref_ids"], list)


def test_api_memories_empty_campaign_ok(client):
    r = client.get("/api/memories/ghost-campaign")
    assert r.status_code == 200
    body = r.json()
    assert body["campaign_id"] == "ghost-campaign"
    assert body["status"] == "ok"
    assert body["play_status"] is None
    for mt in ("episodic", "semantic", "shortterm", "longterm"):
        assert body["memories"][mt] == []
