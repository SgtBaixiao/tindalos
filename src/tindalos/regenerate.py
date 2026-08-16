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

import hashlib
import json
import warnings
from pathlib import Path
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
    campaign: Any,
    node_id: str,
    generator: Any = None,
    *,
    db_path: Path | None = None,
) -> tuple[Campaign, list[str]]:
    """重生成单个节点 → (新 Campaign 深拷贝, applied 清单)；未知 id → ValueError。

    输入 Campaign/dict 均不修改（内部深拷贝）；重生成后重建 world + 宽松构造 + 严格校验，
    任一失败（含生成器异常/引用悬空）→ 回滚为输入原样 + UserWarning（applied 为空）。

    P1 #3 记忆一致性钩子：传入 db_path（memory_entries.sqlite 路径）时，成功路径自动
    同步记忆（capture_episodic + 相关 semantic/longterm 置 superseded 并写新版）；
    失败/回滚不写记忆。默认 None → 纯重生成，行为不变（cli/serve 契约）。
    """
    original = _coerce_campaign(campaign)
    work = original.model_copy(deep=True)
    gen = generator or DeterministicGenerator(seed=f"regenerate:{original.id}")
    nid = _strip_kind_prefix(node_id)

    kind = _classify_node(work, nid)
    if kind is None:
        raise ValueError(
            f"未知节点 id：{node_id}（支持 act-N-scene-N / act-N-scene-N-ev-N / npc-N / clue-N）"
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
    if db_path is not None:
        _apply_memory_hook(work, nid, kind, db_path)  # 仅成功路径（失败/回滚已提前返回）
    return work, applied


# ---------------------------------------------------------------- P1 #3 记忆一致性钩子


def _clip_200(text: str) -> str:
    """与 memory_entries._clip 同构：纯截断到 200 字（不复用 24 字 _clip，避免哈希判重漂移）。"""
    return text if len(text) <= 200 else text[:200] + "…"


def _related_subject_keys(kind: str, node_id: str) -> list[str]:
    """节点种类 → 相关语义 subject_key（npc 节点 → npc:<id>；scene 节点 → place:<id>）。"""
    if kind == "npc":
        return [f"npc:{node_id}"]
    if kind == "scene":
        return [f"place:{node_id}"]
    return []


def _entry_refers_node(row: dict, node_id: str, episodic_id: str) -> bool:
    """条目 ref_ids 是否引用该节点（含事件的情景条目 id evm:<cid>:<event>）。"""
    try:
        refs = json.loads(row.get("ref_ids") or "[]")
    except (TypeError, ValueError):
        return False
    return node_id in refs or episodic_id in refs


def _related_memory_rows(
    campaign_id: str, subject_keys: list[str], node_id: str, db_path: Path | None
) -> list[dict]:
    """regenerate 改动的节点 → 相关 active semantic/longterm 条目。

    匹配：subject_key 由节点 id 派生（npc:<id>/place:<id>），或 ref_ids 引用该节点。
    """
    from tindalos import memory_entries as me

    episodic_id = f"evm:{campaign_id}:{node_id}"
    related: list[dict] = []
    for mt in ("semantic", "longterm"):
        for row in me.list_entries(campaign_id, mt, db_path, status="active"):
            if subject_keys and row.get("subject_key") in subject_keys:
                related.append(row)
            elif _entry_refers_node(row, node_id, episodic_id):
                related.append(row)
    return related


def _refresh_semantic_content(campaign: Campaign, row: dict) -> str | None:
    """由重生成后的 Campaign 重算语义条目内容（与 memory_entries._semantic_items 同构）。"""
    from tindalos.memory import npc_impression  # 延迟导入避免循环依赖

    sk = row.get("subject_key") or ""
    if sk.startswith("npc:"):
        npc = campaign.npcs.get(sk[len("npc:"):])
        if npc is None:
            return None
        return _clip_200(npc_impression(npc))
    if sk.startswith("place:"):
        scene_id = sk[len("place:"):]
        for act in campaign.acts:
            for scene in act.scenes:
                if scene.id != scene_id:
                    continue
                setting = scene.setting or {}
                time_, place = setting.get("time", ""), setting.get("place", "")
                if not (time_ or place):
                    return None
                return _clip_200(
                    f"[{act.title}·{scene.title}] 地点：{place or '未知'}，时间：{time_ or '未知'}"
                )
    return None


def _refresh_memory_content(
    campaign: Campaign, row: dict, db_path: Path | None
) -> str | None:
    """重生成后重算相关条目内容：semantic 从新 Campaign 派生；longterm 确定性重算。

    无法确定性重算（自定义 subject_key）→ None，钩子跳过该条（不擅自改动）。
    """
    if row.get("memory_type") == "semantic":
        return _refresh_semantic_content(campaign, row)
    if row.get("memory_type") == "longterm":
        from tindalos import memory_entries as me

        key = row.get("subject_key") or ""
        if key not in ("synopsis", "plotline", "npc_arcs"):
            return None
        episodic = me.list_entries(campaign.id, "episodic", db_path, status="active")
        semantic = me.list_entries(campaign.id, "semantic", db_path, status="active")
        return me._deterministic_longterm(campaign.id, key, episodic + semantic)
    return None


def _versioned_id(campaign_id: str, row: dict, content: str) -> str:
    """新版本条目 id：内容哈希后缀（与 memory_entries._apply_ops / _write_longterm 同构）。"""
    c_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]
    prefix = "sem" if row.get("memory_type") == "semantic" else "ltm"
    return f"{prefix}:{campaign_id}:{row.get('subject_key') or 'item'}:{c_hash}"


def _apply_memory_hook(campaign: Campaign, node_id: str, kind: str, db_path: Path) -> None:
    """regenerate 成功后的记忆一致性钩子（设计文档 §3.3 修复钩子 / 风险表第 4 行）。

    1. capture_episodic：整份 campaign 幂等 upsert（未变事件 content_hash 相同 → 跳过）；
    2. 相关 semantic/longterm（subject_key 命中该节点 / ref_ids 引用该节点）：
       内容有变化 → 写新版（id 含内容哈希）+ 旧版用 supersede_entries 置 superseded
       （supersedes_id 链）；内容未变 → 保持 active（无漂移即不版本化）。
    失败只告警不回滚：记忆是增强层，绝不阻断 regenerate 成功返回。
    """
    try:
        from tindalos import memory_entries as me

        me.capture_episodic(campaign, db_path)  # (a) 情景条目覆盖新内容

        subject_keys = _related_subject_keys(kind, node_id)
        related = _related_memory_rows(campaign.id, subject_keys, node_id, db_path)
        if not related:
            return
        to_version: list[tuple[dict, str]] = []
        for row in related:
            content = _refresh_memory_content(campaign, row, db_path)
            if content is None:
                continue
            if hashlib.sha256(content.encode("utf-8")).hexdigest() == row.get("content_hash"):
                continue  # 内容未变 → 无漂移，保持 active
            to_version.append((row, content))
        if not to_version:
            return
        me.supersede_entries(campaign.id, db_path, ids=[r["id"] for r, _ in to_version])  # (b) 旧版置 superseded
        conn = me._connect(db_path)
        try:
            for row, content in to_version:
                new_id = _versioned_id(campaign.id, row, content)
                me._insert_entry(
                    conn,
                    campaign.id,
                    row.get("memory_type") or "semantic",
                    new_id,
                    content,
                    subject_key=row.get("subject_key"),
                    ref_ids=json.loads(row.get("ref_ids") or "[]"),
                    source_episode=row.get("source_episode"),
                    importance=float(row.get("importance") or 0.6),
                )
                conn.execute(
                    "UPDATE memory_entries SET supersedes_id = ?, updated_at = ? WHERE id = ?",
                    (new_id, me._now(), row["id"]),
                )
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 —— 记忆增强失败绝不阻断 regenerate
        warnings.warn(f"记忆一致性钩子失败，已跳过：{e}", UserWarning, stacklevel=2)


__all__ = ["regenerate_node", "regenerate_scene_events"]
