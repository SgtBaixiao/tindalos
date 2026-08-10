"""tindalos.kg 测试：WorldGraph 六类边 / 时间窗 / 多跳路径 / JSON 往返 / 一致性检查 + campaign 映射。

依赖模型（t2 并行落地）：build_from_campaign / campaign_consistency 相关用例经
pytest.importorskip 门控——t2 未落地时跳过，其余用例保持独立可测。
"""
import json
import pathlib
import sys
from datetime import datetime, timezone

_SRC = pathlib.Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import pytest

from tindalos import kg
from tindalos.kg import WorldGraph, build_from_campaign, campaign_consistency


def make_graph() -> WorldGraph:
    w = WorldGraph()
    for eid, kind in [
        ("npc_a", "npc"), ("npc_b", "npc"), ("npc_c", "npc"),
        ("clue_1", "clue"), ("ev_1", "event"),
    ]:
        w.add_entity(eid, kind, {"name": eid})
    return w


# ---------------------------------------------------------------- 六类边

def test_six_relation_types_add_and_query():
    w = make_graph()
    rels = [
        ("npc_a", "npc_b", kg.RelationType.KNOWS, "互相认识"),
        ("npc_b", "clue_1", kg.RelationType.POINTS_TO, "指向线索"),
        ("clue_1", "ev_1", kg.RelationType.CAUSES, "导致事件"),
        ("ev_1", "npc_a", kg.RelationType.BELONGS_TO, "归属关系"),
        ("npc_a", "clue_1", kg.RelationType.LEARNS, "获知旧信"),
        ("npc_b", "clue_1", kg.RelationType.EXPIRES, "失效"),
    ]
    for src, tgt, typ, label in rels:
        w.add_relation(src, tgt, typ, label, "2024-01-01")
    out = w.relations_of("npc_b")
    assert {r["type"] for r in out} == {"认识", "指向", "失效"}
    assert all(set(r) == {"source", "target", "type", "label", "valid_from", "valid_to"} for r in out)
    assert w.relations_of("npc_c") == []


def test_add_relation_accepts_enum_name_and_label():
    w = make_graph()
    w.add_relation("npc_a", "npc_b", "KNOWS", "a", "2024-01-01")
    w.add_relation("npc_a", "npc_c", "认识", "b", "2024-01-01")
    assert {r["type"] for r in w.relations_of("npc_a")} == {"认识"}


def test_add_relation_unknown_type_raises():
    w = make_graph()
    with pytest.raises(ValueError):
        w.add_relation("npc_a", "npc_b", "SUMMONS", "x", "2024-01-01")


def test_add_relation_dedupe_same_window():
    w = make_graph()
    w.add_relation("npc_a", "npc_b", "KNOWS", "x", "2024-01-01", "2024-06-01")
    w.add_relation("npc_a", "npc_b", "KNOWS", "x2", "2024-01-01", "2024-06-01")
    assert len(w.relations_of("npc_a")) == 1
    # 不同有效窗 → 允许并行边
    w.add_relation("npc_a", "npc_b", "KNOWS", "y", "2024-06-02", "2024-12-31")
    assert len(w.relations_of("npc_a")) == 2


def test_relations_of_both_directions():
    w = make_graph()
    w.add_relation("npc_a", "npc_b", "KNOWS", "a->b", "2024-01-01")
    w.add_relation("npc_b", "npc_c", "KNOWS", "b->c", "2024-01-01")
    labels = {r["label"] for r in w.relations_of("npc_b")}
    assert labels == {"a->b", "b->c"}


# ---------------------------------------------------------------- 时间窗

def test_active_relations_time_window_semantics():
    w = make_graph()
    w.add_relation("npc_a", "npc_b", "KNOWS", "permanent", "2024-01-01", None)
    w.add_relation("npc_b", "npc_c", "KNOWS", "expired", "2024-01-01", "2024-06-01")
    w.add_relation("npc_a", "npc_c", "KNOWS", "future", "2025-01-01", "2025-12-31")
    w.add_relation("npc_c", "clue_1", "LEARNS", "active-now", "2024-01-01", "2024-07-15")
    # valid_from <= as_of < valid_to；valid_to=None 永久
    active = {r["label"] for r in w.active_relations(as_of="2024-07-01")}
    assert active == {"permanent", "active-now"}
    # as_of == valid_to 时刻不再 active
    active2 = {r["label"] for r in w.active_relations(as_of="2024-07-15")}
    assert "active-now" not in active2
    assert "permanent" in active2
    # as_of 用 datetime 同样成立
    asof = datetime(2024, 7, 1, tzinfo=timezone.utc)
    assert {r["label"] for r in w.active_relations(as_of=asof)} == {"permanent", "active-now"}


def test_active_relations_default_asof_now():
    w = make_graph()
    w.add_relation("npc_a", "npc_b", "KNOWS", "past-perm", "2000-01-01", None)
    w.add_relation("npc_a", "npc_c", "KNOWS", "past-expired", "2000-01-01", "2001-01-01")
    w.add_relation("npc_b", "npc_c", "KNOWS", "future", "2099-01-01", None)
    assert {r["label"] for r in w.active_relations()} == {"past-perm"}


# ---------------------------------------------------------------- 多跳路径

def test_path_multihop_bfs_depth():
    w = WorldGraph()
    for n in "abcd":
        w.add_entity(n, "npc", {})
    w.add_relation("a", "b", "KNOWS", "", "2020-01-01")
    w.add_relation("b", "c", "KNOWS", "", "2020-01-01")
    w.add_relation("c", "d", "KNOWS", "", "2020-01-01")
    assert w.path("a", "d") == [["a", "b", "c", "d"]]
    assert w.path("a", "d", max_depth=2) == []
    assert w.path("a", "b") == [["a", "b"]]
    assert w.path("a", "a") == [["a"]]
    assert w.path("zz", "a") == []


def test_path_multiple_branches_and_direction():
    w = WorldGraph()
    for n in ("a", "b1", "b2", "z"):
        w.add_entity(n, "npc", {})
    w.add_relation("a", "b1", "KNOWS", "", "2020-01-01")
    w.add_relation("a", "b2", "KNOWS", "", "2020-01-01")
    w.add_relation("b1", "z", "KNOWS", "", "2020-01-01")
    w.add_relation("b2", "z", "KNOWS", "", "2020-01-01")
    assert sorted(w.path("a", "z")) == [["a", "b1", "z"], ["a", "b2", "z"]]
    # 有向：只沿 source→target
    w2 = WorldGraph()
    for n in ("a", "b"):
        w2.add_entity(n, "npc", {})
    w2.add_relation("b", "a", "KNOWS", "", "2020-01-01")
    assert w2.path("a", "b") == []
    assert w2.path("b", "a") == [["b", "a"]]


# ---------------------------------------------------------------- JSON 往返

def test_to_json_from_json_roundtrip():
    w = make_graph()
    w.add_relation("npc_a", "npc_b", "KNOWS", "认识", "2024-01-01", "2024-06-01")
    w.add_relation("npc_b", "clue_1", "LEARNS", "获知", "2024-01-01", None)
    doc = w.to_json()
    assert set(doc) == {"nodes", "edges"}
    assert set(doc["nodes"][0]) == {"id", "kind", "attrs"}
    assert set(doc["edges"][0]) == {"source", "target", "type", "label", "valid_from", "valid_to"}
    w2 = WorldGraph.from_json(doc)
    w3 = WorldGraph.from_json(json.dumps(doc))
    assert w2.to_json() == doc
    assert w3.to_json() == doc
    for eid in ("npc_a", "npc_b", "clue_1"):
        assert w2.relations_of(eid) == w.relations_of(eid)


def test_to_json_empty_graph():
    w = WorldGraph()
    assert w.to_json() == {"nodes": [], "edges": []}


# ---------------------------------------------------------------- 一致性检查

def test_consistency_check_dangling_entity():
    w = WorldGraph()
    w.add_entity("npc_a", "npc", {})
    w.add_relation("npc_a", "ghost", "KNOWS", "x", "2024-01-01")
    problems = w.consistency_check()
    assert any("ghost" in p for p in problems)


def test_consistency_check_overlapping_windows():
    w = make_graph()
    w.add_relation("npc_a", "npc_b", "KNOWS", "w1", "2024-01-01", "2024-06-30")
    w.add_relation("npc_a", "npc_b", "KNOWS", "w2", "2024-06-01", "2024-12-31")
    assert any("重叠" in p for p in w.consistency_check())
    # 半开区间 [from,to)：端点相接不算重叠
    w2 = make_graph()
    w2.add_relation("npc_a", "npc_b", "KNOWS", "w1", "2024-01-01", "2024-06-30")
    w2.add_relation("npc_a", "npc_b", "KNOWS", "w2", "2024-06-30", "2024-12-31")
    assert not any("重叠" in p for p in w2.consistency_check())


def test_consistency_check_inverted_window():
    w = make_graph()
    w.add_relation("npc_a", "npc_b", "KNOWS", "x", "2024-06-01", "2024-01-01")
    problems = w.consistency_check()
    assert any("倒置" in p for p in problems)


def test_consistency_check_clean_graph():
    w = make_graph()
    w.add_relation("npc_a", "npc_b", "KNOWS", "x", "2024-01-01", "2024-06-30")
    w.add_relation("npc_b", "clue_1", "LEARNS", "y", "2024-01-01", None)
    assert w.consistency_check() == []


# ---------------------------------------------------------------- campaign 映射（依赖 t2 models）

def _make_campaign(models):
    m = models
    a = m.NPC(id="npc_a", name="Alice", archetype="侦探", personality=["谨慎"],
              description="本地侦探", acts_roles={})
    b = m.NPC(id="npc_b", name="Bob", archetype="教授", personality=["博学"],
              description="大学教师", acts_roles={})
    ev = m.Event(id="ev_1", title="发现旧信", kind="entry", description="图书馆内发现",
                 conditions=[], next_event_ids=[])
    scene = m.Scene(id="scene_1", title="开场", setting={"time": "夜", "place": "图书馆"},
                    events=[ev], npc_ids=["npc_a", "npc_b"])
    act = m.Act(id="act_1", title="第一幕", roman="I", summary="雾都开场",
                scenes=[scene], npc_ids=["npc_a", "npc_b"])
    clue = m.Clue(id="clue_1", name="旧信", description="泛黄的信",
                  linked_npc_ids=["npc_a"], linked_event_ids=["ev_1"], found_at="图书馆")
    rel = m.WorldRelation(source="npc_a", target="clue_1", type=m.RelationType.LEARNS,
                          label="获知旧信", valid_from="2024-01-01", valid_to=None, note=None)
    return m.Campaign(id="c1", title="雾都", premise="调查旧信", acts=[act],
                      npcs={"npc_a": a, "npc_b": b}, clues=[clue], relations=[rel])


def test_build_from_campaign_registers_entities_and_relations():
    models = pytest.importorskip("tindalos.models")
    campaign = _make_campaign(models)
    world = build_from_campaign(campaign)
    edges = [r for r in world.relations_of("npc_a") if r["target"] == "clue_1"]
    assert edges and edges[0]["type"] == "获知"
    kinds = {n["kind"] for n in world.to_json()["nodes"]}
    assert kinds == {"npc", "clue"}
    assert campaign_consistency(campaign, world) == []


def test_campaign_consistency_flags_dangling_refs():
    models = pytest.importorskip("tindalos.models")
    campaign = _make_campaign(models)
    world = build_from_campaign(campaign)
    # 场景引用未注册 NPC（构造后直接改，绕过构造期校验）
    bad = campaign.model_copy(deep=True)
    bad.acts[0].scenes[0].npc_ids.append("ghost_npc")
    assert any("ghost_npc" in p for p in campaign_consistency(bad, world))
    # 线索引用未知事件
    bad2 = campaign.model_copy(deep=True)
    bad2.clues[0].linked_event_ids.append("no_such_event")
    assert any("no_such_event" in p for p in campaign_consistency(bad2, world))
    # world 缺注册线索
    world2 = WorldGraph()
    world2.add_entity("npc_a", "npc", {})
    world2.add_entity("npc_b", "npc", {})
    world2.add_relation("npc_a", "clue_1", "LEARNS", "获知旧信", "2024-01-01", None)
    assert any("clue_1" in p for p in campaign_consistency(campaign, world2))
