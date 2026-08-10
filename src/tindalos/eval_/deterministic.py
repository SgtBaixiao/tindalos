"""确定性评估：models 校验 + kg 一致性 + 结构计数 → 4 维分数 + checks（零 LLM 依赖）。

run_deterministic(campaign, world=None) -> {
    dims: {structural: {score, evidence[]}, consistency: {...}, depth: {...}, playability: {...}},
    total: float,          # 4 维平均，保留 1 位小数
    checks: [ {id, name, dims[], passed, evidence}, ... ],   # 确定性检查清单逐条结果
}

评分：每维 = 5 × 该维通过检查数 / 该维检查总数（下限 1）；
evidence 为字段级说明（campaign 结构路径 + id，如 acts[act-1].scenes[sc-1].npc_ids[ghost-npc]），
低分维度必然携带 evidence，可直接定位到具体字段。
输入 campaign 可为 Campaign 实例或原始 dict；dict 无法通过 models 校验时以宽松构造
（model_construct，跳过校验）继续跑其余检查，schema 失败以 ValidationError 字段路径作为 evidence。
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

from pydantic import ValidationError

from tindalos import kg
from tindalos.eval_.rubric import DETERMINISTIC_CHECKS, DIMENSIONS
from tindalos.models import Act, Campaign, Clue, Event, NPC, Scene, WorldRelation

_CHECK_META: dict[str, dict] = {c["id"]: c for c in DETERMINISTIC_CHECKS}


# --------------------------------------------------------------------------- #
# 输入归一化
# --------------------------------------------------------------------------- #

def _first_error_msg(e: ValidationError) -> str:
    errs = e.errors()
    if not errs:
        return str(e)
    first = errs[0]
    loc = ".".join(str(p) for p in first.get("loc", []))
    msg = str(first.get("msg", e))
    return f"{loc}: {msg}" if loc else msg


def _construct_loose(raw: dict) -> Campaign:
    """宽松构造（model_construct 跳过校验）：schema 校验失败的 dict 也能继续跑结构检查。"""
    acts = [
        Act.model_construct(
            id=a.get("id", ""), title=a.get("title", ""), roman=a.get("roman", ""),
            summary=a.get("summary", ""),
            scenes=[
                Scene.model_construct(
                    id=s.get("id", ""), title=s.get("title", ""),
                    setting=s.get("setting", {}),
                    events=[
                        Event.model_construct(
                            id=e.get("id", ""), title=e.get("title", ""),
                            kind=e.get("kind"), description=e.get("description", ""),
                            conditions=e.get("conditions", []),
                            next_event_ids=e.get("next_event_ids", []),
                        )
                        for e in s.get("events", [])
                    ],
                    npc_ids=s.get("npc_ids", []),
                )
                for s in a.get("scenes", [])
            ],
            npc_ids=a.get("npc_ids", []),
        )
        for a in raw.get("acts", [])
    ]
    npcs = {
        k: NPC.model_construct(
            id=v.get("id", k), name=v.get("name", ""), archetype=v.get("archetype", ""),
            personality=v.get("personality", []), description=v.get("description", ""),
            acts_roles=v.get("acts_roles", {}),
        )
        for k, v in raw.get("npcs", {}).items()
    }
    clues = [
        Clue.model_construct(
            id=c.get("id", ""), name=c.get("name", ""), description=c.get("description", ""),
            linked_npc_ids=c.get("linked_npc_ids", []),
            linked_event_ids=c.get("linked_event_ids", []), found_at=c.get("found_at"),
        )
        for c in raw.get("clues", [])
    ]
    relations = [
        WorldRelation.model_construct(
            source=r.get("source", ""), target=r.get("target", ""), type=r.get("type"),
            label=r.get("label", ""), valid_from=r.get("valid_from", ""),
            valid_to=r.get("valid_to"), note=r.get("note"),
        )
        for r in raw.get("relations", [])
    ]
    return Campaign.model_construct(
        id=raw.get("id", ""), title=raw.get("title", ""), premise=raw.get("premise", ""),
        acts=acts, npcs=npcs, clues=clues, relations=relations,
    )


def _coerce_campaign(campaign: Any) -> tuple[Optional[Campaign], str]:
    """返回 (model, schema_err)；model 保证非 None（宽松构造兜底），除非输入类型不可识别。"""
    if isinstance(campaign, Campaign):
        return campaign, ""
    if isinstance(campaign, dict):
        try:
            return Campaign.model_validate(campaign), ""
        except ValidationError as e:
            err = _first_error_msg(e)
            try:
                return _construct_loose(campaign), err
            except Exception:
                return None, err
    return None, f"不支持的输入类型: {type(campaign).__name__}"


# --------------------------------------------------------------------------- #
# 逐项确定性检查
# --------------------------------------------------------------------------- #

def _check_schema(model: Campaign) -> tuple[bool, str]:
    """schema 合法：对完整数据重新过 models 校验（model_construct 绕过校验的输入在此被拦截）。"""
    try:
        Campaign.model_validate(model.model_dump())
        return True, ""
    except ValidationError as e:
        return False, f"schema 校验失败：{_first_error_msg(e)}"


def _check_id_unique(model: Campaign) -> tuple[bool, str]:
    ids: dict[str, list[str]] = defaultdict(list)
    for a in model.acts:
        ids[a.id].append("acts[]")
        for s in a.scenes:
            ids[s.id].append(f"acts[{a.id}].scenes[]")
            for e in s.events:
                ids[e.id].append(f"acts[{a.id}].scenes[{s.id}].events[]")
    for nid in model.npcs:
        ids[nid].append("npcs[]")
    for c in model.clues:
        ids[c.id].append("clues[]")
    dups = {k: v for k, v in ids.items() if len(v) > 1}
    if not dups:
        return True, ""
    detail = "；".join(f"{k} 重复出现于 {'、'.join(places)}" for k, places in dups.items())
    return False, f"id 不唯一：{detail}"


def _check_refs_resolvable(model: Campaign) -> tuple[bool, str]:
    event_ids = {e.id for a in model.acts for s in a.scenes for e in s.events}
    npc_ids = set(model.npcs)
    act_ids = {a.id for a in model.acts}
    problems: list[str] = []
    for a in model.acts:
        for nid in a.npc_ids:
            if nid not in npc_ids:
                problems.append(f"acts[{a.id}].npc_ids[{nid}] 引用未注册 NPC")
        for s in a.scenes:
            for nid in s.npc_ids:
                if nid not in npc_ids:
                    problems.append(f"acts[{a.id}].scenes[{s.id}].npc_ids[{nid}] 引用未注册 NPC")
            for e in s.events:
                for nxt in e.next_event_ids:
                    if nxt not in event_ids:
                        problems.append(f"events[{e.id}].next_event_ids[{nxt}] 引用未知事件")
    for c in model.clues:
        for nid in c.linked_npc_ids:
            if nid not in npc_ids:
                problems.append(f"clues[{c.id}].linked_npc_ids[{nid}] 引用未注册 NPC")
        for ev in c.linked_event_ids:
            if ev not in event_ids:
                problems.append(f"clues[{c.id}].linked_event_ids[{ev}] 引用未知事件")
    for nid, npc in model.npcs.items():
        for act_id in npc.acts_roles:
            if act_id not in act_ids:
                problems.append(f"npcs[{nid}].acts_roles[{act_id}] 引用未知幕")
    if not problems:
        return True, ""
    return False, "；".join(problems)


def _check_kg_consistent(model: Campaign, world: Any) -> tuple[bool, str]:
    try:
        w = world if world is not None else kg.build_from_campaign(model)
        problems = list(w.consistency_check()) + list(kg.campaign_consistency(model, w))
    except Exception as e:  # noqa: BLE001 —— KG 异常也作为一致性矛盾上报
        problems = [f"KG 一致性检查异常：{e}"]
    if not problems:
        return True, ""
    return False, "；".join(problems)


def _run_all_checks(model: Optional[Campaign], schema_err: str, world: Any) -> list[dict]:
    results: list[dict] = []

    def emit(cid: str, passed: bool, evidence: str = "") -> None:
        meta = _CHECK_META[cid]
        results.append({
            "id": cid, "name": meta["name"], "dims": list(meta["dims"]),
            "passed": bool(passed), "evidence": evidence,
        })

    if model is None:
        emit("schema_valid", False, schema_err)
        for cid in _CHECK_META:
            if cid != "schema_valid":
                emit(cid, False, "数据无法解析为 Campaign，跳过该检查")
        return results

    if schema_err:
        # 宽松构造丢弃了未知键：schema 漂移必须按原始错误判 False（G5 评审修正）
        emit("schema_valid", False, schema_err)
    else:
        ok, ev = _check_schema(model)
        emit("schema_valid", ok, ev)
    ok, ev = _check_id_unique(model)
    emit("id_unique", ok, ev)
    ok, ev = _check_refs_resolvable(model)
    emit("refs_resolvable", ok, ev)
    ok, ev = _check_kg_consistent(model, world)
    emit("kg_consistent", ok, ev)

    # —— 结构计数检查 ——
    act_problems = [f"acts[{a.id}] 没有任何场景" for a in model.acts if not a.scenes]
    emit("act_has_scene", not act_problems, "；".join(act_problems))

    empty_scenes = [
        f"acts[{a.id}].scenes[{s.id}] 没有任何事件"
        for a in model.acts for s in a.scenes if not s.events
    ]
    emit("scene_has_event", not empty_scenes, "；".join(empty_scenes))

    no_personality = [
        f"npcs[{nid}].personality 为空" for nid, n in model.npcs.items() if not n.personality
    ]
    emit("npc_personality", not no_personality, "；".join(no_personality))

    unlinked = [
        f"clues[{c.id}] 没有 linked_npc_ids 也没有 linked_event_ids"
        for c in model.clues if not c.linked_npc_ids and not c.linked_event_ids
    ]
    emit("clue_linked", not unlinked, "；".join(unlinked))

    # —— 深度（结构计数）——
    scenes = [s for a in model.acts for s in a.scenes]
    if scenes:
        avg = sum(len(s.events) for s in scenes) / len(scenes)
        emit("event_density", avg >= 2, f"平均每场景事件数 {avg:.1f} < 2")
    else:
        emit("event_density", False, "没有任何场景，事件密度不可计算")

    missing_setting = [
        f"acts[{a.id}].scenes[{s.id}].setting 缺少 time 或 place（{s.setting}）"
        for a in model.acts for s in a.scenes
        if not (s.setting.get("time") and s.setting.get("place"))
    ]
    emit("setting_complete", not missing_setting, "；".join(missing_setting))

    flat_npcs = [
        f"npcs[{nid}] 无 description 且无 personality"
        for nid, n in model.npcs.items() if not n.description and not n.personality
    ]
    emit("npc_described", not flat_npcs, "；".join(flat_npcs))

    plain_clues = [f"clues[{c.id}].description 为空" for c in model.clues if not c.description]
    emit("clue_described", not plain_clues, "；".join(plain_clues))

    n_rel = len(model.relations)
    emit("relation_richness", n_rel >= 2, f"世界知识图谱关系数 {n_rel} < 2")

    # —— 可玩性 ——
    empty_events = [
        f"events[{e.id}]（acts[{a.id}].scenes[{s.id}]）description 为空"
        for a in model.acts for s in a.scenes for e in s.events if not e.description
    ]
    emit("event_complete", not empty_events, "；".join(empty_events))

    has_branch = any(
        len(e.next_event_ids) >= 2 for a in model.acts for s in a.scenes for e in s.events
    )
    emit("branching", has_branch, "没有任何事件存在 ≥2 条后续分支（next_event_ids）")

    return results


# --------------------------------------------------------------------------- #
# 维度评分
# --------------------------------------------------------------------------- #

def _score_dims(checks: list[dict]) -> dict[str, dict]:
    dims: dict[str, dict] = {}
    for dim in DIMENSIONS:
        cs = [c for c in checks if dim in c["dims"]]
        if not cs:
            dims[dim] = {"score": 5, "evidence": []}
            continue
        passed = sum(1 for c in cs if c["passed"])
        score = max(1, int(5 * passed / len(cs) + 0.5))
        evidence = [c["evidence"] for c in cs if not c["passed"] and c["evidence"]]
        dims[dim] = {"score": score, "evidence": evidence}
    return dims


def run_deterministic(campaign: Any, world: Any = None) -> dict:
    """确定性评估入口。

    :param campaign: Campaign 实例或原始 dict（dict 先过 models 校验，失败则宽松构造继续）
    :param world: WorldGraph；None 时按 campaign 自动 build（kg.build_from_campaign）
    :return: {dims: {4 维 {score, evidence[]}}, total, checks[]}
    """
    model, schema_err = _coerce_campaign(campaign)
    checks = _run_all_checks(model, schema_err, world)
    dims = _score_dims(checks)
    total = round(sum(d["score"] for d in dims.values()) / len(dims), 1)
    return {"dims": dims, "total": total, "checks": checks}
