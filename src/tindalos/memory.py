"""跨会话记忆（task t14-memory）：剧本 → 记忆事实 → store（namespace ('campaigns', <id>, 'facts')）。

记忆事实三类（spec）：
- NPC 印象：每 NPC 一条（独立 store 项 `npc:<id>` + 聚合 facts.npc_impressions）；
- 关键事件：各幕各场景的 outcome 事件（无 outcome 时取末事件）；
- 世界状态摘要：规模统计 + 关系列表 + 一句话摘要。

存储复用既有 LangGraph store 接口（InMemoryStore / SqliteStore 同构 put/get）：
- build_store(settings)：settings.store_dir 可写 → SqliteStore 落盘（跨会话可读）；
  否则回退 InMemoryStore（显式传入的内存 store 语义不变）；
- write_memory_facts / read_memory_facts / list_memories 为 store 类型无关的读写入口，
  供 pipeline.compose（写入）与 cli.memories（列出）共用。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langgraph.store.memory import InMemoryStore
from langgraph.store.sqlite import SqliteStore

from tindalos.config import Settings, get_settings

_FACTS_KEY = "facts"
_NPC_PREFIX = "npc:"


def _namespace(campaign_id: str) -> tuple[str, str, str]:
    return ("campaigns", campaign_id, _FACTS_KEY)


# ---------------------------------------------------------------- store 构造（可落盘）


def build_store(settings: Settings | None = None) -> Any:
    """按 settings.store_dir 构造持久化 store：目录可写 → SqliteStore 落盘；否则 InMemoryStore。

    store_dir 缺省存在（data/store），因此缺省即跨会话落盘；测试 / 一次性运行可显式
    传入 InMemoryStore 保持既有内存语义。
    """
    settings = settings or get_settings()
    store_dir = getattr(settings, "store_dir", None)
    if store_dir:
        path = Path(store_dir)
        path.mkdir(parents=True, exist_ok=True)
        # isolation_level=None（autocommit）：langgraph _cursor 显式 BEGIN/COMMIT，
        # 缺省隔离级别下二次事务会报 "cannot start a transaction within a transaction"。
        conn = sqlite3.connect(
            str(path / "memory.sqlite").replace("\\", "/"),
            check_same_thread=False,
            isolation_level=None,
        )
        store = SqliteStore(conn)
        store.setup()
        return store
    return InMemoryStore()


# ---------------------------------------------------------------- 纯函数：记忆事实派生


def npc_impression(npc: Any) -> str:
    """单条 NPC 印象文本：身份 + 特质 + 本局角色分工 + 描述（宽松容错缺失字段）。"""
    personality = "、".join(npc.personality or []) or "（无特质）"
    roles = "；".join((npc.acts_roles or {}).values())
    text = f"{npc.name}（{npc.archetype}）：{personality}"
    if roles:
        text += f"；本局角色：{roles}"
    if npc.description:
        text += f"。{npc.description}"
    return text


def build_memory_facts(campaign: Any) -> dict:
    """纯函数：Campaign → 记忆事实文档（NPC 印象 / 关键事件 / 世界状态摘要 + 元数据）。

    宽松容错（与 render_notes 同哲学）：personality/acts_roles/scenes/events 缺失或
    为空时仍可派生，不抛异常。
    """
    npc_impressions: list[dict] = []
    for npc in campaign.npcs.values():
        npc_impressions.append(
            {
                "npc_id": npc.id,
                "name": npc.name,
                "archetype": npc.archetype,
                "personality": list(npc.personality or []),
                "acts_roles": dict(npc.acts_roles or {}),
                "impression": npc_impression(npc),
            }
        )
    key_events: list[dict] = []
    for act in campaign.acts:
        for scene in act.scenes:
            evs = list(scene.events or [])
            if not evs:
                continue
            chosen = [e for e in evs if getattr(e, "kind", None) == "outcome"] or evs[-1:]
            for ev in chosen:
                key_events.append(
                    {
                        "act_id": act.id,
                        "act_title": act.title,
                        "scene_id": scene.id,
                        "scene_title": scene.title,
                        "event_id": ev.id,
                        "event_title": ev.title,
                        "kind": getattr(ev, "kind", ""),
                        "description": getattr(ev, "description", ""),
                    }
                )
    relations = [
        f"{r.source} --[{r.type.value if hasattr(r.type, 'value') else r.type}]--> "
        f"{r.target}（{r.label}）"
        for r in campaign.relations
    ]
    summary = (
        f"已推进 {len(campaign.acts)} 幕 / {len(campaign.npcs)} 名 NPC / "
        f"{len(campaign.clues)} 条线索 / {len(campaign.relations)} 条世界关系；"
        f"关键事件 {len(key_events)} 个。"
    )
    return {
        "campaign_id": campaign.id,
        "title": campaign.title,
        "premise": campaign.premise or "",
        "npc_impressions": npc_impressions,
        "key_events": key_events,
        "world_summary": {
            "npc_count": len(campaign.npcs),
            "act_count": len(campaign.acts),
            "scene_count": sum(len(a.scenes) for a in campaign.acts),
            "event_count": sum(len(s.events) for a in campaign.acts for s in a.scenes),
            "clue_count": len(campaign.clues),
            "relation_count": len(campaign.relations),
            "relations": relations,
            "summary": summary,
        },
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


# ---------------------------------------------------------------- store 读写


def write_memory_facts(store: Any, campaign: Any) -> dict:
    """把记忆事实写入 store（namespace ('campaigns', <campaign_id>, 'facts')）。

    - 聚合文档键 "facts"：NPC 印象 / 关键事件 / 世界状态摘要（跨会话读取入口）；
    - 每 NPC 一条独立印象项键 "npc:<id>"（spec：每 NPC 一条印象）。
    返回写入的事实文档。
    """
    facts = build_memory_facts(campaign)
    ns = _namespace(campaign.id)
    store.put(ns, _FACTS_KEY, facts)
    for imp in facts["npc_impressions"]:
        store.put(ns, f"{_NPC_PREFIX}{imp['npc_id']}", imp)
    return facts


def read_memory_facts(store: Any, campaign_id: str) -> dict | None:
    """读回聚合记忆事实（无则 None）。store 类型无关（InMemoryStore / SqliteStore 同接口）。"""
    item = store.get(_namespace(campaign_id), _FACTS_KEY)
    if item is None:
        return None
    value = item.value
    return value if isinstance(value, dict) else dict(value or {})


# ---------------------------------------------------------------- 渲染


def render_memory_section(campaign: Any) -> str:
    """备团笔记「记忆」节 markdown（KP 续备团用）：世界状态 / NPC 印象 / 关键事件。"""
    facts = build_memory_facts(campaign)
    ws = facts["world_summary"]
    lines = ["## 记忆", ""]
    lines.append(f"**世界状态**：{ws['summary']}")
    lines += ["", "**NPC 印象**：", ""]
    for imp in facts["npc_impressions"]:
        lines.append(f"- {imp['impression']}")
    lines += ["", "**关键事件**：", ""]
    if facts["key_events"]:
        for ev in facts["key_events"]:
            lines.append(
                f"- [{ev['act_title']}·{ev['scene_title']}] {ev['event_title']}"
                f"（{ev['kind']}）：{ev['description']}"
            )
    else:
        lines.append("（暂无）")
    return "\n".join(lines)


def render_memories_doc(facts: dict) -> str:
    """记忆事实文档 → CLI 可读 markdown（与 notes 记忆节同构，附更新时刻）。"""
    lines = [f"# 记忆：{facts.get('title') or facts.get('campaign_id', '')}", ""]
    lines.append(f"**campaign**：`{facts.get('campaign_id', '')}`（更新于 {facts.get('updated_at', '')}）")
    lines += ["", "## NPC 印象", ""]
    for imp in facts.get("npc_impressions", []):
        lines.append(f"- {imp.get('impression') or imp.get('name', '')}")
    lines += ["", "## 关键事件", ""]
    events = facts.get("key_events", [])
    if events:
        for ev in events:
            lines.append(
                f"- [{ev.get('act_title', '')}·{ev.get('scene_title', '')}] "
                f"{ev.get('event_title', '')}（{ev.get('kind', '')}）：{ev.get('description', '')}"
            )
    else:
        lines.append("（暂无）")
    lines += ["", "## 世界状态", ""]
    ws = facts.get("world_summary") or {}
    lines.append(ws.get("summary") or "（无）")
    for r in ws.get("relations", []):
        lines.append(f"- {r}")
    return "\n".join(lines)


def list_memories(store: Any, campaign_id: str) -> str:
    """CLI memories 入口：读 store 列事实；无事实时输出「暂无」提示（退出码仍为 0）。"""
    facts = read_memory_facts(store, campaign_id)
    if facts is None:
        return (
            f"# 记忆：{campaign_id}\n\n"
            "（暂无记忆事实——先运行 generate / pipeline 写入 store，并确认 store_dir 与写入时一致）"
        )
    return render_memories_doc(facts)


__all__ = [
    "build_store",
    "npc_impression",
    "build_memory_facts",
    "write_memory_facts",
    "read_memory_facts",
    "render_memory_section",
    "render_memories_doc",
    "list_memories",
]
