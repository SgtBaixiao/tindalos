"""web.py 统一站点服务测试（wayfinder ticket 02 集成）。

覆盖：
1. /api/health；
2. /api/generate SSE 流：帧序 data:{stage,message} → data:{done,campaign}，生成后写入历史库+内存缓存；
   空 module_text → 400；generate 失败 → SSE 错误帧 + done:false 语义；
3. /api/campaigns/<id>（缓存读取）与 /api/regenerate（{ok,campaign,applied}）；
4. /api/modules/upload：真实 pdfio 解析小程序化 PDF（pdfium 新建），重复上传 sha256 幂等返回 200 已有行；
   /api/modules 列表、/api/modules/<id> 详情（text_preview + images image_url）；
   /api/modules/<id>/images 人工确认写回；
5. /api/rag/{ingest,search,qa}：注入 fake rag 模块验证端点接线（真实 rag 由 test_rag 覆盖，
   e2e 阶段跑真实链路）；
6. /api/history/{modules,campaigns,campaigns/<id>} 与 DELETE；
7. 无 frontend/dist 时应用不崩、/api 404 为 JSON、/files 静态挂载；
   有 dist 时 / 返回 index.html。
生成 SSE 用 fake default_generate（与 test_serve 同哲学——不跑 LangGraph 管线）。
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

from tindalos import history
from tindalos import web as web_mod

# ---------------------------------------------------------------- 脚手架


@pytest.fixture(autouse=True)
def _fresh_state():
    """隔离 web 模块级 campaign 缓存（测试间不串）。"""
    web_mod._state.campaigns.clear()
    yield
    web_mod._state.campaigns.clear()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """隔离实例：tmp 历史库 + tmp 数据目录 + 无 dist；lifespan 内 init_db。"""
    monkeypatch.setenv("TINDALOS_SITE_DB", str(tmp_path / "site.db"))
    monkeypatch.setenv("TINDALOS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TINDALOS_FRONTEND_DIST", str(tmp_path / "no-such-dist"))
    with TestClient(web_mod.create_app()) as c:
        yield c


def _fake_generate(events: list[tuple[str, str]], campaign: dict):
    """确定性伪生成：逐条 emit(stage,message)，返回 campaign dict。

    module_images 为新增的关键字透传（spec §四.3）：api_generate 无条件传该参数，
    fake 同样接收并忽略（不影响既有断言）。
    """
    emitted: list[tuple[str, str]] = []

    def gen(module_text: str, llm: bool, emit, *, module_images=None) -> dict:
        for stage, message in events:
            assert emit(stage, message) is True
            emitted.append((stage, message))
        return campaign

    gen.emitted = emitted  # type: ignore[attr-defined]
    return gen


def _sse_data(body: bytes) -> list[dict]:
    frames = []
    for chunk in body.split(b"\n\n"):
        if chunk.startswith(b"data:"):
            frames.append(json.loads(chunk[len(b"data:"):].decode("utf-8")))
    return frames


def _make_pdf(path: Path) -> Path:
    """用 pypdfium2 新建一个 1 页空 PDF（不依赖仓库内版权 PDF）。"""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument.new()
    doc.new_page(612, 792)
    doc.save(str(path))
    doc.close()
    return path


def _sample_campaign(cid: str = "c-web-1") -> dict:
    """合法最小 Campaign（含一名 NPC，供 regenerate 测）。"""
    return {
        "id": cid,
        "title": "雾镇疑云",
        "premise": "海雾弥漫的小镇，连续失踪的镇民。",
        "rules": "COC7",
        "acts": [],
        "npcs": {
            "npc-1": {"id": "npc-1", "name": "老吴", "archetype": "富商"},
        },
        "clues": [],
        "relations": [],
    }


# ---------------------------------------------------------------- 健康 / 静态


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["version"]


def test_api_404_is_json(client):
    """未知 /api 路径 → 404 JSON（不是 index.html 回退）。"""
    r = client.get("/api/nope")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")


def test_with_dist_serves_index(tmp_path, monkeypatch):
    """有 frontend/dist 时 / 与未知路径返回 index.html（hash 路由）。"""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>site</html>", encoding="utf-8")
    monkeypatch.setenv("TINDALOS_SITE_DB", str(tmp_path / "site.db"))
    monkeypatch.setenv("TINDALOS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TINDALOS_FRONTEND_DIST", str(dist))
    with TestClient(web_mod.create_app()) as c:
        assert c.get("/").status_code == 200
        assert c.get("/").text == "<html>site</html>"
        r = c.get("/some/deep/hash/route")
        assert r.status_code == 200
        assert "<html>site</html>" in r.text
        # /api 仍优先 JSON 404，不落入 index 回退
        assert c.get("/api/nope").status_code == 404


# ---------------------------------------------------------------- 生成（SSE）


def test_generate_sse_and_persist(client, monkeypatch):
    """SSE 帧序 + 写入历史库 + 内存缓存。"""
    campaign = _sample_campaign("c-web-gen")
    monkeypatch.setattr(
        web_mod, "default_generate",
        _fake_generate([("plan", "读取模组"), ("write", "撰写初稿")], campaign),
    )
    r = client.post("/api/generate", json={"module_text": "雾镇疑云模组正文"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")

    frames = _sse_data(r.content)
    assert frames[0]["stage"] == "plan"
    assert frames[0]["message"] == "读取模组"
    assert frames[1]["stage"] == "write"
    last = frames[-1]
    assert last["done"] is True
    assert last["campaign"]["id"] == "c-web-gen"

    # 历史库落盘
    rec = history.get_campaign("c-web-gen")
    assert rec is not None
    assert rec["snapshot"]["title"] == "雾镇疑云"

    # 内存缓存读取：{campaign, meta}（serve 契约向前兼容，工作台 ?? json 取 campaign）
    got = client.get("/api/campaigns/c-web-gen")
    assert got.status_code == 200
    body = got.json()
    assert body["campaign"]["id"] == "c-web-gen"
    assert body["meta"]["title"] == "雾镇疑云"


def test_generate_empty_400(client):
    assert client.post("/api/generate", json={"module_text": "  "}).status_code == 400


def test_generate_failure_sse_error_frame(client, monkeypatch):
    """生成抛异常 → SSE 错误帧 + done:true/campaign:null（前端据此中止 Loading）。"""

    def boom(module_text: str, llm: bool, emit, *, module_images=None):
        raise RuntimeError("pipeline broke")

    monkeypatch.setattr(web_mod, "default_generate", boom)
    r = client.post("/api/generate", json={"module_text": "x"})
    frames = _sse_data(r.content)
    err = next(f for f in frames if "error" in f)
    assert "pipeline broke" in err["error"]
    last = frames[-1]
    assert last["done"] is True and last["campaign"] is None


def test_campaign_missing_404(client):
    assert client.get("/api/campaigns/nope").status_code == 404


def test_regenerate(client, monkeypatch):
    """regenerate → {ok, campaign, applied}，命中生成后的缓存 campaign。"""
    campaign = _sample_campaign("c-web-regen")
    monkeypatch.setattr(
        web_mod, "default_generate", _fake_generate([], campaign)
    )
    client.post("/api/generate", json={"module_text": "x"})
    r = client.post("/api/regenerate", json={"campaign_id": "c-web-regen", "node_id": "npc-1"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["campaign"]["id"] == "c-web-regen"
    assert any("npc-1" in a for a in body["applied"])


def test_regenerate_unknown_campaign_404(client):
    r = client.post("/api/regenerate", json={"campaign_id": "nope", "node_id": "npc-1"})
    assert r.status_code == 404


def test_regenerate_unknown_node_400(client, monkeypatch):
    campaign = _sample_campaign("c-web-regen2")
    monkeypatch.setattr(web_mod, "default_generate", _fake_generate([], campaign))
    client.post("/api/generate", json={"module_text": "x"})
    r = client.post("/api/regenerate", json={"campaign_id": "c-web-regen2", "node_id": "nope-9"})
    assert r.status_code == 400


# ---------------------------------------------------------------- 上传 + 模组


def test_upload_module_and_dedup(client, tmp_path):
    """真实 pdfio 解析小 PDF → 201；同 sha256 重复上传 → 200 返回已有行。"""
    pdf = _make_pdf(tmp_path / "tiny.pdf")
    data = pdf.read_bytes()

    r1 = client.post(
        "/api/modules/upload",
        files={"file": ("tiny.pdf", data, "application/pdf")},
        data={"rules": "COC7"},
    )
    assert r1.status_code == 201
    mod1 = r1.json()["module"]
    assert mod1["id"].startswith("mod-")
    assert mod1["filename"] == "tiny.pdf"
    assert mod1["pages"] == 1
    assert mod1["images"] == []
    assert mod1["rules"] == "COC7"

    # 重复上传 → 幂等返回已有行
    r2 = client.post(
        "/api/modules/upload",
        files={"file": ("tiny.pdf", data, "application/pdf")},
    )
    assert r2.status_code == 200
    assert r2.json()["module"]["id"] == mod1["id"]

    # 列表与详情
    listed = client.get("/api/modules").json()["modules"]
    assert any(m["id"] == mod1["id"] for m in listed)
    detail = client.get(f"/api/modules/{mod1['id']}").json()["module"]
    assert "text_preview" in detail  # text.txt 已落盘
    assert detail["images"] == []

    # history 同源
    assert client.get("/api/history/modules").json()["modules"][0]["id"] == mod1["id"]


def test_upload_non_pdf_400(client):
    r = client.post(
        "/api/modules/upload",
        files={"file": ("a.txt", b"hello", "text/plain")},
    )
    assert r.status_code == 400


def test_confirm_image(client):
    """人工确认写回：kind/name 落库，needs_confirmation 置 false，image_url 派生。"""
    history.register_module(
        "mod-confirm", "a.pdf", "h1", 1, 0,
        meta={"images": [{"image_path": "/tmp/a.png", "kind": "unknown", "needs_confirmation": True}]},
    )
    r = client.post(
        "/api/modules/mod-confirm/images",
        json={"image_path": "/tmp/a.png", "kind": "portrait", "name": "老吴"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    img = body["images"][0]
    assert img["kind"] == "portrait"
    assert img["name"] == "老吴"
    assert img["needs_confirmation"] is False

    # 持久化验证
    detail = client.get("/api/modules/mod-confirm").json()["module"]
    assert detail["images"][0]["kind"] == "portrait"


def test_confirm_image_not_found(client):
    history.register_module(
        "mod-confirm2", "a.pdf", "h2", 1, 0,
        meta={"images": [{"image_path": "/tmp/a.png", "kind": "unknown"}]},
    )
    r = client.post(
        "/api/modules/mod-confirm2/images",
        json={"image_path": "/tmp/other.png", "kind": "scene"},
    )
    assert r.status_code == 404


def test_module_missing_404(client):
    assert client.get("/api/modules/nope").status_code == 404


# ---------------------------------------------------------------- RAG（stub 接线）


class _FakeRag:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def ingest_module(self, module_id, filename, full_text):
        self.calls.append(("ingest", module_id, filename, len(full_text)))
        return 42

    def search(self, query, module_id=None, top_k=6):
        self.calls.append(("search", query, module_id, top_k))
        return [{"chunk_id": "c1", "score": 0.91, "text": "片段"}]

    def qa(self, question, module_id=None, rules=None):
        self.calls.append(("qa", question, module_id, rules))
        return {"answer": "确定性回答", "sources": [], "mode": "deterministic"}


@pytest.fixture()
def fake_rag(monkeypatch):
    rag = _FakeRag()
    import tindalos as _pkg

    # `from tindalos import rag` 走 hasattr 捷径取包属性；真实 rag 已被 test_rag
    # 导入时会绑定为包属性，sys.modules 替换会被跳过。故：
    # - 包属性存在（真实 rag 已导入）→ 覆写之；
    # - 否则经 sys.modules 注入（import_ 查缓存拿到）。
    if hasattr(_pkg, "rag"):
        monkeypatch.setattr(_pkg, "rag", rag)
    monkeypatch.setitem(sys.modules, "tindalos.rag", rag)
    return rag


def test_rag_search_and_qa(client, fake_rag):
    r = client.post("/api/rag/search", json={"query": "吴老爷", "top_k": 3})
    assert r.status_code == 200
    assert r.json()["results"][0]["chunk_id"] == "c1"
    assert fake_rag.calls[-1] == ("search", "吴老爷", None, 3)

    r = client.post("/api/rag/qa", json={"question": "吴老爷是谁", "rules": "DND5e"})
    assert r.status_code == 200
    assert r.json()["answer"] == "确定性回答"
    assert fake_rag.calls[-1][0] == "qa"
    assert fake_rag.calls[-1][3] == "DND5e"


def test_rag_ingest_needs_module(client, fake_rag):
    """不存在的模组 → 404（未触达 rag）。"""
    assert client.post("/api/rag/ingest", json={"module_id": "nope"}).status_code == 404
    assert fake_rag.calls == []


def test_rag_search_empty_400(client, fake_rag):
    assert client.post("/api/rag/search", json={"query": "  "}).status_code == 400
    assert fake_rag.calls == []


# ---------------------------------------------------------------- 契约别名（site 层 API）


def test_alias_post_modules_upload(client, tmp_path):
    """site 层用 POST /api/modules 上传——与 /api/modules/upload 等价。"""
    pdf = _make_pdf(tmp_path / "alias.pdf")
    r = client.post(
        "/api/modules",
        files={"file": ("alias.pdf", pdf.read_bytes(), "application/pdf")},
        data={"rules": "COC7"},
    )
    assert r.status_code == 201
    assert r.json()["module"]["filename"] == "alias.pdf"


def test_alias_modules_history(client):
    """GET /api/modules/history 不落入 {module_id} 参数路由，返回列表。"""
    r = client.get("/api/modules/history")
    assert r.status_code == 200
    assert isinstance(r.json()["modules"], list)


def test_alias_module_ingest_and_confirm(client, fake_rag, tmp_path):
    """/api/modules/<id>/ingest（id 取自路径）+ confirm-image（与 /images 同 handler）。"""
    history.register_module(
        "mod-alias-1", "a.pdf", "h1", 1, 10,
        meta={"images": [{"image_path": "/tmp/a.png", "kind": "unknown", "needs_confirmation": True}]},
    )
    text = tmp_path / "data" / "modules" / "mod-alias-1" / "text.txt"
    text.parent.mkdir(parents=True, exist_ok=True)
    text.write_text("模组全文text", encoding="utf-8")

    # ingest 别名：id 取自路径，不要求 body
    r = client.post("/api/modules/mod-alias-1/ingest")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["indexed"] is True
    kind, mid, fname, flen = fake_rag.calls[-1]
    assert (kind, mid, fname) == ("ingest", "mod-alias-1", "a.pdf")
    assert flen == 8

    # confirm-image 别名：与 /images 走同一 handler
    r = client.post(
        "/api/modules/mod-alias-1/confirm-image",
        json={"image_path": "/tmp/a.png", "kind": "portrait", "name": "老吴"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    img = r.json()["images"][0]
    assert img["kind"] == "portrait"
    assert img["name"] == "老吴"


def test_alias_qa(client, fake_rag):
    r = client.post("/api/qa", json={"question": "谁杀了人", "rules": "DND5e"})
    assert r.status_code == 200
    assert r.json()["answer"] == "确定性回答"
    assert fake_rag.calls[-1][0] == "qa"


def test_campaigns_list_and_wrap(client, monkeypatch):
    """GET /api/campaigns 列表（不含快照）+ /api/campaigns/<id> 返回 {campaign, meta}。"""
    campaign = _sample_campaign("c-web-list")
    monkeypatch.setattr(web_mod, "default_generate", _fake_generate([], campaign))
    client.post("/api/generate", json={"module_text": "x"})

    rows = client.get("/api/campaigns").json()["campaigns"]
    assert any(r["id"] == "c-web-list" for r in rows)
    assert "snapshot" not in rows[0]

    got = client.get("/api/campaigns/c-web-list").json()
    assert got["campaign"]["id"] == "c-web-list"
    assert got["meta"]["title"] == "雾镇疑云"


# ---------------------------------------------------------------- 历史记录


def test_history_campaigns_crud(client, monkeypatch):
    campaign = _sample_campaign("c-web-hist")
    monkeypatch.setattr(web_mod, "default_generate", _fake_generate([], campaign))
    client.post("/api/generate", json={"module_text": "x"})

    rows = client.get("/api/history/campaigns").json()["campaigns"]
    assert any(r["id"] == "c-web-hist" for r in rows)
    assert "snapshot" not in rows[0]

    rec = client.get("/api/history/campaigns/c-web-hist").json()
    assert rec["campaign"]["id"] == "c-web-hist"
    assert rec["meta"]["title"] == "雾镇疑云"

    r = client.delete("/api/history/campaigns/c-web-hist")
    assert r.status_code == 204
    assert client.get("/api/history/campaigns/c-web-hist").status_code == 404


def test_history_campaign_missing_404(client):
    assert client.get("/api/history/campaigns/nope").status_code == 404


# ---------------------------------------------------------------- CLI


def test_cli_web_command():
    from typer.testing import CliRunner

    from tindalos.cli import app

    result = CliRunner().invoke(app, ["web", "--help"])
    assert result.exit_code == 0
    assert "个人网站统一服务" in result.output


# ---------------------------------------------------------------- 多模态参考 + /files 安全（spec §四.3）


class _FakeVisionResp:
    """transport 伪响应：恒 200，json() 返回 choices 载荷。"""

    def __init__(self, payload) -> None:
        self._payload = payload
        self.status_code = 200
        self.text = ""

    def raise_for_status(self) -> None:
        pass

    def json(self):
        return self._payload


class _FakeVisionTransport:
    """统一客户端传输注入：记录调用，恒回 200（零网络零 LLM）。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, method, url, *, json=None, headers=None, timeout=None):
        self.calls.append({"method": method, "url": url, "json": json, "headers": headers or {}, "timeout": timeout})
        return _FakeVisionResp({"choices": [{"message": {"content": '{"ok": 1}'}}]})


class _CannedVisionResult:
    """伪 vision 单图结果（等价 VisionResult.to_dict()）。"""

    def to_dict(self) -> dict:
        return {
            "image_path": "/tmp/mod/images/a.png",
            "page_no": 1,
            "kind": "portrait",
            "name": "老吴",
            "caption": "神秘富商",
            "confidence": 0.9,
            "needs_confirmation": False,
            "meta": {},
        }


class _FakeVision:
    """伪 vision 模块：classify_images 返回固定的识别结果（零网络零 LLM）。"""

    def __init__(self) -> None:
        self.calls: list[list] = []

    def classify_images(self, infos):
        self.calls.append(list(infos))
        return [_CannedVisionResult()]


@pytest.fixture()
def fake_vision(monkeypatch):
    """同 fake_rag 双路径补丁：api_upload 内 `from tindalos import vision` 取到伪模块。"""
    vision = _FakeVision()
    import tindalos as _pkg

    if hasattr(_pkg, "vision"):
        monkeypatch.setattr(_pkg, "vision", vision)
    monkeypatch.setitem(sys.modules, "tindalos.vision", vision)
    return vision


def test_files_mount_restricted(client, tmp_path):
    """/files 安全收窄：仅 modules/<id>/images/<白名单扩展> 可下载，其余（store/*、text.txt）一律 404。"""
    data = tmp_path / "data"
    store = data / "store"
    store.mkdir(parents=True)
    (store / "memory_entries.sqlite").write_bytes(b"sqlite-blob")
    mod_dir = data / "modules" / "mod-1"
    mod_dir.mkdir(parents=True)
    (mod_dir / "text.txt").write_text("模组全文", encoding="utf-8")
    img_dir = mod_dir / "images"
    img_dir.mkdir(parents=True)
    (img_dir / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)

    # 整目录泄露面：数据库与正文一律不可下载
    assert client.get("/files/store/memory_entries.sqlite").status_code == 404
    assert client.get("/files/modules/mod-1/text.txt").status_code == 404
    # 图像白名单内 → 200；白名单外扩展 → 404
    assert client.get("/files/modules/mod-1/images/a.png").status_code == 200
    assert client.get("/files/modules/mod-1/images/a.txt").status_code == 404


def test_generate_with_module_images(client, monkeypatch):
    """POST /api/generate 携带 module_images → 透传 default_generate → 生成器注入图像参考（unknown 跳过）。"""
    from tindalos.config import Settings
    from tindalos.generator import OllamaGenerator

    transport = _FakeVisionTransport()

    def gen_with_images(module_text, llm, emit, *, module_images=None):
        s = Settings()
        s.ollama_base_url = "http://localhost:11434/v1"
        s.model = "test-model"
        s.llm_enabled = True
        s.api_key = ""
        g = OllamaGenerator(s, retry_delay=0, transport=transport)
        g.set_module_context(module_text, title="留地不留头")
        g.set_module_images(module_images or [])
        g._chat("生成")
        return _sample_campaign("c-web-img")

    monkeypatch.setattr(web_mod, "default_generate", gen_with_images)
    r = client.post(
        "/api/generate",
        json={
            "module_text": "模组正文",
            "module_images": [
                {"kind": "portrait", "name": "老吴", "caption": "神秘富商"},
                {"kind": "unknown", "name": None, "caption": "无法识别"},
            ],
        },
    )
    assert r.status_code == 200
    frames = _sse_data(r.content)
    assert frames[-1]["done"] is True and frames[-1]["campaign"]["id"] == "c-web-img"

    assert transport.calls, "生成器未发起请求"
    prompt = transport.calls[0]["json"]["messages"][0]["content"]
    assert "模组图像参考" in prompt
    assert "图1（portrait）：老吴——神秘富商" in prompt
    assert "unknown" not in prompt  # unknown 条目不渲染


def test_upload_module_writes_vision_meta(client, fake_vision, tmp_path):
    """上传 → asyncio.to_thread(vision.classify_images) → 识别结果写入模块 meta（Task D 解阻塞验证）。"""
    pdf = _make_pdf(tmp_path / "vision.pdf")
    r = client.post(
        "/api/modules/upload",
        files={"file": ("vision.pdf", pdf.read_bytes(), "application/pdf")},
        data={"rules": "COC7"},
    )
    assert r.status_code == 201, r.text
    assert fake_vision.calls, "classify_images 未被调用"
    mod = r.json()["module"]
    assert mod["images"][0]["kind"] == "portrait"
    assert mod["images"][0]["name"] == "老吴"
    assert mod["images"][0]["caption"] == "神秘富商"
