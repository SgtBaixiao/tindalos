"""局部节点重生成（task t12-regenerate）：对 scene/event/npc/clue 单节点重产，其余保持不动。

regenerate_node(campaign, node_id, generator) -> (Campaign, applied[])：
  - scene-*：重产该场景全部事件（保留 scene.id/title/setting/npc_ids；事件 id 用
    scene.id + 递增后缀（-ev-N，被占用时 -rN 递增，绝不无限循环），next_event_ids
    重建为链式 j → j+1..n）；
  - event-*：只重产 description/conditions（id/title/kind/next_event_ids 保持）；
  - npc-*：重注入 archetype/personality/description（id/name/acts_roles 保持）；
  - clue-*：重产 name/description（id/linked_npc_ids/linked_event_ids/found_at 保持）；
  - 未知 id → ValueError（serve 转 400 / cli 退出码非 0）。

输入可为 Campaign 或 dict（校验失败宽松构造，与 t5/t6 同哲学）；内部深拷贝，不修改输入。
重生成后重建 world（build_from_campaign + consistency_check）+ 宽松构造 + 严格校验
（models 跨层引用规则）；任一失败 → 回滚为输入原样 + UserWarning（applied 为空）。

scene 事件重产公共函数 regenerate_scene_events 收编自 evolve._regenerate_scene
（evolve 改 import），供自进化空场景修复与 regenerate_node 共用。
"""

from __future__ import annotations

import warnings
from typing import Any

from pydantic import ValidationError

from tindalos.generator import DeterministicGenerator
from tindalos.kg import build_from_campaign
from tindalos.models import (
    Act,
    Campaign,
    Clue,
    Event,
    NPC,
    Scene,
    construct_loose_campaign,
    normalize_relation_types,
)

_KNOWN_KINDS = ("act", "scene", "event", "npc", "clue")


def _clip(text: str, limit: int = 24) -> str:
    text = " ".join((text or "").split()).strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _strip_kind_prefix(node_id: str) -> str:
    """兼容 ScriptGraph 呈现层 id（"scene:act-1-scene-1"）→ 领域 id（"act-1-scene-1"）。"""
    if ":" in node_id:
        kind, _, rest = node_id.partition(":")
        if kind in _KNOWN_KINDS:
            return rest
    return node_id


def _coerce_campaign(campaign: Any) -> Campaign:
    """输入归一化 + 深拷贝 → Campaign（dict 校验失败宽松构造，不 raise）。"""
    if isinstance(campaign, Campaign):
        return campaign.model_copy(deep=True)
    if isinstance(campaign, dict):
        try:
            return Campaign.model_validate(normalize_relation_types(campaign))
        except ValidationError:
            return construct_loose_campaign(campaign)
    raise TypeError(f"不支持的 campaign 类型: {type(campaign).__name__}")


def _all_event_ids(campaign: Campaign) -> set[str]:
    return {e.id for a in campaign.acts for s in a.scenes for e in s.events}


def _find_scene(campaign: Campaign, scene_id: str) -> tuple[Act, Scene]:
    for act in campaign.acts:
        for scene in act.scenes:
            if scene.id == scene_id:
                return act, scene
    raise ValueError(f"未知场景 id：{scene_id}")


def _find_event(campaign: Campaign, event_id: str) -> tuple[Act, Scene, Event]:
    for act in campaign.acts:
        for scene in act.scenes:
            for ev in scene.events:
                if ev.id == event_id:
                    return act, scene, ev
    raise ValueError(f"未知事件 id：{event_id}")


def _find_clue(campaign: Campaign, clue_id: str) -> Clue:
    for clue in campaign.clues:
        if clue.id == clue_id:
            return clue
    raise ValueError(f"未知线索 id：{clue_id}")


def _classify_node(campaign: Campaign, node_id: str) -> str | None:
    """按 id 定位节点种类（scene/event/npc/clue）；未知返回 None。"""
    if node_id in campaign.npcs:
        return "npc"
    if any(c.id == node_id for c in campaign.clues):
        return "clue"
    if any(s.id == node_id for a in campaign.acts for s in a.scenes):
        return "scene"
    if node_id in _all_event_ids(campaign):
        return "event"
    return None


# ---------------------------------------------------------------- 公共：场景事件重产

def regenerate_scene_events(
    generator: Any,
    campaign: Campaign,
    act: Any,
    scene: Any,
    used_ids: set[str],
    *,
    salt: str | None = None,
) -> int:
    """用生成器重产场景事件序列（公共化自 evolve._regenerate_scene；evolve 改 import）。

    - 只替换 scene.events（scene 字段由调用方保证不动）；
    - 事件 id = scene.id + "-ev-" + 序号，被占用时递增后缀（-r1/-r2，绝不无限循环）；
    - 全部事件构造成功后才提交新 id（失败不留幻影 id，保持跨轮确定性）；
    - next_event_ids 重建为链式（j → j+1..n）。
    返回新事件数量（0 = 生成器无事件产出，scene.events 置空由调用方处置）。
    """
    salt = salt or act.title
    draft = generator.generate_scene(salt, campaign.premise, list(scene.npc_ids))
    events: list[Event] = []
    new_ids: set[str] = set()
    for j, ev in enumerate(draft.get("events", []) or []):
        data = dict(ev)
        base = f"{scene.id}-ev-{j + 1}"
        eid, counter = base, 0
        while eid in used_ids:
            counter += 1
            eid = f"{base}-r{counter}"
        new_ids.add(eid)
        data["id"] = eid
        events.append(Event(**data))
    for j, ev in enumerate(events):
        ev.next_event_ids = [events[k].id for k in range(j + 1, len(events))]
    scene.events = events
    used_ids.update(new_ids)
    return len(events)


# ---------------------------------------------------------------- 单节点重生成

def _regen_scene_node(generator: Any, campaign: Campaign, scene_id: str, applied: list[str]) -> None:
    act, scene = _find_scene(campaign, scene_id)
    # 占用集合 = 其他场景的事件 id：新事件可复用本场景旧事件的 scene.id 模式 → 事件 id 稳定；
    # 若同 id 也被其他场景占用（集合差会误删）则走递增后缀（-rN）。
    used = {e.id for a in campaign.acts for s in a.scenes if s.id != scene.id for e in s.events}
    n = regenerate_scene_events(
        generator, campaign, act, scene, used, salt=f"{act.title}·{scene.title}"
    )
    if n == 0:
        raise ValueError(f"重生成场景 {scene_id} 返回空事件序列")
    applied.append(f"重生成场景 {scene_id} 的事件（{n} 个）")


def _regen_event_node(generator: Any, campaign: Campaign, event_id: str, applied: list[str]) -> None:
    act, scene, ev = _find_event(campaign, event_id)
    draft = generator.generate_scene(f"{act.title}·{scene.title}", campaign.premise, list(scene.npc_ids))
    events = draft.get("events") or []
    if not events:
        raise ValueError(f"重生成事件 {event_id} 无生成素材")
    src = next((e for e in events if e.get("kind") == ev.kind), events[0])
    ev.description = str(src.get("description") or "")
    ev.conditions = [str(c) for c in (src.get("conditions") or [])]
    applied.append(f"重生成事件 {event_id} 的描述与条件")


def _regen_npc_node(generator: Any, campaign: Campaign, npc_id: str, applied: list[str]) -> None:
    npc: NPC = campaign.npcs[npc_id]
    drafts = generator.generate_npcs(campaign.premise, max(1, len(campaign.npcs)))
    if not drafts:
        raise ValueError(f"重生成 NPC {npc_id} 返回空草案")
    src = next((d for d in drafts if d.get("id") == npc_id), None)
    if src is None:
        # id 不匹配时按 archetype/name 相似匹配；仍无 → raise 走回滚（不静默错注入，评审修正）
        src = next((d for d in drafts if d.get("archetype") == npc.archetype), None)
        if src is None:
            src = next((d for d in drafts if d.get("name") == npc.name), None)
        if src is None:
            raise ValueError(f"重生成 NPC {npc_id}：生成草案无匹配 id/archetype/name，回滚")
    npc.archetype = str(src.get("archetype") or npc.archetype)
    npc.personality = [str(p) for p in (src.get("personality") or []) if str(p).strip()]
    npc.description = str(src.get("description") or "")
    applied.append(f"重注入 NPC {npc_id} 的原型/人格/描述")


def _regen_clue_node(generator: Any, campaign: Campaign, clue_id: str, applied: list[str]) -> None:
    clue = _find_clue(campaign, clue_id)
    draft = generator.generate_scene(f"{campaign.title}·{clue_id}", campaign.premise, [])
    setting = draft.get("setting") or {}
    place = str(setting.get("place") or "").strip()
    time_ = str(setting.get("time") or "").strip()
    if not place or not time_:
        raise ValueError(f"重生成线索 {clue_id} 无生成素材（场景草案缺 setting）")
    clue.name = f"{place}的线索"
    clue.description = (
        f"重生成线索：{time_}在{place}发现的物证，指向「{_clip(campaign.premise)}」背后的秘密。"
    )
    applied.append(f"重生成线索 {clue_id} 的名称与描述")


def _validate_regenerated(campaign: Campaign) -> None:
    """重建 world + 宽松构造 + 严格校验；任一失败抛异常（调用方回滚）。"""
    world = build_from_campaign(campaign)  # 重建 world
    problems = world.consistency_check()
    if problems:
        raise ValueError("重建 world 一致性失败：" + "；".join(problems[:3]))
    loose = construct_loose_campaign(campaign.model_dump(mode="json"))
    Campaign.model_validate(loose.model_dump(mode="json"))


# ---------------------------------------------------------------- 主入口

def regenerate_node(
    campaign: Any, node_id: str, generator: Any = None
) -> tuple[Campaign, list[str]]:
    """重生成单个节点 → (新 Campaign 深拷贝, applied 清单)；未知 id → ValueError。

    输入 Campaign/dict 均不修改（内部深拷贝）；重生成后重建 world + 宽松构造 + 严格校验，
    任一失败（含生成器异常/引用悬空）→ 回滚为输入原样 + UserWarning（applied 为空）。
    """
    original = _coerce_campaign(campaign)
    work = original.model_copy(deep=True)
    gen = generator or DeterministicGenerator(seed=f"regenerate:{original.id}")
    nid = _strip_kind_prefix(node_id)

    kind = _classify_node(work, nid)
    if kind is None:
        raise ValueError(
            f"未知节点 id：{node_id}（支持 scene-*/event-*/npc-*/clue-*）"
        )

    applied: list[str] = []
    try:
        if kind == "npc":
            _regen_npc_node(gen, work, nid, applied)
        elif kind == "clue":
            _regen_clue_node(gen, work, nid, applied)
        elif kind == "scene":
            _regen_scene_node(gen, work, nid, applied)
        else:  # event
            _regen_event_node(gen, work, nid, applied)
        _validate_regenerated(work)
    except Exception as e:  # noqa: BLE001 —— 校验/生成失败回滚原样 + 告警（契约）
        warnings.warn(
            f"重生成节点 {node_id} 失败，已回滚为原样：{e}",
            UserWarning, stacklevel=2,
        )
        return original, []
    return work, applied


__all__ = ["regenerate_node", "regenerate_scene_events"]
