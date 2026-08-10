"""评测报告：4 维总表 + 四类失败源归因（对齐 JD 要求）。

归因四类：
- structure  结构问题：schema 非法 / id 重复 / 空幕 / 空场景 等结构性缺陷
- data       引用/数据问题：悬空引用 / KG 矛盾 / 线索无 linked 目标
- model      生成风格/质量：NPC 无 personality、事件密度低、设定缺失等生成侧问题
- evaluation 评测规则本身：LLM 裁判未参与（降级）、规则覆盖范围说明等评测侧备注

attribution 恒含四键（零填充），每键 {label, count, items}。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from tindalos.eval_.rubric import DIMENSIONS

_CATEGORY_BY_CHECK: dict[str, str] = {
    "schema_valid": "structure",
    "id_unique": "structure",
    "act_has_scene": "structure",
    "scene_has_event": "structure",
    "refs_resolvable": "data",
    "kg_consistent": "data",
    "clue_linked": "data",
    "npc_personality": "model",
    "npc_described": "model",
    "event_density": "model",
    "setting_complete": "model",
    "clue_described": "model",
    "relation_richness": "model",
    "event_complete": "model",
    "branching": "model",
}

_CATEGORY_LABELS: dict[str, str] = {
    "structure": "结构问题",
    "data": "引用/数据问题",
    "model": "生成风格/质量",
    "evaluation": "评测规则本身",
}


def _attribution(deterministic_result: dict, judge_result: Optional[dict]) -> dict:
    out = {
        cat: {"label": _CATEGORY_LABELS[cat], "count": 0, "items": []}
        for cat in ("structure", "data", "model", "evaluation")
    }
    for c in deterministic_result.get("checks", []):
        if c.get("passed"):
            continue
        cat = _CATEGORY_BY_CHECK.get(c["id"])
        if cat:
            out[cat]["count"] += 1
            out[cat]["items"].append(
                {"check": c["id"], "name": c.get("name", ""), "evidence": c.get("evidence", "")}
            )

    # evaluation 类：评测规则自身的说明（LLM 裁判状态 / 规则覆盖）；items 与其余类统一为 dict 结构（G5 评审修正）
    ev_items: list[dict] = []
    judge = (judge_result or {}).get("judge", "none")
    if judge == "none":
        reason = (judge_result or {}).get("reason", "未提供 judge_result")
        ev_items.append({"kind": "note", "text": f"LLM 裁判未参与（{reason}），全部评分来自确定性规则"})
    if not any(not c.get("passed") for c in deterministic_result.get("checks", [])):
        ev_items.append({"kind": "note", "text": "确定性检查全部通过，无失败项需要归因"})
    if not ev_items:
        ev_items.append({"kind": "note", "text": "无额外评测规则说明"})
    out["evaluation"]["count"] = len(ev_items)
    out["evaluation"]["items"] = ev_items
    return out


def eval_report(
    campaign: Any,
    world: Any,
    deterministic_result: dict,
    judge_result: Optional[dict] = None,
) -> dict:
    """生成评测报告。

    :param campaign: 被评剧本（Campaign 实例或 dict，用于取 id/title）
    :param world: 世界知识图谱（供外部展示；评分来自 deterministic_result）
    :param deterministic_result: run_deterministic 输出
    :param judge_result: LLMJudge.evaluate 输出（可选；judge='llm' 时用裁判分数/建议覆盖总表）
    :return: {campaign_id, campaign_title, total, table{4 维}, attribution, judge, generated_at}
    """
    det_dims = deterministic_result["dims"]
    judge = (judge_result or {}).get("judge", "none")
    jdims = (judge_result or {}).get("dims") if judge == "llm" else None

    table: dict[str, dict] = {}
    for dim in DIMENSIONS:
        entry: dict[str, Any] = {
            "score": det_dims[dim]["score"],
            "evidence": list(det_dims[dim].get("evidence", [])),
            "suggestion": None,
        }
        if jdims and dim in jdims:
            j = jdims[dim]
            entry["score"] = j["score"]
            entry["comment"] = j["comment"]
            entry["suggestion"] = j["suggestion"]
        table[dim] = entry

    total = round(sum(table[d]["score"] for d in DIMENSIONS) / len(DIMENSIONS), 1)

    cid = getattr(campaign, "id", None)
    ctitle = getattr(campaign, "title", None)
    if isinstance(campaign, dict):
        cid = campaign.get("id")
        ctitle = campaign.get("title")

    return {
        "campaign_id": cid,
        "campaign_title": ctitle,
        "total": total,
        "table": table,
        "attribution": _attribution(deterministic_result, judge_result),
        "judge": judge,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
