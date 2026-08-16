"""历史记录模块（wayfinder ticket 07）测试。

用 ``TINDALOS_SITE_DB`` 指向 tmp 文件（每测试独立 tmp + monkeypatch），覆盖：
1. db_path 默认路径与 env 覆盖；
2. 模组注册/查询/更新、重复 sha256 幂等（返回已有行）；
3. 剧本注册（snapshot 往返：写整份 dict、读回解析对象）、列表不含 snapshot、删除；
4. 中文 meta/snapshot 的 ensure_ascii=False 往返；init_db 幂等。
tmp 由 pytest 的 tmp_path 自动回收（会话结束清理）。
"""
import json
import time
from pathlib import Path

import pytest

import tindalos.history as history


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """把历史库指向 tmp 文件并建表；返回库路径。"""
    path = tmp_path / "site.db"
    monkeypatch.setenv("TINDALOS_SITE_DB", str(path))
    history.init_db()
    return path


def test_db_path_default(monkeypatch):
    """缺省 TINDALOS_SITE_DB 时指向仓库根 data/site.db。"""
    monkeypatch.delenv("TINDALOS_SITE_DB", raising=False)
    p = history.db_path()
    assert p == Path(__file__).resolve().parents[1] / "data" / "site.db"


def test_db_path_env_override(db, tmp_path):
    """TINDALOS_SITE_DB 覆盖默认库路径。"""
    assert history.db_path() == tmp_path / "site.db"


def test_init_db_idempotent(db):
    """重复 init_db 不报错，且不影响既有数据。"""
    history.init_db()
    history.register_module("m1", "a.pdf", "h1", 1, 10)
    history.init_db()
    assert history.get_module("m1") is not None


def test_register_and_get_module(db):
    """注册模组 → 返回行 dict（键为列名，meta_json 解析回对象），可查询。"""
    row = history.register_module(
        "m1",
        "雾镇疑云.pdf",
        "abc123",
        pages=12,
        chars=3456,
        meta={"author": "爱手艺"},
    )
    assert row["id"] == "m1"
    assert row["filename"] == "雾镇疑云.pdf"
    assert row["sha256"] == "abc123"
    assert row["pages"] == 12
    assert row["chars"] == 3456
    assert row["rules"] == "COC7"
    assert row["status"] == "uploaded"
    assert row["meta_json"] == {"author": "爱手艺"}
    assert row["created_at"]

    got = history.get_module("m1")
    assert got == row
    assert got["meta_json"] == {"author": "爱手艺"}


def test_get_module_missing(db):
    """查询不存在的模组返回 None。"""
    assert history.get_module("nope") is None


def test_register_defaults(db):
    """rules/status 缺省为 COC7/uploaded；meta 缺省为 NULL。"""
    row = history.register_module("m1", "a.pdf", "h1", 1, 10)
    assert row["rules"] == "COC7"
    assert row["status"] == "uploaded"
    assert row["meta_json"] is None


def test_duplicate_sha256_returns_existing_row(db):
    """重复 sha256 → 更新已有行（保留原 id/created_at），不新建。"""
    r1 = history.register_module("m1", "a.pdf", "hashX", 1, 10)
    r2 = history.register_module("m2", "b.pdf", "hashX", 2, 20)
    assert r2["id"] == "m1"  # 返回已有行
    assert r2["sha256"] == "hashX"
    assert r2["filename"] == "b.pdf"  # 元数据按本次注册刷新
    assert r2["created_at"] == r1["created_at"]  # 保留首次注册时间
    assert history.get_module("m2") is None  # 未插入新行
    assert len(history.list_modules()) == 1


def test_list_modules_order(db):
    """list_modules 按 created_at 倒序。"""
    history.register_module("m1", "a.pdf", "h1", 1, 10)
    time.sleep(1.1)  # created_at 秒级精度，错开以保证排序可断言
    history.register_module("m2", "b.pdf", "h2", 2, 20)
    rows = history.list_modules()
    assert [r["id"] for r in rows] == ["m2", "m1"]
    assert all("created_at" in r for r in rows)


def test_update_module(db):
    """update_module 更新白名单字段；白名单外字段忽略；不存在返回 None。"""
    history.register_module("m1", "a.pdf", "h1", 1, 10)
    updated = history.update_module(
        "m1", status="parsed", rules="DND5e", meta_json={"n": 1}
    )
    assert updated["status"] == "parsed"
    assert updated["rules"] == "DND5e"
    assert updated["meta_json"] == {"n": 1}
    assert updated["pages"] == 1  # 未变字段保留

    # 白名单外字段被忽略
    updated2 = history.update_module("m1", filename="evil.pdf", pages=99)
    assert updated2["filename"] == "a.pdf"
    assert updated2["pages"] == 1

    # 不存在的模组返回 None
    assert history.update_module("nope", status="x") is None


def test_campaign_roundtrip(db):
    """注册剧本 → event_count 全场景事件总数、size 为 JSON 字节数、snapshot 往返。"""
    campaign = {
        "id": "c1",
        "title": "雾镇疑云",
        "rules": "COC7",
        "acts": [
            {
                "id": "act-1",
                "scenes": [
                    {"id": "s1", "events": [{"id": "e1"}, {"id": "e2"}]},
                    {"id": "s2", "events": [{"id": "e3"}]},
                ],
            },
            {
                "id": "act-2",
                "scenes": [
                    {"id": "s3", "events": [{"id": "e4"}, {"id": "e5"}, {"id": "e6"}]},
                ],
            },
        ],
    }
    row = history.register_campaign("c1", "雾镇疑云", "COC7", campaign)
    assert row["id"] == "c1"
    assert row["title"] == "雾镇疑云"
    assert row["rules"] == "COC7"
    assert row["event_count"] == 6
    assert row["size"] == len(json.dumps(campaign, ensure_ascii=False).encode("utf-8"))

    got = history.get_campaign("c1")
    assert got["id"] == "c1"
    assert got["title"] == "雾镇疑云"
    assert got["event_count"] == 6
    assert got["size"] == row["size"]
    assert got["snapshot"] == campaign  # 整份 dict 往返
    assert got["snapshot"]["acts"][1]["scenes"][0]["events"][2]["id"] == "e6"


def test_campaign_empty_acts(db):
    """无 acts 时 event_count 为 0，size 仍按实际 JSON 计算。"""
    row = history.register_campaign("c1", "空", "COC7", {"id": "c1", "acts": []})
    assert row["event_count"] == 0
    assert row["size"] > 0
    assert history.get_campaign("c1")["snapshot"] == {"id": "c1", "acts": []}


def test_campaign_list_without_snapshot(db):
    """list_campaigns 不含 snapshot，仅元数据，按 created_at 倒序。"""
    history.register_campaign("c1", "A", "COC7", {"id": "c1", "acts": []})
    time.sleep(1.1)  # 错开秒级时间戳保证排序断言稳定
    history.register_campaign("c2", "B", "COC7", {"id": "c2", "acts": []})
    rows = history.list_campaigns()
    assert [r["id"] for r in rows] == ["c2", "c1"]
    assert set(rows[0].keys()) == {
        "id", "title", "rules", "created_at", "event_count", "size",
    }
    assert "snapshot" not in rows[0]
    assert "snapshot_json" not in rows[0]


def test_get_campaign_missing(db):
    """查询不存在的剧本返回 None。"""
    assert history.get_campaign("nope") is None


def test_delete_campaign(db):
    """删除剧本；删除成功 True，再次删除（不存在）False。"""
    history.register_campaign("c1", "A", "COC7", {"id": "c1"})
    assert history.delete_campaign("c1") is True
    assert history.get_campaign("c1") is None
    assert history.delete_campaign("c1") is False


def test_campaign_re_register_overwrites(db):
    """同一 id 再次注册为覆盖式更新（新快照、新 size）。"""
    history.register_campaign("c1", "A", "COC7", {"id": "c1", "acts": []})
    history.register_campaign(
        "c1", "A v2", "COC7", {"id": "c1", "acts": [{"scenes": [{"events": [{"id": "e1"}]}]}]}
    )
    got = history.get_campaign("c1")
    assert got["title"] == "A v2"
    assert got["event_count"] == 1
    assert got["snapshot"]["acts"][0]["scenes"][0]["events"][0]["id"] == "e1"
    assert len(history.list_campaigns()) == 1


def test_unicode_roundtrip(db):
    """ensure_ascii=False：中文 meta / snapshot 原样往返（不转义为 \\uXXXX）。"""
    history.register_module(
        "m1", "吴老爷娶亲.pdf", "h1", 5, 100, meta={"译名": "吴老爷娶亲"}
    )
    got = history.get_module("m1")
    assert got["meta_json"]["译名"] == "吴老爷娶亲"

    c = {"id": "c1", "title": "雾镇疑云·第一章", "acts": []}
    history.register_campaign("c1", c["title"], "COC7", c)
    assert history.get_campaign("c1")["snapshot"]["title"] == "雾镇疑云·第一章"
