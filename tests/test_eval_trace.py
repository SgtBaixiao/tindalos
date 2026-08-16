"""tindalos eval trace 测试（task #43，P0-b 零 LLM）。

覆盖：
- eval_store：roundtrip + append-only（源码零 DELETE、finalize 幂等 no-op、非法状态拒绝）
- run_eval 好剧本：L1/L2 通过、L3(llm_disabled)/L5(manual_only)/L4(no_corpus)/
  L6(no_prior_run) 跳过 → verdict pass + status completed + 零 LLM，trace 可回放
- run_eval 坏剧本：级联门短路 status='short_circuited' verdict='fail'，
  L3/L4/L5 skipped(cascade_gate_failed)，L6 免费重放仍执行
- L6 replay：同 trace 无回归 → passed；构造高分历史 → regression → warning
- 预算门：LLM judge enabled + 极低 max_usd → L3 skipped(budget_exceeded)，judge 零调用
- L3 真实参与：judge enabled + 预算充足 → L3 passed(judge=llm)、llm_calls=1
- L4 假 search_fn 注入：支持 / 不支持(→warning) / 无命中(→no_corpus)
- dict 输入（web POST /api/eval/run 的 history 快照场景）不崩、正常跑完
"""
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from tindalos import eval_store, kg
from tindalos.config import Settings
from tindalos.eval_.judge import LLMJudge
from tindalos.eval_.runner import estimate_usd, run_eval
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
# 夹具
# --------------------------------------------------------------------------- #

def make_settings(tmp_path) -> Settings:
    return Settings(
        llm_enabled=False,
        checkpoint_dir=tmp_path / "checkpoints",
        store_dir=tmp_path / "store",
    )


def make_good_campaign() -> Campaign:
    """好剧本：4 事件 ev-1~ev-4（含 entry 起点），2 线索均可从 entry 可达。"""
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


GOOD_JSON = """{
  "structural": {"score": 4, "comment": "结构完整", "suggestion": "补充第三幕"},
  "consistency": {"score": 5, "comment": "一致", "suggestion": "无"},
  "depth": {"score": 3, "comment": "尚可", "suggestion": "丰富 NPC 刻画"},
  "playability": {"score": 4, "comment": "可玩", "suggestion": "增加分支选择"}
}"""


class StubClient:
    def __init__(self, text: str):
        self.text = text

    def __call__(self, messages):
        return self.text


def _empty_search(query, module_id=None):
    """L4 语料空：任何查询零命中 → no_corpus 降级。"""
    return []


# --------------------------------------------------------------------------- #
# eval_store：roundtrip + append-only
# --------------------------------------------------------------------------- #

def test_store_roundtrip_and_append_only(tmp_path):
    db = eval_store.eval_db_path(make_settings(tmp_path))
    run_id = eval_store.create_run(
        campaign_id="good-1", campaign_title="深海低语",
        params={"module_id": None, "max_usd": 2.0}, db_path=db,
    )
    assert run_id

    run = eval_store.get_run(run_id, db_path=db)
    assert run["status"] == "running"
    assert run["verdict"] is None
    assert run["params"]["max_usd"] == 2.0  # params 解析为 dict
    assert run["campaign_id"] == "good-1"

    n = eval_store.append_annotations(
        run_id,
        [
            {"layer": "L4", "subject_ref": "event:ev-1", "score": 1.0,
             "explanation": "…", "evidence_refs": [{"module_id": "m1", "chunk_index": 0, "score": 0.5}]},
            {"layer": "L4", "subject_ref": "event:ev-2", "score": 0.0,
             "explanation": "…", "evidence_refs": []},
        ],
        db_path=db,
    )
    assert n == 2
    anns = eval_store.list_annotations(run_id, db_path=db)
    assert len(anns) == 2
    assert anns[0]["evidence_refs"][0]["module_id"] == "m1"  # evidence_refs 解析为 list

    ok = eval_store.finalize_run(
        run_id, status="completed", verdict="pass",
        layers={"L1": {"status": "passed"}}, budget_spent_usd=0.0, duration_ms=3,
        db_path=db,
    )
    assert ok
    run = eval_store.get_run(run_id, db_path=db)
    assert run["status"] == "completed"
    assert run["verdict"] == "pass"
    assert run["layers"]["L1"]["status"] == "passed"


def test_store_finalize_idempotent_and_rejects_bad_status(tmp_path):
    db = eval_store.eval_db_path(make_settings(tmp_path))
    run_id = eval_store.create_run(campaign_id="c", campaign_title="t", db_path=db)

    assert eval_store.finalize_run(run_id, status="completed", db_path=db) is True
    # 终态再写 → 幂等 no-op
    assert eval_store.finalize_run(run_id, status="error", verdict="error", db_path=db) is False
    run = eval_store.get_run(run_id, db_path=db)
    assert run["status"] == "completed"
    assert run["verdict"] is None  # 未篡改

    # 非法目标态 → False
    run2 = eval_store.create_run(campaign_id="c", campaign_title="t", db_path=db)
    assert eval_store.finalize_run(run2, status="hacked", db_path=db) is False
    assert eval_store.get_run(run2, db_path=db)["status"] == "running"


def test_store_source_has_no_delete_statements():
    """append-only 契约：eval_store.py 内不允许出现 DELETE/REPLACE/truncate。"""
    src = Path(__file__).resolve().parents[1] / "src" / "tindalos" / "eval_store.py"
    text = src.read_text(encoding="utf-8")
    # 只匹配可执行 SQL 语句模式——文档字符串里的 "绝不可 DELETE" 是契约描述，不是违规
    for bad in ("DELETE FROM", "REPLACE INTO", "DROP TABLE", "TRUNCATE TABLE"):
        assert bad not in text, f"eval_store 违反 append-only 契约：出现 {bad}"


# --------------------------------------------------------------------------- #
# run_eval：好剧本全绿（零 LLM）
# --------------------------------------------------------------------------- #

def test_good_campaign_full_green_zero_llm(tmp_path):
    settings = make_settings(tmp_path)
    db = eval_store.eval_db_path(settings)
    good = make_good_campaign()

    tr = run_eval(good, settings=settings, search_fn=_empty_search, db_path=db)

    assert tr["verdict"] == "pass"
    assert tr["status"] == "completed"
    assert tr["llm_calls"] == 0
    assert tr["budget"]["spent_usd"] == 0.0
    assert tr["layers"]["L1"]["status"] == "passed"
    assert tr["layers"]["L2"]["status"] == "passed"
    assert tr["layers"]["L2"]["problems"] == []
    assert tr["layers"]["L3"] == {"status": "skipped", "reason": "llm_disabled"}
    assert tr["layers"]["L4"]["reason"] == "no_corpus"
    assert tr["layers"]["L5"]["reason"] == "manual_only"
    assert tr["layers"]["L6"]["reason"] == "no_prior_run"
    assert tr["campaign_id"] == "good-1"

    # trace 落盘可回放：同一 run_id 从库中读出终态
    stored = eval_store.get_run(tr["run_id"], db_path=db)
    assert stored["status"] == "completed"
    assert stored["verdict"] == "pass"
    assert stored["layers"]["L2"]["status"] == "passed"


# --------------------------------------------------------------------------- #
# run_eval：坏剧本短路 + L6 免费重放仍执行
# --------------------------------------------------------------------------- #

def test_bad_campaign_short_circuit(tmp_path):
    settings = make_settings(tmp_path)
    db = eval_store.eval_db_path(settings)
    bad = make_bad_campaign()

    tr = run_eval(bad, settings=settings, search_fn=_empty_search, db_path=db)

    assert tr["verdict"] == "fail"
    assert tr["status"] == "short_circuited"
    assert tr["llm_calls"] == 0
    for lid in ("L3", "L4", "L5"):
        assert tr["layers"][lid] == {"status": "skipped", "reason": "cascade_gate_failed"}, lid
    assert tr["layers"]["L1"]["status"] == "passed"  # L1 本身跑完（只是未过门）
    assert "L6" in tr["layers"]                       # L6 免费重放仍尝试
    # 落盘状态与 trace 一致
    assert eval_store.get_run(tr["run_id"], db_path=db)["status"] == "short_circuited"


def test_bad_campaign_l6_still_runs_and_detects_regression(tmp_path):
    """短路路径下 L6 仍执行：坏剧本 vs 高分历史 → regression 被检出。"""
    settings = make_settings(tmp_path)
    db = eval_store.eval_db_path(settings)
    bad = make_bad_campaign()
    prior = {
        "run_id": "prior-high",
        "layers": {"L1": {"total": 5.0, "dims": {d: {"score": 5} for d in
                    ("structural", "consistency", "depth", "playability")}}},
    }

    tr = run_eval(bad, settings=settings, replay_of=prior, search_fn=_empty_search, db_path=db)

    assert tr["verdict"] == "fail"                      # 短路优先
    assert tr["layers"]["L6"]["status"] == "failed"
    assert tr["layers"]["L6"]["regression"] is True
    assert tr["layers"]["L6"]["delta"] <= -0.5


# --------------------------------------------------------------------------- #
# L6 replay：无回归 / 回归 → warning
# --------------------------------------------------------------------------- #

def test_replay_l6_no_regression(tmp_path):
    settings = make_settings(tmp_path)
    db = eval_store.eval_db_path(settings)
    good = make_good_campaign()

    tr1 = run_eval(good, settings=settings, search_fn=_empty_search, db_path=db)
    tr2 = run_eval(good, settings=settings, replay_of=tr1, search_fn=_empty_search, db_path=db)

    assert tr2["layers"]["L6"]["status"] == "passed"
    assert tr2["layers"]["L6"]["regression"] is False
    assert tr2["layers"]["L6"]["delta"] == 0.0
    assert tr2["verdict"] == "pass"


def test_l6_regression_warns(tmp_path):
    """L6 检测到当前总分比历史低 0.5+ → 回归 → verdict 升 warning。"""
    settings = make_settings(tmp_path)
    db = eval_store.eval_db_path(settings)
    good = make_good_campaign()
    # 构造高分历史（模拟先前版本在别的评分体系下 total 更高）
    prior = {
        "run_id": "prior-higher",
        "layers": {"L1": {"total": 5.5, "dims": {d: {"score": 5.5} for d in
                    ("structural", "consistency", "depth", "playability")}}},
    }

    tr = run_eval(good, settings=settings, replay_of=prior, search_fn=_empty_search, db_path=db)

    assert tr["verdict"] == "warning"
    assert tr["status"] == "completed"
    assert tr["layers"]["L6"]["regression"] is True
    assert tr["layers"]["L6"]["delta"] == round(5.0 - 5.5, 1)


# --------------------------------------------------------------------------- #
# 预算门：超限在 LLM 调用前拦截
# --------------------------------------------------------------------------- #

def test_budget_gate_skips_l3_before_llm(tmp_path):
    settings = make_settings(tmp_path)
    db = eval_store.eval_db_path(settings)
    good = make_good_campaign()
    calls = {"n": 0}

    def counting_client(messages):
        calls["n"] += 1
        return GOOD_JSON

    judge = LLMJudge(settings=Settings(llm_enabled=True), client=counting_client)
    tr = run_eval(good, settings=settings, judge=judge, search_fn=_empty_search,
                  max_usd=0.0, db_path=db)

    assert calls["n"] == 0                                  # judge 零调用
    assert tr["llm_calls"] == 0
    assert tr["layers"]["L3"]["reason"] == "budget_exceeded"
    assert tr["layers"]["L3"]["estimate_usd"] > 0
    assert tr["verdict"] == "pass"                          # L3 skip 不降 verdict


# --------------------------------------------------------------------------- #
# L3 真实参与：judge enabled + 预算充足
# --------------------------------------------------------------------------- #

def test_l3_llm_judge_participates_when_enabled(tmp_path):
    settings = make_settings(tmp_path)
    db = eval_store.eval_db_path(settings)
    good = make_good_campaign()
    judge = LLMJudge(settings=Settings(llm_enabled=True), client=StubClient(GOOD_JSON))

    tr = run_eval(good, settings=settings, judge=judge, search_fn=_empty_search, db_path=db)

    assert tr["llm_calls"] == 1
    assert tr["layers"]["L3"]["status"] == "passed"
    assert tr["layers"]["L3"]["judge"] == "llm"
    assert tr["layers"]["L3"]["dims"]["structural"]["score"] == 4
    assert tr["budget"]["spent_usd"] == tr["budget"]["estimate_usd"]
    assert tr["verdict"] == "pass"


# --------------------------------------------------------------------------- #
# L4 faithfulness：假 search_fn 注入
# --------------------------------------------------------------------------- #

def test_l4_faithfulness_supported(tmp_path):
    settings = make_settings(tmp_path)
    db = eval_store.eval_db_path(settings)
    good = make_good_campaign()
    # 每个 claim 命中自身 → 全支持
    tr = run_eval(
        good, settings=settings,
        search_fn=lambda q, module_id=None: [
            {"text": q, "score": 0.5, "module_id": "m1", "chunk_index": 0}
        ],
        db_path=db,
    )

    l4 = tr["layers"]["L4"]
    assert l4["status"] == "passed"
    assert l4["support_ratio"] == 1.0
    assert l4["claim_count"] >= 1
    assert tr["verdict"] == "pass"
    assert len(tr["annotations"]) == l4["claim_count"]       # 逐 claim 落标注
    assert eval_store.list_annotations(tr["run_id"], db_path=db)  # annotations 持久化


def test_l4_faithfulness_unsupported_warns(tmp_path):
    settings = make_settings(tmp_path)
    db = eval_store.eval_db_path(settings)
    good = make_good_campaign()
    # 命中但内容与 claim 零 token 重叠（拉丁串，中文剧本不会出现）→ 全不支持
    tr = run_eval(
        good, settings=settings,
        search_fn=lambda q, module_id=None: [
            {"text": "quantum flux resonance", "score": 0.5, "module_id": "m1", "chunk_index": 0}
        ],
        db_path=db,
    )

    l4 = tr["layers"]["L4"]
    assert l4["status"] == "passed"
    assert l4["supported"] == 0
    assert l4["support_ratio"] == 0.0
    assert tr["verdict"] == "warning"                       # 支持比过低 → warning


def test_l4_faithfulness_no_corpus_skips(tmp_path):
    settings = make_settings(tmp_path)
    db = eval_store.eval_db_path(settings)
    good = make_good_campaign()

    tr = run_eval(good, settings=settings, search_fn=_empty_search, db_path=db)

    assert tr["layers"]["L4"]["status"] == "skipped"
    assert tr["layers"]["L4"]["reason"] == "no_corpus"
    assert tr["annotations"] == []                          # 零命中不产标注


# --------------------------------------------------------------------------- #
# dict 输入（web POST /api/eval/run 快照场景）
# --------------------------------------------------------------------------- #

def test_run_eval_accepts_dict_snapshot(tmp_path):
    """history 快照是 dict——_ensure_campaign_model 归一化后不应崩。"""
    settings = make_settings(tmp_path)
    db = eval_store.eval_db_path(settings)
    raw = make_good_campaign().model_dump()

    tr = run_eval(raw, settings=settings, search_fn=_empty_search, db_path=db)

    assert tr["verdict"] == "pass"
    assert tr["status"] == "completed"
    assert tr["layers"]["L2"]["problems"] == []
    assert tr["campaign_id"] == "good-1"


# --------------------------------------------------------------------------- #
# 预算估算
# --------------------------------------------------------------------------- #

def test_estimate_usd_positive_and_small():
    good = make_good_campaign()
    est = estimate_usd(good)
    assert est > 0
    assert est < 0.1  # 好剧本几千字符 → 远低于默认 $2 预算


def test_estimate_usd_accepts_dict():
    raw = make_good_campaign().model_dump()
    assert estimate_usd(raw) > 0
