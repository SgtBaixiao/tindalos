"""自进化循环：eval → 建议提取 → 确定性修复应用 → 重建 world → 复评（依赖 t4 pipeline / t5 eval）。

evolve(campaign, world, pipeline, evaluator, rounds=2, out_path=None) -> {
    campaign: Campaign,      # 修复后的剧本（输入不被修改，内部深拷贝）
    report:   dict,          # 末轮 eval_report（4 维总表 + 四类归因）
    loop_log: list[dict],    # 自进化简历：每轮 {round, applied, failed, pending,
                             #   score_before, score_after, delta, evidence}
    pending:  list[dict],    # LLM 建议（人审待定，不自动应用）
}

每轮循环：
  eval（deterministic 全量 + judge 可选）→ 建议提取 → 确定性修复自动应用 →
  重建 world → 复评 → loop_log.append(...)。收敛：当轮无修复、无失败且无分数提升
  → 提前终止（纯失败轮不触发收敛，下轮重试）；最多 rounds 轮。

确定性修复（自动应用，幂等）：
  (a) 悬空 npc 引用：引用未在 campaign.npcs 注册 → 注册该 NPC（名字从引用场景提取，
      剧情内引用保留）；
  (b) 空 scene（无 event）：调 pipeline 局部重生成（传入 act 上下文与 premise，
      DeterministicGenerator 重产 entry/trigger/outcome 事件序列；事件 id 递增后缀
      重编号保证全局唯一——base 与 base-r 均被占用时继续 -r1/-r2，不无限循环）；
      失败单独记入 failed/evidence，applied 仅成功项；
  (c) KG 矛盾关系：倒置窗（valid_to < valid_from）与同对同型重叠窗 → 标记失效
      = 窗口置空（valid_to := valid_from）。空窗永不 active、不与任何窗重叠、不触发
      倒置检查——且 valid_to == valid_from 是稳定状态，天然真幂等（与时间无关，无需
      注入时钟）。重叠对「两条」全部失效（评审修正：伙伴为永久窗 valid_to=None 时，
      仅失效后一条无法消解）；重复窗（完全相等）不视为矛盾（与 kg 同语义）；
  (d) 无 linked 目标（或 linked 全部悬空）的 clue 实体：补 linked_event 指向首幕首事件；
  (e) LLM 建议仅记录 pending，不自动应用（人审待定）。

输入归一化：campaign 可为 Campaign 实例（含 model_construct 未校验的坏状态）或 dict；
dict 无法通过 models 校验时宽松构造（model_construct，与 t5 eval 同哲学），不 raise。
rounds=0（或负数）→ 不进化：仅做一次基线评估，返回原剧本、空 loop_log 与基线 report。

全程确定性：固定种子的 DeterministicGenerator + 时间无关的失效标记 → 同输入两次运行
结果一致（幂等）；零网络零 LLM（judge 缺省关闭）。
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from pydantic import ValidationError

from tindalos.eval_ import eval_report, run_deterministic
from tindalos.generator import DeterministicGenerator
from tindalos.kg import (
    WorldGraph,
    build_from_campaign,
    parse_time,
    time_key,
    window_overlaps,
)
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

# 场景标题中的结构性提示词：含这些词的标题不作为 NPC 名字提取候选
_STRUCTURAL_HINTS = ("幕", "场景", "第", "场", "act", "scene", "sc-")


def _default_now() -> datetime:
    return datetime.now(timezone.utc)


def _rel_type(rel: WorldRelation) -> str:
    return rel.type.value if isinstance(rel.type, RelationType) else str(rel.type)


# ---------------------------------------------------------------- 输入归一化

def _coerce_type(t: Any) -> Any:
    """把关系类型归一化为 RelationType（中文标签/枚举名 → 成员）；无法识别则原样保留（宽松构造不校验）。"""
    if isinstance(t, RelationType):
        return t
    if isinstance(t, str):
        for m in RelationType:
            if t == m.value or t == m.name:
                return m
    return t


def _construct_loose(raw: dict) -> Campaign:
    """宽松构造（model_construct 跳过校验）：dict 无法通过 models 校验时也能继续修复循环。

    与 t5 eval 的宽松哲学一致（坏剧本本就是本循环的输入对象，不应因 schema 缺陷而中断）。
    """
    return Campaign.model_construct(
        id=raw.get("id", ""),
        title=raw.get("title", ""),
        premise=raw.get("premise", ""),
        acts=[
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
        ],
        npcs={
            k: NPC.model_construct(
                id=v.get("id", k), name=v.get("name", ""), archetype=v.get("archetype", ""),
                personality=v.get("personality", []), description=v.get("description", ""),
                acts_roles=v.get("acts_roles", {}),
            )
            for k, v in raw.get("npcs", {}).items()
        },
        clues=[
            Clue.model_construct(
                id=c.get("id", ""), name=c.get("name", ""), description=c.get("description", ""),
                linked_npc_ids=c.get("linked_npc_ids", []),
                linked_event_ids=c.get("linked_event_ids", []), found_at=c.get("found_at"),
            )
            for c in raw.get("clues", [])
        ],
        relations=[
            WorldRelation.model_construct(
                source=r.get("source", ""), target=r.get("target", ""), type=_coerce_type(r.get("type")),
                label=r.get("label", ""), valid_from=r.get("valid_from", ""),
                valid_to=r.get("valid_to"), note=r.get("note"),
            )
            for r in raw.get("relations", [])
        ],
    )


def _coerce_campaign(campaign: Any) -> tuple[Campaign, str]:
    """归一化输入并深拷贝 → (campaign, schema_err)。

    dict 校验失败不 raise——宽松构造后继续修复循环（与 t5 宽松构造哲学一致）；
    schema_err 携带原始 ValidationError 文本，保证 eval 证据链不丢失 schema 漂移信号
    （models.Campaign extra=forbid 的检出能力在自进化证据链中必须可见）。
    """
    if isinstance(campaign, Campaign):
        return campaign.model_copy(deep=True), ""
    if isinstance(campaign, dict):
        try:
            return Campaign.model_validate(campaign), ""
        except ValidationError as e:
            err_text = str(e) if str(e) else "Unknown ValidationError"
            return _construct_loose(campaign), "\n".join(err_text.splitlines()[:3])
    raise TypeError(f"不支持的 campaign 类型: {type(campaign).__name__}")


def _coerce_world(campaign: Campaign, world: Any) -> WorldGraph:
    """world 归一化：None → 由 campaign 重建；dict（pipeline 状态的 world.to_json 视图）→ from_json。"""
    if world is None:
        return build_from_campaign(campaign)
    if isinstance(world, dict):
        try:
            return WorldGraph.from_json(world)
        except Exception:  # noqa: BLE001 —— 视图损坏则退回重建
            return build_from_campaign(campaign)
    if hasattr(world, "consistency_check") and hasattr(world, "relations_of"):
        return world
    return build_from_campaign(campaign)


def _resolve_generator(pipeline: Any, campaign: Campaign) -> Any:
    """从 pipeline 解析场景重生成器（t4 集成点）。

    接受：Generator 协议实例（有 generate_scene）/ 编译后的 LangGraph app（挂 .generator）/
    None → 回退 DeterministicGenerator（seed 由 campaign.id 派生，保证确定性）。
    """
    if pipeline is not None:
        if hasattr(pipeline, "generate_scene"):
            return pipeline
        gen = getattr(pipeline, "generator", None)
        if gen is not None and hasattr(gen, "generate_scene"):
            return gen
    cid = getattr(campaign, "id", "campaign") or "campaign"
    return DeterministicGenerator(seed=f"evolve:{cid}")


# ---------------------------------------------------------------- 修复 (a)-(d)

def _all_event_ids(campaign: Campaign) -> set[str]:
    return {e.id for a in campaign.acts for s in a.scenes for e in s.events}


def _first_event_id(campaign: Campaign) -> Optional[str]:
    """首幕首场景首事件（clue 补 linked 的目标）。"""
    for act in campaign.acts:
        for scene in act.scenes:
            if scene.events:
                return scene.events[0].id
    return None


def _extract_npc_name(campaign: Campaign, nid: str) -> str:
    """从首个引用该 NPC 的场景提取名字；场景标题含结构提示词时回退 id 人类化。"""
    for act in campaign.acts:
        for scene in act.scenes:
            if nid in scene.npc_ids and scene.title:
                title = scene.title.strip()
                if title and not any(h in title.lower() for h in _STRUCTURAL_HINTS):
                    return title[:16]
    humanized = re.sub(r"[-_]+", " ", nid).strip().title()
    return humanized or nid


def _fix_dangling_npcs(campaign: Campaign, applied: list[str]) -> None:
    """(a) 悬空 npc 引用：注册该 NPC 到 campaign.npcs（名字取自场景），剧本内引用保留。"""
    for act in campaign.acts:
        for nid in act.npc_ids:
            if nid not in campaign.npcs:
                _register_npc(campaign, nid, "", applied)
        for scene in act.scenes:
            for nid in scene.npc_ids:
                if nid not in campaign.npcs:
                    _register_npc(campaign, nid, scene.id, applied)


def _register_npc(campaign: Campaign, nid: str, scene_id: str, applied: list[str]) -> None:
    if nid in campaign.npcs:
        return
    name = _extract_npc_name(campaign, nid)
    campaign.npcs[nid] = NPC(
        id=nid,
        name=name,
        archetype="佚名角色",
        personality=["来历不明"],
        description=(
            f"自进化修复注册：原为悬空引用（场景 {scene_id}），"
            f"已按场景提取名字「{name}」。"
        ),
        acts_roles={},
    )
    applied.append(f"注册悬空 NPC {nid}（名「{name}」）")


def _regenerate_scene(
    gen: Any, campaign: Campaign, act: Any, scene: Any, used_ids: set[str]
) -> int:
    """用生成器局部重产空场景的事件序列。

    id 递增后缀重编号（base 与 base-r 均被占用时继续 -r1/-r2，绝不无限循环）；
    全部事件构造成功后才提交新 id（失败不留幻影 id，保持跨轮确定性）。
    """
    draft = gen.generate_scene(act.title, campaign.premise, list(scene.npc_ids))
    events: list[Event] = []
    new_ids: set[str] = set()
    for j, ev in enumerate(draft.get("events", []) or []):
        data = dict(ev)
        base = f"{scene.id}-ev-{j + 1}"
        eid, counter = base, 0
        while eid in used_ids:
            counter += 1
            eid = f"{base}-r{counter}"  # 递增后缀：占用再多也不死循环
        new_ids.add(eid)
        data["id"] = eid
        events.append(Event(**data))
    for j, ev in enumerate(events):
        ev.next_event_ids = [events[k].id for k in range(j + 1, len(events))]
    scene.events = events
    used_ids.update(new_ids)
    return len(events)


def _fix_empty_scenes(campaign: Campaign, gen: Any, applied: list[str], failed: list[str]) -> None:
    """(b) 空 scene：调 pipeline 局部重生成事件（传入 act 上下文）。

    失败仅记入 failed（并进 evidence），applied 只含成功项（评审修正）。
    """
    used_ids = _all_event_ids(campaign)
    for act in campaign.acts:
        for scene in act.scenes:
            if scene.events:
                continue
            try:
                n = _regenerate_scene(gen, campaign, act, scene, used_ids)
            except Exception as e:  # noqa: BLE001 —— 生成失败记 evidence，不阻塞循环
                failed.append(f"重生成空场景 {scene.id} 失败（{e}）")
                continue
            if n > 0:
                applied.append(f"重生成空场景 {scene.id}（{n} 个事件）")
            else:
                failed.append(f"重生成空场景 {scene.id} 返回空事件序列（n=0）")


# ---------------------------------------------------------------- KG 矛盾修复 (c)

def _already_expired(rel: WorldRelation) -> bool:
    """空窗（valid_to == valid_from，kg 时间语义相等）即已失效——真幂等守卫，与时间无关。"""
    if rel.valid_to is None:
        return False
    f, t = rel.valid_from, rel.valid_to
    try:
        return parse_time(t) == parse_time(f)
    except Exception:  # noqa: BLE001 —— 不可解析时间退化为字符串相等
        return str(t) == str(f)


def _is_inverted(rel: WorldRelation) -> bool:
    """有效窗倒置（valid_to < valid_from）——复用 kg 时间比较公共 API（评审：不用裸字符串比较）。"""
    return (
        rel.valid_from is not None
        and rel.valid_to is not None
        and time_key(rel.valid_to, upper=True) < time_key(rel.valid_from)
    )


def _same_window(r1: WorldRelation, r2: WorldRelation) -> bool:
    """两条关系有效窗完全相等（原始值相等，与 kg consistency_check 的 w1==w2 语义一致）——重复窗不视为矛盾。"""
    return str(r1.valid_from) == str(r2.valid_from) and str(r1.valid_to) == str(r2.valid_to)


def _expire(rel: WorldRelation, applied: list[str], reason: str) -> None:
    """标记关系失效：窗口置空（valid_to := valid_from）。

    空窗 [f, f) 永不 active、不与任何窗重叠、不触发倒置检查；valid_to == valid_from
    是稳定状态，天然幂等（与时间无关，无需注入时钟）。幂等守卫非时间戳相等（评审）。
    """
    if _already_expired(rel):
        return
    rel.valid_to = rel.valid_from
    applied.append(f"失效 KG 矛盾关系 {rel.source}→{rel.target}({_rel_type(rel)})：{reason}")


def _fix_kg_conflicts(campaign: Campaign, applied: list[str]) -> None:
    """(c) KG 矛盾关系：倒置窗 / 同对同型重叠窗 → 标记失效（空窗）。

    评审修正：重叠对「两条」全部失效——仅失效后一条在伙伴为永久窗（valid_to=None）
    时无法消解（后一条空窗后，永久窗与任何非空窗依旧重叠）；空窗标记使重叠对彻底
    消解，复评 kg_consistent 不再检出矛盾。
    """
    groups: dict[tuple[str, str, str], list[tuple[int, WorldRelation]]] = defaultdict(list)
    for idx, rel in enumerate(campaign.relations):
        groups[(rel.source, rel.target, _rel_type(rel))].append((idx, rel))

    expire: set[int] = set()
    for items in groups.values():
        items.sort(key=lambda t: (str(t[1].valid_from or ""), t[0]))
        # 倒置窗：直接失效
        for idx, rel in items:
            if _is_inverted(rel):
                expire.add(idx)
        # 同对同型重叠窗：重叠对「两条」全部失效（完全相等的重复窗除外，与 kg 同语义）
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                ri, rj = items[i][1], items[j][1]
                if _same_window(ri, rj):
                    continue
                if window_overlaps((ri.valid_from, ri.valid_to), (rj.valid_from, rj.valid_to)):
                    expire.add(items[i][0])
                    expire.add(items[j][0])

    for idx in sorted(expire):
        rel = campaign.relations[idx]
        _expire(rel, applied, "倒置窗" if _is_inverted(rel) else "重叠窗")


def _fix_unlinked_clues(campaign: Campaign, applied: list[str]) -> None:
    """(d) 无 linked 目标（或 linked 全部悬空）的 clue：补 linked_event 指向首幕首事件。"""
    first_event = _first_event_id(campaign)
    if first_event is None:
        return
    event_ids = _all_event_ids(campaign)
    for clue in campaign.clues:
        if any(e in event_ids for e in clue.linked_event_ids):
            continue
        old = list(clue.linked_event_ids)
        clue.linked_event_ids = [first_event]
        applied.append(f"补线索 {clue.id} 的 linked_event → {first_event}")


def _apply_fixes(campaign: Campaign, gen: Any) -> tuple[list[str], list[str]]:
    """确定性修复自动应用：返回 (本轮成功修复清单, 本轮失败证据清单)。"""
    applied: list[str] = []
    failed: list[str] = []
    _fix_dangling_npcs(campaign, applied)           # (a)
    _fix_empty_scenes(campaign, gen, applied, failed)  # (b) 失败单记 failed
    _fix_kg_conflicts(campaign, applied)            # (c)
    _fix_unlinked_clues(campaign, applied)          # (d)
    return applied, failed


# ---------------------------------------------------------------- 评估与证据

def _evaluate(
    campaign: Campaign, world: WorldGraph, evaluator: Callable, judge: Any,
    schema_err: str = "",
) -> tuple[dict, Optional[dict], dict]:
    """deterministic 全量 + judge 可选 → (deterministic 结果, judge 结果, eval_report)。"""
    det = evaluator(campaign, world)
    if schema_err:
        det = {**det, "schema_err": schema_err}  # 证据链携带 schema 漂移（G5 复审修正）
    judge_res: Optional[dict] = None
    if judge is not None:
        try:
            judge_res = judge.evaluate(campaign, world, det)
        except Exception as e:  # noqa: BLE001 —— 裁判异常降级，不阻塞进化循环
            judge_res = {"judge": "none", "reason": f"judge_error: {e}"}
    report = eval_report(campaign, world, det, judge_res)
    return det, judge_res, report


def _collect_pending(judge_res: Optional[dict], r: int) -> list[dict]:
    """(e) LLM 建议仅记录 pending（人审待定），不自动应用。"""
    if not judge_res or judge_res.get("judge") != "llm":
        return []
    return [
        {
            "round": r,
            "dim": dim,
            "comment": v.get("comment"),
            "suggestion": v.get("suggestion"),
            "status": "pending",
        }
        for dim, v in (judge_res.get("dims") or {}).items()
        if isinstance(v, dict)
    ]


def _round_evidence(
    applied: list[str],
    failed: list[str],
    score_before: float,
    score_after: float,
    det_before: dict,
    det_after: dict,
) -> list[str]:
    """本轮证据：分数变化 + 失败重生成 + 修复前失败检查 + 复评残留失败检查（字段级）。"""
    evidence = [f"total {score_before} -> {score_after} (delta {round(score_after - score_before, 2)})"]
    for f in failed:
        evidence.append(f"[failed] {f}")
    for c in det_before.get("checks", []):
        if not c.get("passed") and c.get("evidence"):
            evidence.append(f"[before] {c['id']}: {c['evidence']}")
    for c in det_after.get("checks", []):
        if not c.get("passed") and c.get("evidence"):
            evidence.append(f"[after] {c['id']}: {c['evidence']}")
    return evidence


# ---------------------------------------------------------------- 主入口

def evolve(
    campaign: Any,
    world: Any,
    pipeline: Any,
    evaluator: Optional[Callable] = None,
    rounds: int = 2,
    out_path: Optional[str] = None,
    *,
    judge: Any = None,
    now_fn: Optional[Callable[[], datetime]] = None,
) -> dict:
    """自进化循环主入口。

    :param campaign: Campaign 实例（可含未校验的坏状态）或 dict（校验失败宽松构造）；
        内部深拷贝，不修改输入
    :param world: WorldGraph；None → 由 campaign 重建；dict → WorldGraph.from_json
    :param pipeline: 场景重生成器——Generator 协议实例（如 DeterministicGenerator）/
        编译后的 LangGraph app（t4 集成，取 .generator 或回退固定种子确定性生成器）
    :param evaluator: 确定性评估 callable(campaign, world)->{dims,total,checks}；
        缺省 tindalos.eval_.run_deterministic
    :param rounds: 循环轮数上限（默认 2）；0 或负数 = 不进化——空 loop_log、
        剧本原样、仅基线评估 report
    :param out_path: 可选；写 {campaign, report, loop_log, pending} JSON
    :param judge: 可选 LLMJudge（默认 None）；建议只进 pending 不自动应用
    :param now_fn: 保留以兼容旧调用——失效标记已改为时间无关的空窗（valid_to==valid_from），
        幂等不再依赖注入时钟
    """
    campaign, schema_err = _coerce_campaign(campaign)
    world = _coerce_world(campaign, world)
    evaluator = evaluator or run_deterministic
    gen = _resolve_generator(pipeline, campaign)
    rounds = max(0, int(rounds))

    loop_log: list[dict] = []
    pending: list[dict] = []
    report: dict = {}

    if rounds == 0:
        # 0=不进化：仅做一次基线评估（评审钉死语义）
        _, _, report = _evaluate(campaign, world, evaluator, judge, schema_err)
        if out_path is not None:
            _write_out(out_path, campaign, report, loop_log, pending)
        return {"campaign": campaign, "report": report, "loop_log": loop_log, "pending": pending}

    for r in range(1, rounds + 1):
        det_before, judge_before, _ = _evaluate(campaign, world, evaluator, judge, schema_err)
        score_before = float(det_before.get("total", 0.0))

        applied, failed = _apply_fixes(campaign, gen)
        round_pending = _collect_pending(judge_before, r)
        pending.extend(round_pending)

        if applied:
            world = build_from_campaign(campaign)  # 重生成受影响节点后重建 world

        det_after, _, report = _evaluate(campaign, world, evaluator, judge, schema_err)
        score_after = float(det_after.get("total", 0.0))
        delta = round(score_after - score_before, 2)

        loop_log.append(
            {
                "round": r,
                "applied": applied,
                "failed": failed,
                "pending": round_pending,
                "score_before": score_before,
                "score_after": score_after,
                "delta": delta,
                "evidence": _round_evidence(
                    applied, failed, score_before, score_after, det_before, det_after
                ),
                "schema_err": schema_err if r == 1 else "",
            }
        )

        # 收敛：当轮无修复、无失败且无分数提升 → 提前终止（纯失败轮忽略，下轮重试）
        if not applied and not failed and score_after <= score_before:
            break

    if out_path is not None:
        _write_out(out_path, campaign, report, loop_log, pending)

    return {
        "campaign": campaign,
        "report": report,
        "loop_log": loop_log,
        "pending": pending,
    }


def _write_out(
    out_path: str, campaign: Campaign, report: dict, loop_log: list[dict], pending: list[dict]
) -> Path:
    p = Path(out_path)
    if p.is_dir() or str(out_path).endswith(("/", "\\")):
        p = p / "evolve_report.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "campaign": campaign.model_dump(mode="json"),
        "report": report,
        "loop_log": loop_log,
        "pending": pending,
    }
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


__all__ = ["evolve"]
