"""tindalos.eval_ 评估模块测试（task t5-eval）。

覆盖：
- rubric：4 维 × {1,5} 锚点；确定性检查清单覆盖 spec 8 项核心检查
- deterministic：坏剧本（悬空引用/空场景）低分且 evidence 命中具体字段；好剧本高分；
  dict 输入经 models 校验失败时仍能给出字段级 evidence
- report：归因四类字段齐全（structure/data/model/evaluation）、judge 状态、judge 合并
- judge：禁用 → judge='none'；合法 JSON → judge='llm'；键缺失/类型错/乱码 → 降级 'none'
"""
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from tindalos import kg
from tindalos.config import Settings
from tindalos.eval_.deterministic import run_deterministic
from tindalos.eval_.judge import JUDGE_PROMPT, LLMJudge, parse_judge_json
from tindalos.eval_.report import eval_report
from tindalos.eval_.rubric import DETERMINISTIC_CHECKS, DIMENSIONS, RUBRIC
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


# --------------------------------------------------------------------------- #
# 工厂：好剧本 / 坏剧本
# --------------------------------------------------------------------------- #

def make_good_campaign() -> Campaign:
    npcs = {
        "npc-1": NPC(
            id="npc-1", name="老渔夫", archetype="向导",
            personality=["谨慎", "话少"],
            description="在码头生活四十年的老渔夫，见过潮汐也见过不该见的东西。",
            acts_roles={"act-1": "线人"},
        ),
        "npc-2": NPC(
            id="npc-2", name="警长", archetype="权威",
            personality=["固执", "多疑"],
            description="当地警长，对超自然事件嗤之以鼻。",
            acts_roles={"act-1": "对立方"},
        ),
    }
    acts = [
        Act(id="act-1", title="第一幕", roman="I", summary="调查员在码头发现旧渔网。", scenes=[
            Scene(id="sc-1", title="旧码头", setting={"time": "深夜", "place": "旧码头"}, events=[
                Event(
                    id="ev-1", title="发现渔网", kind="entry",
                    description="调查员在码头木桩下发现一张缠着海草的旧渔网。",
                    conditions=["调查员持有线索"], next_event_ids=["ev-2", "ev-3"],
                ),
                Event(
                    id="ev-2", title="盘问老渔夫", kind="trigger",
                    description="老渔夫闪烁其词，暗示海里有东西。",
                    conditions=[], next_event_ids=["ev-4"],
                ),
            ], npc_ids=["npc-1", "npc-2"]),
            Scene(id="sc-2", title="警局", setting={"time": "次日清晨", "place": "警局审讯室"}, events=[
                Event(
                    id="ev-3", title="警长对峙", kind="trigger",
                    description="警长拒绝配合，警告调查员离开。",
                    conditions=[], next_event_ids=["ev-4"],
                ),
                Event(
                    id="ev-4", title="真相浮现", kind="outcome",
                    description="渔网上的符号指向深潜者仪式。",
                    conditions=["集齐两条线索"],
                ),
            ], npc_ids=["npc-2"]),
        ]),
    ]
    clues = [
        Clue(
            id="clue-1", name="旧渔网", description="缠着海草的旧渔网，符号与古籍一致。",
            linked_npc_ids=["npc-1"], linked_event_ids=["ev-1"], found_at="sc-1",
        ),
        Clue(
            id="clue-2", name="警长日记", description="日记里夹着一张潮汐表。",
            linked_event_ids=["ev-3"], found_at="sc-2",
        ),
    ]
    relations = [
        WorldRelation(source="npc-1", target="npc-2", type=RelationType.KNOWS, label="互相认识", valid_from="2024-01-01"),
        WorldRelation(source="npc-1", target="clue-1", type=RelationType.LEARNS, label="获知", valid_from="2024-01-01"),
        WorldRelation(source="npc-2", target="clue-2", type=RelationType.POINTS_TO, label="指向", valid_from="2024-01-01"),
    ]
    return Campaign(
        id="good-1", title="深海低语",
        premise="海边小镇接连失踪案背后是深潜者的仪式。",
        acts=acts, npcs=npcs, clues=clues, relations=relations,
    )


def make_bad_campaign() -> Campaign:
    """坏剧本：悬空 NPC/事件引用 + 空场景 + NPC 无 personality + 线索无描述。"""
    return Campaign.model_construct(
        id="bad-1", title="坏剧本", premise="",
        acts=[Act.model_construct(
            id="act-1", title="第一幕", roman="I", summary="", scenes=[
                Scene.model_construct(
                    id="sc-1", title="空场景",
                    setting={"time": "夜", "place": "码头"},
                    events=[], npc_ids=["ghost-npc"],
                ),
            ], npc_ids=[],
        )],
        npcs={"npc-1": NPC.model_construct(
            id="npc-1", name="老渔夫", archetype="向导",
            personality=[], description="", acts_roles={},
        )},
        clues=[Clue.model_construct(
            id="clue-1", name="渔网", description="",
            linked_npc_ids=[], linked_event_ids=["ghost-ev"], found_at=None,
        )],
        relations=[],
    )


# --------------------------------------------------------------------------- #
# rubric
# --------------------------------------------------------------------------- #

def test_rubric_has_four_dims_with_anchors():
    assert DIMENSIONS == ["structural", "consistency", "depth", "playability"]
    for dim in DIMENSIONS:
        assert 1 in RUBRIC[dim] and 5 in RUBRIC[dim]
        assert isinstance(RUBRIC[dim][1], str) and isinstance(RUBRIC[dim][5], str)
        assert len(RUBRIC[dim][1]) > 5 and len(RUBRIC[dim][5]) > 5


def test_deterministic_checklist_covers_spec_items():
    ids = {c["id"] for c in DETERMINISTIC_CHECKS}
    for cid in [
        "schema_valid", "id_unique", "refs_resolvable", "kg_consistent",
        "act_has_scene", "scene_has_event", "npc_personality", "clue_linked",
    ]:
        assert cid in ids, f"spec 要求的确定性检查缺失：{cid}"
    for c in DETERMINISTIC_CHECKS:
        assert c["dims"], f"检查 {c['id']} 未声明贡献维度"


# --------------------------------------------------------------------------- #
# deterministic：坏剧本低分 + evidence 可定位
# --------------------------------------------------------------------------- #

def test_bad_campaign_low_score_with_locatable_evidence():
    bad = make_bad_campaign()
    world = kg.build_from_campaign(bad)
    res = run_deterministic(bad, world)

    assert res["dims"]["structural"]["score"] <= 2
    assert res["dims"]["consistency"]["score"] <= 2
    assert res["dims"]["depth"]["score"] <= 2
    assert res["total"] <= 3.0

    ev = "\n".join(
        res["dims"]["structural"]["evidence"]
        + res["dims"]["consistency"]["evidence"]
        + res["dims"]["depth"]["evidence"]
    )
    # evidence 命中具体字段 / id
    assert "ghost-npc" in ev            # 悬空 NPC 引用
    assert "ghost-ev" in ev             # 悬空事件引用
    assert "sc-1" in ev                 # 空场景
    assert any("npc_ids" in e or "linked_event_ids" in e or "personality" in e
               for e in res["dims"]["structural"]["evidence"] + res["dims"]["consistency"]["evidence"])


def test_bad_campaign_checks_flag_specific_problems():
    bad = make_bad_campaign()
    world = kg.build_from_campaign(bad)
    res = run_deterministic(bad, world)
    by_id = {c["id"]: c for c in res["checks"]}

    assert by_id["schema_valid"]["passed"] is False          # models 校验拦截悬空引用
    assert by_id["refs_resolvable"]["passed"] is False
    assert "ghost-npc" in by_id["refs_resolvable"]["evidence"]
    assert "ghost-ev" in by_id["refs_resolvable"]["evidence"]
    assert by_id["scene_has_event"]["passed"] is False       # 空场景
    assert by_id["npc_personality"]["passed"] is False       # NPC 无 personality
    assert by_id["kg_consistent"]["passed"] is False         # KG 引用一致性矛盾
    assert by_id["clue_linked"]["passed"] is True            # 有 linked 目标（虽悬空，引用问题由 refs/kg 覆盖）

    assert all({"id", "name", "dims", "passed", "evidence"} <= set(c) for c in res["checks"])


# --------------------------------------------------------------------------- #
# deterministic：好剧本高分 + dict 输入
# --------------------------------------------------------------------------- #

def test_good_campaign_high_score():
    good = make_good_campaign()
    world = kg.build_from_campaign(good)
    res = run_deterministic(good, world)

    assert all(c["passed"] for c in res["checks"])
    assert res["dims"]["structural"]["score"] == 5
    assert res["dims"]["consistency"]["score"] == 5
    assert res["dims"]["depth"]["score"] == 5
    assert res["dims"]["playability"]["score"] == 5
    assert res["total"] == 5.0
    assert res["dims"]["structural"]["evidence"] == []


def test_deterministic_accepts_dict_input_and_reports_field_evidence():
    good = make_good_campaign()
    raw = good.model_dump()
    # 把 sc-1 的事件清空 → 空场景 + clue-1 的 linked_event_ids 悬空
    raw["acts"][0]["scenes"][0]["events"] = []
    res = run_deterministic(raw, None)  # world=None → 自动 build
    by_id = {c["id"]: c for c in res["checks"]}

    assert by_id["schema_valid"]["passed"] is False
    assert "ev-1" in by_id["schema_valid"]["evidence"]       # models ValidationError 字段路径
    assert by_id["scene_has_event"]["passed"] is False
    assert "sc-1" in by_id["scene_has_event"]["evidence"]
    assert res["dims"]["structural"]["score"] <= 3


# --------------------------------------------------------------------------- #
# report：归因四类字段齐全
# --------------------------------------------------------------------------- #

def test_report_attribution_has_four_categories():
    bad = make_bad_campaign()
    world = kg.build_from_campaign(bad)
    det = run_deterministic(bad, world)
    rep = eval_report(bad, world, det, None)

    assert set(rep["attribution"].keys()) == {"structure", "data", "model", "evaluation"}
    for cat in ("structure", "data", "model", "evaluation"):
        entry = rep["attribution"][cat]
        assert {"label", "count", "items"} <= set(entry), f"归因 {cat} 字段不全"
    assert rep["attribution"]["structure"]["count"] >= 1     # schema/空场景 → structure
    assert rep["attribution"]["data"]["count"] >= 1          # 悬空引用 → data
    assert rep["attribution"]["model"]["count"] >= 1         # NPC 无 personality → model
    assert rep["attribution"]["evaluation"]["count"] >= 1    # judge 未参与 → evaluation

    # attribution 各类别均为四类之一
    for cat in rep["attribution"]:
        assert cat in {"structure", "data", "model", "evaluation"}

    assert rep["judge"] == "none"
    assert rep["total"] == det["total"]
    for dim in DIMENSIONS:
        assert {"score", "evidence", "suggestion"} <= set(rep["table"][dim])


def test_report_good_campaign_no_failure_attribution():
    good = make_good_campaign()
    world = kg.build_from_campaign(good)
    det = run_deterministic(good, world)
    rep = eval_report(good, world, det)

    assert rep["attribution"]["structure"]["count"] == 0
    assert rep["attribution"]["data"]["count"] == 0
    assert rep["attribution"]["model"]["count"] == 0
    assert rep["attribution"]["evaluation"]["count"] >= 1   # “全部通过”说明
    assert rep["total"] == 5.0
    assert rep["campaign_id"] == "good-1"


def test_report_uses_judge_scores_when_available():
    good = make_good_campaign()
    world = kg.build_from_campaign(good)
    det = run_deterministic(good, world)
    judge_res = {"judge": "llm", "dims": parse_judge_json(GOOD_JSON)}
    rep = eval_report(good, world, det, judge_res)

    assert rep["judge"] == "llm"
    assert rep["table"]["structural"]["score"] == 4
    assert rep["table"]["structural"]["suggestion"] == "补充第三幕"
    assert rep["table"]["structural"]["comment"] == "结构完整"
    assert rep["total"] == 4.0
    # 确定性 evidence 保留在总表
    assert rep["table"]["structural"]["evidence"] == []


# --------------------------------------------------------------------------- #
# judge：可选路径与降级
# --------------------------------------------------------------------------- #

GOOD_JSON = """{
  "structural": {"score": 4, "comment": "结构完整", "suggestion": "补充第三幕"},
  "consistency": {"score": 5, "comment": "一致", "suggestion": "无"},
  "depth": {"score": 3, "comment": "尚可", "suggestion": "丰富 NPC 刻画"},
  "playability": {"score": 4, "comment": "可玩", "suggestion": "增加分支选择"}
}"""

MISSING_KEY_JSON = (
    '{"structural": {"score": 4, "comment": "缺键"},'
    ' "consistency": {"score": 4, "comment": "x", "suggestion": "y"},'
    ' "depth": {"score": 4, "comment": "x", "suggestion": "y"},'
    ' "playability": {"score": 4, "comment": "x", "suggestion": "y"}}'
)

WRONG_TYPE_JSON = (
    '{"structural": {"score": "四", "comment": "x", "suggestion": "y"},'
    ' "consistency": {"score": 4, "comment": "x", "suggestion": "y"},'
    ' "depth": {"score": 4, "comment": "x", "suggestion": "y"},'
    ' "playability": {"score": 4, "comment": "x", "suggestion": "y"}}'
)


class StubClient:
    def __init__(self, text: str):
        self.text = text

    def __call__(self, messages):
        return self.text


def _det(campaign=None):
    c = campaign or make_good_campaign()
    return run_deterministic(c, kg.build_from_campaign(c))


def test_judge_disabled_returns_none():
    j = LLMJudge(settings=Settings(llm_enabled=False))
    res = j.evaluate(make_good_campaign(), None, _det())
    assert res["judge"] == "none"
    assert res["reason"] == "llm_disabled"


def test_judge_parse_valid_json_and_fences():
    dims = parse_judge_json(GOOD_JSON)
    assert dims is not None
    assert set(dims) == {"structural", "consistency", "depth", "playability"}
    assert set(dims["structural"]) == {"score", "comment", "suggestion"}
    assert dims["structural"] == {"score": 4, "comment": "结构完整", "suggestion": "补充第三幕"}
    # 代码围栏剥离
    assert parse_judge_json("```json\n" + GOOD_JSON + "\n```") == dims


def test_judge_invalid_output_degrades():
    assert parse_judge_json("这不是 JSON 输出") is None
    assert parse_judge_json("") is None
    assert parse_judge_json(MISSING_KEY_JSON) is None      # 键缺失
    assert parse_judge_json(WRONG_TYPE_JSON) is None       # 类型错
    assert parse_judge_json(GOOD_JSON.replace('"score": 4', '"score": 6')) is None  # 分数越界


def test_judge_enabled_with_stub_client():
    good = make_good_campaign()
    world = kg.build_from_campaign(good)
    det = _det(good)
    j = LLMJudge(settings=Settings(llm_enabled=True), client=StubClient(GOOD_JSON))
    res = j.evaluate(good, world, det)
    assert res["judge"] == "llm"
    assert res["dims"]["structural"]["score"] == 4
    assert res["dims"]["depth"]["score"] == 3


def test_judge_parse_failure_degrades_to_none():
    j = LLMJudge(settings=Settings(llm_enabled=True), client=StubClient("乱码输出"))
    res = j.evaluate(make_good_campaign(), None, _det())
    assert res["judge"] == "none"
    assert res["reason"] == "parse_failed"


def test_judge_client_error_degrades_to_none():
    def boom(messages):
        raise RuntimeError("network down")

    j = LLMJudge(settings=Settings(llm_enabled=True), client=boom)
    res = j.evaluate(make_good_campaign(), None, _det())
    assert res["judge"] == "none"
    assert "llm_error" in res["reason"]


def test_judge_prompt_requires_four_dims_and_three_keys():
    for dim in DIMENSIONS:
        assert dim in JUDGE_PROMPT
    assert "score" in JUDGE_PROMPT and "comment" in JUDGE_PROMPT and "suggestion" in JUDGE_PROMPT
    assert "JSON" in JUDGE_PROMPT
