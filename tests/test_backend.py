"""tindalos.backend（generator + pipeline）测试。

覆盖：
1. DeterministicGenerator 离线结构完整 + 全程确定性（零网络零 LLM）；
2. 端到端：小模组文本 → campaign JSON 结构合法（≥1 幕、幕含 scene/event、NPC 被场景引用）
   + 备团笔记 markdown 生成（含幕标题与 NPC 名）；
3. 进度事件序列完整（kp→npc→act→compose 顺序）；
4. checkpoint：同一 thread_id 跨实例二次运行继承前次状态（不重跑）；
5. store：compose 写入 facts 后，新图实例（共享 store）可读；
6. kg_query 工具经 ToolNode 的 Function Calling 回路。

全程使用 DeterministicGenerator（settings.llm_enabled=False），零网络。
"""
import json
import os
import pathlib
import sys

_SRC = pathlib.Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.store.memory import InMemoryStore

from tindalos.config import Settings
from tindalos.generator import DeterministicGenerator, OllamaGenerator, build_generator
from tindalos.kg import WorldGraph
from tindalos.models import Campaign
from tindalos.pipeline import build_pipeline, campaign_id_for, kg_query

MODULE_TEXT = """# 雾镇疑云

前提：雾镇接连发生失踪案，镇长委托外乡的调查员查明镇上古宅与守夜人的秘密。
镇民对新来者充满戒心，古宅地窖里传出不属于人类的低语。
"""

PREMISE = "雾镇接连发生失踪案，镇长委托外乡的调查员查明镇上古宅与守夜人的秘密。镇民对新来者充满戒心，古宅地窖里传出不属于人类的低语。"


def make_settings(tmp_path) -> Settings:
    return Settings(
        llm_enabled=False,
        checkpoint_dir=tmp_path / "checkpoints",
        store_dir=tmp_path / "store",
    )


def db_path(tmp_path) -> str:
    return str(tmp_path / "checkpoints.db").replace("\\", "/")


def as_dict(campaign):
    """checkpoint 往返后 campaign 为 pydantic 模型；无 checkpoint 时为 dict——统一转 dict。"""
    return campaign.model_dump(mode="json") if hasattr(campaign, "model_dump") else campaign


# ---------------------------------------------------------------- 1. 生成器


def test_deterministic_generator_structures_and_determinism():
    gen = DeterministicGenerator()
    acts = gen.generate_acts(PREMISE, 2)
    assert len(acts) == 2
    for a in acts:
        assert {"id", "roman", "title", "summary", "npc_ids", "scene_titles"} <= set(a)
    npcs = gen.generate_npcs(PREMISE, 3)
    assert len(npcs) == 3
    for n in npcs:
        assert {"id", "name", "archetype", "personality", "description", "acts_roles"} <= set(n)
        assert isinstance(n["personality"], list) and n["personality"]
    scene = gen.generate_scene(acts[0]["title"], PREMISE, ["npc-1", "npc-2"])
    assert {"id", "title", "setting", "events", "npc_ids"} <= set(scene)
    kinds = [ev["kind"] for ev in scene["events"]]
    assert kinds == ["entry", "trigger", "outcome"]
    for ev in scene["events"]:
        for nxt in ev["next_event_ids"]:
            assert nxt in {e["id"] for e in scene["events"]}  # 引用可解析
    # 全程确定性：同一 premise → 同一输出
    assert gen.generate_acts(PREMISE, 2) == DeterministicGenerator().generate_acts(PREMISE, 2)
    assert gen.generate_npcs(PREMISE, 3) == DeterministicGenerator().generate_npcs(PREMISE, 3)


def test_build_generator_switch():
    off = build_generator(Settings(llm_enabled=False))
    assert isinstance(off, DeterministicGenerator)
    on = build_generator(Settings(llm_enabled=True))
    assert isinstance(on, OllamaGenerator)  # 构造不发起网络请求


# ---------------------------------------------------------------- 2. 端到端结构


def test_end_to_end_campaign_structure(tmp_path):
    with SqliteSaver.from_conn_string(db_path(tmp_path)) as cp:
        app = build_pipeline(settings=make_settings(tmp_path), checkpointer=cp, store=InMemoryStore())
        result = app.invoke(
            {"module_text": MODULE_TEXT},
            config={"configurable": {"thread_id": "t1"}},
        )
    campaign = as_dict(result["campaign"])
    assert campaign["id"] == campaign_id_for(MODULE_TEXT)
    assert campaign["premise"]
    assert len(campaign["acts"]) >= 1
    for act in campaign["acts"]:
        assert act["scenes"], f"幕 {act['id']} 至少含一个场景"
        for scene in act["scenes"]:
            assert scene["events"], f"场景 {scene['id']} 至少含一个事件"
            for nid in scene["npc_ids"]:
                assert nid in campaign["npcs"], f"场景引用未注册 NPC {nid}"
        for nid in act["npc_ids"]:
            assert nid in campaign["npcs"]
    assert campaign["npcs"], "NPC 已生成"
    # JSON 结构合法：model_validate_json 往返通过（含跨层引用校验）
    Campaign.model_validate_json(json.dumps(campaign))


def test_notes_markdown_contains_act_title_and_npc_name(tmp_path):
    with SqliteSaver.from_conn_string(db_path(tmp_path)) as cp:
        app = build_pipeline(settings=make_settings(tmp_path), checkpointer=cp, store=InMemoryStore())
        result = app.invoke(
            {"module_text": MODULE_TEXT},
            config={"configurable": {"thread_id": "t1"}},
        )
    notes = result["notes_md"]
    campaign = as_dict(result["campaign"])
    assert isinstance(notes, str) and notes.strip()
    assert any(act["title"] in notes for act in campaign["acts"]), "备团笔记含幕标题"
    assert any(npc["name"] in notes for npc in campaign["npcs"].values()), "备团笔记含 NPC 名"


# ---------------------------------------------------------------- 3. 进度事件序列


def _run_with_progress(tmp_path):
    with SqliteSaver.from_conn_string(db_path(tmp_path)) as cp:
        app = build_pipeline(settings=make_settings(tmp_path), checkpointer=cp, store=InMemoryStore())
        events: list[str] = []
        final = None
        for mode, chunk in app.stream(
            {"module_text": MODULE_TEXT},
            config={"configurable": {"thread_id": "t1"}},
            stream_mode=["custom", "values"],
        ):
            if mode == "custom":
                # 本版本 custom chunk 即载荷字典本身：{'progress': '...'}
                if isinstance(chunk, dict) and "progress" in chunk:
                    events.append(str(chunk["progress"]))
            else:
                final = chunk
    return events, final


def test_progress_event_sequence_complete(tmp_path):
    events, final = _run_with_progress(tmp_path)
    assert events, "应收到 custom 进度事件"
    # 四类事件齐全，且相对顺序 kp → npc → act → compose
    assert any("KP 拟定幕结构" in e for e in events)
    assert any("KG 查询" in e for e in events)
    npc_events = [e for e in events if "注入人格" in e and "NPC " in e]
    act_events = [e for e in events if "写作第" in e]
    assert npc_events and len(npc_events) >= 2, "每个 NPC 一条注入人格事件"
    assert act_events and len(act_events) >= 2, "每幕一条写作事件"
    assert any("校对付印" in e for e in events)
    i_kp = next(i for i, e in enumerate(events) if "KP 拟定幕结构" in e)
    i_npc = next(i for i, e in enumerate(events) if "注入人格" in e)
    i_act = next(i for i, e in enumerate(events) if "写作第" in e)
    i_comp = next(i for i, e in enumerate(events) if "校对付印" in e)
    assert i_kp < i_npc < i_act < i_comp, f"进度顺序错乱: {events}"
    # state.progress 同样完整
    assert final is not None and "progress" in final
    state_progress = [str(p) for p in final["progress"]]
    assert any("KP 拟定幕结构" in p for p in state_progress)
    assert any("校对付印" in p for p in state_progress)


# ---------------------------------------------------------------- 4. checkpoint 继承


def test_checkpoint_second_run_inherits_state(tmp_path):
    db = db_path(tmp_path)
    cfg = {"configurable": {"thread_id": "same"}}
    with SqliteSaver.from_conn_string(db) as cp:
        app1 = build_pipeline(settings=make_settings(tmp_path), checkpointer=cp, store=InMemoryStore())
        r1 = app1.invoke({"module_text": MODULE_TEXT}, config=cfg)
    # 新图实例 + 同一 db 文件 + 同一 thread_id：不重跑，直接继承前次最终状态
    with SqliteSaver.from_conn_string(db) as cp2:
        app2 = build_pipeline(settings=make_settings(tmp_path), checkpointer=cp2, store=InMemoryStore())
        r2 = app2.invoke(None, config=cfg)
        snapshot = app2.get_state(cfg).values
    assert as_dict(r1["campaign"])["id"] == as_dict(r2["campaign"])["id"]
    assert r2["notes_md"] == r1["notes_md"]
    assert [str(p) for p in r2["progress"]] == [str(p) for p in r1["progress"]]
    # get_state 视角同样继承（campaign 存在、进度完整）
    assert snapshot["campaign"] is not None
    assert any("校对付印" in str(p) for p in snapshot["progress"])


# ---------------------------------------------------------------- 5. store 持久化


def test_store_facts_readable_by_new_instance(tmp_path):
    store = InMemoryStore()
    campaign_id = campaign_id_for(MODULE_TEXT)
    with SqliteSaver.from_conn_string(db_path(tmp_path)) as cp:
        app1 = build_pipeline(settings=make_settings(tmp_path), checkpointer=cp, store=store)
        r1 = app1.invoke({"module_text": MODULE_TEXT}, config={"configurable": {"thread_id": "t1"}})
    # 新图实例（共享同一 store 对象）可读 compose 写入的事实：直接读 store + 二次运行验证
    rel_item = store.get(("campaigns", campaign_id, "facts"), "relations")
    assert rel_item is not None and rel_item.value.get("items"), "facts.relations 已持久化"
    camp_item = store.get(("campaigns", campaign_id, "facts"), "campaign")
    assert camp_item is not None and camp_item.value["id"] == campaign_id
    # 二次运行（新图实例、同 campaign_id）kp_plan 从 store 恢复关系 → kg 查询有内容
    with SqliteSaver.from_conn_string(db_path(tmp_path) + "2") as cp3:
        app3 = build_pipeline(settings=make_settings(tmp_path), checkpointer=cp3, store=store)
        r3 = app3.invoke({"module_text": MODULE_TEXT}, config={"configurable": {"thread_id": "t3"}})
    kg_lines = [p for p in (r3.get("progress") or []) if str(p).startswith("KG 查询")]
    assert kg_lines, "kp_plan 的 kg_query 工具调用有结果"
    assert "关系" in str(kg_lines[0]), "store 恢复的关系出现在 KG 摘要中"


# ---------------------------------------------------------------- 7. 评审回归（G5）


def test_default_checkpointer_creates_checkpoint_file(tmp_path):
    """回归（G5 阻塞项）：build_pipeline 缺省 checkpointer 必须可编译——
    不再对 @contextmanager 的 from_conn_string 调 next()（TypeError），
    改为 sqlite3.connect + SqliteSaver(conn)；invoke 后 checkpoint 文件必须生成。"""
    settings = make_settings(tmp_path)
    app = build_pipeline(settings=settings)  # 不注入 checkpointer
    result = app.invoke(
        {"module_text": MODULE_TEXT},
        config={"configurable": {"thread_id": "t1"}},
    )
    cp_file = settings.checkpoint_dir / "checkpoints.sqlite"
    assert cp_file.exists(), "缺省 checkpointer 应落盘 checkpoint 文件"
    assert cp_file.stat().st_size > 0
    assert as_dict(result["campaign"])["id"] == campaign_id_for(MODULE_TEXT)


def test_act_roman_is_roman_numerals():
    """回归（G5）：_ROMANS 为真罗马数字 I..VI（而非中文数字）。"""
    gen = DeterministicGenerator()
    acts = gen.generate_acts(PREMISE, 3)
    assert [a["roman"] for a in acts] == ["I", "II", "III"]
    assert all(a["roman"] in {"I", "II", "III", "IV", "V", "VI"} for a in acts)
    # 幕 id 与罗马编号对齐
    assert [a["id"] for a in acts] == ["act-1", "act-2", "act-3"]


def test_default_act_npc_counts(tmp_path, monkeypatch):
    """文档化缺省（G5）：未设 TINDALOS_N_ACTS/TINDALOS_N_NPCS 时 n_acts=2、n_npcs=3。"""
    monkeypatch.delenv("TINDALOS_N_ACTS", raising=False)
    monkeypatch.delenv("TINDALOS_N_NPCS", raising=False)
    with SqliteSaver.from_conn_string(db_path(tmp_path)) as cp:
        app = build_pipeline(settings=make_settings(tmp_path), checkpointer=cp, store=InMemoryStore())
        result = app.invoke(
            {"module_text": MODULE_TEXT},
            config={"configurable": {"thread_id": "t1"}},
        )
    campaign = as_dict(result["campaign"])
    assert len(campaign["acts"]) == 2
    assert len(campaign["npcs"]) == 3


# ---------------------------------------------------------------- 6. kg_query 工具


def test_kg_query_tool_summary():
    wg = WorldGraph()
    for eid in ("npc-1", "npc-2", "clue-act-1"):
        wg.add_entity(eid, "npc" if eid.startswith("npc") else "clue", {})
    wg.add_relation("npc-1", "clue-act-1", "POINTS_TO", "指向线索", "1900-01-01")
    wg.add_relation("npc-1", "npc-2", "KNOWS", "互相认识", "1900-01-01")
    summary = kg_query("npc-1", {"world": wg.to_json()})
    assert "npc-1" in summary and "2 条" in summary and "指向线索" in summary
    empty = kg_query("npc-9", {"world": wg.to_json()})
    assert "暂无已注册关系" in empty
