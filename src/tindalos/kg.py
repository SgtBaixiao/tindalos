"""世界知识图谱（推理层）：networkx 六类语义边 + 时间窗过滤 + 多跳路径线索推理 + 一致性检查。

WorldGraph 是剧本的「推理层」投影（呈现层投影为 models.ScriptGraph）：
- 边 schema（source/target/type/label/valid_from/valid_to）与 ScriptGraph 对齐，直接可 JSON 化；
- 时间窗语义「当时为真」而非「现在为真」：关系在 valid_from <= as_of < valid_to 时 active，
  valid_to=None 视为永久；as_of == valid_to 时刻不再 active——过期关系不物理删除，由语义表达；
- build_from_campaign / campaign_consistency 负责剧本 ↔ 世界图的双向投影一致性。
"""
from __future__ import annotations

import json
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any, Optional

import networkx as nx

from tindalos.models import RelationType
_TYPE_BY_NAME: dict[str, str] = {m.name: m.value for m in RelationType}
_LABELS: frozenset[str] = frozenset(m.value for m in RelationType)


def _norm_type(t: Any) -> str:
    """把 RelationType 成员 / 枚举名 / 中文标签统一为中文标签字符串（JSON 友好）。"""
    if isinstance(t, RelationType):
        return t.value
    if isinstance(t, str):
        s = t.strip()
        if s in _TYPE_BY_NAME:
            return _TYPE_BY_NAME[s]
        if s in _LABELS:
            return s
    if hasattr(t, "value") and t.value in _LABELS:
        return t.value
    raise ValueError(f"未知关系类型: {t!r}；合法值: {sorted(_LABELS)}")


def parse_time(v: Any):
    """解析时间为可比较对象（公共 API）：ISO-8601（日期或 datetime）→ 归一化 UTC datetime；否则原样字符串。"""
    if v is None or isinstance(v, datetime):
        return v
    s = str(v)
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return s
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def time_key(v: Any, *, upper: bool = False) -> tuple[int, Any]:
    """可比较键（公共 API）：(桶, 值)——None 按无限处理（upper=True 为 +inf，否则 -inf）；datetime 桶在字符串桶之前。"""
    if v is None:
        return (2, None) if upper else (-2, None)
    parsed = parse_time(v)
    if isinstance(parsed, datetime):
        return (0, parsed)
    return (1, parsed)


def _degenerate_window(f: Any, t: Any) -> bool:
    """退化窗（空窗/倒置窗）：半开区间 [f, t) 为空——f >= t（None 端点视为无限，永不退化）。"""
    if f is None or t is None:
        return False
    return not (time_key(f) < time_key(t, upper=True))


def window_overlaps(w1: tuple[Any, Any], w2: tuple[Any, Any]) -> bool:
    """半开区间 [from, to) 相交（公共 API）：f1 < t2 且 f2 < t1（None 端点视为无限）。

    退化窗（valid_from >= valid_to，含空窗与倒置窗）恒不相交——交集为空。
    """
    f1, t1 = w1
    f2, t2 = w2
    if _degenerate_window(f1, t1) or _degenerate_window(f2, t2):
        return False
    return time_key(f1) < time_key(t2, upper=True) and time_key(f2) < time_key(t1, upper=True)


class WorldGraph:
    """世界知识图谱：实体（kind+attrs）+ 六类语义边 + 时间窗 + 多跳路径 + 一致性检查。"""

    def __init__(self) -> None:
        self._g: nx.MultiDiGraph = nx.MultiDiGraph()
        self._entities: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------- 实体
    def add_entity(self, entity_id: str, kind: str, attrs: Optional[dict] = None) -> None:
        self._entities[entity_id] = {"kind": kind, "attrs": dict(attrs or {})}
        if entity_id not in self._g:
            self._g.add_node(entity_id)

    def has_entity(self, entity_id: str) -> bool:
        return entity_id in self._entities

    def entity_kind(self, entity_id: str) -> Optional[str]:
        ent = self._entities.get(entity_id)
        return ent["kind"] if ent else None

    # ------------------------------------------------------------- 关系边
    def add_relation(
        self,
        source: str,
        target: str,
        type: Any,
        label: str,
        valid_from: Any = None,
        valid_to: Any = None,
    ) -> None:
        """加一条语义边；同源同型同有效窗不重复（允许同对同型不同窗的并行边）。"""
        typ = _norm_type(type)
        data = {"type": typ, "label": label, "valid_from": valid_from, "valid_to": valid_to}
        existing = self._g.get_edge_data(source, target, default={})
        for d in existing.values():
            if (
                d.get("type") == typ
                and d.get("valid_from") == valid_from
                and d.get("valid_to") == valid_to
            ):
                return
        self._g.add_edge(source, target, **data)

    def relations_of(self, entity_id: str) -> list[dict]:
        """实体相关的全部边（出边 + 入边，保留方向），schema 与 ScriptGraph 边对齐。"""
        out: list[dict] = []
        if entity_id in self._g:
            for nxt, edges in self._g[entity_id].items():
                for d in edges.values():
                    out.append({"source": entity_id, "target": nxt, **d})
            for prv in self._g.predecessors(entity_id):
                if prv == entity_id:  # 自环已在出边侧收集
                    continue
                for d in self._g[prv][entity_id].values():
                    out.append({"source": prv, "target": entity_id, **d})
        out.sort(key=_edge_sort_key)
        return out

    # ------------------------------------------------------------- 时间窗过滤
    def active_relations(self, as_of: Any = None) -> list[dict]:
        """as_of 时刻为真的关系（valid_from <= as_of < valid_to）；缺省 as_of = 当前时间。"""
        if as_of is None:
            as_of = datetime.now(timezone.utc)
        k_asof = time_key(as_of)
        out = [
            {"source": u, "target": v, **d}
            for u, v, d in self._g.edges(data=True)
            if time_key(d.get("valid_from")) <= k_asof < time_key(d.get("valid_to"), upper=True)
        ]
        out.sort(key=_edge_sort_key)
        return out

    # ------------------------------------------------------------- 多跳路径
    def path(self, start: str, end: str, max_depth: int = 5) -> list[list[str]]:
        """start→end 的全部简单路径（BFS 限深，边数 <= max_depth）；每条 = entity_id 序列。"""
        if start not in self._g:
            return []
        if start == end:
            return [[start]]
        results: list[list[str]] = []
        queue: deque[tuple[str, list[str]]] = deque([(start, [start])])
        while queue:
            node, path_so_far = queue.popleft()
            if len(path_so_far) - 1 >= max_depth:
                continue
            for nxt in self._g.successors(node):
                if nxt in path_so_far:
                    continue
                cand = path_so_far + [nxt]
                if nxt == end:
                    results.append(cand)
                else:
                    queue.append((nxt, cand))
        results.sort(key=lambda p: (len(p), p))
        return results

    # ------------------------------------------------------------- JSON 往返
    def to_json(self) -> dict:
        """nodes/edges 视图：node={id,kind,attrs}，edge={source,target,type,label,valid_from,valid_to}。"""
        nodes = [
            {"id": eid, "kind": ent["kind"], "attrs": ent["attrs"]}
            for eid, ent in sorted(self._entities.items())
        ]
        edges = [{"source": u, "target": v, **d} for u, v, d in self._g.edges(data=True)]
        edges.sort(key=_edge_sort_key)
        return {"nodes": nodes, "edges": edges}

    @classmethod
    def from_json(cls, doc: Any) -> "WorldGraph":
        """从 to_json() 视图重建（接受 dict 或 JSON 字符串）。"""
        if isinstance(doc, (str, bytes, bytearray)):
            doc = json.loads(doc)
        w = cls()
        for n in doc.get("nodes", []):
            w.add_entity(n["id"], n.get("kind", ""), n.get("attrs") or {})
        for e in doc.get("edges", []):
            w.add_relation(
                e["source"], e["target"], e["type"], e.get("label", ""),
                e.get("valid_from"), e.get("valid_to"),
            )
        return w

    # ------------------------------------------------------------- 一致性检查
    def consistency_check(self) -> list[str]:
        """检出 (a) 悬空端点 (b) 同对同型有效窗重叠 (c) 有效窗倒置；空列表 = 无矛盾。"""
        problems: list[str] = []
        for u, v, d in self._g.edges(data=True):
            typ = d["type"]
            if u not in self._entities:
                problems.append(f"关系边 {u}→{v}({typ}) 引用了未注册实体 {u}")
            if v not in self._entities:
                problems.append(f"关系边 {u}→{v}({typ}) 引用了未注册实体 {v}")
            if (
                d.get("valid_from") is not None
                and d.get("valid_to") is not None
                and time_key(d["valid_to"], upper=True) < time_key(d["valid_from"])
            ):
                problems.append(
                    f"关系边 {u}→{v}({typ}) 有效窗倒置: "
                    f"valid_to({d['valid_to']}) < valid_from({d['valid_from']})"
                )
        grouped: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
        for u, v, d in self._g.edges(data=True):
            grouped[(u, v, d["type"])].append(d)
        for (u, v, typ), items in sorted(grouped.items()):
            for i in range(len(items)):
                for j in range(i + 1, len(items)):
                    w1 = (items[i].get("valid_from"), items[i].get("valid_to"))
                    w2 = (items[j].get("valid_from"), items[j].get("valid_to"))
                    if w1 == w2:
                        continue  # 同窗重复由 add_relation 去重拦截，双保险
                    if window_overlaps(w1, w2):
                        problems.append(f"关系 {u}→{v}({typ}) 有效窗重叠: {w1} vs {w2}")
        return problems


def _edge_sort_key(e: dict) -> tuple:
    return (
        e["source"],
        str(e.get("valid_from") or ""),
        e["target"],
        e["type"],
        e.get("label") or "",
    )


def build_from_campaign(campaign: Any) -> WorldGraph:
    """遍历 campaign.relations 建图，并把 NPC / Clue 注册为实体。"""
    world = WorldGraph()
    for npc in campaign.npcs.values():
        world.add_entity(npc.id, "npc", {"name": npc.name, "description": npc.description})
    for clue in campaign.clues:
        world.add_entity(clue.id, "clue", {"name": clue.name, "description": clue.description})
    for rel in campaign.relations:
        world.add_relation(rel.source, rel.target, rel.type, rel.label, rel.valid_from, rel.valid_to)
    return world


def campaign_consistency(campaign: Any, world: WorldGraph) -> list[str]:
    """剧本引用一致性：scene.npc_ids / clue.linked_event_ids 可解析，且与 world 注册一致。"""
    problems: list[str] = []
    event_ids = {
        ev.id for act in campaign.acts for scene in act.scenes for ev in scene.events
    }
    npc_ids = set(campaign.npcs)
    for act in campaign.acts:
        for scene in act.scenes:
            for nid in scene.npc_ids:
                if nid not in npc_ids:
                    problems.append(f"场景 {scene.id} 引用未知 NPC {nid}")
                elif world.entity_kind(nid) != "npc":
                    problems.append(f"场景 {scene.id} 引用的 NPC {nid} 未在 world 注册")
    for clue in campaign.clues:
        if world.entity_kind(clue.id) != "clue":
            problems.append(f"线索 {clue.id} 未在 world 注册")
        for ev in clue.linked_event_ids:
            if ev not in event_ids:
                problems.append(f"线索 {clue.id} 引用未知事件 {ev}")
    for rel in campaign.relations:
        for eid in (rel.source, rel.target):
            if not world.has_entity(eid):
                problems.append(f"关系 {rel.source}→{rel.target}({rel.label}) 端点 {eid} 未在 world 注册")
    return problems
