"""Tindalos 领域模型：Campaign→Act→Scene→Event 层级 + NPC/Clue/WorldRelation + ScriptGraph。

仅依赖 pydantic v2（零外部依赖）。所有模型支持 model_dump_json / model_validate_json 往返；
Campaign 内置跨层引用校验（跨层 id 唯一性 + 引用可解析），不写业务启发式。
术语遵循 CONTEXT.md 词汇表（KP 主控 / NPC subagent / 幕 Act / 场景 Scene / 事件 Event /
线索 Clue / 世界知识图谱 / 备团笔记 / 分幕创作 / 剧本节点图）。
"""

from __future__ import annotations

import enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RelationType(str, enum.Enum):
    """世界知识图谱六类语义边。value 为中文标签（落 JSON），name 为英文键。"""

    KNOWS = "认识"
    POINTS_TO = "指向"
    CAUSES = "起因"
    BELONGS_TO = "归属"
    LEARNS = "获知"
    EXPIRES = "失效"


class WorldRelation(BaseModel):
    """一条世界知识图谱语义关系（KG 边）。

    时间窗语义（"当时为真"）：valid_from <= 时刻 < valid_to 时生效；
    valid_to=None 视为永久。valid_from/valid_to 为 ISO 日期字符串。
    """

    model_config = ConfigDict(extra="ignore")

    source: str
    target: str
    type: RelationType
    label: str
    valid_from: str
    valid_to: str | None = None
    note: str | None = None


class NPC(BaseModel):
    """非玩家角色（NPC subagent 产出）。acts_roles：幕 id → 角色分工。"""

    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    archetype: str
    personality: list[str] = Field(default_factory=list)
    description: str = ""
    acts_roles: dict[str, str] = Field(default_factory=dict)


class Clue(BaseModel):
    """线索：调查员可获得的信息单元。linked_event_ids 必须可解析到某幕某场景的事件。"""

    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    description: str = ""
    linked_npc_ids: list[str] = Field(default_factory=list)
    linked_event_ids: list[str] = Field(default_factory=list)
    found_at: str | None = None


class Event(BaseModel):
    """事件：场景内剧情推进的最小节点。kind ∈ entry（进入）/ trigger（触发）/ outcome（结局）。"""

    model_config = ConfigDict(extra="ignore")

    id: str
    title: str
    kind: Literal["entry", "trigger", "outcome"]
    description: str = ""
    conditions: list[str] = Field(default_factory=list)
    next_event_ids: list[str] = Field(default_factory=list)


class Scene(BaseModel):
    """场景：幕内时间/地点设定（setting: {"time","place"}）与其事件序列的锚点。"""

    model_config = ConfigDict(extra="ignore")

    id: str
    title: str
    setting: dict[str, str] = Field(default_factory=dict)
    events: list[Event] = Field(default_factory=list)
    npc_ids: list[str] = Field(default_factory=list)


class Act(BaseModel):
    """幕：剧本一级结构单元，罗马数字编号（roman），含场景序列与本幕出场 NPC。"""

    model_config = ConfigDict(extra="ignore")

    id: str
    title: str
    roman: str
    summary: str = ""
    scenes: list[Scene] = Field(default_factory=list)
    npc_ids: list[str] = Field(default_factory=list)


class Campaign(BaseModel):
    """剧本聚合根。extra=forbid：eval 的 schema 合法检查需要检出未知键漂移。

    校验（跨层 id 唯一性与引用可解析，仅此两类规则）：
    - acts 内 scene/event id 跨幕全局唯一；
    - scene/act 引用的 npc_id、clue.linked_npc_ids 必须存在于 campaign.npcs；
    - clue.linked_event_ids 与 event.next_event_ids 必须解析到某幕某场景的事件；
    - npc.acts_roles 的幕 id 必须存在。

    规则中立（wayfinder ticket 06）：`rules` 标注生成/展示所依据的规则体系
    （COC7 默认；DND5e 预留），`rules_config` 携带规则定制项（判定参数等）。
    领域字段保持规则中立——下层结构不做 COC 专属硬编码，规则差异仅体现于
    顶层标注与可选配置，DND 后续独立 effort 无需大改 schema。
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    premise: str = ""
    rules: Literal["COC7", "DND5e"] = "COC7"
    rules_config: dict[str, Any] = Field(default_factory=dict)
    acts: list[Act] = Field(default_factory=list)
    npcs: dict[str, NPC] = Field(default_factory=dict)
    clues: list[Clue] = Field(default_factory=list)
    relations: list[WorldRelation] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_cross_layer_refs(self) -> "Campaign":
        act_ids: set[str] = set()
        scene_ids: set[str] = set()
        event_ids: set[str] = set()

        # 第一遍：收集 id，检出跨幕重复
        for act in self.acts:
            if act.id in act_ids:
                raise ValueError(f"重复的幕 id：{act.id}")
            act_ids.add(act.id)
            for scene in act.scenes:
                if scene.id in scene_ids:
                    raise ValueError(f"跨幕重复的场景 id：{scene.id}")
                scene_ids.add(scene.id)
                for ev in scene.events:
                    if ev.id in event_ids:
                        raise ValueError(f"跨幕重复的事件 id：{ev.id}")
                    event_ids.add(ev.id)

        # 第二遍：引用可解析
        for act in self.acts:
            for nid in act.npc_ids:
                if nid not in self.npcs:
                    raise ValueError(f"幕 {act.id} 引用了未注册的 NPC：{nid}")
            for scene in act.scenes:
                for nid in scene.npc_ids:
                    if nid not in self.npcs:
                        raise ValueError(f"场景 {scene.id} 引用了未注册的 NPC：{nid}")
                for ev in scene.events:
                    for nxt in ev.next_event_ids:
                        if nxt not in event_ids:
                            raise ValueError(
                                f"事件 {ev.id} 的 next_event_ids 引用了未知事件：{nxt}"
                            )

        for clue in self.clues:
            for nid in clue.linked_npc_ids:
                if nid not in self.npcs:
                    raise ValueError(f"线索 {clue.id} 引用了未注册的 NPC：{nid}")
            for ev_id in clue.linked_event_ids:
                if ev_id not in event_ids:
                    raise ValueError(
                        f"线索 {clue.id} 的 linked_event_ids 引用了未知事件：{ev_id}"
                    )

        for npc_id, npc in self.npcs.items():
            for act_id in npc.acts_roles:
                if act_id not in act_ids:
                    raise ValueError(f"NPC {npc_id} 的 acts_roles 引用了未知幕：{act_id}")

        return self


class ScriptGraph(BaseModel):
    """剧本节点图：同一 Campaign 的呈现层投影，供前端 React Flow 直接消费。

    节点：act/scene/event/npc/clue 五类，id 前缀（如 "act:act-1"）保证全局唯一；
    节点 schema：{id, type, label, data}。
    边 schema：{id, source, target, type, label}——
      - contains：act→scene、scene→event（层级包含）
      - appears：npc→出现场景（scene.npc_ids 中出现即连边）
      - links：clue→linked event（线索指向事件）
    """

    model_config = ConfigDict(extra="ignore")

    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)

    @classmethod
    def from_campaign(cls, campaign: Campaign) -> "ScriptGraph":
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []

        def nid(kind: str, ident: str) -> str:
            return f"{kind}:{ident}"

        for act in campaign.acts:
            nodes.append(
                {
                    "id": nid("act", act.id),
                    "type": "act",
                    "label": act.title,
                    "data": {"roman": act.roman, "summary": act.summary},
                }
            )
            for scene in act.scenes:
                nodes.append(
                    {
                        "id": nid("scene", scene.id),
                        "type": "scene",
                        "label": scene.title,
                        "data": {"setting": scene.setting, "act_id": act.id},
                    }
                )
                edges.append(
                    {
                        "id": f"e:act_scene:{act.id}:{scene.id}",
                        "source": nid("act", act.id),
                        "target": nid("scene", scene.id),
                        "type": "contains",
                        "label": "包含",
                    }
                )
                for ev in scene.events:
                    nodes.append(
                        {
                            "id": nid("event", ev.id),
                            "type": "event",
                            "label": ev.title,
                            "data": {"kind": ev.kind, "description": ev.description},
                        }
                    )
                    edges.append(
                        {
                            "id": f"e:scene_event:{scene.id}:{ev.id}",
                            "source": nid("scene", scene.id),
                            "target": nid("event", ev.id),
                            "type": "contains",
                            "label": "包含",
                        }
                    )

        for npc_id, npc in campaign.npcs.items():
            nodes.append(
                {
                    "id": nid("npc", npc_id),
                    "type": "npc",
                    "label": npc.name,
                    "data": {"archetype": npc.archetype, "acts_roles": npc.acts_roles},
                }
            )

        for act in campaign.acts:
            for scene in act.scenes:
                for npc_id in scene.npc_ids:
                    edges.append(
                        {
                            "id": f"e:npc_scene:{npc_id}:{scene.id}",
                            "source": nid("npc", npc_id),
                            "target": nid("scene", scene.id),
                            "type": "appears",
                            "label": "出现于",
                        }
                    )

        for clue in campaign.clues:
            nodes.append(
                {
                    "id": nid("clue", clue.id),
                    "type": "clue",
                    "label": clue.name,
                    "data": {"description": clue.description, "found_at": clue.found_at},
                }
            )
            for ev_id in clue.linked_event_ids:
                edges.append(
                    {
                        "id": f"e:clue_event:{clue.id}:{ev_id}",
                        "source": nid("clue", clue.id),
                        "target": nid("event", ev_id),
                        "type": "links",
                        "label": "指向",
                    }
                )

        return cls(nodes=nodes, edges=edges)


__all__ = [
    "RelationType",
    "WorldRelation",
    "NPC",
    "Clue",
    "Event",
    "Scene",
    "Act",
    "Campaign",
    "ScriptGraph",
]


RELATION_TYPE_ALIASES = {
    "KNOWS": "认识", "POINTS_TO": "指向", "CAUSES": "起因",
    "BELONGS_TO": "归属", "LEARNS": "获知", "EXPIRES": "失效",
}


def normalize_relation_types(campaign_dict: dict) -> dict:
    """把 relations[].type 的英文枚举/任意大小写归一化为 RelationType 中文值（深拷贝，不就地改）。

    统一入口：eval/evolve/cli 在边界处先归一化再校验，保证坏输入也能对齐领域语言
    （英文 KNOWS 与中文 认识 在重叠检测/一致性检查中视为同一关系）。
    """
    out = dict(campaign_dict)
    rels = list(out.get("relations", []) or [])
    norm = []
    for r in rels:
        r2 = dict(r)
        t = r2.get("type")
        if isinstance(t, str) and t not in ("认识", "指向", "起因", "归属", "获知", "失效"):
            r2["type"] = RELATION_TYPE_ALIASES.get(t.upper(), t)
        norm.append(r2)
    out["relations"] = norm
    return out


def construct_loose_campaign(raw: dict) -> "Campaign":
    """宽松构造（model_construct 跳过校验）：schema 校验失败的 dict 也能继续跑结构检查。

    全仓唯一实现（G5 评审修正：eval_/evolve/cli 三处私有副本统一收编于此），
    供 eval 的确定性检查 / evolve 的修复循环 / cli 的输入容错共用。
    """
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
    raw = normalize_relation_types(raw)
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
        rules=raw.get("rules", "COC7"), rules_config=raw.get("rules_config", {}),
        acts=acts, npcs=npcs, clues=clues, relations=relations,
    )
