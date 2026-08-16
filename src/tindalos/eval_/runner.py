"""Eval 六层编排（P0-b）：run_eval 分层执行 + 级联门 + 预算门 + trace 持久化。

设计文档《云数据库 + 记忆系统 + Eval 系统统一落地路线》§4.2/§4.3：
- L1 确定性结构（run_deterministic，零 LLM）→ 门：structural & consistency >= 4；
- L2 图谱一致性（kg.consistency_check + campaign_consistency + 线索可达性，零 LLM）
  → 门：无 problems；
- 级联门：L1 门失败或 L2 有 problems → 短路 status='short_circuited' verdict='fail'，
  L3/L4/L5 全部 skipped(cascade_gate_failed)，L6 免费重放仍尝试；
- L3 内容质量（LLMJudge，预算门：estimate_usd 超 EVAL_MAX_USD 降级 skip）；
- L4 faithfulness（零 LLM）：拆声明 → search_fn（默认 rag.search）判「不被模组支持」，
  全部 claims 零命中 → skipped('no_corpus') 避免误判；逐 claim 标注 + evidence_refs；
- L5 KP 可用性：仅手动触发 → 恒 skipped('manual_only')；
- L6 回归（replay_of 对比 L1 分数，零 LLM）；
- verdict：fail（短路）/ warning（L4 支持比低 或 L6 回归）/ pass。

trace 结构（完整可回放，落盘 eval.sqlite，见 eval_store）：
{run_id, campaign_id, campaign_title, subject_type, subject_ref, params, verdict,
 status, budget:{max_usd, spent_usd, estimate_usd}, llm_calls, layers:{L1..L6},
 annotations, created_at, updated_at, duration_ms}

克制原则：LLM 只在 L3 调用（最多 1 次）；L4 检索按 claim 上限截断；
estimate_usd 为调用前最坏情况估算，超限即降级，不实际花费。
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Optional

from tindalos import eval_store, kg, rag
from tindalos.config import Settings, get_settings
from tindalos.eval_.deterministic import run_deterministic
from tindalos.eval_.judge import LLMJudge

# 克制：单次 L4 评估的声明上限（超出的声明截断，避免大剧本爆检索）
_MAX_CLAIMS = 40
# L1 门阈值：structural / consistency 维度分数下限
_L1_GATE_MIN_DIM = 4
# L4 支持比低于该值 → verdict 升 warning
_SUPPORT_RATIO_WARN = 0.5
# L6 回归判定：total 下降超过该值视为回归
_L6_REGRESSION_DELTA = 0.5

# 预算估算单价（USD / 1M tokens，worst-case 上界）
_PRICE_IN_USD_PER_M = 1.0
_PRICE_OUT_USD_PER_M = 4.0
# CoT 额外输出 token 系数（设计文档 §4.3：CoT +30~60%，预算取上界 +50%）
_COT_OUTPUT_FACTOR = 1.5


# --------------------------------------------------------------------------- #
# 预算估算
# --------------------------------------------------------------------------- #

def _ensure_campaign_model(campaign: Any) -> Any:
    """把 dict 归一化为 Campaign 实例；非 dict（已是模型/别的对象）原样返回。

    供 world 构建 / L2 / L3 / L4 等**属性访问路径**使用——web POST 端点传入的
    history 快照是 dict，kg.build_from_campaign / _clue_reachability / _split_claims
    都遍历 campaign.acts / npcs 等属性，直接喂 dict 会 AttributeError。
    schema 校验失败的 dict 用 construct_loose_campaign 兜底（跳过校验）。
    注意：L1 run_deterministic 仍消费**原始 dict**（_coerce_campaign 自己会宽松
    构造并保留 schema_err），这里不做替换，避免吞掉结构性问题。
    """
    if not isinstance(campaign, dict):
        return campaign
    try:
        from tindalos.models import Campaign, normalize_relation_types

        return Campaign.model_validate(normalize_relation_types(campaign))
    except Exception:  # noqa: BLE001 —— schema 校验失败按宽松构造兜底
        from tindalos.models import construct_loose_campaign

        return construct_loose_campaign(campaign)


def estimate_usd(
    campaign: Any,
    price_in_usd_per_m: float = _PRICE_IN_USD_PER_M,
    price_out_usd_per_m: float = _PRICE_OUT_USD_PER_M,
) -> float:
    """L3 judge 调用的最坏情况成本估算（调用前，零实际花费）。

    按 campaign 序列化长度 × 1.5 token/字符（中文保守上界）+ 提示词开销，
    加上 4 维 JSON 输出 token 估算；输出按 _COT_OUTPUT_FACTOR 计入 CoT
    逐步推理额外 token（设计文档 §4.3：CoT +30~60%，取 +50% 上界）。
    对典型剧本（数千字符）远低于默认 $2。
    """
    try:
        cdata = campaign.model_dump() if hasattr(campaign, "model_dump") else dict(campaign)
        body = json.dumps(cdata, ensure_ascii=False)
    except Exception:  # noqa: BLE001 —— 无法序列化按字符串估算
        body = str(campaign)
    in_tokens = int(len(body) * 1.5) + 800  # +世界图/提示词开销
    out_tokens = int(400 * _COT_OUTPUT_FACTOR)  # 4 维 JSON + CoT 推理输出（+50%）
    return (in_tokens * price_in_usd_per_m + out_tokens * price_out_usd_per_m) / 1_000_000


# --------------------------------------------------------------------------- #
# L2 线索可达性（新增检查）
# --------------------------------------------------------------------------- #

def _reachable(entry_ids: set[str], adj: dict[str, list[str]], target: str) -> bool:
    """BFS：target 是否可从任一 entry 事件沿 next_event_ids 图到达。"""
    if target in entry_ids:
        return True
    seen: set[str] = set()
    stack = list(entry_ids)
    while stack:
        node = stack.pop()
        if node == target:
            return True
        if node in seen:
            continue
        seen.add(node)
        stack.extend(adj.get(node, []))
    return False


def _clue_reachability(campaign: Any) -> list[str]:
    """线索可达性：每个 clue 的 linked_event_ids 需从 entry 事件经 next_event_ids 可达。

    无 entry 事件 → 明确上报（事件流起点缺失），起点退化为全部事件继续检查，
    避免连锁误报；无 linked events 的线索本身即 problem。
    """
    problems: list[str] = []
    events = [ev for act in campaign.acts for sc in act.scenes for ev in sc.events]
    if not events:
        for clue in campaign.clues:
            problems.append(f"线索 {clue.id} 无可达事件：剧本没有任何事件")
        return problems
    event_ids = {ev.id for ev in events}
    entry_ids = {ev.id for ev in events if ev.kind == "entry"}
    if not entry_ids:
        problems.append("剧本没有任何 kind='entry' 事件，事件流缺少起点")
    adj = {ev.id: [n for n in ev.next_event_ids if n in event_ids] for ev in events}
    starts = entry_ids or set(event_ids)  # 无 entry 时全事件兜底
    for clue in campaign.clues:
        if not clue.linked_event_ids:
            problems.append(
                f"线索 {clue.id} 没有 linked_event_ids，无法从 entry 事件可达"
            )
            continue
        for target in clue.linked_event_ids:
            if target not in event_ids:
                continue  # 引用未知事件由 L1 refs_resolvable 上报，这里不重复
            if not _reachable(starts, adj, target):
                problems.append(
                    f"线索 {clue.id} 的 linked_event {target} 无法从 entry 事件经 next_event_ids 可达"
                )
    return problems


# --------------------------------------------------------------------------- #
# L4 faithfulness（零 LLM）
# --------------------------------------------------------------------------- #

def _split_claims(campaign: Any) -> list[dict]:
    """拆声明：事件描述 / 场景设定 / 线索描述 / NPC 描述 → 逐句 claim。

    每条 claim: {text, subject_ref}（subject_ref 溯源到具体字段，供人工复核）。
    克制：_MAX_CLAIMS 上限截断（保留前面的声明）。
    """
    claims: list[dict] = []

    def push(text: str, subject_ref: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        for sentence in rag._split_sentences(text):
            if len(claims) >= _MAX_CLAIMS:
                return
            claims.append({"text": sentence, "subject_ref": subject_ref})

    for act in campaign.acts:
        for scene in act.scenes:
            setting = scene.setting or {}
            if setting.get("time") or setting.get("place"):
                push(f"{setting.get('time', '')} {setting.get('place', '')}", f"scene:{scene.id}")
            for ev in scene.events:
                push(ev.description, f"event:{ev.id}")
    for clue in campaign.clues:
        push(clue.description, f"clue:{clue.id}")
    for npc in campaign.npcs.values():
        push(npc.description, f"npc:{npc.id}")
    return claims


def _token_overlap(a: str, b: str) -> int:
    """词元重叠数：rag.tokenize 后仅保留 ≥2 字（二元词/拉丁词）——单字对中文无关紧要。"""
    ta = {t for t in rag.tokenize(a) if len(t) >= 2}
    tb = {t for t in rag.tokenize(b) if len(t) >= 2}
    return len(ta & tb)


def _claim_supported(claim: str, hits: list[dict]) -> bool:
    """支持判据：任一 hit score>0 且与 claim 共享 ≥1 个 ≥2 字二元词 token。

    RRF 分数跨语料不可绝对阈值化（1/(k+rank+1) ≈ 0.016 量级），故用
    score>0 + token 重叠的相对判据，既确定又可测。
    """
    for h in hits:
        if not h.get("score") or h["score"] <= 0:
            continue
        if _token_overlap(claim, h.get("text") or "") >= 1:
            return True
    return False


def _l4_faithfulness(
    campaign: Any,
    search_fn: Callable[..., list[dict]],
    module_id: str | None,
) -> dict:
    """L4 faithfulness：拆声明 → 逐条检索 → 判「不被模组支持」+ evidence_refs。"""
    claims = _split_claims(campaign)
    if not claims:
        return {"status": "skipped", "reason": "no_claims"}
    annotations: list[dict] = []
    corpus_hits = False
    supported = 0
    for c in claims:
        hits = search_fn(c["text"], module_id=module_id) or []
        hits = [h for h in hits if h.get("score", 0) > 0]
        if hits:
            corpus_hits = True
        ok = _claim_supported(c["text"], hits)
        if ok:
            supported += 1
        evidence = [
            {
                "module_id": h.get("module_id"),
                "chunk_index": h.get("chunk_index"),
                "score": round(float(h.get("score", 0)), 4),
            }
            for h in hits[:3]
        ]
        annotations.append(
            {
                "layer": "L4",
                "subject_ref": c["subject_ref"],
                "score": 1.0 if ok else 0.0,
                "explanation": c["text"],
                "evidence_refs": evidence,
            }
        )
    if not corpus_hits:
        # 语料不存在（零命中）→ 无法判定 faithfulness，降级跳过而非误判
        return {"status": "skipped", "reason": "no_corpus"}
    return {
        "status": "passed",
        "claim_count": len(claims),
        "supported": supported,
        "support_ratio": round(supported / len(claims), 3),
        "annotations": annotations,
    }


# --------------------------------------------------------------------------- #
# L6 回归（重放对比）
# --------------------------------------------------------------------------- #

def _prior_l1(replay_of: Any) -> dict | None:
    """从先前 trace（或先前 run 的 dict）取 L1 结果。"""
    if not isinstance(replay_of, dict):
        return None
    layers = replay_of.get("layers")
    if isinstance(layers, dict):
        l1 = layers.get("L1") or layers.get("l1")
        if isinstance(l1, dict):
            return l1
    if isinstance(replay_of.get("L1"), dict):
        return replay_of["L1"]
    return None


def _l6_replay(l1: dict, prior_l1: dict | None) -> dict:
    if prior_l1 is None:
        return {"status": "skipped", "reason": "no_prior_run"}
    try:
        prior_total = float(prior_l1.get("total") or 0)
        current_total = float(l1.get("total") or 0)
    except (TypeError, ValueError):
        return {"status": "skipped", "reason": "prior_incomparable"}
    delta = round(current_total - prior_total, 1)
    dim_deltas: dict[str, float] = {}
    prior_dims = prior_l1.get("dims") or {}
    cur_dims = l1.get("dims") or {}
    for dim in ("structural", "consistency", "depth", "playability"):
        p = float((prior_dims.get(dim) or {}).get("score", 0) or 0)
        c = float((cur_dims.get(dim) or {}).get("score", 0) or 0)
        dim_deltas[dim] = round(c - p, 1)
    regression = delta <= -_L6_REGRESSION_DELTA
    return {
        "status": "passed" if not regression else "failed",
        "prior_total": prior_total,
        "current_total": current_total,
        "delta": delta,
        "dim_deltas": dim_deltas,
        "regression": bool(regression),
    }


# --------------------------------------------------------------------------- #
# 编排入口
# --------------------------------------------------------------------------- #

def run_eval(
    campaign: Any,
    *,
    settings: Settings | None = None,
    world: Any = None,
    judge: Any = None,
    search_fn: Callable[..., list[dict]] | None = None,
    module_id: str | None = None,
    replay_of: Any = None,
    db_path: Any = None,
    params: dict[str, Any] | None = None,
    max_usd: float | None = None,
) -> dict:
    """跑一次完整评测并落盘 trace（append-only）。返回完整 trace dict（可回放）。

    :param campaign: Campaign 实例或原始 dict
    :param world: WorldGraph；None 时按 campaign 构建
    :param judge: LLMJudge 实例（或具有 evaluate 的对象）；None 时按 settings 自动解析
    :param search_fn: L4 检索函数 search(query, *, module_id) -> hits；默认 rag.search
    :param module_id: L4 限定检索的模组 id
    :param replay_of: 先前 trace dict（L6 重放对比）
    :param max_usd: 预算上限；None → settings.eval_max_usd
    """
    settings = settings or get_settings()
    max_usd = settings.eval_max_usd if max_usd is None else max_usd
    search_fn = search_fn or rag.search
    # dict 快照（web POST）→ Campaign 实例，供 world 构建与 L2/L3/L4 属性访问；
    # L1 仍吃原始 dict 以保留 schema_err（见 _ensure_campaign_model 注释）
    model = _ensure_campaign_model(campaign)
    if world is None:
        world = kg.build_from_campaign(model)

    title = getattr(model, "title", None) or getattr(model, "id", "campaign")
    campaign_id = getattr(model, "id", None) or "campaign"
    run_params = {"module_id": module_id, "max_usd": max_usd}
    run_params.update(params or {})
    run_id = eval_store.create_run(
        campaign_id=campaign_id,
        campaign_title=str(title),
        subject_type="campaign",
        subject_ref=campaign_id,
        params=run_params,
        db_path=db_path,
    )

    layers: dict[str, dict] = {}
    annotations: list[dict] = []
    llm_calls = 0
    start = time.monotonic()
    try:
        # ---------------- L1 确定性结构 ----------------
        l1 = run_deterministic(campaign, world)
        layers["L1"] = {"status": "passed", "total": l1["total"], "dims": l1["dims"], "checks": l1["checks"]}
        l1_gate = (
            l1["dims"]["structural"]["score"] >= _L1_GATE_MIN_DIM
            and l1["dims"]["consistency"]["score"] >= _L1_GATE_MIN_DIM
        )

        # ---------------- L2 图谱一致性 ----------------
        l2_problems = list(world.consistency_check())
        l2_problems += list(kg.campaign_consistency(model, world))
        l2_problems += _clue_reachability(model)
        layers["L2"] = (
            {"status": "passed", "problems": []}
            if not l2_problems
            else {"status": "failed", "problems": l2_problems}
        )
        l2_gate = not l2_problems

        gate_ok = l1_gate and l2_gate
        if not gate_ok:
            for lid in ("L3", "L4", "L5"):
                layers[lid] = {"status": "skipped", "reason": "cascade_gate_failed"}
        else:
            # ---------------- L3 内容质量（LLM，预算门） ----------------
            estimate = estimate_usd(model)
            if max_usd is not None and estimate > max_usd:
                layers["L3"] = {"status": "skipped", "reason": "budget_exceeded", "estimate_usd": estimate}
            else:
                j = judge
                if j is not None and getattr(j, "enabled", True) is False:
                    # 注入的 judge 明确禁用（如 CLI 未 --judge）→ 零 LLM 降级
                    layers["L3"] = {"status": "skipped", "reason": "llm_disabled"}
                elif j is None:
                    if settings.llm_enabled:
                        j = LLMJudge(settings)
                    else:
                        layers["L3"] = {"status": "skipped", "reason": "llm_disabled"}
                if "L3" not in layers:
                    llm_calls += 1
                    det = {"total": l1["total"], "dims": l1["dims"]}
                    res = j.evaluate(model, world, det)
                    # judge_model / self_preference_risk 落 trace（设计文档 §3.5 L3 / §4.3）
                    judge_meta = {
                        "judge_model": res.get("judge_model"),
                        "self_preference_risk": bool(res.get("self_preference_risk", False)),
                    }
                    if res.get("judge") == "llm":
                        layers["L3"] = {"status": "passed", "judge": "llm", "dims": res.get("dims"), **judge_meta}
                    else:
                        layers["L3"] = {"status": "degraded", "reason": res.get("reason", "judge_failed"), **judge_meta}

            # ---------------- L4 faithfulness（零 LLM） ----------------
            l4 = _l4_faithfulness(model, search_fn, module_id)
            layers["L4"] = {k: v for k, v in l4.items() if k != "annotations"}
            annotations.extend(l4.get("annotations", []))

            # ---------------- L5 KP 可用性（手动） ----------------
            layers["L5"] = {"status": "skipped", "reason": "manual_only"}

        # ---------------- L6 回归（零 LLM） ----------------
        layers["L6"] = _l6_replay(layers["L1"], _prior_l1(replay_of))

        # ---------------- verdict ----------------
        verdict, status = _final_verdict(gate_ok, layers)
    except Exception as e:  # noqa: BLE001 —— 运行异常落盘 error 态再上抛
        eval_store.finalize_run(
            run_id,
            status="error",
            verdict="error",
            layers=layers,
            duration_ms=int((time.monotonic() - start) * 1000),
            db_path=db_path,
        )
        raise

    spent = estimate_usd(model) if layers.get("L3", {}).get("status") == "passed" else 0.0
    eval_store.append_annotations(run_id, annotations, db_path=db_path)
    eval_store.finalize_run(
        run_id,
        status=status,
        verdict=verdict,
        layers=layers,
        budget_spent_usd=spent,
        duration_ms=int((time.monotonic() - start) * 1000),
        db_path=db_path,
    )

    trace = {
        "run_id": run_id,
        "campaign_id": campaign_id,
        "campaign_title": str(title),
        "subject_type": "campaign",
        "subject_ref": campaign_id,
        "params": run_params,
        "verdict": verdict,
        "status": status,
        "budget": {"max_usd": max_usd, "spent_usd": spent, "estimate_usd": estimate_usd(model)},
        "llm_calls": llm_calls,
        "layers": layers,
        "annotations": annotations,
        "created_at": eval_store.get_run(run_id, db_path=db_path)["created_at"],
        "updated_at": eval_store.get_run(run_id, db_path=db_path)["updated_at"],
        "duration_ms": int((time.monotonic() - start) * 1000),
    }
    return trace


def _final_verdict(gate_ok: bool, layers: dict[str, dict]) -> tuple[str, str]:
    """verdict/status 聚合：短路 → fail；否则按 L4 支持比 / L6 回归升 warning。

    返回 (verdict, status)。status 语义与 eval_store 生命周期一致：
    completed（跑完全部可达层）/ short_circuited（级联门短路）。
    """
    if not gate_ok:
        return "fail", "short_circuited"
    warning = False
    l4 = layers.get("L4", {})
    if l4.get("status") == "passed" and l4.get("support_ratio") is not None:
        if l4["support_ratio"] < _SUPPORT_RATIO_WARN:
            warning = True
    l6 = layers.get("L6", {})
    if l6.get("status") == "failed" and l6.get("regression"):
        warning = True
    return ("warning" if warning else "pass"), "completed"


__all__ = [
    "run_eval",
    "estimate_usd",
    "_clue_reachability",
    "_l4_faithfulness",
]
