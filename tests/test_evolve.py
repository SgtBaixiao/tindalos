"""tindalos.evolve 自进化循环测试（task t6-evolve · 按 G5 评审意见修订）。

覆盖（逐条对齐验收与评审）：
1. 坏剧本（悬空 NPC 引用 + 空 scene + 无链接线索）→ evolve 后结构合法（引用可解析、
   无空 scene、models 校验通过）、consistency 满分；loop_log 记录每轮
   round/applied/failed/score_before/score_after/delta/evidence（自进化简历）；
2. KG 矛盾修复（评审修正）：
   - 倒置窗（valid_to < valid_from）→ 失效 = 空窗（valid_to == valid_from），复评
     consistency 满分；失效标记真幂等（与时间无关，无需注入时钟）；
   - 同对同型重叠窗且一方为永久窗（valid_to=None）→ 重叠对「两条」全部失效
     （仅失效后一条无法消解——评审阻塞项）；
   - kg 公共 API window_overlaps：空窗/倒置窗恒不相交（退化窗语义钉死）；
3. 提前终止：好剧本无修复无提升 → 只跑一轮；
4. 幂等：同输入两次运行（不注入时钟）→ campaign 与 loop_log 完全一致；输入不被修改；
   已修复结果再进化 → KG 修复不再重复应用；
5. t4 管线集成：编译后的 LangGraph app 直接作为 pipeline 传入；
6. 空场景重生成失败（评审修正）：失败单独记入 failed/evidence，applied 仅成功项，
   纯失败轮不触发提前终止（下轮重试）；持续失败跑满 rounds 上限；
7. 事件 id 去重（评审阻塞项）：base 与 base-r 均被占用时递增后缀（-r1/-r2），不无限循环；
8. LLM 建议仅记录 pending，不自动应用；
9. rounds=0（评审钉死语义）：不进化——空 loop_log、剧本原样、仅基线评估 report；
10. 坏 dict 剧本（models 校验失败）→ 宽松构造不 raise（与 t5 同哲学），修复照常；
11. out_path 落盘。
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import pytest
from pydantic import ValidationError

from tindalos import kg
from tindalos.eval_.deterministic import run_deterministic
from tindalos.evolve import evolve
from tindalos.generator import DeterministicGenerator
from tindalos.kg import window_overlaps
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
# 剧本工厂
# --------------------------------------------------------------------------- #

def make_bad_campaign() -> Campaign:
    """坏剧本：悬空 NPC 引用（ghost-npc）+ 空 scene（sc-1 无事件）+ 无链接线索 + 悬空关系端点。"""
    return Campaign.model_construct(
        id="bad-evolve-1",
        title="坏剧本",
        premise="海边小镇的失踪案背后藏着深海的低语。",
        acts=[
            Act.model_construct(
                id="act-1", title="第一幕", roman="I", summary="",
                scenes=[
                    Scene.model_construct(
                        id="sc-1", title="空场景",
                        setting={"time": "深夜", "place": "旧码头"},
                        events=[], npc_ids=["ghost-npc"],
                    ),
                    Scene.model_construct(
                        id="sc-2", title="警局",
                        setting={"time": "清晨", "place": "警局"},
                        events=[
                            Event.model_construct(
                                id="ev-1", title="对峙", kind="entry",
                                description="警长拒绝合作。",
                                conditions=[], next_event_ids=[],
                            )
                        ],
                        npc_ids=[],
                    ),
                ],
                npc_ids=[],
            )
        ],
        npcs={
            "npc-1": NPC.model_construct(
                id="npc-1", name="老渔夫", archetype="向导",
                personality=["谨慎", "话少"], description="码头的老人。", acts_roles={},
            )
        },
        clues=[
            Clue.model_construct(
                id="clue-1", name="渔网", description="旧渔网上的符号。",
                linked_npc_ids=[], linked_event_ids=[], found_at=None,
            )
        ],
        relations=[
            WorldRelation.model_construct(
                source="npc-1", target="clue-1", type=RelationType.POINTS_TO,
                label="指向线索", valid_from="2024-01-01", valid_to=None,
            ),
            WorldRelation.model_construct(
                source="npc-1", target="ghost-npc", type=RelationType.KNOWS,
                label="互相认识", valid_from="2024-01-01", valid_to=None,
            ),
        ],
    )


def make_good_campaign() -> Campaign:
    """好剧本：全部确定性检查通过（total=5.0）。"""
    npcs = {
        "npc-1": NPC(
            id="npc-1", name="老渔夫", archetype="向导",
            personality=["谨慎", "话少"], description="码头的老人。",
            acts_roles={"act-1": "线人"},
        ),
        "npc-2": NPC(
            id="npc-2", name="警长", archetype="权威",
            personality=["固执", "多疑"], description="当地警长。",
            acts_roles={"act-1": "对立方"},
        ),
    }
    acts = [
        Act(id="act-1", title="第一幕", roman="I", summary="", scenes=[
            Scene(
                id="sc-1", title="旧码头", setting={"time": "深夜", "place": "旧码头"},
                events=[
                    Event(
                        id="ev-1", title="发现渔网", kind="entry",
                        description="木桩下发现缠着海草的旧渔网。",
                        conditions=[], next_event_ids=["ev-2", "ev-3"],
                    ),
                    Event(
                        id="ev-2", title="盘问", kind="trigger",
                        description="老渔夫闪烁其词。",
                        conditions=[], next_event_ids=[],
                    ),
                    Event(
                        id="ev-3", title="警长到场", kind="outcome",
                        description="警长赶来打断盘问。",
                        conditions=[], next_event_ids=[],
                    ),
                ],
                npc_ids=["npc-1", "npc-2"],
            ),
            Scene(
                id="sc-2", title="警局", setting={"time": "清晨", "place": "警局"},
                events=[
                    Event(
                        id="ev-4", title="审讯", kind="entry",
                        description="警长单独审讯调查员。",
                        conditions=[], next_event_ids=["ev-5"],
                    ),
                    Event(
                        id="ev-5", title="释放", kind="outcome",
                        description="证据不足，调查员被释放。",
                        conditions=[], next_event_ids=[],
                    ),
                ],
                npc_ids=["npc-2"],
            ),
        ], npc_ids=["npc-1", "npc-2"]),
    ]
    clues = [
        Clue(id="clue-1", name="旧渔网", description="符号与古籍一致。",
             linked_npc_ids=["npc-1"], linked_event_ids=["ev-1"], found_at="sc-1"),
        Clue(id="clue-2", name="审讯记录", description="记录里的潮汐表。",
             linked_event_ids=["ev-4"], found_at="sc-2"),
    ]
    relations = [
        WorldRelation(source="npc-1", target="clue-1", type=RelationType.POINTS_TO,
                      label="指向线索", valid_from="2024-01-01"),
        WorldRelation(source="npc-1", target="npc-2", type=RelationType.KNOWS,
                      label="互相认识", valid_from="2024-01-01"),
    ]
    return Campaign(id="good-evolve-1", title="好剧本", premise="雾镇疑云。",
                    acts=acts, npcs=npcs, clues=clues, relations=relations)


def _campaign_with_only_empty_scene() -> Campaign:
    """唯一缺陷 = 一个空场景（无悬空引用/无线索问题），用于隔离「失败重生成」路径。"""
    return Campaign.model_construct(
        id="only-empty-1", title="唯一缺陷", premise="雾镇。",
        acts=[
            Act.model_construct(
                id="act-1", title="第一幕", roman="I", summary="",
                scenes=[
                    Scene.model_construct(
                        id="sc-1", title="空场景",
                        setting={"time": "深夜", "place": "码头"},
                        events=[], npc_ids=["npc-1"],
                    ),
                ],
                npc_ids=["npc-1"],
            )
        ],
        npcs={
            "npc-1": NPC.model_construct(
                id="npc-1", name="老渔夫", archetype="向导",
                personality=["谨慎"], description="老人。", acts_roles={},
            )
        },
        clues=[],
        relations=[],
    )


class FlakyGenerator:
    """前 fail_first_n 次 generate_scene 调用抛错，之后成功（确定性，可复现）。"""

    def __init__(self, fail_first_n: int = 1, seed: str = "flaky") -> None:
        self._calls = 0
        self._fail_first_n = fail_first_n
        self._inner = DeterministicGenerator(seed=seed)

    def generate_scene(self, act_title, premise, npc_ids):
        self._calls += 1
        if self._calls <= self._fail_first_n:
            raise RuntimeError("生成器暂时不可用（测试注入）")
        return self._inner.generate_scene(act_title, premise, npc_ids)


def _event_ids(campaign) -> set:
    return {e.id for a in campaign.acts for s in a.scenes for e in s.events}


def _assert_structurally_valid(campaign: Campaign) -> None:
    """引用可解析 + 无空 scene + 模型校验通过。"""
    Campaign.model_validate(campaign.model_dump())
    npc_ids = set(campaign.npcs)
    evids = _event_ids(campaign)
    for act in campaign.acts:
        for nid in act.npc_ids:
            assert nid in npc_ids, f"幕 {act.id} 引用未注册 NPC {nid}"
        for scene in act.scenes:
            assert scene.events, f"场景 {scene.id} 为空"
            for nid in scene.npc_ids:
                assert nid in npc_ids, f"场景 {scene.id} 引用未注册 NPC {nid}"
            for ev in scene.events:
                for nxt in ev.next_event_ids:
                    assert nxt in evids, f"事件 {ev.id} 引用未知事件 {nxt}"
    for clue in campaign.clues:
        for ev in clue.linked_event_ids:
            assert ev in evids, f"线索 {clue.id} 引用未知事件 {ev}"


# --------------------------------------------------------------------------- #
# 1. 坏剧本 → 确定性修复闭环收敛
# --------------------------------------------------------------------------- #

def test_evolve_fixes_bad_campaign_and_improves_consistency():
    bad = make_bad_campaign()
    world = kg.build_from_campaign(bad)
    gen = DeterministicGenerator(seed="evolve-test")
    before = run_deterministic(bad, world)

    result = evolve(bad, world, gen, run_deterministic, rounds=3)
    evolved = result["campaign"]

    # 结构合法：引用可解析、无空 scene、模型校验通过
    _assert_structurally_valid(evolved)
    assert "ghost-npc" in evolved.npcs, "悬空 NPC 已注册"
    assert evolved.npcs["ghost-npc"].name != ""

    # consistency 分提升
    after = run_deterministic(evolved, kg.build_from_campaign(evolved))
    assert after["dims"]["consistency"]["score"] > before["dims"]["consistency"]["score"]
    assert after["dims"]["consistency"]["score"] == 5
    assert result["report"]["table"]["consistency"]["score"] == 5

    # 自进化简历：loop_log 每轮含 applied/delta/evidence（+ failed）
    assert result["loop_log"], "loop_log 非空"
    fix_rounds = [e for e in result["loop_log"] if e["applied"]]
    assert fix_rounds, "存在应用了修复的轮次"
    for entry in result["loop_log"]:
        assert set(entry) >= {"round", "applied", "failed", "score_before",
                              "score_after", "delta", "evidence"}
        assert entry["delta"] == round(entry["score_after"] - entry["score_before"], 2)
        assert isinstance(entry["evidence"], list) and entry["evidence"]
    assert any(e["delta"] > 0 for e in result["loop_log"]), "存在分数提升轮"
    assert len(result["loop_log"]) <= 3, "rounds 上限内收敛"


# --------------------------------------------------------------------------- #
# 2. KG 矛盾关系：倒置窗 → 空窗失效（评审修正：真幂等，无时间依赖）
# --------------------------------------------------------------------------- #

def test_evolve_expires_inverted_kg_relation():
    cam = make_good_campaign()
    cam.relations.append(
        WorldRelation(
            source="npc-1", target="clue-1", type=RelationType.LEARNS,
            label="获知", valid_from="2025-06-01", valid_to="2024-01-01",  # 倒置窗
        )
    )
    before = run_deterministic(cam, kg.build_from_campaign(cam))
    assert before["dims"]["consistency"]["score"] < 5, "前置：倒置窗导致一致性扣分"

    result = evolve(cam, None, DeterministicGenerator(seed="kg"), run_deterministic, rounds=3)
    evolved = result["campaign"]
    # 矛盾关系已失效：空窗（valid_to == valid_from）——稳定状态，与时间无关（评审：非时间戳相等）
    expired = [r for r in evolved.relations
               if r.source == "npc-1" and r.target == "clue-1" and r.type == RelationType.LEARNS]
    assert expired and expired[0].valid_to == expired[0].valid_from
    # 复评：KG 无矛盾 → consistency 满分
    after = run_deterministic(evolved, kg.build_from_campaign(evolved))
    assert after["dims"]["consistency"]["score"] == 5
    assert any("失效" in a for e in result["loop_log"] for a in e["applied"])


def test_evolve_expires_both_windows_of_overlap_with_permanent():
    """同对同型重叠窗且一方 valid_to=None（永久窗）：重叠对「两条」全部失效（评审阻塞项）。"""
    cam = make_good_campaign()
    cam.relations.append(
        WorldRelation(source="npc-1", target="npc-2", type=RelationType.CAUSES,
                      label="旧怨", valid_from="2024-01-01", valid_to=None)  # 永久窗
    )
    cam.relations.append(
        WorldRelation(source="npc-1", target="npc-2", type=RelationType.CAUSES,
                      label="和解", valid_from="2025-01-01", valid_to="2025-06-01")  # 落在永久窗内
    )
    before = run_deterministic(cam, kg.build_from_campaign(cam))
    assert before["dims"]["consistency"]["score"] < 5, "前置：永久窗 + 有限窗重叠"

    result = evolve(cam, None, DeterministicGenerator(seed="kg"), run_deterministic, rounds=3)
    evolved = result["campaign"]
    overlap_pair = [r for r in evolved.relations
                    if r.source == "npc-1" and r.target == "npc-2" and r.type == RelationType.CAUSES]
    assert len(overlap_pair) == 2
    assert all(r.valid_to == r.valid_from for r in overlap_pair), "重叠对两条全部失效（空窗）"
    # 重建 world 后 KG 无矛盾，复评 consistency 满分
    assert not kg.build_from_campaign(evolved).consistency_check()
    after = run_deterministic(evolved, kg.build_from_campaign(evolved))
    assert after["dims"]["consistency"]["score"] == 5
    assert any("失效" in a for e in result["loop_log"] for a in e["applied"])


def test_kg_window_overlaps_public_api_degenerate():
    """kg 公共 window_overlaps：空窗/倒置窗恒不相交（退化窗语义钉死，评审：提升为公共 API）。"""
    assert window_overlaps(("2024-01-01", "2024-01-01"), ("2023-01-01", "2025-01-01")) is False
    assert window_overlaps(("2025-06-01", "2024-01-01"), ("2024-01-01", None)) is False
    assert window_overlaps(("2024-01-01", None), ("2025-01-01", "2025-06-01")) is True
    assert window_overlaps(("2024-01-01", "2024-06-30"), ("2024-06-30", "2024-12-31")) is False


# --------------------------------------------------------------------------- #
# 3. 提前终止
# --------------------------------------------------------------------------- #

def test_evolve_early_termination_on_healthy_campaign():
    good = make_good_campaign()
    result = evolve(
        good, kg.build_from_campaign(good),
        DeterministicGenerator(seed="healthy"), run_deterministic, rounds=5,
    )
    # 好剧本：第一轮无修复且无提升 → 提前终止，只记录一轮
    assert len(result["loop_log"]) == 1
    entry = result["loop_log"][0]
    assert entry["applied"] == []
    assert entry["failed"] == []
    assert entry["score_before"] == 5.0
    assert entry["score_after"] == 5.0
    assert entry["delta"] == 0.0
    assert entry["round"] == 1
    # 输入未被修改
    assert good.acts[0].scenes[0].events[0].id == "ev-1"


# --------------------------------------------------------------------------- #
# 4. 幂等（不注入时钟，证明失效标记与时间无关）+ 输入不被修改
# --------------------------------------------------------------------------- #

def test_evolve_idempotent_same_input_same_result():
    gen = DeterministicGenerator(seed="evolve-test")
    r1 = evolve(make_bad_campaign(), None, gen, run_deterministic, rounds=3)
    r2 = evolve(make_bad_campaign(), None, gen, run_deterministic, rounds=3)
    assert r1["campaign"].model_dump(mode="json") == r2["campaign"].model_dump(mode="json")
    assert r1["loop_log"] == r2["loop_log"]
    assert r1["report"]["total"] == r2["report"]["total"]


def test_evolve_kg_fix_truly_idempotent_without_clock():
    """失效标记真幂等：不注入时钟两次运行结果一致；已修复结果再进化不再重复应用失效修复。"""
    cam = make_good_campaign()
    cam.relations.append(
        WorldRelation(source="npc-1", target="npc-2", type=RelationType.CAUSES,
                      label="旧怨", valid_from="2024-01-01", valid_to=None)
    )
    cam.relations.append(
        WorldRelation(source="npc-1", target="npc-2", type=RelationType.CAUSES,
                      label="和解", valid_from="2025-01-01", valid_to="2025-06-01")
    )
    r1 = evolve(cam, None, DeterministicGenerator(seed="kg"), run_deterministic, rounds=3)
    r2 = evolve(cam, None, DeterministicGenerator(seed="kg"), run_deterministic, rounds=3)
    assert r1["campaign"].model_dump(mode="json") == r2["campaign"].model_dump(mode="json")
    assert r1["loop_log"] == r2["loop_log"]
    # 再进化一次已修复的结果：KG 修复不再重复应用（空窗守卫 → 真幂等）
    r3 = evolve(r1["campaign"], None, DeterministicGenerator(seed="kg"), run_deterministic, rounds=3)
    kg_fixes = [a for e in r3["loop_log"] for a in e["applied"] if "失效" in a]
    assert kg_fixes == []


def test_evolve_does_not_mutate_input_campaign():
    bad = make_bad_campaign()
    evolve(bad, None, DeterministicGenerator(seed="evolve-test"), run_deterministic, rounds=3)
    # 输入剧本保持原样（空 scene 未被填、悬空引用未注册）
    assert bad.acts[0].scenes[0].events == []
    assert "ghost-npc" not in bad.npcs


# --------------------------------------------------------------------------- #
# 5. t4 管线集成：编译后的 LangGraph app 直接作为 pipeline 传入
# --------------------------------------------------------------------------- #

def test_evolve_accepts_langgraph_pipeline_app(tmp_path):
    from langgraph.checkpoint.sqlite import SqliteSaver
    from langgraph.store.memory import InMemoryStore

    from tindalos.config import Settings
    from tindalos.pipeline import build_pipeline

    settings = Settings(
        llm_enabled=False,
        checkpoint_dir=tmp_path / "checkpoints",
        store_dir=tmp_path / "store",
    )
    with SqliteSaver.from_conn_string(str(tmp_path / "cp.db").replace("\\", "/")) as cp:
        app = build_pipeline(settings=settings, checkpointer=cp, store=InMemoryStore())
        result = evolve(make_bad_campaign(), None, app, run_deterministic, rounds=3)
    evolved = result["campaign"]
    _assert_structurally_valid(evolved)
    assert "ghost-npc" in evolved.npcs
    # 空 scene 经「pipeline 局部重生成」补上了事件
    assert all(scene.events for act in evolved.acts for scene in act.scenes)


# --------------------------------------------------------------------------- #
# 6. 空场景重生成失败：单记 failed/evidence，applied 仅成功项，纯失败轮不收敛
# --------------------------------------------------------------------------- #

def test_evolve_regen_failure_recorded_separately_and_retried():
    cam = _campaign_with_only_empty_scene()
    flaky = FlakyGenerator(fail_first_n=1)
    result = evolve(cam, None, flaky, run_deterministic, rounds=3)
    log = result["loop_log"]

    # 第 1 轮：重生成失败 → applied 为空、failed 单独记录（评审：applied 仅成功项）
    assert log[0]["applied"] == []
    assert log[0]["failed"], "失败重生成应单独记入 failed"
    assert any("失败" in f for f in log[0]["failed"])
    assert any("重生成" in f for f in log[0]["failed"])
    # 失败也进入 evidence
    assert any("失败" in e for e in log[0]["evidence"])
    # 纯失败轮不触发提前终止 → 第 2 轮重试成功
    assert any(e["applied"] for e in log), "后续轮重试成功"
    success_round = next(e for e in log if e["applied"])
    assert any("重生成空场景 sc-1" in a for a in success_round["applied"])
    # 最终结构合法
    _assert_structurally_valid(result["campaign"])


def test_evolve_persistent_failure_runs_to_rounds_cap():
    cam = _campaign_with_only_empty_scene()
    flaky = FlakyGenerator(fail_first_n=999)
    result = evolve(cam, None, flaky, run_deterministic, rounds=3)
    log = result["loop_log"]
    assert len(log) == 3, "纯失败轮不触发收敛 → 跑满 rounds 上限"
    assert all(e["applied"] == [] for e in log)
    assert all(e["failed"] for e in log)
    assert result["campaign"].acts[0].scenes[0].events == [], "未成功修复则场景保持空"


# --------------------------------------------------------------------------- #
# 7. 事件 id 去重：base 与 base-r 均被占用 → 递增后缀，不无限循环（评审阻塞项）
# --------------------------------------------------------------------------- #

def test_evolve_regen_event_id_suffix_no_infinite_loop():
    bad = make_bad_campaign()
    # 让非空场景 sc-2 占用「空场景 sc-1 重生成时的 base 与 base-r」两个 id
    bad.acts[0].scenes[1].events = [
        Event(id="sc-1-ev-1", title="占用A", kind="entry",
              description="x", conditions=[], next_event_ids=[]),
        Event(id="sc-1-ev-1-r", title="占用B", kind="trigger",
              description="x", conditions=[], next_event_ids=[]),
    ]
    result = evolve(bad, None, DeterministicGenerator(seed="suffix"), run_deterministic, rounds=3)
    evolved = result["campaign"]
    _assert_structurally_valid(evolved)
    evids = _event_ids(evolved)
    assert "sc-1-ev-1-r1" in evids, "递增后缀生效（base 与 base-r 均被占用）"
    assert len(evids) == len(set(evids)), "事件 id 全局唯一"


# --------------------------------------------------------------------------- #
# 8. LLM 建议仅记录 pending，不自动应用
# --------------------------------------------------------------------------- #

GOOD_JSON = """{
  "structural": {"score": 4, "comment": "结构完整", "suggestion": "补充第三幕"},
  "consistency": {"score": 5, "comment": "一致", "suggestion": "无"},
  "depth": {"score": 3, "comment": "尚可", "suggestion": "丰富 NPC 刻画"},
  "playability": {"score": 4, "comment": "可玩", "suggestion": "增加分支选择"}
}"""


class StubClient:
    def __init__(self, text):
        self.text = text

    def __call__(self, messages):
        return self.text


def test_evolve_judge_suggestions_recorded_as_pending_only():
    from tindalos.config import Settings
    from tindalos.eval_.judge import LLMJudge

    judge = LLMJudge(settings=Settings(llm_enabled=True), client=StubClient(GOOD_JSON))
    good = make_good_campaign()
    result = evolve(good, kg.build_from_campaign(good), DeterministicGenerator(seed="j"),
                    run_deterministic, rounds=2, judge=judge)

    # LLM 建议进入 pending（人审待定），未被自动应用
    assert result["pending"], "LLM 建议应记录为 pending"
    assert all(p["status"] == "pending" for p in result["pending"])
    assert all("suggestion" in p and p["suggestion"] for p in result["pending"])
    # 无任何修复被应用（建议不自动应用 → 好剧本只跑一轮即收敛）
    assert all(not e["applied"] for e in result["loop_log"])
    assert result["loop_log"][0]["pending"], "每轮 pending 记录在 loop_log"

    # judge 关闭时 pending 为空
    result_off = evolve(good, None, DeterministicGenerator(seed="j"), run_deterministic, rounds=2)
    assert result_off["pending"] == []


# --------------------------------------------------------------------------- #
# 9. rounds=0：不进化（评审钉死语义：0=不进化，仅基线评估）
# --------------------------------------------------------------------------- #

def test_evolve_rounds_zero_no_evolution():
    bad = make_bad_campaign()
    result = evolve(bad, None, DeterministicGenerator(seed="evolve-test"),
                    run_deterministic, rounds=0)
    assert result["loop_log"] == []
    assert result["pending"] == []
    assert "ghost-npc" not in result["campaign"].npcs, "rounds=0 不应用修复"
    assert result["campaign"].acts[0].scenes[0].events == [], "rounds=0 空场景保持空"
    assert result["report"], "rounds=0 仍产出基线评估 report"
    assert result["report"]["total"] > 0
    # 负数轮数钳制为 0（同样不进化）
    result_neg = evolve(bad, None, DeterministicGenerator(seed="evolve-test"),
                        run_deterministic, rounds=-3)
    assert result_neg["loop_log"] == []
    assert "ghost-npc" not in result_neg["campaign"].npcs


# --------------------------------------------------------------------------- #
# 10. 坏 dict 剧本 → 宽松构造不 raise（与 t5 同哲学），修复照常
# --------------------------------------------------------------------------- #

def test_evolve_accepts_invalid_dict_campaign_loosely():
    raw = make_bad_campaign().model_dump(mode="json")
    raw["acts"][0]["scenes"].append(
        {  # 悬空引用 → models 校验失败
            "id": "sc-99", "title": "额外场景",
            "setting": {"time": "午夜", "place": "巷口"},
            "events": [], "npc_ids": ["no-such-npc-99"],
        }
    )
    with pytest.raises(ValidationError):
        Campaign.model_validate(raw)  # 前置：dict 确实无法通过 models 校验

    result = evolve(raw, None, DeterministicGenerator(seed="loose"), run_deterministic, rounds=3)
    evolved = result["campaign"]
    assert "no-such-npc-99" in evolved.npcs, "宽松构造 + 悬空修复照常工作"
    _assert_structurally_valid(evolved)


# --------------------------------------------------------------------------- #
# 11. out_path 落盘
# --------------------------------------------------------------------------- #

def test_evolve_out_path_writes_report(tmp_path):
    out = tmp_path / "evolve_report.json"
    result = evolve(make_bad_campaign(), None, DeterministicGenerator(seed="evolve-test"),
                    run_deterministic, rounds=3, out_path=str(out))
    assert out.exists() and out.stat().st_size > 0
    import json
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["campaign"]["id"] == "bad-evolve-1"
    assert payload["loop_log"] == result["loop_log"]
    assert payload["report"]["total"] == result["report"]["total"]


def test_evolve_carries_schema_err_into_evidence():
    """G5 复审回归：含未知键的 dict 进 evolve，第 1 轮 loop_log 必须携带 schema 漂移信号。"""
    from tindalos.evolve import evolve
    from tindalos.generator import DeterministicGenerator
    from tindalos.pipeline import build_pipeline, run_pipeline
    from tindalos.eval_.deterministic import run_deterministic
    from tindalos.config import Settings
    from pathlib import Path

    settings = Settings(checkpoint_dir=Path("/tmp/t-check"), store_dir=Path("/tmp/t-store"))
    raw = run_pipeline("测试模组：雾港之夜", settings=settings, generator=DeterministicGenerator())
    raw["rogue_key"] = 123  # extra=forbid 未知键
    out = evolve(raw, world=None, rounds=1, pipeline=build_pipeline(settings=settings),
                 evaluator=run_deterministic, judge=None)
    assert out["loop_log"][0]["schema_err"], "第 1 轮必须携带 schema_err"
    assert "rogue" in out["loop_log"][0]["schema_err"] or "extra" in out["loop_log"][0]["schema_err"].lower() \
        or "未知键" in out["loop_log"][0]["schema_err"] or "Extra inputs" in out["loop_log"][0]["schema_err"]
