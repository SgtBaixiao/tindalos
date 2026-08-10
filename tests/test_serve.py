"""tindalos.serve handler 层 mock 单测（task t11-serve）。

覆盖（对齐验收）：
1. POST /api/generate → SSE 帧序：data:{stage,message} 逐条 → data:{done:true,campaign} 结束帧，
   每帧 data: 前缀 + 空行分隔；
2. 400：body 非 JSON / 缺 module_text；404：未知路径 / 未知 campaign；
3. CORS：Access-Control-Allow-Origin:* 出现在全部响应（SSE / 200 / 400 / 404）；
4. campaign 内存缓存：生成完成后 GET /api/campaigns/<id> 返回缓存；未知 id 404；
5. POST /api/regenerate：mock regenerate_node → {ok,campaign,applied} + 缓存更新；
   未知 campaign 404 / 未知 node ValueError → 400 JSON；
6. 连接断开（BrokenPipeError）→ 停止生成（emit 返回 False，executor 中断）；
7. CLI：serve 子命令注册 + --help 可用；serve.py 顶层导入仅 stdlib + tindalos（零新依赖）。
全部 handler 层 mock：不开真实端口、不跑 LangGraph 管线。
"""
from __future__ import annotations

import io
import json
import sys
import threading
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import pytest
from typer.testing import CliRunner

from tindalos.cli import app
from tindalos.serve import ServeState, make_handler, sse_frame, sse_stage

runner = CliRunner()

# ---------------------------------------------------------------- 测试脚手架（FakeSocket，不开端口）

FAKE_CAMPAIGN = {"id": "campaign-test-1", "title": "测试模组《雾港之夜》", "premise": "测试前提"}


class _FakeSocket:
    """模拟 socket：makefile('rb') 返回请求字节流，makefile('wb') 返回捕获缓冲。"""

    def __init__(self, raw: bytes) -> None:
        self._raw = raw
        self._wbuf = io.BytesIO()

    def makefile(self, mode: str, *args, **kwargs):  # noqa: ARG002
        if "r" in mode:
            return io.BytesIO(self._raw)
        if "w" in mode:
            return self._wbuf
        raise ValueError(f"bad mode: {mode}")

    def sendall(self, data: bytes) -> None:  # socketserver._SocketWriter 依赖
        self._wbuf.write(data)

    def settimeout(self, timeout):  # noqa: ARG002
        pass

    def shutdown(self, how):  # noqa: ARG002
        pass

    def close(self):
        pass


class _FakeServer:
    def __init__(self, state: ServeState) -> None:
        self.state = state


def _raw_request(method: str, path: str, body: bytes = b"", headers: dict | None = None) -> bytes:
    lines = [f"{method} {path} HTTP/1.1", "Host: test.local", "Connection: close"]
    if body:
        lines.append(f"Content-Length: {len(body)}")
        lines.append("Content-Type: application/json")
    for k, v in (headers or {}).items():
        lines.append(f"{k}: {v}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8") + body


def _split_response(raw: bytes) -> tuple[str, dict[str, str], bytes]:
    head, _, body = raw.partition(b"\r\n\r\n")
    status_line = head.split(b"\r\n", 1)[0].decode("utf-8", "replace")
    headers: dict[str, str] = {}
    for line in head.split(b"\r\n")[1:]:
        k, _, v = line.partition(b":")
        headers[k.strip().lower().decode("utf-8", "replace")] = v.strip().decode("utf-8", "replace")
    return status_line, headers, body


def _sse_data(body: bytes) -> list[dict]:
    frames = []
    for chunk in body.split(b"\n\n"):
        if chunk.startswith(b"data:"):
            frames.append(json.loads(chunk[len(b"data:"):].decode("utf-8")))
    return frames


def _run(state: ServeState, method: str, path: str, body: bytes = b"", headers: dict | None = None):
    raw = _raw_request(method, path, body, headers)
    sock = _FakeSocket(raw)
    handler_cls = make_handler(state)
    handler_cls(sock, None, _FakeServer(state))
    return _split_response(sock._wbuf.getvalue())


def _fake_generate(events: list[tuple[str, str]], campaign: dict | None):
    """确定性伪生成器：逐条 emit 进度事件，返回 campaign dict。"""
    emitted: list[tuple[str, str]] = []

    def generate(module_text: str, llm: bool, emit) -> dict | None:
        for stage, message in events:
            assert emit(stage, message) is True
            emitted.append((stage, message))
        return campaign

    generate.emitted = emitted  # type: ignore[attr-defined]
    return generate


def _fake_regenerate(campaign, node_id: str, generator):
    return ({"id": campaign.id, "title": "重生成后"}, [node_id])


def _json_body(doc) -> bytes:
    return json.dumps(doc, ensure_ascii=False).encode("utf-8")


# ---------------------------------------------------------------- SSE 帧序 / 契约


def test_generate_sse_frame_order_and_blank_sep():
    """进度事件逐条 data:{stage,message}，结束帧 data:{done:true,campaign}；data: 前缀+空行分隔。"""
    state = ServeState(
        generate=_fake_generate([("plan", "KP 拟定幕结构"), ("write", "写作第 1 幕")], dict(FAKE_CAMPAIGN))
    )
    status, headers, body = _run(state, "POST", "/api/generate", _json_body({"module_text": "雾港之夜"}))
    assert status.endswith(" 200 OK")
    assert headers["content-type"].startswith("text/event-stream")
    assert headers["access-control-allow-origin"] == "*"
    # 每帧 data: 前缀 + 空行分隔
    assert body.endswith(b"\n\n")
    assert body.count(b"data:") == 3
    frames = _sse_data(body)
    assert frames[:2] == [
        {"stage": "plan", "message": "KP 拟定幕结构"},
        {"stage": "write", "message": "写作第 1 幕"},
    ]
    assert frames[-1] == {"done": True, "campaign": FAKE_CAMPAIGN}
    # 生成完成后进入内存缓存
    assert state.campaigns["campaign-test-1"] == FAKE_CAMPAIGN


def test_sse_frame_unit():
    assert sse_frame({"done": True, "campaign": None}) == b'data:{"done": true, "campaign": null}\n\n'
    assert sse_frame({"stage": "plan", "message": "KP 拟定幕结构"}) == (
        'data:{"stage": "plan", "message": "KP 拟定幕结构"}\n\n'.encode("utf-8")
    )


def test_sse_stage_mapping():
    assert sse_stage("KP 拟定幕结构") == "plan"
    assert sse_stage("KG 查询：实体关系") == "plan"
    assert sse_stage("NPC 林晚 注入人格") == "npc"
    assert sse_stage("写作第 1 幕") == "write"
    assert sse_stage("校对付印") == "compose"
    assert sse_stage("其他消息") == "progress"


def test_generate_error_frame_then_done_null():
    def boom(module_text: str, llm: bool, emit):
        raise RuntimeError("pipeline boom")

    state = ServeState(generate=boom)
    status, _, body = _run(state, "POST", "/api/generate", _json_body({"module_text": "x"}))
    assert status.endswith(" 200 OK")
    frames = _sse_data(body)
    assert frames == [{"error": "pipeline boom"}, {"done": True, "campaign": None}]


# ---------------------------------------------------------------- 请求解析 / 400


def test_generate_missing_module_text_400():
    state = ServeState()
    status, headers, body = _run(state, "POST", "/api/generate", _json_body({}))
    assert status.endswith(" 400 Bad Request")
    assert headers["access-control-allow-origin"] == "*"
    assert json.loads(body)["error"]


def test_generate_invalid_json_400():
    state = ServeState()
    status, _, body = _run(state, "POST", "/api/generate", b"{not json")
    assert status.endswith(" 400 Bad Request")
    assert json.loads(body)["error"]


def test_generate_empty_body_400():
    state = ServeState()
    status, _, body = _run(state, "POST", "/api/generate")
    assert status.endswith(" 400 Bad Request")


def test_generate_non_dict_json_400():
    state = ServeState()
    status, _, body = _run(state, "POST", "/api/generate", b'["array"]')
    assert status.endswith(" 400 Bad Request")


# ---------------------------------------------------------------- 404 / CORS


def test_unknown_post_path_404_json():
    state = ServeState()
    status, headers, body = _run(state, "POST", "/api/nope", _json_body({"a": 1}))
    assert status.endswith(" 404 Not Found")
    assert headers["access-control-allow-origin"] == "*"
    assert headers["content-type"].startswith("application/json")
    doc = json.loads(body)
    assert doc["error"] and doc["path"] == "/api/nope"


def test_unknown_get_path_404_json():
    state = ServeState()
    status, headers, body = _run(state, "GET", "/favicon.ico")
    assert status.endswith(" 404 Not Found")
    assert headers["access-control-allow-origin"] == "*"
    assert json.loads(body)["error"]


# ---------------------------------------------------------------- campaign 缓存


def test_get_campaign_cache_after_generate():
    state = ServeState(
        generate=_fake_generate([("compose", "校对付印")], dict(FAKE_CAMPAIGN))
    )
    _run(state, "POST", "/api/generate", _json_body({"module_text": "x"}))
    status, headers, body = _run(state, "GET", "/api/campaigns/campaign-test-1")
    assert status.endswith(" 200 OK")
    assert headers["access-control-allow-origin"] == "*"
    assert json.loads(body) == FAKE_CAMPAIGN


def test_get_unknown_campaign_404():
    state = ServeState()
    status, _, body = _run(state, "GET", "/api/campaigns/nope")
    assert status.endswith(" 404 Not Found")
    assert json.loads(body)["error"]


def test_get_campaign_missing_id_404():
    state = ServeState()
    status, _, body = _run(state, "GET", "/api/campaigns/")
    assert status.endswith(" 404 Not Found")


# ---------------------------------------------------------------- regenerate


def test_regenerate_ok_updates_cache():
    state = ServeState(
        generate=_fake_generate([], dict(FAKE_CAMPAIGN)),
        regenerate=_fake_regenerate,
    )
    _run(state, "POST", "/api/generate", _json_body({"module_text": "x"}))
    status, headers, body = _run(
        state, "POST", "/api/regenerate",
        _json_body({"campaign_id": "campaign-test-1", "node_id": "act-1"}),
    )
    assert status.endswith(" 200 OK")
    assert headers["access-control-allow-origin"] == "*"
    doc = json.loads(body)
    assert doc["ok"] is True
    assert doc["applied"] == ["act-1"]
    assert doc["campaign"]["title"] == "重生成后"
    # 缓存已更新为重生成结果
    assert state.campaigns["campaign-test-1"]["title"] == "重生成后"


def test_regenerate_unknown_campaign_404():
    state = ServeState(regenerate=_fake_regenerate)
    status, _, body = _run(
        state, "POST", "/api/regenerate",
        _json_body({"campaign_id": "ghost", "node_id": "act-1"}),
    )
    assert status.endswith(" 404 Not Found")
    assert json.loads(body)["error"]


def test_regenerate_unknown_node_400():
    def boom(campaign, node_id: str, generator):
        raise ValueError(f"未知节点 id: {node_id}")

    state = ServeState(
        generate=_fake_generate([], dict(FAKE_CAMPAIGN)),
        regenerate=boom,
    )
    _run(state, "POST", "/api/generate", _json_body({"module_text": "x"}))
    status, _, body = _run(
        state, "POST", "/api/regenerate",
        _json_body({"campaign_id": "campaign-test-1", "node_id": "ghost"}),
    )
    assert status.endswith(" 400 Bad Request")
    doc = json.loads(body)
    assert doc["ok"] is False and "未知节点" in doc["error"]


def test_regenerate_missing_fields_400():
    state = ServeState(regenerate=_fake_regenerate)
    status, _, body = _run(
        state, "POST", "/api/regenerate", _json_body({"campaign_id": "x"}),
    )
    assert status.endswith(" 400 Bad Request")


def test_regenerate_internal_error_500():
    def boom(campaign, node_id: str, generator):
        raise RuntimeError("内部错误")

    state = ServeState(
        generate=_fake_generate([], dict(FAKE_CAMPAIGN)),
        regenerate=boom,
    )
    _run(state, "POST", "/api/generate", _json_body({"module_text": "x"}))
    status, _, body = _run(
        state, "POST", "/api/regenerate",
        _json_body({"campaign_id": "campaign-test-1", "node_id": "act-1"}),
    )
    assert status.endswith(" 500 Internal Server Error")
    assert json.loads(body)["ok"] is False


# ---------------------------------------------------------------- 连接断开停止生成


def test_disconnect_stops_generation(monkeypatch):
    """第二次写帧抛 BrokenPipeError（客户端断开）→ stop 置位 → executor 后续 emit 返回 False 并中断。"""
    calls = {"n": 0}
    results: list[bool] = []

    def fail_write(self, payload: dict) -> None:  # noqa: ARG001
        calls["n"] += 1
        if calls["n"] > 1:
            raise BrokenPipeError("client gone")

    def fake_generate(module_text: str, llm: bool, emit) -> dict | None:
        results.append(emit("plan", "步骤一"))
        for _ in range(2000):  # 轮询直到 stop 生效（handler 处理第一帧后抛 BrokenPipe → stop.set）
            if not emit("write", "步骤二"):
                results.append(False)
                break
        return None

    state = ServeState(generate=fake_generate)
    handler_cls = make_handler(state)
    monkeypatch.setattr(handler_cls, "_sse_write", fail_write)
    sock = _FakeSocket(_raw_request("POST", "/api/generate", _json_body({"module_text": "x"})))
    handler_cls(sock, None, _FakeServer(state))
    # 第一帧已写（成功），第二帧断连 → executor 收到 stop 信号
    assert results[0] is True
    assert results[1] is False
    assert calls["n"] >= 2


# ---------------------------------------------------------------- CLI / 零新依赖


def _strip_ansi(s: str) -> str:
    import re

    return re.sub(r"\x1b\[[0-9;]*m", "", s)


def test_serve_subcommand_help():
    # 参数级断言（终端/rich 版本无关，CI 渲染差异不破坏测试）：
    cmd = app.typer_instance.get_command(None, "serve")
    assert cmd is not None, "serve 子命令未注册"
    opts = {n for p in cmd.get_params(None) for n in p.opts}
    assert "--host" in opts and "--port" in opts
    # 帮助文本出口码 + 含默认端口（ANSI 剥离后，容 CI rich 差异）
    r = runner.invoke(app, ["serve", "--help"])
    assert r.exit_code == 0, r.stdout
    assert "8347" in _strip_ansi(r.stdout)


def test_serve_subcommand_registered_in_help():
    r = runner.invoke(app, ["--help"])
    assert r.exit_code == 0, r.stdout
    assert "serve" in r.stdout


def test_serve_module_stdlib_only_imports():
    """serve.py 顶层导入仅 stdlib + tindalos（零新依赖）。"""
    import tindalos.serve as serve_mod

    src = Path(serve_mod.__file__).read_text(encoding="utf-8")
    for line in src.splitlines():
        s = line.strip()
        if s.startswith("import ") or s.startswith("from "):
            mod = s.split()[1].split(".")[0]
            assert mod == "tindalos" or mod in sys.stdlib_module_names, f"非 stdlib 导入: {line}"


def test_options_preflight_cors():
    """回归：OPTIONS preflight 必须返回 204 + CORS 头（前端跨域 POST 依赖，G5 修正）。"""
    state = ServeState(generate=_fake_generate([], dict(FAKE_CAMPAIGN)))
    status, headers, _body = _run(state, "OPTIONS", "/api/generate", b"")
    assert " 204 " in status or status.endswith(" 204")
    assert headers.get("access-control-allow-origin") == "*"
    assert "POST" in headers.get("access-control-allow-methods", "")
    assert headers.get("access-control-allow-headers") == "Content-Type"
