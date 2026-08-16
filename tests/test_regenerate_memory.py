"""P1 #3 /api/regenerate 记忆一致性钩子测试（ticket 04）。

覆盖（对齐验收）：
1. supersede_entries 公开函数：subject_keys 路径、ids 路径、幂等（二跑零变更）、
   至少给一个参数否则 ValueError；
2. regenerate npc 成功后：相关 semantic 旧条目 superseded + 新版 active +
   supersedes_id 链正确（新内容 = 重生成后 NPC 印象，确定性可复算）；
3. regenerate 事件成功后：episodic 条目同 id 幂等覆盖为新内容，数量不变，
   不波及 semantic；
4. regenerate 场景成功后：setting 未变 → place 语义无漂移保持 active（零版本化）；
5. regenerate 事件成功后：ref_ids 命中该节点的 longterm 旧条目 superseded + 新版 active；
6. 回滚（空事件生成器）与未知 id → 记忆零变化；
7. 不传 db_path → 纯重生成（cli/serve 契约），记忆零变化；
8. web 路由 /api/regenerate 经 TestClient 触发钩子（语义链 + 情景条目落库）。

全程零网络零真实 LLM：regenerate 用确定性生成器，记忆全确定性。
"""
from __future__ import annotations

import hashlib
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
from tindalos.generator import DeterministicGenerator
from tindalos.memory import npc_impression
from tindalos.memory_entries import (
    capture_memory_entries,
    list_entries,
    supersede_entries,
)
from tindalos.models import (
    Act,
    Campaign,
    Clue,
    Event,
    NPC,
    RelationType,
    Scene,
    WorldRelation,
)
from tindalos.regenerate import regenerate_node

GEN = DeterministicGenerator(seed="regen-test")


# --------------------------------------------------------------------------- #
# 剧本工厂（与 test_regenerate.make_campaign(style="scene") 同构，可指定 campaign id）
# --------------------------------------------------------------------------- #

def _make_campaign(cid: str = "c-regen-mem") -> Campaign:
    npcs = {
        "npc-1": NPC(id="npc-1", name="老渔夫", archetype="向导",
                     personality=["谨慎", "话少"], description="码头的老人。",
                     acts_roles={"act-1": "线人"}),
        "npc-2": NPC(id="npc-2", name="警长", archetype="权威",
                     personality=["固执", "多疑"], description="当地警长。",
                     acts_roles={"act-1": "对立方"}),
    }
    scene1_events = [
        Event(id="scene-1-ev-1", title="抵达现场", kind="entry",
              description="众人抵达旧码头。", conditions=[], next_event_ids=["scene-1-ev-2"]),
        Event(id="scene-1-ev-2", title="发现渔网", kind="trigger",
              description="木桩下的旧渔网。", conditions=["夜色"], next_event_ids=["scene-1-ev-3"]),
        Event(id="scene-1-ev-3", title="事态升级", kind="outcome",
              description="迷雾涌起。", conditions=[], next_event_ids=[]),
    ]
    scene2_events = [
        Event(id="scene-2-ev-1", title="审讯", kind="entry",
              description="警长审讯。", conditions=[], next_event_ids=["scene-2-ev-2"]),
        Event(id="scene-2-ev-2", title="释放", kind="outcome",
              description="证据不足释放。", conditions=[], next_event_ids=[]),
    ]
    acts = [
        Act(id="act-1", title="第一幕", roman="I", summary="", npc_ids=["npc-1", "npc-2"],
            scenes=[
                Scene(id="scene-1", title="旧码头",
                      setting={"time": "深夜", "place": "旧码头"},
                      events=scene1_events, npc_ids=["npc-1", "npc-2"]),
                Scene(id="scene-2", title="警局",
                      setting={"time": "清晨", "place": "警局"},
                      events=scene2_events, npc_ids=["npc-2"]),
            ]),
    ]
    clues = [
        Clue(id="clue-1", name="旧渔网", description="符号与古籍一致。",
             linked_npc_ids=["npc-1"], linked_event_ids=["scene-1-ev-1"], found_at="scene-1"),
        Clue(id="clue-2", name="审讯记录", description="记录里的潮汐表。",
             linked_npc_ids=[], linked_event_ids=["scene-2-ev-1"], found_at="scene-2"),
    ]
    relations = [
        WorldRelation(source="npc-1", target="clue-1", type=RelationType.POINTS_TO,
                      label="指向线索", valid_from="2024-01-01"),
        WorldRelation(source="npc-1", target="npc-2", type=RelationType.KNOWS,
                      label="互相认识", valid_from="2024-01-01"),
    ]
    return Campaign(id=cid, title="雾港之夜",
                    premise="海边小镇的失踪案背后藏着深海的低语。",
                    acts=acts, npcs=npcs, clues=clues, relations=relations)


def _all_memory_rows(db) -> list[dict]:
    """整表快照（含非 active）——记忆零变化断言用：前后行级完全一致。"""
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT * FROM memory_entries ORDER BY id").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


class EmptySceneGenerator:
    """generate_scene 返回空事件序列 → 场景重生成失败回滚（与 test_regenerate 同构）。"""

    def generate_scene(self, act_title, premise, npc_ids):
        return {"setting": {}, "events": []}


# --------------------------------------------------------------------------- #
# 1. supersede_entries 公开函数
# --------------------------------------------------------------------------- #

def test_supersede_entries_subject_keys_and_idempotent(tmp_path):
    cid = "c-sup"
    campaign = _make_campaign(cid)
    db = tmp_path / "store" / "memory_entries.sqlite"
    capture_memory_entries(campaign, db)

    # subject_keys 路径：只命中 npc-1 语义，place/npc-2 不受影响
    n = supersede_entries(cid, db, subject_keys=["npc:npc-1"])
    assert n == 1
    sup = [e for e in list_entries(cid, "semantic", db, status="superseded")]
    assert {e["subject_key"] for e in sup} == {"npc:npc-1"}
    active_keys = {e["subject_key"] for e in list_entries(cid, "semantic", db, status="active")}
    assert active_keys == {"npc:npc-2", "place:scene-1", "place:scene-2"}

    # 幂等：第二次运行 0 受影响
    assert supersede_entries(cid, db, subject_keys=["npc:npc-1"]) == 0

    # ids 路径 + 幂等
    npc2 = next(e for e in list_entries(cid, "semantic", db, status="active")
                if e["subject_key"] == "npc:npc-2")
    assert supersede_entries(cid, db, ids=[npc2["id"]]) == 1
    assert supersede_entries(cid, db, ids=[npc2["id"]]) == 0


def test_supersede_entries_requires_ids_or_subject_keys(tmp_path):
    with pytest.raises(ValueError):
        supersede_entries("c", tmp_path / "store" / "memory_entries.sqlite")


# --------------------------------------------------------------------------- #
# 2. regenerate npc 成功后：semantic 链
# --------------------------------------------------------------------------- #

def test_regenerate_npc_semantic_supersede_chain(tmp_path):
    cid = "c-chain"
    campaign = _make_campaign(cid)
    db = tmp_path / "store" / "memory_entries.sqlite"
    capture_memory_entries(campaign, db)

    old_id = f"sem:{cid}:npc:npc-1"
    old_row = next(e for e in list_entries(cid, "semantic", db, status="active")
                   if e["id"] == old_id)
    assert old_row["subject_key"] == "npc:npc-1"

    new_cam, applied = regenerate_node(campaign, "npc-1", GEN, db_path=db)
    assert applied and any("npc-1" in a for a in applied)

    # 新内容 = 重生成后 NPC 印象（确定性可复算，且与旧内容不同）
    expected_content = npc_impression(new_cam.npcs["npc-1"])
    assert expected_content != old_row["content"]

    # 旧版 superseded、新版 active，supersedes_id 链正确
    old_sup = [e for e in list_entries(cid, "semantic", db, status="superseded")
               if e["id"] == old_id]
    assert len(old_sup) == 1
    assert old_sup[0]["subject_key"] == "npc:npc-1"

    new_rows = [e for e in list_entries(cid, "semantic", db, status="active")
                if e["subject_key"] == "npc:npc-1"]
    assert len(new_rows) == 1
    new_row = new_rows[0]
    assert new_row["id"].startswith("sem:")
    assert new_row["content"] == expected_content
    assert old_sup[0]["supersedes_id"] == new_row["id"]
    # 新版 id = 内容哈希后缀（与 _write_longterm/_apply_ops 同构）
    c_hash = hashlib.sha256(expected_content.encode("utf-8")).hexdigest()[:12]
    assert new_row["id"] == f"sem:{cid}:npc:npc-1:{c_hash}"

    # 无关语义条目保持 active
    active_keys = {e["subject_key"] for e in list_entries(cid, "semantic", db, status="active")}
    assert {"npc:npc-2", "place:scene-1", "place:scene-2"} <= active_keys


# --------------------------------------------------------------------------- #
# 3. regenerate 事件成功后：episodic 覆盖新内容
# --------------------------------------------------------------------------- #

def test_regenerate_event_updates_episodic(tmp_path):
    cid = "c-ev"
    campaign = _make_campaign(cid)
    db = tmp_path / "store" / "memory_entries.sqlite"
    capture_memory_entries(campaign, db)

    ev_id = f"evm:{cid}:scene-1-ev-1"
    before = next(e for e in list_entries(cid, "episodic", db) if e["id"] == ev_id)
    total_before = len(list_entries(cid, "episodic", db))

    new_cam, applied = regenerate_node(campaign, "scene-1-ev-1", GEN, db_path=db)
    assert applied and any("scene-1-ev-1" in a for a in applied)

    after = next(e for e in list_entries(cid, "episodic", db) if e["id"] == ev_id)
    assert after["status"] == "active"
    assert after["id"] == ev_id  # 同 id 幂等覆盖，非新增行
    assert after["content"] != before["content"]  # 新内容
    assert len(list_entries(cid, "episodic", db)) == total_before  # 数量不变
    new_desc = new_cam.acts[0].scenes[0].events[0].description
    assert new_desc in after["content"]

    # 事件节点不命中 npc/place 语义 → 语义零变化
    assert len(list_entries(cid, "semantic", db, status="superseded")) == 0
    active_keys = {e["subject_key"] for e in list_entries(cid, "semantic", db, status="active")}
    assert active_keys == {"npc:npc-1", "npc:npc-2", "place:scene-1", "place:scene-2"}


# --------------------------------------------------------------------------- #
# 4. regenerate 场景成功后：place 语义无漂移保持 active
# --------------------------------------------------------------------------- #

def test_regenerate_scene_keeps_place_semantic_active(tmp_path):
    cid = "c-scene"
    campaign = _make_campaign(cid)
    db = tmp_path / "store" / "memory_entries.sqlite"
    capture_memory_entries(campaign, db)

    place_before = next(e for e in list_entries(cid, "semantic", db)
                        if e["subject_key"] == "place:scene-1")
    ev_before = {e["id"]: e for e in list_entries(cid, "episodic", db)}

    new_cam, applied = regenerate_node(campaign, "scene-1", GEN, db_path=db)
    assert applied and any("scene-1" in a for a in applied)

    # setting 未变 → place 语义无漂移 → 保持 active，不版本化
    place_after = [e for e in list_entries(cid, "semantic", db, status="active")
                   if e["subject_key"] == "place:scene-1"]
    assert len(place_after) == 1
    assert place_after[0]["id"] == place_before["id"]
    assert place_after[0]["content"] == place_before["content"]
    assert len(list_entries(cid, "semantic", db, status="superseded")) == 0

    # scene-1 事件情景条目覆盖为新内容；scene-2 事件未变
    ev_after = {e["id"]: e for e in list_entries(cid, "episodic", db)}
    assert ev_after[f"evm:{cid}:scene-1-ev-1"]["content"] != ev_before[f"evm:{cid}:scene-1-ev-1"]["content"]
    assert ev_after[f"evm:{cid}:scene-2-ev-1"]["content"] == ev_before[f"evm:{cid}:scene-2-ev-1"]["content"]
    assert new_cam.acts[0].scenes[0].setting == {"time": "深夜", "place": "旧码头"}


# --------------------------------------------------------------------------- #
# 5. regenerate 事件成功后：ref_ids 命中的 longterm 链
# --------------------------------------------------------------------------- #

def test_regenerate_event_supersedes_longterm_ref_ids(tmp_path):
    from tindalos import memory_entries as me

    cid = "c-ltm-ev"
    campaign = _make_campaign(cid)
    db = tmp_path / "store" / "memory_entries.sqlite"
    capture_memory_entries(campaign, db)
    # 手工放一条 ref_ids 命中事件节点的 longterm（synopsis）作为旧长期条目
    conn = me._connect(db)
    try:
        me._insert_entry(conn, cid, "longterm", f"ltm:{cid}:synopsis:legacy",
                         "剧情概要：旧版主线。", subject_key="synopsis",
                         ref_ids=["scene-1-ev-1"], importance=0.8)
    finally:
        conn.close()
    legacy = next(e for e in list_entries(cid, "longterm", db, status="active")
                  if e["subject_key"] == "synopsis")
    assert "scene-1-ev-1" in json.loads(legacy["ref_ids"])
    ev_before = next(e for e in list_entries(cid, "episodic", db)
                     if e["id"] == f"evm:{cid}:scene-1-ev-1")

    new_cam, applied = regenerate_node(campaign, "scene-1-ev-1", GEN, db_path=db)
    assert applied and any("scene-1-ev-1" in a for a in applied)

    # 旧 longterm superseded + 新版 active + supersedes_id 链正确
    old_ltm = [e for e in list_entries(cid, "longterm", db, status="superseded")
               if e["subject_key"] == "synopsis"]
    assert len(old_ltm) == 1 and old_ltm[0]["id"] == legacy["id"]
    new_ltm = [e for e in list_entries(cid, "longterm", db, status="active")
               if e["subject_key"] == "synopsis"]
    assert len(new_ltm) == 1
    assert new_ltm[0]["content"] != legacy["content"]
    assert new_ltm[0]["id"].startswith("ltm:")
    assert old_ltm[0]["supersedes_id"] == new_ltm[0]["id"]

    # 事件情景条目已覆盖为新内容；语义零变化
    ev_after = next(e for e in list_entries(cid, "episodic", db)
                    if e["id"] == f"evm:{cid}:scene-1-ev-1")
    assert ev_after["content"] != ev_before["content"]
    assert len(list_entries(cid, "semantic", db, status="superseded")) == 0
    assert len(list_entries(cid, "semantic", db, status="active")) == 4


# --------------------------------------------------------------------------- #
# 6. 回滚 / 未知 id → 记忆零变化
# --------------------------------------------------------------------------- #

def test_regenerate_rollback_no_memory_change(tmp_path):
    cid = "c-roll"
    campaign = _make_campaign(cid)
    db = tmp_path / "store" / "memory_entries.sqlite"
    capture_memory_entries(campaign, db)
    snapshot = _all_memory_rows(db)

    with pytest.warns(UserWarning, match="回滚"):
        new_cam, applied = regenerate_node(campaign, "scene-1", EmptySceneGenerator(), db_path=db)
    assert applied == []
    assert _all_memory_rows(db) == snapshot


def test_regenerate_unknown_id_no_memory_write(tmp_path):
    cid = "c-unknown"
    campaign = _make_campaign(cid)
    db = tmp_path / "store" / "memory_entries.sqlite"
    capture_memory_entries(campaign, db)
    snapshot = _all_memory_rows(db)

    with pytest.raises(ValueError):
        regenerate_node(campaign, "npc-99", GEN, db_path=db)
    assert _all_memory_rows(db) == snapshot


# --------------------------------------------------------------------------- #
# 7. 不传 db_path → 纯重生成（cli/serve 契约）
# --------------------------------------------------------------------------- #

def test_regenerate_hook_without_db_path_pure(tmp_path):
    cid = "c-pure"
    campaign = _make_campaign(cid)
    db = tmp_path / "store" / "memory_entries.sqlite"
    capture_memory_entries(campaign, db)
    snapshot = _all_memory_rows(db)

    new_cam, applied = regenerate_node(campaign, "npc-1", GEN)  # 无 db_path → 纯重生成
    assert applied and any("npc-1" in a for a in applied)
    assert _all_memory_rows(db) == snapshot


# --------------------------------------------------------------------------- #
# 8. web 路由 /api/regenerate 触发钩子
# --------------------------------------------------------------------------- #

@pytest.fixture(autouse=True)
def _fresh_state():
    """隔离 web 模块级 campaign 缓存（测试间不串）。"""
    web_mod._state.campaigns.clear()
    yield
    web_mod._state.campaigns.clear()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """隔离实例：tmp 历史库 + tmp 数据目录 + 无 dist；lifespan 内 init_db。"""
    monkeypatch.setenv("TINDALOS_SITE_DB", str(tmp_path / "site.db"))
    monkeypatch.setenv("TINDALOS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TINDALOS_FRONTEND_DIST", str(tmp_path / "no-such-dist"))
    with TestClient(web_mod.create_app()) as c:
        yield c


def _fake_generate(events, campaign):
    """确定性伪生成：逐条 emit(stage,message)，返回 campaign dict。

    签名与 serve.default_generate 对齐（#22 新增 keyword-only module_images），
    否则 web 路由按新签名调用会 TypeError 被吞成 failed 帧。
    """
    def gen(module_text: str, llm: bool, emit, *, module_images=None) -> dict:
        for stage, message in events:
            emit(stage, message)
        return campaign

    return gen


def test_web_regenerate_triggers_memory_hook(client, tmp_path, monkeypatch):
    cid = "c-web-mem"
    campaign = _make_campaign(cid)
    raw = campaign.model_dump(mode="json")
    db = tmp_path / "data" / "store" / "memory_entries.sqlite"
    capture_memory_entries(campaign, db)
    old_id = f"sem:{cid}:npc:npc-1"
    old_content = next(e for e in list_entries(cid, "semantic", db, status="active")
                       if e["id"] == old_id)["content"]

    monkeypatch.setattr(web_mod, "default_generate", _fake_generate([], raw))
    client.post("/api/generate", json={"module_text": "x"})

    r = client.post("/api/regenerate", json={"campaign_id": cid, "node_id": "npc-1"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["campaign"]["id"] == cid
    assert any("npc-1" in a for a in body["applied"])

    # 语义链：旧 npc-1 superseded、新版 active、supersedes_id 链正确
    old_sup = [e for e in list_entries(cid, "semantic", db, status="superseded")
               if e["id"] == old_id]
    assert len(old_sup) == 1
    assert old_sup[0]["content"] == old_content
    new_act = [e for e in list_entries(cid, "semantic", db, status="active")
               if e["subject_key"] == "npc:npc-1"]
    assert len(new_act) == 1
    assert new_act[0]["id"] != old_id
    assert old_sup[0]["supersedes_id"] == new_act[0]["id"]

    # 情景条目已捕获（存于 TINDALOS_DATA_DIR 指向的 store 库）
    ev = [e for e in list_entries(cid, "episodic", db, status="active")
          if e["id"] == f"evm:{cid}:scene-1-ev-1"]
    assert len(ev) == 1 and ev[0]["content"]
