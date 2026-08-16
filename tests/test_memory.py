"""跨会话记忆（task t14-memory）测试。

覆盖（对齐验收）：
1. build_memory_facts 纯函数：每 NPC 一条印象、关键事件、世界状态摘要齐全；
2. 管线 compose 生成后 store 有 facts：InMemoryStore 直读 + 每 NPC 独立印象项；
3. 跨会话可读：SqliteStore 落盘（settings.store_dir）后，新 store 实例（模拟新会话）可读；
4. tindalos memories <campaign> 列事实（NPC 印象 / 关键事件 / 世界状态摘要）；
   campaign 参数支持 id 或 campaign JSON 路径；未知 campaign 输出「暂无」且退出码 0；
5. 备团笔记（render_notes 与 CLI notes）含「记忆」节。

全程 DeterministicGenerator（零网络零 LLM）。
"""
import json
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.store.memory import InMemoryStore
from typer.testing import CliRunner

import tindalos.config as config
from tindalos.cli import app
from tindalos.config import Settings
from tindalos.memory import (
    build_memory_facts,
    build_store,
    list_memories,
    read_memory_facts,
    render_memory_section,
    write_memory_facts,
)
from tindalos.memory_entries import (
    assemble_memory_context,
    capture_episodic,
    capture_memory_entries,
    capture_semantic_initial,
    count_entries,
    entries_db_path,
    list_entries,
    render_entries_doc,
)
from tindalos.pipeline import build_pipeline, campaign_id_for, render_notes

MODULE_TEXT = """# 雾镇疑云

前提：雾镇接连发生失踪案，镇长委托外乡的调查员查明镇上古宅与守夜人的秘密。
镇民对新来者充满戒心，古宅地窖里传出不属于人类的低语。
"""

runner = CliRunner()


def make_settings(tmp_path) -> Settings:
    return Settings(
        llm_enabled=False,
        checkpoint_dir=tmp_path / "checkpoints",
        store_dir=tmp_path / "store",
    )


def _cp_uri(tmp_path, suffix: str = "") -> str:
    return str(tmp_path / f"cp{suffix}.db").replace("\\", "/")


def run_campaign(tmp_path, settings=None, store=None):
    """跑一次管线（缺省 store 走 build_store 落盘），返回 (campaign, store)。"""
    settings = settings or make_settings(tmp_path)
    store = store if store is not None else build_store(settings)
    with SqliteSaver.from_conn_string(_cp_uri(tmp_path)) as cp:
        app_obj = build_pipeline(settings=settings, checkpointer=cp, store=store)
        result = app_obj.invoke(
            {"module_text": MODULE_TEXT}, config={"configurable": {"thread_id": "t1"}}
        )
    return result["campaign"], store


# ---------------------------------------------------------------- 1. 纯函数：记忆事实结构


def test_build_memory_facts_structure(tmp_path):
    campaign, _ = run_campaign(tmp_path)
    facts = build_memory_facts(campaign)
    assert facts["campaign_id"] == campaign.id
    assert facts["title"] == campaign.title
    # 每 NPC 一条印象
    assert len(facts["npc_impressions"]) == len(campaign.npcs)
    for imp in facts["npc_impressions"]:
        assert imp["npc_id"] and imp["name"] and imp["impression"]
        assert imp["archetype"]
    # 关键事件非空（每幕至少一条）
    assert facts["key_events"], "关键事件非空"
    assert all("event_id" in ev and "event_title" in ev for ev in facts["key_events"])
    # 世界状态摘要
    ws = facts["world_summary"]
    assert ws["npc_count"] == len(campaign.npcs)
    assert ws["act_count"] == len(campaign.acts)
    assert ws["relation_count"] == len(campaign.relations)
    assert ws["relations"] and ws["summary"]
    assert "updated_at" in facts


def test_npc_impression_includes_personality_and_roles(tmp_path):
    campaign, _ = run_campaign(tmp_path)
    facts = build_memory_facts(campaign)
    npc = next(iter(campaign.npcs.values()))
    imp = next(i for i in facts["npc_impressions"] if i["npc_id"] == npc.id)
    assert npc.name in imp["impression"]
    if npc.personality:
        assert npc.personality[0] in imp["impression"]
    if npc.acts_roles:
        assert any(role in imp["impression"] for role in npc.acts_roles.values())


# ---------------------------------------------------------------- 2. store 写入读取（InMemoryStore）


def test_store_facts_written_by_pipeline_and_readable(tmp_path):
    store = InMemoryStore()
    campaign, _ = run_campaign(tmp_path, store=store)
    ns = ("campaigns", campaign.id, "facts")
    facts = read_memory_facts(store, campaign.id)
    assert facts is not None, "compose 已把记忆事实写入 store"
    assert len(facts["npc_impressions"]) == len(campaign.npcs)
    assert facts["key_events"]
    assert facts["world_summary"]["summary"]
    # 每 NPC 一条独立印象项（spec：每 NPC 一条印象）
    for npc in campaign.npcs.values():
        item = store.get(ns, f"npc:{npc.id}")
        assert item is not None and item.value["npc_id"] == npc.id
    # 既有 relations/campaign 键不被破坏
    assert store.get(ns, "relations").value.get("items")
    assert store.get(ns, "campaign").value["id"] == campaign.id


def test_write_read_memory_facts_roundtrip(tmp_path):
    campaign, _ = run_campaign(tmp_path)
    store = InMemoryStore()
    written = write_memory_facts(store, campaign)
    read_back = read_memory_facts(store, campaign.id)
    assert read_back["campaign_id"] == written["campaign_id"]
    assert read_back["npc_impressions"] == written["npc_impressions"]
    assert read_back["key_events"] == written["key_events"]
    assert read_memory_facts(store, "campaign-ghost") is None


# ---------------------------------------------------------------- 3. 跨会话（SqliteStore 落盘）


def test_store_facts_persist_across_sessions(tmp_path):
    settings = make_settings(tmp_path)
    campaign, _ = run_campaign(tmp_path, settings=settings)  # 会话 1：落盘写入
    store_file = settings.store_dir / "memory.sqlite"
    assert store_file.exists(), "settings.store_dir 落盘 memory.sqlite"
    # 会话 2：同一 store_dir 的新 store 实例（模拟新进程/新会话）可读
    store2 = build_store(settings)
    facts = read_memory_facts(store2, campaign.id)
    assert facts is not None, "跨会话可读（SqliteStore 落盘）"
    assert facts["campaign_id"] == campaign.id
    assert len(facts["npc_impressions"]) == len(campaign.npcs)
    assert facts["world_summary"]["summary"]
    # 二次运行后 facts 仍可读（同 namespace 覆写不冲突）
    campaign2, _ = run_campaign(tmp_path, settings=settings, store=build_store(settings))
    assert campaign2.id == campaign.id
    assert read_memory_facts(build_store(settings), campaign.id) is not None


# ---------------------------------------------------------------- 4. CLI memories


def test_memories_cli_lists_facts(tmp_path):
    settings = make_settings(tmp_path)
    campaign, _ = run_campaign(tmp_path, settings=settings)
    old = config._settings
    config._settings = settings
    try:
        r = runner.invoke(app, ["memories", campaign.id])
    finally:
        config._settings = old
    assert r.exit_code == 0, r.stdout
    assert "记忆" in r.stdout
    assert "NPC 印象" in r.stdout and "关键事件" in r.stdout and "世界状态" in r.stdout
    for npc in campaign.npcs.values():
        assert npc.name in r.stdout
    assert campaign.id in r.stdout


def test_memories_cli_accepts_campaign_json_path(tmp_path):
    settings = make_settings(tmp_path)
    campaign, _ = run_campaign(tmp_path, settings=settings)
    cj = tmp_path / "campaign.json"
    cj.write_text(json.dumps(campaign.model_dump(mode="json"), ensure_ascii=False), encoding="utf-8")
    old = config._settings
    config._settings = settings
    try:
        r = runner.invoke(app, ["memories", str(cj)])
    finally:
        config._settings = old
    assert r.exit_code == 0, r.stdout
    assert "记忆" in r.stdout and campaign.id in r.stdout


def test_memories_cli_unknown_campaign_prints_empty(tmp_path):
    settings = make_settings(tmp_path)
    old = config._settings
    config._settings = settings
    try:
        r = runner.invoke(app, ["memories", "campaign-ghost"])
    finally:
        config._settings = old
    assert r.exit_code == 0, r.stdout
    assert "暂无" in r.stdout


def test_list_memories_markdown(tmp_path):
    campaign, store = run_campaign(tmp_path)
    doc = list_memories(store, campaign.id)
    assert "NPC 印象" in doc and "关键事件" in doc and "世界状态" in doc
    assert "暂无" not in doc
    assert "暂无" in list_memories(store, "campaign-ghost")


# ---------------------------------------------------------------- 5. 备团笔记含记忆节


def test_render_notes_contains_memory_section(tmp_path):
    campaign, _ = run_campaign(tmp_path)
    md = render_notes(campaign)
    assert "## 记忆" in md
    assert "NPC 印象" in md and "关键事件" in md and "世界状态" in md
    for npc in campaign.npcs.values():
        assert npc.name in md


def test_render_memory_section_direct(tmp_path):
    campaign, _ = run_campaign(tmp_path)
    section = render_memory_section(campaign)
    assert section.startswith("## 记忆")
    assert "NPC 印象" in section and "关键事件" in section and "世界状态" in section


def test_cli_notes_contains_memory_section(tmp_path):
    campaign, _ = run_campaign(tmp_path)
    out = tmp_path / "notes.md"
    cj = tmp_path / "campaign.json"
    cj.write_text(json.dumps(campaign.model_dump(mode="json"), ensure_ascii=False), encoding="utf-8")
    r = runner.invoke(app, ["notes", str(cj), "--out", str(out)])
    assert r.exit_code == 0, r.stdout
    text = out.read_text(encoding="utf-8")
    assert "## 记忆" in text and "NPC 印象" in text and "关键事件" in text


# ---------------------------------------------------------------- 6. memory_entries 增量层（P0-a，零 LLM）


def test_capture_episodic_structure(tmp_path):
    """情景记忆：每 event 一条，id 前缀 evm:，source_episode 溯源，content_hash 判重键就位。"""
    campaign, _ = run_campaign(tmp_path)
    db = entries_db_path(make_settings(tmp_path))
    stats = capture_episodic(campaign, db)
    assert stats["total"] > 0
    events = sum(len(sc.events) for act in campaign.acts for sc in act.scenes)
    assert stats["total"] == events, "每 event 一条情景记忆"
    entries = list_entries(campaign.id, "episodic", db)
    for e in entries:
        assert e["id"].startswith(f"evm:{campaign.id}:")
        assert e["memory_type"] == "episodic"
        assert e["source_episode"], "act/scene/event 溯源"
        assert e["content_hash"]
        assert 0 <= e["importance"] <= 1
    # 幂等键 = event id（Campaign 跨层唯一性保证 → 条目 id 唯一）
    assert len({e["id"] for e in entries}) == len(entries)


def test_capture_episodic_idempotent(tmp_path):
    """幂等：同 campaign 两次捕获不重复（id + content_hash 均命中跳过）。"""
    campaign, _ = run_campaign(tmp_path)
    db = entries_db_path(make_settings(tmp_path))
    first = capture_episodic(campaign, db)
    second = capture_episodic(campaign, db)
    assert first["total"] == second["total"]
    assert second["inserted"] == 0, "二次捕获零新增"
    assert len(list_entries(campaign.id, "episodic", db)) == first["total"]


def test_capture_semantic_initial_structure(tmp_path):
    """语义记忆：NPC 事实（subject_key=npc:<id>）+ 地点事实（place:<scene_id>），去重键就位。"""
    campaign, _ = run_campaign(tmp_path)
    db = entries_db_path(make_settings(tmp_path))
    stats = capture_semantic_initial(campaign, db)
    entries = list_entries(campaign.id, "semantic", db)
    assert len(entries) == stats["total"]
    keys = {e["subject_key"] for e in entries}
    npc_keys = {f"npc:{nid}" for nid in campaign.npcs}
    assert npc_keys <= keys, "每 NPC 一条语义事实"
    assert any(k.startswith("place:") for k in keys), "地点事实存在"
    # 确定性抽取复用 npc_impression 视角：条目内容含 NPC 名
    for nid, npc in campaign.npcs.items():
        row = next(e for e in entries if e["subject_key"] == f"npc:{nid}")
        assert npc.name in row["content"]


def test_capture_semantic_initial_idempotent(tmp_path):
    campaign, _ = run_campaign(tmp_path)
    db = entries_db_path(make_settings(tmp_path))
    first = capture_semantic_initial(campaign, db)
    second = capture_semantic_initial(campaign, db)
    assert first["total"] == second["total"]
    assert second["inserted"] == 0, "subject_key 去重：二次捕获零新增"


def test_capture_memory_entries_both_types(tmp_path):
    campaign, _ = run_campaign(tmp_path)
    db = entries_db_path(make_settings(tmp_path))
    stats = capture_memory_entries(campaign, db)
    assert stats["episodic"]["total"] > 0 and stats["semantic"]["total"] > 0
    counts = count_entries(campaign.id, db)
    assert counts["episodic"] == stats["episodic"]["total"]
    assert counts["semantic"] == stats["semantic"]["total"]
    assert counts["shortterm"] == 0 and counts["longterm"] == 0, "P1 才填充保留时限轴"


def test_pipeline_compose_writes_memory_entries(tmp_path):
    """compose 收敛点：跑管线后 memory_entries.sqlite 落盘并含情景+语义。"""
    settings = make_settings(tmp_path)
    campaign, _ = run_campaign(tmp_path, settings=settings)
    db = entries_db_path(settings)
    assert db.exists(), "compose 后 memory_entries.sqlite 落盘（尊重 store_dir）"
    assert list_entries(campaign.id, "episodic", db), "情景记忆已写入"
    assert list_entries(campaign.id, "semantic", db), "语义记忆已写入"


def test_assemble_memory_context_empty_without_history(tmp_path):
    """无历史 → 空串（首轮生成零影响，保持确定性）。"""
    campaign, _ = run_campaign(tmp_path)
    db = entries_db_path(make_settings(tmp_path))
    # compose 已为该 campaign 写入历史；用未跑过 compose 的 campaign 验证空历史
    assert assemble_memory_context("campaign-ghost", "失踪案", db_path=db) == ""
    # 全新 db（未写入任何条目）→ 空串
    fresh_db = entries_db_path(make_settings(tmp_path / "fresh"))
    assert assemble_memory_context(campaign.id, "失踪案", db_path=fresh_db) == ""


def test_assemble_memory_context_retrieves_relevant(tmp_path):
    """有历史 → BM25 检索命中相关条目，命中条目内容出现在上下文中。"""
    campaign, _ = run_campaign(tmp_path)
    db = entries_db_path(make_settings(tmp_path))
    capture_memory_entries(campaign, db)
    first_ev_title = campaign.acts[0].scenes[0].events[0].title
    ctx = assemble_memory_context(campaign.id, f"{first_ev_title} 发生了什么", db_path=db)
    assert ctx.startswith("【既有记忆】")
    assert "（情景记忆）" in ctx or "（语义记忆）" in ctx
    assert first_ev_title in ctx, "相关事件标题被 BM25 检索命中"


def test_render_entries_doc_groups_by_type(tmp_path):
    campaign, _ = run_campaign(tmp_path)
    db = entries_db_path(make_settings(tmp_path))
    capture_memory_entries(campaign, db)
    entries = list_entries(campaign.id, db_path=db)
    doc = render_entries_doc(campaign.id, entries)
    assert "## 记忆条目（四类）" in doc
    assert "### 情景记忆" in doc and "### 语义记忆" in doc
    assert "### 短期记忆" not in doc and "### 长期记忆" not in doc, "P1 前不出现空分组"
    assert doc.rstrip().endswith("〕"), "语义记忆条目行尾带 source 溯源"


def test_memories_cli_lists_four_types(tmp_path):
    """CLI memories 升级：聚合视角后追加四类条目节（现有聚合断言保持）。"""
    settings = make_settings(tmp_path)
    campaign, _ = run_campaign(tmp_path, settings=settings)
    old = config._settings
    config._settings = settings
    try:
        r = runner.invoke(app, ["memories", campaign.id])
    finally:
        config._settings = old
    assert r.exit_code == 0, r.stdout
    assert "NPC 印象" in r.stdout and "世界状态" in r.stdout  # 聚合视角不变
    assert "记忆条目（四类）" in r.stdout
    assert "情景记忆" in r.stdout and "语义记忆" in r.stdout
    # 语义记忆内容来自 npc_impression：含 NPC 名
    for npc in campaign.npcs.values():
        assert npc.name in r.stdout


def test_memories_cli_unknown_campaign_still_empty(tmp_path):
    """未知 campaign：聚合输出「暂无」，四类无条目不追加（退出码 0）。"""
    settings = make_settings(tmp_path)
    old = config._settings
    config._settings = settings
    try:
        r = runner.invoke(app, ["memories", "campaign-ghost"])
    finally:
        config._settings = old
    assert r.exit_code == 0, r.stdout
    assert "暂无" in r.stdout
    assert "记忆条目（四类）" not in r.stdout
