"""tindalos.regenerate 局部节点重生成测试（task t12-regenerate）。

覆盖（逐条对齐验收）：
1. 四类节点各可重生成（id 稳定 / 引用可解析 / models 校验通过 / 未变部分零改动）：
   - scene-*：保留 scene 字段（id/title/setting/npc_ids），事件 id 用 scene.id+递增后缀
     （-ev-N，占用时 -rN），next_event_ids 重建链式；事件 id 与重生成前一致（稳定）；
   - event-*：只重产 description/conditions（id/title/kind/next_event_ids 稳定）；
   - npc-*：只重注入 archetype/personality/description（id/name/acts_roles 稳定）；
   - clue-*：只重产 name/description（id/linked_npc_ids/linked_event_ids/found_at 稳定）；
2. 未知 id → ValueError；CLI 退出码非 0；
3. CLI tindalos regenerate <campaign> --node <id> [--llm] --out <out> 冒烟；
4. 校验失败回滚原样 + UserWarning：外部引用悬空 / 坏生成器（非法 kind / 空事件）；
5. 兼容：dict 输入、ScriptGraph 风格 "kind:id"、pipeline 风格 id（act-1-scene-1）；
6. 确定性幂等：同输入两次运行结果一致；
7. evolve 改 import 公共 regenerate_scene_events（集成断言）+ 事件 id 递增后缀不无限循环。
"""
import json
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import pytest
from typer.testing import CliRunner

from tindalos.cli import app
from tindalos.generator import DeterministicGenerator
from tindalos.kg import build_from_campaign
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
from tindalos.regenerate import regenerate_node, regenerate_scene_events

GEN = DeterministicGenerator(seed="regen-test")
runner = CliRunner()


# --------------------------------------------------------------------------- #
# 剧本工厂
# --------------------------------------------------------------------------- #

def _dump(cam: Campaign) -> dict:
    return cam.model_dump(mode="json")


def make_campaign(style: str = "spec") -> Campaign:
    """style="spec"：spec 风格 id（scene-1/event-1/npc-1/clue-1）；
    style="scene"：场景事件 id 用 scene.id 模式（scene-1-ev-N），供场景重生成 id 稳定测试。
    """
    npcs = {
        "npc-1": NPC(id="npc-1", name="老渔夫", archetype="向导",
                     personality=["谨慎", "话少"], description="码头的老人。",
                     acts_roles={"act-1": "线人"}),
        "npc-2": NPC(id="npc-2", name="警长", archetype="权威",
                     personality=["固执", "多疑"], description="当地警长。",
                     acts_roles={"act-1": "对立方"}),
    }
    if style == "scene":
        scene1_events = [
            Event(id="scene-1-ev-1", title="抵达现场", kind="entry",
                  description="众人抵达旧码头。", conditions=[],
                  next_event_ids=["scene-1-ev-2"]),
            Event(id="scene-1-ev-2", title="发现渔网", kind="trigger",
                  description="木桩下的旧渔网。", conditions=["夜色"],
                  next_event_ids=["scene-1-ev-3"]),
            Event(id="scene-1-ev-3", title="事态升级", kind="outcome",
                  description="迷雾涌起。", conditions=[], next_event_ids=[]),
        ]
        scene2_events = [
            Event(id="scene-2-ev-1", title="审讯", kind="entry",
                  description="警长审讯。", conditions=[], next_event_ids=["scene-2-ev-2"]),
            Event(id="scene-2-ev-2", title="释放", kind="outcome",
                  description="证据不足释放。", conditions=[], next_event_ids=[]),
        ]
        clue1_linked, clue2_linked = ["scene-1-ev-1"], ["scene-2-ev-1"]
    else:
        scene1_events = [
            Event(id="event-1", title="抵达现场", kind="entry",
                  description="众人抵达旧码头。", conditions=["夜色"],
                  next_event_ids=["event-2"]),
            Event(id="event-2", title="发现渔网", kind="trigger",
                  description="木桩下的旧渔网。", conditions=[],
                  next_event_ids=["event-3"]),
            Event(id="event-3", title="事态升级", kind="outcome",
                  description="迷雾涌起。", conditions=[], next_event_ids=[]),
        ]
        scene2_events = [
            Event(id="event-4", title="审讯", kind="entry",
                  description="警长审讯。", conditions=[], next_event_ids=["event-5"]),
            Event(id="event-5", title="释放", kind="outcome",
                  description="证据不足释放。", conditions=[], next_event_ids=[]),
        ]
        clue1_linked, clue2_linked = ["event-1"], ["event-4"]
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
             linked_npc_ids=["npc-1"], linked_event_ids=clue1_linked, found_at="scene-1"),
        Clue(id="clue-2", name="审讯记录", description="记录里的潮汐表。",
             linked_npc_ids=[], linked_event_ids=clue2_linked, found_at="scene-2"),
    ]
    relations = [
        WorldRelation(source="npc-1", target="clue-1", type=RelationType.POINTS_TO,
                      label="指向线索", valid_from="2024-01-01"),
        WorldRelation(source="npc-1", target="npc-2", type=RelationType.KNOWS,
                      label="互相认识", valid_from="2024-01-01"),
    ]
    return Campaign(id="regen-test-1", title="雾港之夜",
                    premise="海边小镇的失踪案背后藏着深海的低语。",
                    acts=acts, npcs=npcs, clues=clues, relations=relations)


def _assert_valid(cam: Campaign) -> None:
    """结构合法：models 校验通过 + 引用可解析 + world 一致。"""
    Campaign.model_validate(_dump(cam))
    evids = {e.id for a in cam.acts for s in a.scenes for e in s.events}
    npc_ids = set(cam.npcs)
    act_ids = {a.id for a in cam.acts}
    for act in cam.acts:
        for nid in act.npc_ids:
            assert nid in npc_ids, f"幕 {act.id} 引用未注册 NPC {nid}"
        for scene in act.scenes:
            for nid in scene.npc_ids:
                assert nid in npc_ids, f"场景 {scene.id} 引用未注册 NPC {nid}"
            for ev in scene.events:
                for nxt in ev.next_event_ids:
                    assert nxt in evids, f"事件 {ev.id} 引用未知事件 {nxt}"
    for clue in cam.clues:
        for ev in clue.linked_event_ids:
            assert ev in evids, f"线索 {clue.id} 引用未知事件 {ev}"
        for n in clue.linked_npc_ids:
            assert n in npc_ids, f"线索 {clue.id} 引用未注册 NPC {n}"
    for npc in cam.npcs.values():
        for aid in npc.acts_roles:
            assert aid in act_ids, f"NPC {npc.id} 引用未知幕 {aid}"
    assert not build_from_campaign(cam).consistency_check(), "world 一致性"


def _find_scene(cam: Campaign, sid: str) -> Scene:
    return next(s for a in cam.acts for s in a.scenes if s.id == sid)


def _find_event(cam: Campaign, eid: str) -> Event:
    return next(e for a in cam.acts for s in a.scenes for e in s.events if e.id == eid)


# --------------------------------------------------------------------------- #
# 1a. scene-*：保留字段 + id 稳定 + next_event_ids 重建 + 未变部分零改动
# --------------------------------------------------------------------------- #

def test_regenerate_scene_preserves_fields_stable_ids():
    cam = make_campaign(style="scene")
    before = _dump(cam)
    new, applied = regenerate_node(cam, "scene-1", GEN)

    assert applied and any("重生成场景 scene-1" in a for a in applied)
    scene = _find_scene(new, "scene-1")
    # 保留 scene 字段
    assert scene.id == "scene-1"
    assert scene.title == "旧码头"
    assert scene.setting == {"time": "深夜", "place": "旧码头"}
    assert scene.npc_ids == ["npc-1", "npc-2"]
    # 事件 id 稳定（scene.id 模式）+ 链式 next_event_ids 重建
    assert [e.id for e in scene.events] == ["scene-1-ev-1", "scene-1-ev-2", "scene-1-ev-3"]
    assert scene.events[0].next_event_ids == ["scene-1-ev-2", "scene-1-ev-3"]
    assert scene.events[1].next_event_ids == ["scene-1-ev-3"]
    assert scene.events[2].next_event_ids == []
    # 内容已重产（来自生成器草案，非原稿）
    assert scene.events[0].description != "众人抵达旧码头。"
    _assert_valid(new)
    # 未变部分零改动：其他场景 / NPC / 线索 / 关系
    after = _dump(new)
    assert after["acts"][0]["scenes"][1] == before["acts"][0]["scenes"][1]
    assert after["npcs"] == before["npcs"]
    assert after["clues"] == before["clues"]          # clue-1 → scene-1-ev-1 仍可解析
    assert after["relations"] == before["relations"]


def test_regenerate_scene_spec_style_ids_renumbered():
    """spec 风格：scene-1 原事件 event-1..3 无外部引用 → 重产为 scene-1-ev-N，仍结构合法。"""
    cam = make_campaign()
    cam.clues[0].linked_event_ids = []  # 解除外部引用（悬空回滚路径由独立测试覆盖）
    new, applied = regenerate_node(cam, "scene-1", GEN)
    assert applied
    scene = _find_scene(new, "scene-1")
    assert [e.id for e in scene.events] == ["scene-1-ev-1", "scene-1-ev-2", "scene-1-ev-3"]
    _assert_valid(new)


# --------------------------------------------------------------------------- #
# 1b. event-*：只重产 description/conditions
# --------------------------------------------------------------------------- #

def test_regenerate_event_only_description_and_conditions():
    cam = make_campaign()
    before = _dump(cam)
    new, applied = regenerate_node(cam, "event-1", GEN)

    assert applied and any("重生成事件 event-1" in a for a in applied)
    ev = _find_event(new, "event-1")
    # id/title/kind/next_event_ids 稳定
    assert ev.id == "event-1"
    assert ev.title == "抵达现场"
    assert ev.kind == "entry"
    assert ev.next_event_ids == ["event-2"]
    # description/conditions 已重产（生成器草案 entry 无条件 → 条件被重产为 []）
    assert ev.description != "众人抵达旧码头。"
    assert ev.conditions == []
    _assert_valid(new)
    # 未变部分零改动
    after = _dump(new)
    assert after["acts"][0]["scenes"][0]["events"][1] == before["acts"][0]["scenes"][0]["events"][1]
    assert after["acts"][0]["scenes"][1] == before["acts"][0]["scenes"][1]
    assert after["npcs"] == before["npcs"]
    assert after["clues"] == before["clues"]
    assert after["relations"] == before["relations"]


# --------------------------------------------------------------------------- #
# 1c. npc-*：只重注入 archetype/personality/description
# --------------------------------------------------------------------------- #

def test_regenerate_npc_reinjects_persona_only():
    cam = make_campaign()
    before = _dump(cam)
    new, applied = regenerate_node(cam, "npc-1", GEN)

    assert applied and any("重注入 NPC npc-1" in a for a in applied)
    npc = new.npcs["npc-1"]
    # id/name/acts_roles 稳定
    assert npc.id == "npc-1"
    assert npc.name == "老渔夫"
    assert npc.acts_roles == {"act-1": "线人"}
    # archetype/personality/description 与生成器草案一致（重注入）
    drafts = GEN.generate_npcs(cam.premise, len(cam.npcs))
    src = next(d for d in drafts if d["id"] == "npc-1")
    assert npc.archetype == src["archetype"]
    assert npc.personality == src["personality"]
    assert npc.description == src["description"]
    assert npc.archetype != "向导", "原型已重注入（生成器素材池不含「向导」）"
    _assert_valid(new)
    # 未变部分零改动
    after = _dump(new)
    assert after["acts"] == before["acts"]
    assert after["clues"] == before["clues"]
    assert after["relations"] == before["relations"]
    assert after["npcs"]["npc-2"] == before["npcs"]["npc-2"]


# --------------------------------------------------------------------------- #
# 1d. clue-*：只重产 name/description
# --------------------------------------------------------------------------- #

def test_regenerate_clue_renames_and_redescribes():
    cam = make_campaign()
    before = _dump(cam)
    new, applied = regenerate_node(cam, "clue-1", GEN)

    assert applied and any("重生成线索 clue-1" in a for a in applied)
    clue = next(c for c in new.clues if c.id == "clue-1")
    # id/linked_*/found_at 稳定
    assert clue.id == "clue-1"
    assert clue.linked_npc_ids == ["npc-1"]
    assert clue.linked_event_ids == ["event-1"]
    assert clue.found_at == "scene-1"
    # name/description 已重产
    assert clue.name != "旧渔网" and clue.name.endswith("的线索")
    assert clue.description != "符号与古籍一致。"
    _assert_valid(new)
    # 未变部分零改动
    after = _dump(new)
    assert after["acts"] == before["acts"]
    assert after["npcs"] == before["npcs"]
    assert after["relations"] == before["relations"]
    assert after["clues"][1] == before["clues"][1]


# --------------------------------------------------------------------------- #
# 2. 未知 id → ValueError
# --------------------------------------------------------------------------- #

def test_regenerate_unknown_id_raises_value_error():
    cam = make_campaign()
    for bad in ("npc-99", "scene-99", "event-99", "clue-99", "bogus", "act-1"):
        with pytest.raises(ValueError, match="未知"):
            regenerate_node(cam, bad, GEN)
    # 输入未被修改
    assert _dump(cam)["acts"][0]["scenes"][0]["events"][0]["description"] == "众人抵达旧码头。"


# --------------------------------------------------------------------------- #
# 3. CLI 冒烟
# --------------------------------------------------------------------------- #

def _write_campaign(tmp_path, cam: Campaign) -> Path:
    p = tmp_path / "campaign.json"
    p.write_text(json.dumps(_dump(cam), ensure_ascii=False), encoding="utf-8")
    return p


def test_cli_regenerate_smoke(tmp_path):
    p = _write_campaign(tmp_path, make_campaign(style="scene"))
    out = tmp_path / "regenerated.json"
    r = runner.invoke(app, ["regenerate", str(p), "--node", "scene-1", "--out", str(out)])
    assert r.exit_code == 0, r.output
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    Campaign.model_validate(data)  # 结果结构合法
    evids = [e["id"] for e in data["acts"][0]["scenes"][0]["events"]]
    assert evids == ["scene-1-ev-1", "scene-1-ev-2", "scene-1-ev-3"]
    assert "重生成场景 scene-1" in r.output


def test_cli_regenerate_llm_flag_falls_back(tmp_path):
    # TINDALOS_LLM_ENABLED 未设置 → --llm 回退确定性生成器，零网络不失败
    p = _write_campaign(tmp_path, make_campaign())
    out = tmp_path / "r.json"
    r = runner.invoke(app, ["regenerate", str(p), "--node", "npc-1", "--out", str(out), "--llm"])
    assert r.exit_code == 0, r.output
    assert out.exists()


def test_cli_regenerate_unknown_id_exit_nonzero(tmp_path):
    p = _write_campaign(tmp_path, make_campaign())
    r = runner.invoke(app, ["regenerate", str(p), "--node", "npc-99",
                            "--out", str(tmp_path / "r.json")])
    assert r.exit_code != 0
    assert "错误" in r.output


def test_cli_regenerate_rollback_warns_keeps_input(tmp_path):
    # spec 风格：scene 重生成使 clue-1 引用悬空 → 校验失败回滚原样 + 告警（输出即输入）
    p = _write_campaign(tmp_path, make_campaign())
    out = tmp_path / "r.json"
    r = runner.invoke(app, ["regenerate", str(p), "--node", "scene-1", "--out", str(out)])
    assert r.exit_code == 0, r.output
    assert "回滚" in r.output
    data = json.loads(out.read_text(encoding="utf-8"))
    assert [e["id"] for e in data["acts"][0]["scenes"][0]["events"]] == ["event-1", "event-2", "event-3"]


# --------------------------------------------------------------------------- #
# 4. 校验失败回滚原样 + UserWarning
# --------------------------------------------------------------------------- #

class BrokenKindGenerator:
    """generate_scene 返回非法 kind → Event 构造失败（重生成失败回滚）。"""

    def generate_scene(self, act_title, premise, npc_ids):
        return {"setting": {"time": "深夜", "place": "巷口"}, "events": [
            {"id": "x-1", "title": "t", "kind": "bogus", "description": "d",
             "conditions": [], "next_event_ids": []},
        ]}


class EmptySceneGenerator:
    """generate_scene 返回空事件序列 → 场景重生成失败回滚。"""

    def generate_scene(self, act_title, premise, npc_ids):
        return {"setting": {}, "events": []}


def test_regenerate_scene_rolls_back_on_broken_generator():
    cam = make_campaign(style="scene")
    before = _dump(cam)
    with pytest.warns(UserWarning, match="回滚"):
        new, applied = regenerate_node(cam, "scene-1", BrokenKindGenerator())
    assert applied == []
    assert _dump(new) == before, "失败回滚为输入原样"


def test_regenerate_scene_rolls_back_on_empty_events():
    cam = make_campaign(style="scene")
    before = _dump(cam)
    with pytest.warns(UserWarning, match="回滚"):
        new, applied = regenerate_node(cam, "scene-1", EmptySceneGenerator())
    assert applied == []
    assert _dump(new) == before


def test_regenerate_rolls_back_when_external_refs_dangle():
    # spec 风格：clue-1 引用 event-1；重生成 scene-1 后 event-1 消失 → models 校验失败回滚
    cam = make_campaign()
    before = _dump(cam)
    with pytest.warns(UserWarning, match="回滚"):
        new, applied = regenerate_node(cam, "scene-1", GEN)
    assert applied == []
    assert _dump(new) == before


# --------------------------------------------------------------------------- #
# 5. 兼容：dict 输入 / ScriptGraph 风格 id / pipeline 风格 id
# --------------------------------------------------------------------------- #

def test_regenerate_accepts_dict_input():
    raw = make_campaign(style="scene").model_dump(mode="json")
    new, applied = regenerate_node(raw, "scene-1", GEN)
    assert isinstance(new, Campaign)
    assert applied
    _assert_valid(new)


def test_regenerate_kind_prefixed_scriptgraph_id():
    cam = make_campaign(style="scene")
    new, applied = regenerate_node(cam, "scene:scene-1", GEN)
    assert applied and any("scene-1" in a for a in applied)
    assert _find_scene(new, "scene-1").events[0].id == "scene-1-ev-1"


def test_regenerate_pipeline_style_ids():
    """pipeline 产出的 id（act-1-scene-1 / act-1-scene-1-ev-1）同样可重生成。"""
    npcs = {"npc-1": NPC(id="npc-1", name="老渔夫", archetype="向导",
                         personality=["谨慎"], description="老人。", acts_roles={})}
    acts = [Act(id="act-1", title="第一幕", roman="I", summary="", npc_ids=["npc-1"], scenes=[
        Scene(id="act-1-scene-1", title="旧码头", setting={"time": "深夜", "place": "旧码头"},
              npc_ids=["npc-1"], events=[
                  Event(id="act-1-scene-1-ev-1", title="抵达", kind="entry",
                        description="抵达。", conditions=[], next_event_ids=[]),
              ]),
    ])]
    cam = Campaign(id="pipeline-1", title="雾港", premise="深海低语。",
                   acts=acts, npcs=npcs, clues=[], relations=[])
    new, applied = regenerate_node(cam, "act-1-scene-1", GEN)
    assert applied
    assert [e.id for e in new.acts[0].scenes[0].events] == [
        "act-1-scene-1-ev-1", "act-1-scene-1-ev-2", "act-1-scene-1-ev-3"]
    _assert_valid(new)
    new2, applied2 = regenerate_node(cam, "act-1-scene-1-ev-1", GEN)
    assert applied2 and any("事件 act-1-scene-1-ev-1" in a for a in applied2)
    assert new2.acts[0].scenes[0].events[0].description != "抵达。"
    _assert_valid(new2)


# --------------------------------------------------------------------------- #
# 6. 确定性幂等
# --------------------------------------------------------------------------- #

def test_regenerate_deterministic_idempotent():
    cam = make_campaign(style="scene")
    r1 = regenerate_node(cam, "scene-1", DeterministicGenerator(seed="regen-test"))
    r2 = regenerate_node(cam, "scene-1", DeterministicGenerator(seed="regen-test"))
    assert _dump(r1[0]) == _dump(r2[0])
    assert r1[1] == r2[1]


def test_regenerate_does_not_mutate_input():
    cam = make_campaign(style="scene")
    before = _dump(cam)
    regenerate_node(cam, "scene-1", GEN)
    assert _dump(cam) == before, "输入剧本不被修改"


# --------------------------------------------------------------------------- #
# 7. evolve 集成：公共 regenerate_scene_events + 递增后缀不无限循环
# --------------------------------------------------------------------------- #

def test_evolve_imports_public_regenerate_scene_events():
    from tindalos.evolve import _regenerate_scene

    assert _regenerate_scene is regenerate_scene_events, "evolve 应改 import 公共实现"


def test_regenerate_scene_events_public_api():
    cam = make_campaign(style="scene")
    act, scene = cam.acts[0], cam.acts[0].scenes[0]
    used = {e.id for a in cam.acts for s in a.scenes for e in s.events} - {e.id for e in scene.events}
    n = regenerate_scene_events(GEN, cam, act, scene, used)
    assert n == 3
    assert [e.id for e in scene.events] == ["scene-1-ev-1", "scene-1-ev-2", "scene-1-ev-3"]
    assert scene.events[0].next_event_ids == ["scene-1-ev-2", "scene-1-ev-3"]
    assert scene.events[2].next_event_ids == []


def test_regenerate_scene_event_id_suffix_no_infinite_loop():
    """base 与 base-r 均被占用 → 递增后缀（-r1/-r2），不无限循环（评审阻塞项语义）。"""
    cam = make_campaign(style="scene")
    # 让 scene-2 额外占用 scene-1 重生成事件的 base（scene-1-ev-1）与 base-r（scene-1-ev-1-r），
    # 保留 scene-2 原事件（clue-2 → scene-2-ev-1 引用保持可解析）
    cam.acts[0].scenes[1].events = cam.acts[0].scenes[1].events + [
        Event(id="scene-1-ev-1", title="占用A", kind="entry", description="x",
              conditions=[], next_event_ids=[]),
        Event(id="scene-1-ev-1-r", title="占用B", kind="trigger", description="x",
              conditions=[], next_event_ids=[]),
    ]
    new, applied = regenerate_node(cam, "scene-1", GEN)
    assert applied
    evids = {e.id for a in new.acts for s in a.scenes for e in s.events}
    assert "scene-1-ev-1-r1" in evids, "递增后缀生效（base 与 base-r 均被占用）"
    assert len(evids) == len(set(evids)), "事件 id 全局唯一"
    _assert_valid(new)
