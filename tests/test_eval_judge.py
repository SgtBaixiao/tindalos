"""L3 LLM judge 增强测试（task #02，P1 #4）。

覆盖：
- CoT + evidence_refs 合法输出 → per-dim evidence_refs 解析正确、judge_model 记录、
  temp=0 生效、self_preference_risk 标注正确
- 坏 JSON / 缺键 / 类型错 → judge='none' 确定性降级，无崩溃
- 预算估算含 CoT 开销；超限跳 L3 的路径仍绿
- run_eval L3 调用后 judge_model / self_preference_risk 落 trace（含落盘回放）

零网络：全部走 FakeLLM stub / monkeypatch urlopen。
"""
import json
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from tindalos import eval_store, kg
from tindalos.config import Settings
from tindalos.eval_.deterministic import run_deterministic
from tindalos.eval_.judge import JUDGE_PROMPT, LLMJudge, _extract_json_object, parse_judge_json
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

def make_good_campaign() -> Campaign:
    """好剧本：entry 事件 ev-1 → ev-2，线索可从 entry 可达，L1/L2 门通过。"""
    npcs = {
        "npc-1": NPC(
            id="npc-1", name="老渔夫", archetype="向导",
            personality=["谨慎", "话少"],
            description="在码头生活四十年的老渔夫。",
            acts_roles={"act-1": "线人"},
        ),
    }
    acts = [
        Act(id="act-1", title="第一幕", roman="I", summary="码头发现旧渔网。", scenes=[
            Scene(id="sc-1", title="旧码头", setting={"time": "深夜", "place": "旧码头"}, events=[
                Event(
                    id="ev-1", title="发现渔网", kind="entry",
                    description="码头木桩下发现缠着海草的旧渔网。",
                    conditions=["持有线索"], next_event_ids=["ev-2"],
                ),
                Event(
                    id="ev-2", title="真相浮现", kind="outcome",
                    description="渔网符号指向深潜者仪式。",
                    conditions=[],
                ),
            ], npc_ids=["npc-1"]),
        ]),
    ]
    clues = [
        Clue(
            id="clue-1", name="旧渔网", description="缠着海草的旧渔网，符号与古籍一致。",
            linked_npc_ids=["npc-1"], linked_event_ids=["ev-1"], found_at="sc-1",
        ),
    ]
    relations = [
        WorldRelation(source="npc-1", target="clue-1", type=RelationType.LEARNS,
                      label="获知", valid_from="2024-01-01"),
    ]
    return Campaign(
        id="good-1", title="深海低语", premise="海边小镇失踪案的背后是深潜者的仪式。",
        acts=acts, npcs=npcs, clues=clues, relations=relations,
    )


class FakeLLM:
    """可调用 stub：记录收到的 messages，返回预设文本（零网络）。"""

    def __init__(self, text: str):
        self.text = text
        self.messages = None

    def __call__(self, messages):
        self.messages = messages
        return self.text


def _empty_search(query, module_id=None):
    """L4 语料空：任何查询零命中 → no_corpus 降级。"""
    return []


def _det(campaign=None):
    c = campaign or make_good_campaign()
    return run_deterministic(c, kg.build_from_campaign(c))


# --------------------------------------------------------------------------- #
# 输出样本
# --------------------------------------------------------------------------- #

VALID_COT = """逐步推理：
structural：场景/事件层级完整、id 唯一、引用可解析 → 4 分。
consistency：线索从 entry 事件可达，KG 无矛盾 → 5 分。
depth：事件密度与 NPC 刻画尚可 → 3 分。
playability：存在分支、线索可引导 → 4 分。
{"structural": {"score": 4, "comment": "结构完整", "suggestion": "补充第三幕", "evidence_refs": ["scene:sc-1", "event:ev-1"]},
 "consistency": {"score": 5, "comment": "一致", "suggestion": "无", "evidence_refs": ["event:ev-1"]},
 "depth": {"score": 3, "comment": "尚可", "suggestion": "丰富 NPC 刻画", "evidence_refs": ["npc:npc-1"]},
 "playability": {"score": 4, "comment": "可玩", "suggestion": "增加分支选择", "evidence_refs": []}}
"""

LEGACY_JSON = """{
  "structural": {"score": 4, "comment": "结构完整", "suggestion": "补充第三幕"},
  "consistency": {"score": 5, "comment": "一致", "suggestion": "无"},
  "depth": {"score": 3, "comment": "尚可", "suggestion": "丰富 NPC 刻画"},
  "playability": {"score": 4, "comment": "可玩", "suggestion": "增加分支选择"}
}"""

BAD_JSON = "逐步推理：结构还行。但输出格式错了，没有 JSON 对象"
UNBALANCED_JSON = '逐步推理：好的。{"structural": {"score": 4, "comment": "x"'

MISSING_KEY_JSON = (
    '{"structural": {"score": 4, "comment": "缺键"},'
    ' "consistency": {"score": 4, "comment": "x", "suggestion": "y"},'
    ' "depth": {"score": 4, "comment": "x", "suggestion": "y"},'
    ' "playability": {"score": 4, "comment": "x", "suggestion": "y"}}'
)

WRONG_TYPE_EV_JSON = (
    '{"structural": {"score": 4, "comment": "x", "suggestion": "y", "evidence_refs": "oops"},'
    ' "consistency": {"score": 4, "comment": "x", "suggestion": "y"},'
    ' "depth": {"score": 4, "comment": "x", "suggestion": "y"},'
    ' "playability": {"score": 4, "comment": "x", "suggestion": "y"}}'
)

NULL_EV_JSON = (
    '{"structural": {"score": 4, "comment": "x", "suggestion": "y", "evidence_refs": null},'
    ' "consistency": {"score": 4, "comment": "x", "suggestion": "y"},'
    ' "depth": {"score": 4, "comment": "x", "suggestion": "y"},'
    ' "playability": {"score": 4, "comment": "x", "suggestion": "y"}}'
)


# --------------------------------------------------------------------------- #
# parse_judge_json：CoT + evidence_refs
# --------------------------------------------------------------------------- #

def test_parse_cot_with_evidence_refs():
    dims = parse_judge_json(VALID_COT)
    assert dims is not None
    assert set(dims) == {"structural", "consistency", "depth", "playability"}
    assert dims["structural"]["evidence_refs"] == ["scene:sc-1", "event:ev-1"]
    assert dims["consistency"]["evidence_refs"] == ["event:ev-1"]
    assert dims["depth"]["evidence_refs"] == ["npc:npc-1"]
    assert dims["playability"]["evidence_refs"] == []
    assert dims["structural"]["score"] == 4
    assert dims["structural"]["comment"] == "结构完整"
    assert dims["depth"]["suggestion"] == "丰富 NPC 刻画"


def test_parse_legacy_json_without_evidence_refs():
    """旧输出（无 evidence_refs）保持兼容：该键省略。"""
    dims = parse_judge_json(LEGACY_JSON)
    assert dims is not None
    assert set(dims["structural"]) == {"score", "comment", "suggestion"}
    assert dims["structural"] == {"score": 4, "comment": "结构完整", "suggestion": "补充第三幕"}


def test_parse_evidence_refs_null_lenient():
    """evidence_refs: null → 宽容为空列表，不降级。"""
    dims = parse_judge_json(NULL_EV_JSON)
    assert dims is not None
    assert dims["structural"]["evidence_refs"] == []


def test_extract_json_object_balanced():
    assert _extract_json_object('前文 {"a": 1} 后文') == '{"a": 1}'
    assert _extract_json_object('{"a": {"b": "含}花括号"}}') == '{"a": {"b": "含}花括号"}}'
    assert _extract_json_object('只有不完整 {"a": 1') is None
    assert _extract_json_object('无花括号') is None


# --------------------------------------------------------------------------- #
# LLMJudge：judge_model / self_preference_risk / temp=0
# --------------------------------------------------------------------------- #

def test_judge_valid_cot_records_meta():
    good = make_good_campaign()
    j = LLMJudge(
        settings=Settings(llm_enabled=True, model="deepseek-chat"),
        client=FakeLLM(VALID_COT),
    )
    res = j.evaluate(good, None, _det(good))

    assert res["judge"] == "llm"
    assert res["judge_model"] == "deepseek-chat"
    assert res["self_preference_risk"] is True          # 与生成同模型 → 风险标注
    assert res["dims"]["structural"]["evidence_refs"] == ["scene:sc-1", "event:ev-1"]
    assert res["dims"]["playability"]["evidence_refs"] == []


def test_judge_model_env_override_and_risk_flag(monkeypatch):
    good = make_good_campaign()
    monkeypatch.setenv("TINDALOS_JUDGE_MODEL", "judge-small")   # 裁判用便宜小模型
    j = LLMJudge(
        settings=Settings(llm_enabled=True, model="deepseek-chat"),
        client=FakeLLM(VALID_COT),
    )
    res = j.evaluate(good, None, _det(good))

    assert res["judge_model"] == "judge-small"
    assert res["self_preference_risk"] is False          # 与生成不同模型 → 无风险


def test_default_client_temperature_zero(monkeypatch):
    """默认 HTTP client 的请求体 temperature 必须为 0（设计文档明确）。"""
    captured = {}

    class FakeResp:
        def read(self):
            return b'{"choices": [{"message": {"content": "{}"}}]}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=60):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResp()

    import tindalos.eval_.judge as judge_mod
    monkeypatch.setattr(judge_mod.urllib.request, "urlopen", fake_urlopen)

    client = judge_mod._default_client(
        Settings(ollama_base_url="http://localhost:11434/v1", model="deepseek-chat")
    )
    client([{"role": "user", "content": "hi"}])

    assert captured["body"]["temperature"] == 0
    assert captured["body"]["model"] == "deepseek-chat"


def test_judge_disabled_still_records_meta():
    j = LLMJudge(settings=Settings(llm_enabled=False, model="deepseek-chat"))
    res = j.evaluate(make_good_campaign(), None, _det())
    assert res["judge"] == "none"
    assert res["reason"] == "llm_disabled"
    assert res["judge_model"] == "deepseek-chat"


# --------------------------------------------------------------------------- #
# 健壮性：坏 JSON / 缺键 / 类型错 → 确定性降级
# --------------------------------------------------------------------------- #

def test_judge_bad_json_degrades():
    j = LLMJudge(settings=Settings(llm_enabled=True), client=FakeLLM(BAD_JSON))
    res = j.evaluate(make_good_campaign(), None, _det())
    assert res["judge"] == "none"
    assert res["reason"] == "parse_failed"


def test_judge_unbalanced_json_degrades():
    j = LLMJudge(settings=Settings(llm_enabled=True), client=FakeLLM(UNBALANCED_JSON))
    res = j.evaluate(make_good_campaign(), None, _det())
    assert res["judge"] == "none"
    assert res["reason"] == "parse_failed"


def test_judge_missing_key_degrades():
    j = LLMJudge(settings=Settings(llm_enabled=True), client=FakeLLM(MISSING_KEY_JSON))
    res = j.evaluate(make_good_campaign(), None, _det())
    assert res["judge"] == "none"
    assert res["reason"] == "parse_failed"


def test_judge_evidence_refs_wrong_type_degrades():
    j = LLMJudge(settings=Settings(llm_enabled=True), client=FakeLLM(WRONG_TYPE_EV_JSON))
    res = j.evaluate(make_good_campaign(), None, _det())
    assert res["judge"] == "none"
    assert res["reason"] == "parse_failed"


def test_judge_client_error_degrades_with_meta():
    def boom(messages):
        raise RuntimeError("network down")

    j = LLMJudge(settings=Settings(llm_enabled=True, model="deepseek-chat"), client=boom)
    res = j.evaluate(make_good_campaign(), None, _det())
    assert res["judge"] == "none"
    assert "llm_error" in res["reason"]
    assert res["judge_model"] == "deepseek-chat"


# --------------------------------------------------------------------------- #
# Prompt：CoT + evidence_refs 要求
# --------------------------------------------------------------------------- #

def test_judge_prompt_requires_cot_and_evidence_refs():
    for dim in ("structural", "consistency", "depth", "playability"):
        assert dim in JUDGE_PROMPT
    assert "推理" in JUDGE_PROMPT                          # CoT 先推理
    assert "evidence_refs" in JUDGE_PROMPT                 # 每维源引用
    assert "score" in JUDGE_PROMPT and "comment" in JUDGE_PROMPT
    assert "JSON" in JUDGE_PROMPT


# --------------------------------------------------------------------------- #
# 预算：含 CoT 开销 + 超限跳 L3 仍绿
# --------------------------------------------------------------------------- #

def _base_estimate_no_cot(campaign) -> float:
    """反推旧公式（4 维 JSON 输出 400 token，无 1.5 CoT 系数）作为基线。"""
    body = json.dumps(campaign.model_dump(), ensure_ascii=False)
    in_tokens = int(len(body) * 1.5) + 800
    out_tokens = 400
    return (in_tokens * 1.0 + out_tokens * 4.0) / 1_000_000


def test_estimate_usd_includes_cot_overhead():
    good = make_good_campaign()
    base = _base_estimate_no_cot(good)
    cot = estimate_usd(good)
    assert base > 0
    assert cot > base                                      # CoT +50% 计入输出 token
    assert cot < 0.1                                       # 典型剧本远低于默认 $2


def test_budget_gate_uses_cot_estimate(tmp_path):
    """max_usd 落在「无 CoT 基线」与「含 CoT 估算」之间 → 预算门按含 CoT 估算拦截。"""
    settings = Settings(
        llm_enabled=False, checkpoint_dir=tmp_path / "ckpt", store_dir=tmp_path / "store",
    )
    db = eval_store.eval_db_path(settings)
    good = make_good_campaign()
    base = _base_estimate_no_cot(good)
    cot = estimate_usd(good)
    assert cot > base

    calls = {"n": 0}

    def counting_client(messages):
        calls["n"] += 1
        return VALID_COT

    judge = LLMJudge(settings=Settings(llm_enabled=True), client=counting_client)
    tr = run_eval(
        good, settings=settings, judge=judge, search_fn=_empty_search,
        max_usd=(base + cot) / 2, db_path=db,
    )

    assert calls["n"] == 0                                  # judge 零调用
    assert tr["llm_calls"] == 0
    assert tr["layers"]["L3"]["reason"] == "budget_exceeded"
    assert tr["layers"]["L3"]["estimate_usd"] == cot        # 预算门用的是含 CoT 的估算


# --------------------------------------------------------------------------- #
# run_eval：judge_model / self_preference_risk 落 trace（含落盘回放）
# --------------------------------------------------------------------------- #

def test_runner_records_judge_model_in_trace(tmp_path):
    settings = Settings(
        llm_enabled=False, checkpoint_dir=tmp_path / "ckpt", store_dir=tmp_path / "store",
    )
    db = eval_store.eval_db_path(settings)
    good = make_good_campaign()
    judge = LLMJudge(
        settings=Settings(llm_enabled=True, model="deepseek-chat"),
        client=FakeLLM(VALID_COT),
    )

    tr = run_eval(good, settings=settings, judge=judge, search_fn=_empty_search, db_path=db)

    l3 = tr["layers"]["L3"]
    assert l3["status"] == "passed"
    assert l3["judge_model"] == "deepseek-chat"
    assert l3["self_preference_risk"] is True
    assert l3["dims"]["structural"]["evidence_refs"] == ["scene:sc-1", "event:ev-1"]
    # 落盘可回放：同一 run 从库中读出终态含 judge_model
    stored = eval_store.get_run(tr["run_id"], db_path=db)
    assert stored["layers"]["L3"]["judge_model"] == "deepseek-chat"
    assert stored["layers"]["L3"]["self_preference_risk"] is True


def test_runner_records_judge_model_on_degraded(tmp_path):
    """L3 降级（parse_failed）同样记录 judge_model / self_preference_risk。"""
    settings = Settings(
        llm_enabled=False, checkpoint_dir=tmp_path / "ckpt", store_dir=tmp_path / "store",
    )
    db = eval_store.eval_db_path(settings)
    good = make_good_campaign()
    judge = LLMJudge(
        settings=Settings(llm_enabled=True, model="deepseek-chat"),
        client=FakeLLM(BAD_JSON),
    )

    tr = run_eval(good, settings=settings, judge=judge, search_fn=_empty_search, db_path=db)

    l3 = tr["layers"]["L3"]
    assert l3["status"] == "degraded"
    assert l3["reason"] == "parse_failed"
    assert l3["judge_model"] == "deepseek-chat"
    assert l3["self_preference_risk"] is True
