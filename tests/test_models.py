"""tindalos.models 领域模型契约测试（task t2-models）。

覆盖：
- 全部模型字段与 RelationType 六类中文标签
- model_dump_json / model_validate_json 往返相等
- Campaign 跨层校验：悬空 npc/事件引用抛 ValidationError、跨幕 id 重复抛错
- ScriptGraph.from_campaign 节点/边数量与结构（React Flow 约定）
- CONTEXT.md 领域词汇表存在且覆盖全部术语
"""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from tindalos.models import (
    Act,
    Campaign,
    Clue,
    Event,
    NPC,
    RelationType,
    Scene,
    ScriptGraph,
    WorldRelation,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------- #
# 工厂：构造合法模型实例
# --------------------------------------------------------------------------- #

def make_event(eid="ev-1", kind="entry", nxt=None):
    return Event(
        id=eid,
        title="发现石碑",
        kind=kind,
        description="调查员在图书馆地下室发现一块刻满符号的石碑。",
        conditions=["调查员持有《死灵之书》副本"],
        next_event_ids=nxt or [],
    )


def make_scene(sid="sc-1", npc_ids=None, events=None):
    return Scene(
        id=sid,
        title="大学图书馆",
        setting={"time": "1930 年秋", "place": "密斯卡托尼克大学图书馆"},
        events=events or [make_event()],
        npc_ids=npc_ids or [],
    )


def make_act(aid="act-1", scenes=None):
    return Act(
        id=aid,
        title="第一幕",
        roman="I",
        summary="调查员接受委托，前往图书馆查阅古籍。",
        scenes=scenes or [make_scene()],
        npc_ids=[],
    )


def make_npc(nid="npc-1"):
    return NPC(
        id=nid,
        name="艾达·卡特",
        archetype="考古学家",
        personality=["谨慎", "求知欲强"],
        description="波士顿考古学会成员，精通古埃及文献。",
        acts_roles={"act-1": "线索提供者"},
    )


def make_clue(cid="clue-1", linked_events=None, linked_npcs=None):
    return Clue(
        id=cid,
        name="石碑拓片",
        description="石碑上的符号指向深潜者教会。",
        linked_npc_ids=linked_npcs or ["npc-1"],
        linked_event_ids=linked_events or ["ev-1"],
        found_at="图书馆地下室",
    )


def make_relation(**kw):
    base = dict(
        source="npc-1",
        target="clue-1",
        type=RelationType.KNOWS,
        label="知道",
        valid_from="1930-09-01",
    )
    base.update(kw)
    return WorldRelation(**base)


def make_campaign():
    return Campaign(
        id="camp-1",
        title="暗潮",
        premise="海边小镇频发失踪案，深潜者教会浮出水面。",
        acts=[make_act()],
        npcs={"npc-1": make_npc()},
        clues=[make_clue()],
        relations=[make_relation()],
    )


# --------------------------------------------------------------------------- #
# ① RelationType 中文标签
# --------------------------------------------------------------------------- #

class TestRelationType:
    @pytest.mark.parametrize(
        "member,label",
        [
            (RelationType.KNOWS, "认识"),
            (RelationType.POINTS_TO, "指向"),
            (RelationType.CAUSES, "起因"),
            (RelationType.BELONGS_TO, "归属"),
            (RelationType.LEARNS, "获知"),
            (RelationType.EXPIRES, "失效"),
        ],
    )
    def test_chinese_labels(self, member, label):
        assert member.value == label

    def test_enum_members_complete(self):
        assert set(RelationType.__members__) == {
            "KNOWS", "POINTS_TO", "CAUSES", "BELONGS_TO", "LEARNS", "EXPIRES",
        }


# --------------------------------------------------------------------------- #
# ② 全部模型字段覆盖
# --------------------------------------------------------------------------- #

class TestFieldCoverage:
    def test_world_relation_fields(self):
        r = make_relation(valid_to="1931-01-01", note="由 NPC 证词获得")
        d = r.model_dump()
        for key in ("source", "target", "type", "label", "valid_from", "valid_to", "note"):
            assert key in d
        assert r.valid_to == "1931-01-01"
        assert r.note == "由 NPC 证词获得"
        assert r.type is RelationType.KNOWS

    def test_world_relation_optionals_default(self):
        r = WorldRelation(
            source="a", target="b", type=RelationType.POINTS_TO,
            label="x", valid_from="2020-01-01",
        )
        assert r.valid_to is None
        assert r.note is None

    def test_npc_fields(self):
        d = make_npc().model_dump()
        for key in ("id", "name", "archetype", "personality", "description", "acts_roles"):
            assert key in d
        assert d["personality"] == ["谨慎", "求知欲强"]
        assert d["acts_roles"] == {"act-1": "线索提供者"}

    def test_clue_fields(self):
        d = make_clue().model_dump()
        for key in ("id", "name", "description", "linked_npc_ids", "linked_event_ids", "found_at"):
            assert key in d

    def test_event_fields(self):
        e = make_event()
        d = e.model_dump()
        for key in ("id", "title", "kind", "description", "conditions", "next_event_ids"):
            assert key in d
        assert e.kind in ("entry", "trigger", "outcome")

    def test_event_kind_literal_rejects_unknown(self):
        with pytest.raises(ValidationError):
            Event(id="e", title="t", kind="sidequest", description="d")

    def test_scene_fields(self):
        s = make_scene()
        d = s.model_dump()
        for key in ("id", "title", "setting", "events", "npc_ids"):
            assert key in d
        assert s.setting["time"] == "1930 年秋"
        assert s.setting["place"] == "密斯卡托尼克大学图书馆"

    def test_act_fields(self):
        d = make_act().model_dump()
        for key in ("id", "title", "roman", "summary", "scenes", "npc_ids"):
            assert key in d

    def test_campaign_fields(self):
        d = make_campaign().model_dump()
        for key in ("id", "title", "premise", "acts", "npcs", "clues", "relations"):
            assert key in d


# --------------------------------------------------------------------------- #
# ③ round-trip：dump → load 相等
# --------------------------------------------------------------------------- #

class TestRoundTrip:
    @pytest.mark.parametrize(
        "factory",
        [
            lambda: make_relation(valid_to="1931-01-01", note="n"),
            lambda: make_npc(),
            lambda: make_clue(),
            lambda: make_event(),
            lambda: make_scene(),
            lambda: make_act(),
            lambda: make_campaign(),
        ],
        ids=["WorldRelation", "NPC", "Clue", "Event", "Scene", "Act", "Campaign"],
    )
    def test_dump_load_equal(self, factory):
        model = factory()
        loaded = type(model).model_validate_json(model.model_dump_json())
        assert loaded == model

    def test_campaign_dict_roundtrip_and_chinese_label_in_json(self):
        c = make_campaign()
        data = json.loads(c.model_dump_json())
        c2 = Campaign.model_validate(data)
        assert c2 == c
        assert data["relations"][0]["type"] == "认识"  # 中文标签落 JSON 并可回读

    def test_scriptgraph_roundtrip(self):
        g = ScriptGraph.from_campaign(make_campaign())
        g2 = ScriptGraph.model_validate_json(g.model_dump_json())
        assert g2 == g


# --------------------------------------------------------------------------- #
# ④ 校验失败用例：悬空引用 / 跨幕 id 重复 → ValidationError
# --------------------------------------------------------------------------- #

class TestValidation:
    def test_dangling_scene_npc_raises(self):
        c = make_campaign()
        c.acts[0].scenes[0].npc_ids = ["ghost-npc"]
        with pytest.raises(ValidationError):
            Campaign.model_validate(c.model_dump())

    def test_dangling_act_npc_raises(self):
        c = make_campaign()
        c.acts[0].npc_ids = ["ghost-npc"]
        with pytest.raises(ValidationError):
            Campaign.model_validate(c.model_dump())

    def test_dangling_clue_npc_raises(self):
        c = make_campaign()
        c.clues[0].linked_npc_ids = ["ghost-npc"]
        with pytest.raises(ValidationError):
            Campaign.model_validate(c.model_dump())

    def test_dangling_clue_event_raises(self):
        c = make_campaign()
        c.clues[0].linked_event_ids = ["ghost-event"]
        with pytest.raises(ValidationError):
            Campaign.model_validate(c.model_dump())

    def test_dangling_next_event_raises(self):
        c = make_campaign()
        c.acts[0].scenes[0].events[0].next_event_ids = ["ghost-event"]
        with pytest.raises(ValidationError):
            Campaign.model_validate(c.model_dump())

    def test_duplicate_scene_id_across_acts_raises(self):
        c = make_campaign()
        c.acts = [
            make_act(aid="act-1"),
            make_act(aid="act-2", scenes=[make_scene(sid="sc-1", events=[make_event(eid="ev-9")])]),
        ]
        with pytest.raises(ValidationError):
            Campaign.model_validate(c.model_dump())

    def test_duplicate_event_id_across_acts_raises(self):
        c = make_campaign()
        c.acts = [
            make_act(aid="act-1"),
            make_act(aid="act-2", scenes=[make_scene(sid="sc-2", events=[make_event(eid="ev-1")])]),
        ]
        with pytest.raises(ValidationError):
            Campaign.model_validate(c.model_dump())

    def test_unknown_act_in_npc_acts_roles_raises(self):
        c = make_campaign()
        c.npcs["npc-1"].acts_roles = {"act-99": "x"}
        with pytest.raises(ValidationError):
            Campaign.model_validate(c.model_dump())


# --------------------------------------------------------------------------- #
# ⑤ ScriptGraph：节点/边结构与数量（React Flow 约定）
# --------------------------------------------------------------------------- #

def _rich_campaign():
    """act-1 含 2 场景 3 事件；2 NPC（npc-a 出现在两个场景）；2 线索各指 1 事件。"""
    npc_a = make_npc(nid="npc-a")
    npc_b = make_npc(nid="npc-b")
    sc1 = Scene(
        id="sc-1", title="图书馆",
        setting={"time": "夜", "place": "馆"},
        events=[make_event(eid="ev-1", kind="entry"), make_event(eid="ev-2", kind="trigger")],
        npc_ids=["npc-a"],
    )
    sc2 = Scene(
        id="sc-2", title="码头",
        setting={"time": "晨", "place": "码头"},
        events=[make_event(eid="ev-3", kind="outcome")],
        npc_ids=["npc-a", "npc-b"],
    )
    act1 = Act(id="act-1", title="第一幕", roman="I", summary="s", scenes=[sc1, sc2], npc_ids=["npc-a"])
    clue1 = make_clue(cid="clue-1", linked_events=["ev-2"], linked_npcs=["npc-a"])
    clue2 = make_clue(cid="clue-2", linked_events=["ev-3"], linked_npcs=["npc-b"])
    return Campaign(
        id="c", title="t", premise="p",
        acts=[act1],
        npcs={"npc-a": npc_a, "npc-b": npc_b},
        clues=[clue1, clue2],
        relations=[],
    )


class TestScriptGraph:
    def test_node_and_edge_counts(self):
        g = ScriptGraph.from_campaign(_rich_campaign())
        # 节点：1 act + 2 scene + 3 event + 2 npc + 2 clue = 10
        assert len(g.nodes) == 10
        # 边：2 层级 act→scene + 3 scene→event + 3 npc→scene + 2 clue→event = 10
        assert len(g.edges) == 10

    def test_node_schema_react_flow(self):
        g = ScriptGraph.from_campaign(_rich_campaign())
        types = {"act", "scene", "event", "npc", "clue"}
        for node in g.nodes:
            assert set(node) >= {"id", "type", "label", "data"}
            assert node["type"] in types
            assert isinstance(node["label"], str)
            assert isinstance(node["data"], dict)
        assert {n["type"] for n in g.nodes} == types  # 五类节点全出现

    def test_edge_schema_and_reference_integrity(self):
        g = ScriptGraph.from_campaign(_rich_campaign())
        node_ids = {n["id"] for n in g.nodes}
        edge_types = set()
        for e in g.edges:
            assert set(e) >= {"id", "source", "target", "type"}
            assert e["source"] in node_ids, f"edge {e['id']} 引用未知源节点"
            assert e["target"] in node_ids, f"edge {e['id']} 引用未知目标节点"
            edge_types.add(e["type"])
        # 三类边齐备：层级 contains / npc 出现于 appears / clue 指向 links
        assert {"contains", "appears", "links"} <= edge_types

    def test_npc_appearance_edges_target_scenes(self):
        g = ScriptGraph.from_campaign(_rich_campaign())
        npc_edges = [e for e in g.edges if e["type"] == "appears"]
        assert len(npc_edges) == 3
        for e in npc_edges:
            assert e["source"].startswith("npc:")
            assert e["target"].startswith("scene:")

    def test_clue_edges_target_events(self):
        g = ScriptGraph.from_campaign(_rich_campaign())
        clue_edges = [e for e in g.edges if e["type"] == "links"]
        assert len(clue_edges) == 2
        for e in clue_edges:
            assert e["source"].startswith("clue:")
            assert e["target"].startswith("event:")

    def test_event_hierarchy_edges(self):
        g = ScriptGraph.from_campaign(_rich_campaign())
        contains = [e for e in g.edges if e["type"] == "contains"]
        assert len(contains) == 5  # 2 act→scene + 3 scene→event
        for e in contains:
            assert e["source"].split(":", 1)[0] in ("act", "scene")
            assert e["target"].split(":", 1)[0] in ("scene", "event")


# --------------------------------------------------------------------------- #
# ⑥ CONTEXT.md 词汇表
# --------------------------------------------------------------------------- #

class TestContextGlossary:
    def test_glossary_exists_and_covers_terms(self):
        path = REPO_ROOT / "CONTEXT.md"
        assert path.exists(), f"缺少领域词汇表：{path}"
        text = path.read_text(encoding="utf-8")
        terms = [
            "KP 主控", "NPC subagent", "幕 Act", "场景 Scene", "事件 Event",
            "线索 Clue", "世界知识图谱", "备团笔记", "分幕创作", "剧本节点图",
        ]
        for term in terms:
            assert term in text, f"词汇表缺少术语：{term}"
