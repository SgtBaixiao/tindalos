"""端到端集成测试（wayfinder ticket 17）：真实模块全链路。

真实 pdfio 解析 + 真实 rag（离线 BM25，TINDALOS_RAG_DIR 隔离）+ 真实 history
（TINDALOS_SITE_DB 隔离）+ 真实确定性生成管线，全部通过 FastAPI 统一服务走一遍：

    POST /api/modules/upload（手工构造的最小文本 PDF）→ 201
    POST /api/rag/ingest（真实 BM25 入库）→ chunks > 0
    POST /api/rag/search（检索 PDF 原文中的词）→ 命中该模组
    POST /api/rag/qa（无 LLM → deterministic 兜底）→ 有答案
    POST /api/generate（真实确定性管线，~11s）→ SSE done 帧 + 剧本落库
    GET  /api/history/campaigns/<id>（历史子页面数据源）→ 快照可读

生成用确定性管线（llm=False），不依赖任何 API key；PDF 用 ASCII 文本（中文需
CID 字体，手工 PDF 过重），检索词用英文。整套件中最慢的单测（生成 ~11s）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import pytest
from fastapi.testclient import TestClient

from tindalos import web as web_mod

MODULE_TEXT = (
    "雾镇疑云：海雾弥漫的港口小镇，镇民连续失踪。"
    "富商吴老爷在码头举办娶亲宴，宾客在雾中看到黑影。"
    "守密人线索：港务局旧档案、吴家祠堂暗门、雾灯塔上的新划痕。"
    "NPC：吴老爷（富商）、阿秀（新娘）、老周（灯塔看守）。"
)

# PDF 原文（ASCII）——与生成输入分开：PDF 走 pdfio/RAG，生成走 DeterministicGenerator
PDF_TEXT = (
    "Mist Town: a foggy harbor town. Townsfolk disappear nightly. "
    "Master Wu the merchant throws a wedding banquet at the pier. "
    "Clues: the harbor master's ledger, the Wu family shrine's hidden door, "
    "fresh scratches on the lighthouse lamp. "
    "NPCs: Master Wu (merchant), A Xiu (the bride), Old Zhou (lighthouse keeper)."
)


def make_text_pdf(path: Path, text: str = PDF_TEXT) -> Path:
    """手工构造最小单页文本 PDF（Helvetica/ASCII），离线可得、无需外部字体。

    显式计算对象字节偏移以写出合法 xref——pypdfium2 可正常提取文本。
    """
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
         b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"),
    ]
    stream = ("BT /F1 11 Tf 72 720 Td (" + text + ") Tj ET").encode("latin-1")
    objs.append(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream))
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += (b"%d 0 obj\n" % i) + body + b"\nendobj\n"
    xref_pos = len(out)
    out += b"xref\n0 6\n0000000000 65535 f \n"
    for off in offsets:
        out += ("%010d 00000 n \n" % off).encode("ascii")
    out += (b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF" % xref_pos)
    path.write_bytes(bytes(out))
    return path


@pytest.fixture()
def e2e(tmp_path, monkeypatch):
    """隔离实例：tmp 历史库 + tmp 数据目录 + tmp RAG 目录 + 无 dist。"""
    monkeypatch.setenv("TINDALOS_SITE_DB", str(tmp_path / "site.db"))
    monkeypatch.setenv("TINDALOS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TINDALOS_RAG_DIR", str(tmp_path / "rag"))
    monkeypatch.setenv("TINDALOS_FRONTEND_DIST", str(tmp_path / "no-dist"))
    web_mod._state.campaigns.clear()
    with TestClient(web_mod.create_app()) as c:
        yield c
    web_mod._state.campaigns.clear()


def _sse_data(body: bytes) -> list[dict]:
    frames = []
    for chunk in body.split(b"\n\n"):
        if chunk.startswith(b"data:"):
            frames.append(json.loads(chunk[len(b"data:"):].decode("utf-8")))
    return frames


def test_full_chain_upload_rag_generate_history(e2e, tmp_path):
    """上传 → 解析 → RAG 入库 → 检索 → 问答 → SSE 生成 → 历史落盘。"""
    # 1) 上传真实解析
    pdf = make_text_pdf(tmp_path / "module.pdf")
    r = e2e.post(
        "/api/modules/upload",
        files={"file": ("module.pdf", pdf.read_bytes(), "application/pdf")},
        data={"rules": "COC7"},
    )
    assert r.status_code == 201, r.text
    mod = r.json()["module"]
    assert mod["pages"] == 1
    assert mod["chars"] > 100  # pdfio 真抽出文本
    module_id = mod["id"]

    # 2) RAG 入库（真实 BM25）
    r = e2e.post("/api/rag/ingest", json={"module_id": module_id})
    assert r.status_code == 200, r.text
    assert r.json()["indexed"] is True
    assert r.json()["chunks"] > 0

    # 3) 检索 PDF 原文词，命中该模组
    r = e2e.post("/api/rag/search", json={"query": "harbor master ledger", "top_k": 3})
    assert r.status_code == 200, r.text
    results = r.json()["results"]
    assert results, "检索应有结果"
    assert results[0]["module_id"] == module_id
    assert results[0]["text"]  # 带原文片段

    # 4) 无 LLM → deterministic 兜底问答
    r = e2e.post("/api/rag/qa", json={"question": "Who throws the wedding banquet?"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["answer"]
    assert body["mode"] == "local"  # 无 LLM key → 诚实降级本地（真实 rag 契约）

    # 5) SSE 生成（真实确定性管线）
    r = e2e.post("/api/generate", json={"module_text": MODULE_TEXT})
    assert r.status_code == 200
    frames = _sse_data(r.content)
    assert frames, "SSE 应有帧"
    last = frames[-1]
    assert last["done"] is True
    campaign = last["campaign"]
    assert campaign and campaign["id"]
    assert campaign["title"]
    # 有舞台/场景/事件实体
    acts = campaign.get("acts", [])
    events = sum(len(s.get("events", [])) for a in acts for s in a.get("scenes", []))
    assert events > 0, "确定性生成应产出事件"
    cid = campaign["id"]

    # 6) 历史落盘 + 可读（历史子页面数据源）
    rec = e2e.get(f"/api/history/campaigns/{cid}").json()
    assert rec["campaign"]["id"] == cid
    assert rec["meta"]["title"] == campaign["title"]
    rows = e2e.get("/api/history/campaigns").json()["campaigns"]
    assert any(row["id"] == cid for row in rows)
    assert "snapshot" not in rows[0]

    # 内存缓存一致（前端工作台读取）：{campaign, meta} 包装
    got = e2e.get(f"/api/campaigns/{cid}").json()
    assert got["campaign"]["id"] == cid
