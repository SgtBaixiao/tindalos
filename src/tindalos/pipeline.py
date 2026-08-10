"""LangGraph 多智能体管线：KP 主控 → NPC 并行注入 → 分幕写作 → 汇总备团笔记。

状态（PipelineState）：module_text / premise / acts / npcs / world / campaign / progress，
外加内部通道（act_drafts、npc_ids、messages、notes_md、campaign_id 与 Send 分支载荷）。

节点流：
  START → kp_parse（模组文本→premise）
        → kp_plan（拟定幕结构草案 + 生成 NPC + 构建世界图）
        → [条件边] tools（kg_query 经 ToolNode 挂载，Function Calling 实证）↔ kp_plan
        → npc_fanout（Send 并行）→ npc_persona×N（每人格注入）
        → act_fanout（Send 并行）→ write_act×M（每幕：@task 并行写场景）
        → compose（汇总 models.Campaign + 备团笔记 markdown + store 事实写入）→ END

基础设施：checkpoint=SqliteSaver（settings.checkpoint_dir 文件）；store=build_store(settings)
（settings.store_dir 可写 → SqliteStore 落盘跨会话，否则 InMemoryStore；namespace
('campaigns', campaign_id, 'facts')）；进度经 get_stream_writer 发 custom 事件。
全程确定性：DeterministicGenerator 下零网络零 LLM。
"""

from __future__ import annotations

import hashlib
import operator
import os
import random
import re
import sqlite3
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AIMessage, AnyMessage, ToolMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.config import get_store, get_stream_writer
from langgraph.func import task
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import InjectedState, ToolNode
from langgraph.types import Send

from tindalos.config import Settings, get_settings
from tindalos.generator import Generator, build_generator
from tindalos.kg import WorldGraph, build_from_campaign
from tindalos.memory import build_store, render_memory_section, write_memory_facts
from tindalos.models import Act, Campaign, Clue, NPC, WorldRelation

# 缺省分幕/NPC 数量（可经 TINDALOS_N_ACTS / TINDALOS_N_NPCS 环境变量覆盖）
_DEFAULT_N_ACTS = 2
_DEFAULT_N_NPCS = 3


# ---------------------------------------------------------------- 状态


def _merge_dicts(a: dict, b: dict) -> dict:
    return {**a, **b}


class PipelineState(TypedDict):
    """管线共享状态。progress/acts/messages 带 reducer 以支持并行分支累加。"""

    module_text: str
    premise: str
    acts: Annotated[list[dict], operator.add]
    npcs: Annotated[dict[str, dict], _merge_dicts]
    world: dict
    campaign: Any
    progress: Annotated[list[str], operator.add]
    # ---- 内部通道 ----
    campaign_id: str
    act_drafts: list[dict]
    npc_ids: list[str]
    npc_id: str  # npc_persona Send 分支载荷
    npc: dict  # npc_persona Send 分支载荷（Send 分支状态=载荷本身）
    act_draft: dict  # write_act Send 分支载荷
    messages: Annotated[list[AnyMessage], operator.add]
    notes_md: str


# ---------------------------------------------------------------- 工具（Function Calling 实证）


def kg_query(entity_id: str, state: Annotated[dict, InjectedState]) -> str:
    """查询某实体在世界知识图谱中的关系摘要（经 ToolNode 挂给 kp 节点）。"""
    world_doc = state.get("world") or {}
    try:
        wg = WorldGraph.from_json(world_doc) if world_doc else WorldGraph()
    except Exception:
        wg = WorldGraph()
    rels = wg.relations_of(entity_id)
    if not rels:
        return f"实体 {entity_id} 在世界知识图谱中暂无已注册关系。"
    lines = [
        f"- {r['source']} --[{r['type']}]--> {r['target']}（{r.get('label', '')}）" for r in rels
    ]
    return f"实体 {entity_id} 的关系（{len(rels)} 条）：\n" + "\n".join(lines)


# ---------------------------------------------------------------- 工具函数


def campaign_id_for(module_text: str) -> str:
    """由模组文本派生稳定 campaign_id（store 命名空间与 checkpoint 复用）。"""
    return "campaign-" + hashlib.sha1((module_text or "").strip().encode("utf-8")).hexdigest()[:8]


def extract_premise(module_text: str) -> str:
    """从模组文本提取前提：优先「前提：」行，其次首段。

    公共入口（t10 去重复）：pipeline 与 cli 共用。
    """
    lines = [l.strip() for l in (module_text or "").splitlines() if l.strip()]
    for line in lines:
        low = line.lower()
        if low.startswith("前提") or low.startswith("premise"):
            return re.split(r"[:：]", line, maxsplit=1)[-1].strip()[:200] or line[:200]
    if not lines:
        return "无名模组"
    first = lines[0].lstrip("#").strip()
    return first[:200] if first else "无名模组"


def title_from_text(module_text: str) -> str:
    """从模组文本派生标题（公共入口：pipeline 与 cli 共用）。

    优先「# 模组：xxx」行与首个 H1；旧逻辑（跳过含冒号行）会把结构化文档的
    「## 元信息」误判为标题（真实模组实验发现，2026-08-11）。
    """
    lines = [l.strip() for l in (module_text or "").splitlines() if l.strip()]
    # 1) 优先「模组：」标题行（organized 格式的 H1）
    for line in lines:
        cleaned = line.lstrip("#").strip()
        if cleaned.startswith("模组"):
            title = re.split(r"[:：]", cleaned, maxsplit=1)[-1].strip()
            return title[:40] if title else cleaned[:40]
    # 2) 首个 H1（# 开头且非 ##）
    for line in lines:
        if line.startswith("# ") and not line.startswith("## "):
            return line.lstrip("#").strip()[:40]
    # 3) 旧逻辑兜底
    for line in lines:
        cleaned = line.lstrip("#").strip()
        if cleaned and "：" not in cleaned[:8]:
            return cleaned[:40]
    return "未命名模组"


def _act_sort_key(act: dict) -> tuple[int, int]:
    m = re.search(r"(\d+)", act.get("id", ""))
    return (0 if m is None else int(m.group(1)), 0)


def _dedupe_acts(acts: list[dict]) -> list[dict]:
    """按 id 去重保序（防同一 thread 带输入重复 invoke 造成重复幕）。"""
    seen: set[str] = set()
    out: list[dict] = []
    for act in acts:
        key = act.get("id", "")
        if key in seen:
            continue
        seen.add(key)
        out.append(act)
    return out


def _checkpoint_uri(path: Any) -> str:
    """sqlite3.connect 可用的路径字符串（Windows 需正斜杠；经实测 uri 前缀不可用）。"""
    return str(path).replace("\\", "/")


def _build_world_from_store(state: PipelineState, npcs: dict[str, dict]) -> dict:
    """kp_plan 用：NPC 实体 + store 既有事实（前次运行的关系边）构建世界图。"""
    wg = WorldGraph()
    for nid, npc in npcs.items():
        wg.add_entity(nid, "npc", {"name": npc.get("name", ""), "description": npc.get("description", "")})
    store = get_store()
    if store is not None:
        item = store.get(("campaigns", state.get("campaign_id", ""), "facts"), "relations")
        if item is not None and item.value:
            for r in item.value.get("items", []):
                for eid in (r.get("source"), r.get("target")):
                    kind = "npc" if str(eid).startswith("npc") else "clue"
                    if not wg.has_entity(eid):
                        wg.add_entity(eid, kind, {})
                wg.add_relation(
                    r["source"], r["target"], r["type"],
                    r.get("label", ""), r.get("valid_from"), r.get("valid_to"),
                )
    return wg.to_json()


# ---------------------------------------------------------------- 幕级场景写作（@task 并行）


@task
def _write_scene(
    generator: Generator,
    act_draft: dict,
    premise: str,
    npc_ids: list[str],
    idx: int,
    scene_title: str,
) -> dict:
    """单场景写作子任务：调用生成器后重编号，保证跨幕全局唯一 id。"""
    # 用「幕标题 + 场景标题」作盐：同幕不同场景内容有差异，且保持确定性
    scene = dict(generator.generate_scene(f"{act_draft['title']}·{scene_title}", premise, npc_ids))
    scene["id"] = f"{act_draft['id']}-scene-{idx + 1}"
    scene["title"] = scene_title
    events = scene.get("events", [])
    for j, ev in enumerate(events):
        ev = dict(ev)
        ev["id"] = f"{act_draft['id']}-scene-{idx + 1}-ev-{j + 1}"
        ev["next_event_ids"] = [
            f"{act_draft['id']}-scene-{idx + 1}-ev-{k + 1}" for k in range(j + 1, len(events))
        ]
        events[j] = ev
    scene["events"] = events
    return scene


# ---------------------------------------------------------------- 节点构造


def _build_nodes(generator: Generator) -> dict[str, Any]:
    """构造全部图节点（闭包绑定生成器）。"""

    def kp_parse(state: PipelineState) -> dict:
        module_text = (state.get("module_text") or "").strip()
        return {
            "premise": extract_premise(module_text),
            "campaign_id": campaign_id_for(module_text),
        }

    def kp_plan(state: PipelineState) -> dict:
        """拟定幕结构草案 + 生成 NPC + 构建世界图；首次运行发起 kg_query 工具调用。

        缺省 n_acts=2、n_npcs=3（模块常量 _DEFAULT_N_ACTS/_DEFAULT_N_NPCS），
        可经环境变量 TINDALOS_N_ACTS / TINDALOS_N_NPCS 覆盖。
        """
        premise = state.get("premise") or extract_premise(state.get("module_text", ""))
        n_acts = max(1, int(os.environ.get("TINDALOS_N_ACTS", str(_DEFAULT_N_ACTS))))
        n_npcs = max(1, int(os.environ.get("TINDALOS_N_NPCS", str(_DEFAULT_N_NPCS))))
        npcs = {npc["id"]: npc for npc in generator.generate_npcs(premise, n_npcs)}
        act_drafts = generator.generate_acts(premise, n_acts)
        npc_ids = list(npcs.keys())
        for i, act in enumerate(act_drafts):
            act["npc_ids"] = npc_ids[i::len(act_drafts)]

        writer = get_stream_writer()
        updates: dict[str, Any] = {
            "npcs": npcs,
            "act_drafts": act_drafts,
            "npc_ids": npc_ids,
            "world": _build_world_from_store(state, npcs),
        }
        msgs = state.get("messages") or []
        if any(isinstance(m, ToolMessage) for m in msgs):
            # 工具结果已回填：记录摘要后继续（不再发起新工具调用）
            last = msgs[-1]
            headline = str(last.content).splitlines()[0][:80]
            writer({"progress": f"KG 查询：{headline}"})
            updates["progress"] = [f"KG 查询：{headline}"]
        else:
            writer({"progress": "KP 拟定幕结构"})
            updates["progress"] = ["KP 拟定幕结构"]
            if npc_ids:
                # 发起一次 kg_query 工具调用（Function Calling 实证；确定性生成器同样走此回路）
                updates["messages"] = [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "kg_query",
                                "args": {"entity_id": npc_ids[0]},
                                "id": "kg_1",
                                "type": "tool_call",
                            }
                        ],
                    )
                ]
        return updates

    def npc_fanout(state: PipelineState) -> dict:
        return {}  # 占位：条件边 npc_fanout_sends 负责 Send 扇出

    def npc_fanout_sends(state: PipelineState) -> list[Send]:
        # Send 分支状态 = 载荷本身（本版本语义），故把分支所需数据全部装入载荷
        premise = state.get("premise", "")
        return [
            Send(
                "npc_persona",
                {
                    "npc_id": nid,
                    "npc": dict(state.get("npcs", {}).get(nid, {})),
                    "premise": premise,
                    "act_drafts": list(state.get("act_drafts", [])),
                },
            )
            for nid in state.get("npc_ids", [])
        ]

    def npc_persona(state: PipelineState) -> dict:
        """单个 NPC 人格注入：确定性追加特质 + 按幕分配角色分工。"""
        npc_id = state.get("npc_id", "")
        npc = dict(state.get("npc", {}) or {})
        if not npc:
            return {}
        rng = random.Random(hashlib.sha256((npc_id + state.get("premise", "")).encode("utf-8")).hexdigest())
        extra = rng.choice(["暗藏秘密", "立场摇摆", "身负旧债"])
        npc["personality"] = list(npc.get("personality", [])) + [extra]
        npc["acts_roles"] = {
            act["id"]: f"{npc.get('name', npc_id)}在本幕的引导者"
            for act in state.get("act_drafts", [])
            if npc_id in act.get("npc_ids", [])
        }
        name = npc.get("name", npc_id)
        writer = get_stream_writer()
        writer({"progress": f"NPC {name} 注入人格"})
        return {"npcs": {npc_id: npc}, "progress": [f"NPC {name} 注入人格"]}

    def act_fanout(state: PipelineState) -> dict:
        return {}  # 占位：条件边 act_fanout_sends 负责 Send 扇出

    def act_fanout_sends(state: PipelineState) -> list[Send]:
        return [
            Send(
                "write_act",
                {
                    "act_draft": dict(d),
                    "premise": state.get("premise", ""),
                },
            )
            for d in state.get("act_drafts", [])
        ]

    def write_act(state: PipelineState) -> dict:
        """单幕写作：@task 并行生成全部场景，装配为 Act 草案返回（reducer 累加）。"""
        draft = dict(state.get("act_draft", {}))
        premise = state.get("premise", "")
        npc_ids = list(draft.get("npc_ids", []))
        scene_titles = list(draft.get("scene_titles", [])) or ["场景一"]
        futures = [
            _write_scene(generator, draft, premise, npc_ids, i, t)
            for i, t in enumerate(scene_titles)
        ]
        scenes = [f.result() for f in futures]
        act = {k: v for k, v in draft.items() if k != "scene_titles"}
        act["scenes"] = scenes
        act["npc_ids"] = npc_ids
        m = re.search(r"(\d+)", draft.get("id", ""))
        idx = int(m.group(1)) if m else 1
        writer = get_stream_writer()
        writer({"progress": f"写作第 {idx} 幕"})
        return {"acts": [act], "progress": [f"写作第 {idx} 幕"]}

    def compose(state: PipelineState) -> dict:
        """汇总：公共 compose_campaign 装配 Campaign + store 事实写入
        （relations/campaign 既有键 + write_memory_facts 记忆事实，t10/t14）。"""
        writer = get_stream_writer()
        writer({"progress": "校对付印"})
        assembled = compose_campaign(
            state.get("module_text", ""),
            state.get("premise", ""),
            state.get("acts", []),
            dict(state.get("npcs", {})),
        )
        campaign = assembled["campaign"]
        store = get_store()
        if store is not None:
            try:
                store.put(
                    ("campaigns", campaign.id, "facts"),
                    "relations",
                    {"items": [r.model_dump(mode="json") for r in campaign.relations]},
                )
                store.put(
                    ("campaigns", campaign.id, "facts"),
                    "campaign",
                    campaign.model_dump(mode="json"),
                )
                write_memory_facts(store, campaign)
            except Exception as mem_err:  # noqa: BLE001 - store 写失败不阻塞生成（G5 修正）
                writer({"progress": f"记忆持久化失败（已跳过）：{mem_err}"})
        return {
            "campaign": campaign,
            "notes_md": assembled["notes_md"],
            "world": assembled["world"],
            "progress": ["校对付印"],
        }

    return {
        "kp_parse": kp_parse,
        "kp_plan": kp_plan,
        "npc_fanout": npc_fanout,
        "npc_fanout_sends": npc_fanout_sends,
        "npc_persona": npc_persona,
        "act_fanout": act_fanout,
        "act_fanout_sends": act_fanout_sends,
        "write_act": write_act,
        "compose": compose,
    }


def _build_clues_and_relations(acts: list[dict], npcs: dict[str, dict]) -> tuple[list[dict], list[dict]]:
    """装配线索与 KG 关系：每幕一条线索 + 指向边 + 幕间认识边。"""
    clues: list[dict] = []
    relations: list[dict] = []
    for i, act in enumerate(acts):
        scenes = act.get("scenes", [])
        if not scenes:
            continue
        outcome = scenes[0]["events"][-1]
        clue_id = f"clue-{act['id']}"
        npc_id = (act.get("npc_ids") or list(npcs))[0]
        clues.append(
            {
                "id": clue_id,
                "name": f"{act.get('title', act['id'])}的关键线索",
                "description": "指向本幕真相的关键线索。",
                "linked_npc_ids": [npc_id],
                "linked_event_ids": [outcome["id"]],
            }
        )
        relations.append(
            {
                "source": npc_id, "target": clue_id, "type": "指向",
                "label": "指向线索", "valid_from": "1900-01-01",
            }
        )
        if i + 1 < len(acts):
            nxt_id = (acts[i + 1].get("npc_ids") or list(npcs))[0]
            relations.append(
                {
                    "source": npc_id, "target": nxt_id, "type": "认识",
                    "label": "互相认识", "valid_from": "1900-01-01",
                }
            )
    return clues, relations


def compose_campaign(module_text: str, premise: str, acts: list[dict], npcs: dict[str, dict]) -> dict:
    """纯装配：acts/npcs 草案 → Campaign 模型 + 线索/关系 + 世界图 + 备团笔记。

    公共入口（t10 去重复）：pipeline.compose 节点与 cli.generate 共用。
    - 对 acts 去重（同 id 保留首个）并按幕序号排序；
    - 每幕一条线索 + 指向边 + 幕间认识边（_build_clues_and_relations）；
    - 返回 {"campaign": Campaign, "clues": [...], "relations": [...],
             "world": dict, "notes_md": str}。
    """
    acts = sorted(_dedupe_acts(list(acts)), key=_act_sort_key)
    npcs = dict(npcs)
    clues, relations = _build_clues_and_relations(acts, npcs)
    campaign = Campaign(
        id=campaign_id_for(module_text),
        title=f"模组《{title_from_text(module_text)}》",
        premise=premise,
        acts=[Act(**a) for a in acts],
        npcs={nid: NPC(**n) for nid, n in npcs.items()},
        clues=[Clue(**c) for c in clues],
        relations=[WorldRelation(**r) for r in relations],
    )
    world = build_from_campaign(campaign)
    return {
        "campaign": campaign,
        "clues": clues,
        "relations": relations,
        "world": world.to_json(),
        "notes_md": render_notes(campaign),
    }


def render_notes(campaign: Campaign) -> str:
    """备团笔记 markdown：前提 / 幕（场景+事件）/ NPC 一览 / 世界关系 / 记忆（t14）。

    记忆节经 render_memory_section 派生（NPC 印象 / 关键事件 / 世界状态摘要，KP 续备团用）。
    宽松容错分支（t10 去重复）：scene.setting / npc.personality / npc.acts_roles
    缺失或为空、relation.type 非枚举时仍可渲染（供 cli.notes 输入容错路径复用）。
    """
    lines = [f"# 备团笔记：{campaign.title}", "", f"**模组 id**：`{campaign.id}`", ""]
    lines += ["## 前提", "", campaign.premise or "（无）", ""]
    lines += ["## 幕"]
    for act in campaign.acts:
        lines += [f"### {act.title}", "", act.summary or "", ""]
        for scene in act.scenes:
            setting = scene.setting or {}
            lines += [
                f"#### {scene.title}（{setting.get('time', '')}·{setting.get('place', '')}）", ""
            ]
            for ev in scene.events:
                lines.append(f"- **{ev.title}**（{ev.kind}）：{ev.description}")
            lines.append("")
    lines += ["## NPC 一览", ""]
    for npc in campaign.npcs.values():
        personality = "、".join(npc.personality or []) or "（无特质）"
        roles = "；".join((npc.acts_roles or {}).values())
        lines.append(
            f"- {npc.name}（{npc.archetype}）：{personality}" + (f"；角色：{roles}" if roles else "")
        )
    lines += ["", "## 世界关系", ""]
    if campaign.relations:
        for rel in campaign.relations:
            typ = rel.type.value if hasattr(rel.type, "value") else rel.type
            lines.append(f"- {rel.source} --[{typ}]--> {rel.target}（{rel.label}）")
    else:
        lines.append("（无）")
    return "\n".join(lines) + "\n\n" + render_memory_section(campaign)


def _route_after_kp(state: PipelineState) -> str:
    """kp_plan 之后的工具回路：末条消息含 tool_calls → tools，否则继续扇出。"""
    msgs = state.get("messages") or []
    if msgs and isinstance(msgs[-1], AIMessage) and msgs[-1].tool_calls:
        return "tools"
    return "npc_fanout"


# ---------------------------------------------------------------- 管线组装


def build_pipeline(
    *,
    settings: Settings | None = None,
    generator: Generator | None = None,
    checkpointer: Any = None,
    store: Any = None,
):
    """组装并编译 LangGraph 管线。

    - settings：配置（缺省 get_settings()）；
    - generator：生成器（缺省按 settings.llm_enabled 构造）；
    - checkpointer：SqliteSaver（缺省用 settings.checkpoint_dir/checkpoints.sqlite）；
    - store：缺省 build_store(settings)——settings.store_dir 可写时 SqliteStore 落盘
      （跨会话记忆），否则 InMemoryStore；命名空间 ('campaigns', <campaign_id>, 'facts')。
    """
    settings = settings or get_settings()
    generator = generator or build_generator(settings)
    store = store if store is not None else build_store(settings)
    if checkpointer is None:
        # from_conn_string 是 @contextmanager，不可 next() 取值；
        # 直接 sqlite3.connect + SqliteSaver(conn)（check_same_thread=False 供并行分支共用）。
        cp_path = settings.checkpoint_dir / "checkpoints.sqlite"
        cp_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(_checkpoint_uri(cp_path), check_same_thread=False)
        checkpointer = SqliteSaver(conn)

    nodes = _build_nodes(generator)

    builder = StateGraph(PipelineState)
    builder.add_node("kp_parse", nodes["kp_parse"])
    builder.add_node("kp_plan", nodes["kp_plan"])
    builder.add_node("tools", ToolNode([kg_query]))
    builder.add_node("npc_fanout", nodes["npc_fanout"])
    builder.add_node("npc_persona", nodes["npc_persona"])
    builder.add_node("act_fanout", nodes["act_fanout"])
    builder.add_node("write_act", nodes["write_act"])
    builder.add_node("compose", nodes["compose"])

    builder.add_edge(START, "kp_parse")
    builder.add_edge("kp_parse", "kp_plan")
    builder.add_conditional_edges(
        "kp_plan", _route_after_kp, {"tools": "tools", "npc_fanout": "npc_fanout"}
    )
    builder.add_edge("tools", "kp_plan")  # 工具结果回填后回到 kp_plan 续跑
    builder.add_conditional_edges("npc_fanout", nodes["npc_fanout_sends"])
    builder.add_edge("npc_persona", "act_fanout")
    builder.add_conditional_edges("act_fanout", nodes["act_fanout_sends"])
    builder.add_edge("write_act", "compose")
    builder.add_edge("compose", END)

    return builder.compile(checkpointer=checkpointer, store=store)


def run_pipeline(
    module_text: str,
    *,
    settings: Settings | None = None,
    generator: Generator | None = None,
    checkpointer: Any = None,
    store: Any = None,
    thread_id: str = "default",
    stream_progress: bool = False,
) -> dict:
    """便捷入口：一次 invoke 返回最终状态；stream_progress 时以 custom 模式流式返回进度事件列表。"""
    app = build_pipeline(
        settings=settings, generator=generator, checkpointer=checkpointer, store=store
    )
    config = {"configurable": {"thread_id": thread_id}}
    if stream_progress:
        events: list[str] = []
        final: dict | None = None
        for mode, chunk in app.stream(
            {"module_text": module_text}, config=config, stream_mode=["custom", "values"]
        ):
            if mode == "custom":
                if isinstance(chunk, dict) and "progress" in chunk:
                    events.append(str(chunk["progress"]))
            else:
                final = chunk
        result = dict(final or {})
        result["streamed_progress"] = events
        return result
    return app.invoke({"module_text": module_text}, config=config)


__all__ = [
    "PipelineState",
    "kg_query",
    "build_store",
    "campaign_id_for",
    "extract_premise",
    "title_from_text",
    "compose_campaign",
    "render_notes",
    "build_pipeline",
    "run_pipeline",
]
