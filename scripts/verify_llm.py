#!/usr/bin/env python3
"""LLM 层运行时连通性验证（task #19）：五个调用面逐一发真请求。

默认 mock 模式：用 stdlib http.server 起本地 loopback OpenAI 兼容假服务，
把 settings 指向 127.0.0.1 后，对 生成(generator) / 裁判(judge) / 问答(rag-qa) /
向量(embedding) / 视觉(vision) 五个调用面逐一发真 HTTP 请求，验证 URL、JSON、
状态码、温度/response_format 契约、以及 500→重试 的退避路径。
零外部依赖、零真实 key、零外网。

--real 模式：不起假服务，直接读真实环境变量连真实 API（HITL 配置 key 后跑）：
  set TINDALOS_API_KEY=sk-... TINDALOS_LLM_ENABLED=1 ^
      [TINDALOS_DASHSCOPE_KEY=... TINDALOS_EMBED_KEY=...]
  python scripts/verify_llm.py --real

退出码：0 = 五路全通；1 = 任一失败。
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

# src 布局（与 tests/conftest.py 同款）：本包不 pip 安装，直接 import 需先加 src/
SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Windows 控制台默认 GBK，U+2713/✗ 等字符编码会抛 UnicodeEncodeError → 强制 UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001 —— 非 Windows / 无 reconfigure 时忽略
    pass

# ---------------------------------------------------------------- 工具

def _reset_settings() -> None:
    """重置 config 单例：脚本必须先设好 env 再调此函数，否则读到旧配置。"""
    import tindalos.config as config

    config._settings = None  # noqa: SLF001 —— 测试/工具脚本按约定直接复位单例


def _set_mock_env(base: str) -> None:
    """mock 模式：三套端点（主 LLM / VL / embed）指向本地假服务 base + 假 key。

    real 模式绝不调用本函数——直接透传用户环境变量（覆盖会冲掉真实 key/base/model）。
    """
    import os

    os.environ["TINDALOS_LLM_ENABLED"] = "1"
    os.environ["TINDALOS_MODEL"] = "mock-model"
    os.environ["TINDALOS_JUDGE_MODEL"] = "mock-model"
    os.environ["TINDALOS_API_BASE"] = base
    os.environ["TINDALOS_VL_BASE"] = base
    os.environ["TINDALOS_EMBED_BASE"] = base
    os.environ["TINDALOS_API_KEY"] = "sk-mock"
    os.environ["TINDALOS_DASHSCOPE_KEY"] = "sk-mock"
    os.environ["TINDALOS_EMBED_KEY"] = "sk-mock"


# ---------------------------------------------------------------- mock 服务

JUDGE_DIMS = ("structural", "consistency", "depth", "playability")


def _judge_json() -> dict:
    return {
        d: {"score": 4, "comment": "结构完整", "suggestion": "无", "evidence_refs": []}
        for d in JUDGE_DIMS
    }


class _Shared:
    """跨请求共享计数（http.server 每请求重建 handler 实例，状态挂服务上）。"""

    def __init__(self) -> None:
        self.retry_hits = 0
        self.judge_contract_ok = True  # 若裁判请求缺 response_format/temp=0 则置 False


class MockOpenAI(BaseHTTPRequestHandler):
    """OpenAI 兼容假服务：/v1/chat/completions 与 /v1/embeddings。"""

    server_version = "TindalosVerifyMock/0.1"
    state: _Shared = _Shared()

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        pass

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length).decode("utf-8")) if length else {}

    # -- 契约校验：裁判必须带 response_format=json_object 且 temperature=0 ---------
    def _check_judge_contract(self, body: dict) -> None:
        if body.get("response_format") != {"type": "json_object"}:
            self.state.judge_contract_ok = False
        if body.get("temperature") != 0:
            self.state.judge_contract_ok = False

    def _chat_reply(self, body: dict) -> dict:
        messages = body.get("messages", [])
        content = json.dumps(messages, ensure_ascii=False)
        # 重试探针：首次 500，之后 200（验证 _is_retryable + max_retries 退避路径）
        if "retry-test" in content:
            self.state.retry_hits += 1
            if self.state.retry_hits == 1:
                return {"error": {"message": "boom", "type": "server_error"}}, 500
            return {"choices": [{"message": {"content": "retried-ok"}}]}, 200
        # 多模态：user content 是含 image_url 的列表 → 视觉分类 JSON
        for m in messages:
            if isinstance(m.get("content"), list):
                return {
                    "choices": [
                        {"message": {"content": '{"kind": "map", "name": null, "caption": "测试地图", "confidence": 0.9}'}}
                    ]
                }, 200
        # 裁判：response_format=json_object 且 system 提示含"评审" → 裁判 JSON
        if body.get("response_format") == {"type": "json_object"}:
            self._check_judge_contract(body)
            return {"choices": [{"message": {"content": json.dumps(_judge_json())}}]}, 200
        # 其余（生成 / 问答）→ 通用 pong
        return {"choices": [{"message": {"content": "pong"}}]}, 200

    def do_POST(self) -> None:
        try:
            body = self._read_body()
        except (ValueError, UnicodeDecodeError):
            self._json(400, {"error": {"message": "bad json", "type": "invalid_request"}})
            return
        if self.path.endswith("/embeddings"):
            n = len(body.get("input", []))
            data = [{"object": "embedding", "index": i, "embedding": [float(i + 1) / 8 for _ in range(8)]} for i in range(n)]
            self._json(200, {"object": "list", "data": data})
            return
        if self.path.endswith("/chat/completions"):
            payload, status = self._chat_reply(body)
            self._json(status, payload)
            return
        self._json(404, {"error": {"message": "not found", "type": "not_found"}})

    do_GET = None  # 仅 POST


def start_mock() -> tuple[ThreadingHTTPServer, str]:
    """起 loopback 假服务并在后台线程 serve_forever（不调用则 socket 排队无人 accept，客户端挂起）。"""
    server = ThreadingHTTPServer(("127.0.0.1", 0), MockOpenAI)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address
    return server, f"http://{host}:{port}/v1"


# ---------------------------------------------------------------- 五路检查


def _check(label: str, fn: Any) -> bool:
    """执行单路检查：异常 → ✗（真实模式只判成功；mock 模式由 fn 内部校验内容）。"""
    try:
        ok, detail = fn()
    except Exception as e:  # noqa: BLE001 —— 连通性检查要捕获一切失败并给出提示
        print(f"  ✗ {label}: {type(e).__name__}: {e}")
        return False
    print(f"  ✓ {label}: {detail}" if ok else f"  ✗ {label}: {detail}")
    return ok


def run_checks(mode: str) -> int:
    from tindalos import llm, vision
    from tindalos.config import get_settings
    from tindalos.eval_.judge import LLMJudge
    from tindalos.generator import OllamaGenerator
    from tindalos.models import construct_loose_campaign

    strict = mode == "mock"  # mock 模式校验返回内容契约，real 模式只判连通
    checks = 0
    passed = 0

    # 1. 主 LLM chat（LLMClient 最小 ping）
    def chat_check():
        out = llm.LLMClient(get_settings()).chat([{"role": "system", "content": "ping"}])
        return (not strict or out == "pong"), f"chat → {out!r}"

    # 2. 生成（generator._chat 全链路：_ctx 拼接 + client.chat）
    def generator_check():
        out = OllamaGenerator(get_settings())._chat("ping")
        return (not strict or out == "pong"), f"generator._chat → {out[:40]!r}"

    # 3. 裁判（LLMJudge.evaluate：temp=0 + json_object + 4 维解析）
    def judge_check():
        class _World:
            def to_json(self):
                return {"nodes": [], "edges": []}

        campaign = construct_loose_campaign({"title": "ping", "acts": [], "npcs": {}, "clues": []})
        res = LLMJudge(get_settings()).evaluate(campaign, _World(), {})
        ok = res["judge"] == "llm" and "dims" in res
        reason = res.get("reason", "") if res["judge"] != "llm" else "4 维解析成功"
        return ok, f"judge → {res['judge']} ({reason})"

    # 4. RAG 问答（_llm_answer：问题+参考来源 → chat）
    def rag_check():
        from tindalos import rag

        out = rag._llm_answer("ping", "参考来源：测试上下文", None, [])
        return (not strict or out == "pong"), f"rag-qa → {out[:40]!r}"

    # 5. 向量（embed：input 条数与返回 index 校验）
    def embed_check():
        vecs = llm.LLMClient(get_settings()).embed(["ping"])
        ok = len(vecs) == 1 and len(vecs[0]) > 0
        return ok, f"embed → {len(vecs)} 条 × {len(vecs[0])} 维"

    # 6. 视觉（classify_image_online：本地 PNG → data URI → chat）
    def vision_check():
        from PIL import Image

        tmp = Path(tempfile.gettempdir()) / "tindalos_verify_vl.png"
        Image.new("RGB", (2, 2), (200, 50, 50)).save(tmp, format="PNG")
        res = vision.classify_image_online(tmp, timeout=30)
        ok = (not strict) or (res.get("kind") == "map" and res.get("confidence", 0) > 0)
        return ok, f"vision → kind={res.get('kind')} conf={res.get('confidence')}"

    # 7. 重试（500 → 退避重试 → 成功；同时校验裁判契约）
    def retry_check():
        out = llm.LLMClient(get_settings()).chat([{"role": "system", "content": "retry-test"}])
        hits = MockOpenAI.state.retry_hits
        ok = (not strict or (out == "retried-ok" and hits >= 2))
        return ok, f"500×{hits} 后 {out!r}"

    for label, fn in [
        ("主 LLM chat", chat_check),
        ("生成 generator", generator_check),
        ("裁判 judge", judge_check),
        ("RAG 问答", rag_check),
        ("向量 embed", embed_check),
        ("视觉 vision", vision_check),
        ("重试与裁判契约", retry_check),
    ]:
        checks += 1
        if _check(label, fn):
            passed += 1
    if strict and MockOpenAI.state.judge_contract_ok is False:
        print("  ✗ 裁判契约: response_format=json_object 与 temperature=0 缺失")
        checks += 1

    print(f"\n{mode} 模式：{passed}/{checks} 通过")
    return 0 if passed == checks else 1


def main(argv: list[str]) -> int:
    real = "--real" in argv
    server: ThreadingHTTPServer | None = None
    if real:
        # 不碰 env：直接透传用户已导出的真实 key/base/model（HITL 配置后跑）
        print("--real 模式：直连真实 API（key/base/model 全部来自当前环境变量）\n")
    else:
        server, base = start_mock()
        _set_mock_env(base)
        print(f"mock 模式：本地假服务 {base}\n")
    _reset_settings()
    try:
        return run_checks("mock" if not real else "real")
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
