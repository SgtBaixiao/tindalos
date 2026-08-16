"""P2 回叙采集链路（ticket 01）测试：POST/GET /api/sessions/{campaign_id}。

覆盖（对齐验收）：
1. POST 建会话 → play_status 更新、session_index 递增、conflicts JSON 往返；
2. POST 空 summary → 4xx（空串/纯空白 → 400；缺字段 → 422）；
3. GET 返回会话列表（升序）+ 最新 play_status；
4. GET 空 campaign → 空列表 + current_play_status=None。

全程零网络零 LLM：record_session 内部 consolidate 走确定性路径（llm=None），
断言全部确定性；不触碰 /api/memories 既有断言（test_memory_p1 已覆盖）。
"""
from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import pytest
from fastapi.testclient import TestClient

from tindalos import web as web_mod


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """隔离实例：tmp 数据目录（memory_entries.sqlite 落 tmp）+ 无 dist。"""
    monkeypatch.setenv("TINDALOS_SITE_DB", str(tmp_path / "site.db"))
    monkeypatch.setenv("TINDALOS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TINDALOS_FRONTEND_DIST", str(tmp_path / "no-such-dist"))
    with TestClient(web_mod.create_app()) as c:
        yield c


def _post(client, cid: str, summary: str, play_status=None, conflicts=None):
    """组装 body：仅包含非空字段（与前端缺省语义一致）。"""
    body = {"summary": summary}
    if play_status is not None:
        body["play_status"] = play_status
    if conflicts is not None:
        body["conflicts"] = conflicts
    return client.post(f"/api/sessions/{cid}", json=body)


def test_post_session_play_status_index_and_conflicts_roundtrip(client):
    cid = "c-web-sess"
    conflicts = [
        {"rule": "COC7", "issue": "判定分歧", "decision": "按克苏鲁神话过意志"},
    ]

    r1 = _post(client, cid, "第一次游玩：调查员进入雾镇", play_status="进行中", conflicts=conflicts)
    assert r1.status_code == 200
    b1 = r1.json()
    assert b1["session_id"] == "sess:c-web-sess:1"
    assert b1["session_index"] == 1
    assert b1["play_status"] == "进行中"
    # conflicts 存库为 JSON 字符串，端点解析回对象（往返）
    assert b1["conflicts"] == conflicts
    assert b1["summary"] == "第一次游玩：调查员进入雾镇"
    assert b1["created_at"]
    assert b1["consolidate"]["llm"] is False  # 确定性整合，零 LLM

    r2 = _post(client, cid, "第二次游玩：发现古宅地窖", play_status="结局")
    assert r2.status_code == 200
    b2 = r2.json()
    assert b2["session_index"] == 2  # session_index 递增
    assert b2["session_id"] == "sess:c-web-sess:2"
    assert b2["play_status"] == "结局"
    assert b2["conflicts"] is None


def test_get_sessions_list_and_latest_play_status(client):
    cid = "c-web-get"
    conflicts = [{"rule": "COC7", "issue": "分歧", "decision": "按规则书"}]
    _post(client, cid, "首场", play_status="进行中", conflicts=conflicts)
    _post(client, cid, "次场", play_status="结局")

    r = client.get(f"/api/sessions/{cid}")
    assert r.status_code == 200
    body = r.json()
    assert body["campaign_id"] == cid
    assert body["current_play_status"] == "结局"  # 最新 play_status
    sessions = body["sessions"]
    assert [s["session_index"] for s in sessions] == [1, 2]  # 升序
    assert sessions[0]["summary"] == "首场"
    assert sessions[0]["conflicts"] == conflicts  # JSON 往返
    assert sessions[1]["conflicts"] is None


def test_post_session_empty_summary_4xx(client):
    for bad in ("", "   ", "\n\t"):
        r = client.post("/api/sessions/c-bad", json={"summary": bad})
        assert r.status_code == 400
    # 缺 summary 字段 → Pydantic 422（同为 4xx）
    r = client.post("/api/sessions/c-bad", json={})
    assert r.status_code == 422


def test_get_sessions_empty_campaign_ok(client):
    r = client.get("/api/sessions/ghost-campaign")
    assert r.status_code == 200
    body = r.json()
    assert body["campaign_id"] == "ghost-campaign"
    assert body["current_play_status"] is None
    assert body["sessions"] == []
