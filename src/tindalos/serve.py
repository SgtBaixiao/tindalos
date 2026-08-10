"""HTTP API 服务（task t11-serve）：stdlib http.server + SSE 流式生成。

零新依赖（仅 stdlib http.server / threading / json / queue / uuid + tindalos 既有模块）。
API 契约（前端依赖，勿改）：
  POST /api/generate    body={module_text, llm?} → SSE 流：
                        pipeline custom 进度事件逐条 data:{stage,message}，
                        结束帧 data:{done:true,campaign}（campaign 同时写入内存缓存）；
                        客户端断开（写帧失败）→ 停止生成。
  GET  /api/campaigns/<id>  → 内存缓存中的 campaign JSON（未知 id 404）。
  POST /api/regenerate  body={campaign_id, node_id} → 调 regenerate_node
                        返回 {ok,campaign,applied}；未知 campaign 404 / 未知节点 ValueError→400。
  其他路径 → 404 JSON。
全部响应带 CORS：Access-Control-Allow-Origin:*（SSE / 200 / 400 / 404 / 500）。

线程模型：ThreadingHTTPServer（每请求一线程）；SSE 响应在请求线程内
消费「后台生成线程 → queue」的事件，写帧失败即置 stop 并中断生成线程。
"""

from __future__ import annotations

import json
import queue
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import urlparse

from tindalos.models import Campaign, construct_loose_campaign

# 进度消息 → SSE stage 映射（前端据此分阶段展示）
_STAGE_PREFIXES = (
    ("KG", "plan"),
    ("KP", "plan"),
    ("NPC", "npc"),
    ("写作", "write"),
    ("校对", "compose"),
)


def sse_stage(message: str) -> str:
    """由 pipeline 进度消息推导 SSE stage；未识别 → 'progress'。"""
    text = str(message or "")
    for prefix, stage in _STAGE_PREFIXES:
        if text.startswith(prefix):
            return stage
    return "progress"


def sse_frame(payload: dict) -> bytes:
    """单条 SSE 帧：`data:` 前缀 + JSON + 空行分隔（前端按 \n\n 解析）。"""
    return ("data:" + json.dumps(payload, ensure_ascii=False) + "\n\n").encode("utf-8")


# ---------------------------------------------------------------- 生成执行器


def _resolve_serve_generator(llm: bool) -> Any:
    """按 body.llm 开关解析生成器：请求 LLM 但未启用 → 回退确定性（与 cli 同哲学）。"""
    from tindalos.config import get_settings
    from tindalos.generator import DeterministicGenerator, OllamaGenerator

    settings = get_settings()
    if llm and settings.llm_enabled:
        return OllamaGenerator(settings)
    return DeterministicGenerator()


def default_generate(module_text: str, llm: bool, emit: Callable[[str, str], bool]) -> dict | None:
    """跑 LangGraph 管线：逐条 emit(stage, message)（返回 False 表示客户端已断开，须停止）。

    成功返回 campaign dict（model_dump json）；客户端断开返回 None；异常抛出（由调用方发错误帧）。
    """
    from tindalos.pipeline import build_pipeline

    generator = _resolve_serve_generator(llm)
    if hasattr(generator, "set_module_context"):  # LLM 生成基于模组全文（loop 迭代改进）
        generator.set_module_context(module_text, title=module_text.strip().splitlines()[0][:40] if module_text.strip() else "")
    app = build_pipeline(generator=generator)
    config = {"configurable": {"thread_id": f"serve-{uuid.uuid4().hex[:8]}"}}
    final: dict | None = None
    for mode, chunk in app.stream(
        {"module_text": module_text}, config=config, stream_mode=["custom", "values"]
    ):
        if mode == "custom" and isinstance(chunk, dict) and "progress" in chunk:
            message = str(chunk["progress"])
            if not emit(sse_stage(message), message):
                return None
        elif mode == "values":
            final = chunk
    campaign = (final or {}).get("campaign")
    if campaign is None:
        raise RuntimeError("管线未产出 campaign")
    return campaign.model_dump(mode="json")


def _default_regenerate_node(campaign: Campaign, node_id: str, generator: Any):
    """延迟导入 regenerate_node（t12 并行落地；未落地时抛 RuntimeError，调用方转 500）。"""
    from tindalos.regenerate import regenerate_node

    return regenerate_node(campaign, node_id, generator)


# ---------------------------------------------------------------- 共享状态


class ServeState:
    """服务器共享状态：campaign 内存缓存 + 生成/重生成执行器（测试可注入 mock）。"""

    def __init__(
        self,
        *,
        generate: Callable | None = None,
        regenerate: Callable | None = None,
    ) -> None:
        self.campaigns: dict[str, dict] = {}
        self.lock = threading.Lock()
        self.generate: Callable = generate or default_generate
        self.regenerate: Callable = regenerate or _default_regenerate_node


_DEFAULT_STATE = ServeState()


# ---------------------------------------------------------------- HTTP handler


class TindalosHandler(BaseHTTPRequestHandler):
    """API 契约处理器：/api/generate（SSE）· /api/campaigns/<id> · /api/regenerate。"""

    server_version = "TindalosServe/0.1"
    state: ServeState = _DEFAULT_STATE

    # -- 基础工具 ------------------------------------------------

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003 - 覆盖父类日志（静默）
        pass

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()

    MAX_BODY = 1_048_576  # 1MB 上限：防 slow-loris/超大 body 挂起请求线程

    def _read_json_body(self) -> dict | None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            return None
        if length <= 0 or length > self.MAX_BODY:
            return None
        raw = self.rfile.read(length)
        try:
            doc = json.loads(raw.decode("utf-8"))
        except UnicodeDecodeError:
            # Windows curl/cmd 常以 GBK 发送中文 body：回退 gbk（评审修正）
            try:
                doc = json.loads(raw.decode("gbk"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                return None
        except json.JSONDecodeError:
            return None
        return doc if isinstance(doc, dict) else None

    def _sse_write(self, payload: dict) -> None:
        self.wfile.write(sse_frame(payload))
        self.wfile.flush()

    # -- OPTIONS（CORS preflight：浏览器非 simple 请求先发 OPTIONS，缺了会被 501 拒） --

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()

    def _sse_headers(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        # HTTP/1.0 + 无 Content-Length 时，body 必须以连接关闭定界；SSE 单发，EventSource 自带重连
        self.send_header("Connection", "close")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.flush()

    # -- POST ----------------------------------------------------

    def do_POST(self) -> None:
        self.close_connection = True
        path = urlparse(self.path).path
        if path == "/api/generate":
            self._api_generate()
        elif path == "/api/regenerate":
            self._api_regenerate()
        else:
            self._json(404, {"error": "not found", "path": path})

    def _api_generate(self) -> None:
        doc = self._read_json_body()
        if doc is None:
            self._json(400, {"error": "body 必须是 JSON 对象"})
            return
        module_text = doc.get("module_text")
        if not isinstance(module_text, str) or not module_text.strip():
            self._json(400, {"error": "module_text 必填字符串"})
            return
        self._stream_generate(module_text, bool(doc.get("llm", False)))

    def _stream_generate(self, module_text: str, llm: bool) -> None:
        """SSE 流式生成：后台线程跑管线 → queue → 逐帧写出；断连即 stop。"""
        q: queue.Queue = queue.Queue(maxsize=64)
        stop = threading.Event()

        def emit(stage: str, message: str) -> bool:
            if stop.is_set():
                return False
            try:
                q.put(("event", stage, message), timeout=1)
            except queue.Full:
                return False
            return True

        def worker() -> None:
            try:
                campaign = self.state.generate(module_text, llm, emit)
                if campaign is not None:
                    q.put(("done", campaign))
                else:
                    q.put(("__eof__", None))
            except Exception as e:  # noqa: BLE001 - 生成失败转为 SSE 错误帧
                q.put(("failed", str(e)))
            finally:
                q.put(("__eof__", None))

        t = threading.Thread(target=worker, daemon=True)
        t.start()
        self._sse_headers()
        while True:
            try:
                item = q.get(timeout=0.25)
            except queue.Empty:
                if stop.is_set():
                    break
                continue
            kind = item[0]
            if kind == "event":
                _, stage, message = item
                try:
                    self._sse_write({"stage": stage, "message": message})
                except OSError:  # 客户端断开（BrokenPipe/ConnectionReset）
                    stop.set()
                    break
            elif kind == "done":
                campaign = item[1]
                try:
                    self._sse_write({"done": True, "campaign": campaign})
                except OSError:
                    stop.set()
                    break
                if isinstance(campaign, dict) and campaign.get("id"):
                    with self.state.lock:
                        self.state.campaigns[campaign["id"]] = campaign
                stop.set()
                break
            elif kind == "failed":
                message = item[1] or "生成失败"
                try:
                    self._sse_write({"error": message})
                    self._sse_write({"done": True, "campaign": None})
                except OSError:
                    pass
                stop.set()
                break
            else:  # __eof__
                break
        stop.set()
        t.join(timeout=5)

    def _api_regenerate(self) -> None:
        doc = self._read_json_body()
        if doc is None:
            self._json(400, {"error": "body 必须是 JSON 对象"})
            return
        cid = doc.get("campaign_id")
        nid = doc.get("node_id")
        if not isinstance(cid, str) or not cid or not isinstance(nid, str) or not nid:
            self._json(400, {"error": "campaign_id/node_id 必填字符串"})
            return
        with self.state.lock:
            raw = self.state.campaigns.get(cid)
        if raw is None:
            self._json(404, {"error": "campaign not found", "campaign_id": cid})
            return
        try:
            campaign = Campaign.model_validate(raw)
        except Exception:  # noqa: BLE001 - 容错输入（与 cli 同哲学）
            campaign = construct_loose_campaign(raw)
        try:
            generator = _resolve_serve_generator(bool(doc.get("llm", False)))
            updated, applied = self.state.regenerate(campaign, nid, generator)
        except ValueError as e:
            self._json(400, {"ok": False, "error": str(e)})
            return
        except Exception as e:  # noqa: BLE001 - 内部错误转 500 JSON
            self._json(500, {"ok": False, "error": str(e)})
            return
        doc_out = updated.model_dump(mode="json") if hasattr(updated, "model_dump") else updated
        with self.state.lock:
            self.state.campaigns[cid] = doc_out
        self._json(200, {"ok": True, "campaign": doc_out, "applied": list(applied or [])})

    # -- GET -----------------------------------------------------

    def do_GET(self) -> None:
        self.close_connection = True
        path = urlparse(self.path).path
        if path.startswith("/api/campaigns/"):
            cid = path[len("/api/campaigns/"):]
            if cid:
                with self.state.lock:
                    doc = self.state.campaigns.get(cid)
                if doc is None:
                    self._json(404, {"error": "campaign not found", "campaign_id": cid})
                else:
                    self._json(200, doc)
                return
        self._json(404, {"error": "not found", "path": path})


def make_handler(state: ServeState | None = None) -> type[TindalosHandler]:
    """构造绑定共享状态的 handler 类（供 ThreadingHTTPServer 与 mock 测试复用）。"""
    return type("TindalosHandler", (TindalosHandler,), {"state": state or ServeState()})


# ---------------------------------------------------------------- 服务入口


def serve(
    host: str = "127.0.0.1",
    port: int = 8347,
    *,
    state: ServeState | None = None,
) -> None:
    """启动 ThreadingHTTPServer（阻塞 serve_forever；Ctrl+C 优雅退出）。

    --host 默认 127.0.0.1，--port 默认 8347（前端契约）。
    """
    server = ThreadingHTTPServer((host, port), make_handler(state or ServeState()))
    server.daemon_threads = True
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


__all__ = [
    "ServeState",
    "TindalosHandler",
    "make_handler",
    "serve",
    "sse_frame",
    "sse_stage",
    "default_generate",
]
