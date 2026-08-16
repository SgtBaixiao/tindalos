"""P2 起步（ticket 05）测试：post-session briefing + 向量检索。

覆盖（对齐验收）：
1. briefing：有最近会话 → 含摘要 + play_status + longterm synopsis 拼入；
   无会话无记忆 → 中文占位文案；
2. embed_entries：stub embedder 返回固定向量 → embedding 列被填（BLOB 可解析）；
   无 embedder → 零写入不崩；幂等（二跑跳过已 embedding）；
3. retrieve_memory：stub embedding 下 cosine 返回相关 top-k（分数降序）；
   无 embedding 条目 → 降级 BM25 仍返回结果；
4. /api/memories 响应含 briefing 字段（TestClient）。

全程零网络零真实 LLM：embedder 用本地 stub，LLM 路径不涉及。
"""
from __future__ import annotations

import json
import math
import sqlite3
import struct
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import pytest
from fastapi.testclient import TestClient

from tindalos import web as web_mod
from tindalos.config import Settings
from tindalos.memory_entries import (
    briefing,
    capture_episodic,
    capture_memory_entries,
    current_play_status,
    embed_entries,
    entries_db_path,
    record_session,
    retrieve_memory,
)
from tindalos.models import construct_loose_campaign


def make_settings(tmp_path) -> Settings:
    return Settings(
        llm_enabled=False,
        checkpoint_dir=tmp_path / "checkpoints",
        store_dir=tmp_path / "store",
    )


def campaign_with_events(cid: str, events: list[tuple[str, str]], n_npcs: int = 1):
    """构造一个战役：1 幕 · 1 场景 · 每个事件 (title, description) · n_npcs 个 NPC。"""
    scenes = [
        {
            "id": f"scene-{i}",
            "title": f"场景{i}",
            "setting": {"time": "夜", "place": "雾镇"},
            "events": [
                {
                    "id": f"ev-{i}",
                    "title": title,
                    "kind": "outcome",
                    "description": desc,
                }
            ],
            "npc_ids": [],
        }
        for i, (title, desc) in enumerate(events)
    ]
    acts = [{"id": "act-1", "title": "第一幕", "roman": "I", "scenes": scenes, "npc_ids": []}]
    npcs = {
        f"npc-{j}": {"id": f"npc-{j}", "name": f"NPC{j}", "archetype": "居民"}
        for j in range(1, n_npcs + 1)
    }
    return construct_loose_campaign(
        {"id": cid, "title": "雾镇疑云", "premise": "测试模组", "acts": acts, "npcs": npcs}
    )


def _embedding_column(db, campaign_id: str) -> list[bytes | None]:
    """读 embedding 列的原始值（未 embedding 为 None）。"""
    conn = sqlite3.connect(str(db))
    try:
        rows = conn.execute(
            "SELECT embedding FROM memory_entries WHERE campaign_id = ?", (campaign_id,)
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------- 1. briefing


def test_briefing_with_session_contains_summary_status_and_longterm(tmp_path):
    # 25 个事件：record_session 内触发确定性 consolidate → 写出 longterm synopsis
    events = [(f"事件{i}", f"描述{i}") for i in range(25)]
    campaign = campaign_with_events("c-brief", events)
    db = entries_db_path(make_settings(tmp_path))
    capture_memory_entries(campaign, db)
    record_session(campaign.id, "第一次游玩：调查员进入雾镇", db, play_status="进行中")

    text = briefing(campaign.id, db)
    assert "最近游玩" in text and "第一次游玩：调查员进入雾镇" in text
    assert "进行中" in text  # 当前 play_status
    assert "剧情概要" in text  # longterm synopsis 拼入
    assert "【上次停在哪】" in text


def test_briefing_no_session_no_memory_returns_placeholder(tmp_path):
    db = entries_db_path(make_settings(tmp_path))
    text = briefing("campaign-ghost", db)
    assert text and "暂无" in text  # 中文占位文案

    # 有 episodic/semantic 记忆但无会话、无 longterm → 仍视为无可回叙 → 占位
    campaign = campaign_with_events("c-ghost2", [("古宅", "古宅地窖"), ("低语", "地窖低语")])
    capture_memory_entries(campaign, db)
    text2 = briefing(campaign.id, db)
    assert "暂无" in text2


def test_briefing_status_from_current_play_status_when_row_missing(tmp_path):
    # play_sessions 行无 play_status 时，回退 current_play_status（此处为 None → 不出现状态行）
    campaign = campaign_with_events("c-st", [("古宅", "古宅地窖")])
    db = entries_db_path(make_settings(tmp_path))
    capture_memory_entries(campaign, db)
    record_session(campaign.id, "首场：进入雾镇", db, play_status=None)
    text = briefing(campaign.id, db)
    assert "首场：进入雾镇" in text
    assert "当前状态" not in text  # 无 play_status → 不输出状态行


# ---------------------------------------------------------------- 2. embed_entries


def test_embed_entries_fixed_vector_fills_blob_and_idempotent(tmp_path):
    campaign = campaign_with_events("c-emb", [("古宅", "古宅地窖低语"), ("失踪", "守夜人失踪")])
    db = entries_db_path(make_settings(tmp_path))
    capture_memory_entries(campaign, db)

    fixed = lambda text: [0.25, 0.5, 0.25]  # noqa: E731 - stub embedder 返回固定向量
    n = embed_entries(campaign.id, db, embedder=fixed)
    assert n > 0  # 全部条目都被填

    blobs = _embedding_column(db, campaign.id)
    assert all(b is not None for b in blobs)
    vec = struct.unpack("<3f", blobs[0])
    assert abs(vec[0] - 0.25) < 1e-6 and abs(vec[1] - 0.5) < 1e-6

    # 幂等：二跑全部已 embedding → 零写入
    assert embed_entries(campaign.id, db, embedder=fixed) == 0


def test_embed_entries_no_embedder_zero_writes_no_crash(tmp_path):
    campaign = campaign_with_events("c-noemb", [("古宅", "古宅地窖")])
    db = entries_db_path(make_settings(tmp_path))
    capture_memory_entries(campaign, db)

    assert embed_entries(campaign.id, db) == 0  # 无 embedder → 零写入不崩
    assert all(b is None for b in _embedding_column(db, campaign.id))


def test_embed_entries_raising_embedder_stops_honestly(tmp_path):
    campaign = campaign_with_events("c-raise", [("古宅", "古宅地窖"), ("低语", "地窖低语")])
    db = entries_db_path(make_settings(tmp_path))
    capture_memory_entries(campaign, db)

    calls = {"n": 0}

    def exploding(text: str):
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("network down")
        return [0.0, 1.0]

    processed = embed_entries(campaign.id, db, embedder=exploding)
    assert processed >= 0  # 不崩，返回已处理数（诚实降级）
    blobs = _embedding_column(db, campaign.id)
    # 第一条成功写入，其余未写（断在抛错处）
    assert blobs[0] is not None and all(b is None for b in blobs[1:])


# ---------------------------------------------------------------- 3. retrieve_memory


def _vocab_embed(text: str) -> list[float]:
    """词袋式伪向量：按共享词激活维度 → 相似内容余弦更高。确定性、零 LLM。"""
    vocab = ["古宅", "地窖", "低语", "雾镇", "守夜", "失踪", "调查"]
    vec = [0.0] * len(vocab)
    for i, w in enumerate(vocab):
        vec[i] = float(text.count(w))
    norm = math.sqrt(sum(v * v for v in vec))
    return [v / norm for v in vec] if norm else vec


def test_retrieve_memory_cosine_topk_ordering(tmp_path):
    campaign = campaign_with_events(
        "c-cos",
        [("古宅地窖", "调查员在古宅地窖发现低语"), ("守夜失踪", "守夜人在雾镇失踪")],
    )
    db = entries_db_path(make_settings(tmp_path))
    capture_episodic(campaign, db)
    embed_entries(campaign.id, db, embedder=_vocab_embed)

    results = retrieve_memory(campaign.id, "古宅地窖的低语", db, k=2, embedder=_vocab_embed)
    assert results, "cosine 检索应返回结果"
    assert results[0]["score"] >= results[1]["score"]  # 分数降序
    assert "古宅" in results[0]["content"]  # 与查询共享词最多的条目居首
    for r in results:
        assert {"id", "memory_type", "content", "score"} <= set(r)
        assert r["score"] >= 0.0 and r["score"] <= 1.0  # 余弦 ∈ [-1, 1]


def test_retrieve_memory_bm25_fallback_no_embedding(tmp_path):
    campaign = campaign_with_events(
        "c-bm",
        [("古宅", "调查员进入古宅地窖"), ("守夜", "守夜人在雾镇失踪")],
    )
    db = entries_db_path(make_settings(tmp_path))
    capture_episodic(campaign, db)

    # 未调 embed_entries → 无任何 embedding → 确定性降级 BM25
    results = retrieve_memory(campaign.id, "古宅", db, k=2)
    assert results, "BM25 降级仍返回结果"
    assert any("古宅" in r["content"] for r in results)
    assert all("score" in r and r["score"] > 0 for r in results)


def test_retrieve_memory_empty_query_returns_empty(tmp_path):
    campaign = campaign_with_events("c-empty", [("古宅", "古宅地窖")])
    db = entries_db_path(make_settings(tmp_path))
    capture_episodic(campaign, db)
    assert retrieve_memory(campaign.id, "", db) == []


# ---------------------------------------------------------------- 4. /api/memories 含 briefing


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("TINDALOS_SITE_DB", str(tmp_path / "site.db"))
    monkeypatch.setenv("TINDALOS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TINDALOS_FRONTEND_DIST", str(tmp_path / "no-such-dist"))
    with TestClient(web_mod.create_app()) as c:
        yield c


def test_api_memories_contains_briefing(client, tmp_path):
    cid = "c-web-brief"
    db = tmp_path / "data" / "store" / "memory_entries.sqlite"
    campaign = campaign_with_events(cid, [("古宅", "古宅地窖"), ("低语", "地窖低语")])
    capture_memory_entries(campaign, db)
    record_session(cid, "首场游玩：进入雾镇", db, play_status="进行中")

    r = client.get(f"/api/memories/{cid}")
    assert r.status_code == 200
    body = r.json()
    assert body["campaign_id"] == cid
    assert body["status"] == "ok"
    assert body["play_status"] == "进行中"
    assert "briefing" in body  # P2 新增字段
    assert "最近游玩" in body["briefing"] and "首场游玩：进入雾镇" in body["briefing"]
    # 既有四类 + play_status 结构不破坏
    assert set(body["memories"].keys()) == {"episodic", "semantic", "shortterm", "longterm"}


def test_api_memories_empty_campaign_contains_briefing_placeholder(client):
    r = client.get("/api/memories/ghost-campaign")
    assert r.status_code == 200
    body = r.json()
    assert "briefing" in body and "暂无" in body["briefing"]
    assert body["play_status"] is None
    for mt in ("episodic", "semantic", "shortterm", "longterm"):
        assert body["memories"][mt] == []
