"""统一 LLM 客户端：单一传输 + 统一重试 + 统一 JSON 容错 + 多模态/embedding 收敛。

背景（2026-08-16 架构审计）：此前 generator._chat（requests+重试+工具）、
judge._default_client（urllib 零依赖）、rag._llm_answer / _online_embed（直读
os.environ 绕过 Settings）、vision.classify_image_online（无重试）四套重复实现
各自为政，重试/JSON 容错/配置来源不一致。本模块收敛为唯一在线调用面。

设计要点：
- 传输可注入：`LLMClient(settings, transport=...)`，transport 是
  `(method, url, *, json, headers, timeout) -> ResponseLike` 的可调用对象。
  生产默认 `_default_transport`（requests.request）；测试注入 fake → 零网络可测。
- requests 保持可选依赖：未安装时在线方法抛 `LLMError(kind="no_requests")`，
  离线降级路径不受影响。
- 错误分类：`LLMError.kind ∈ {no_requests, timeout, connection, http_4xx,
  http_5xx, http_429, parse_failed}`，各调用点按既有降级哲学处理。
- 重试策略（与 generator._is_retryable 对齐并扩展）：连接超时/连接错误/5xx/
  429/408 重试 max_retries 次（指数退避 + 抖动）；4xx 不重试直接 LLMError。
"""

from __future__ import annotations

import base64
import json
import re
import time
from typing import Any, Callable, Sequence

try:  # requests 为可选（离线无网络时缺省降级；在线路径缺 requests 抛 LLMError）
    import requests
except ImportError:  # pragma: no cover - 依赖探测已确认安装，此分支仅防御
    requests = None  # type: ignore[assignment]

LLM_KINDS = ("no_requests", "timeout", "connection", "http_4xx", "http_5xx", "http_429", "parse_failed")

# ResponseLike：transport 返回值（requests.Response 或测试 fake）
# 约定提供 .status_code / .text / .json()


class LLMError(Exception):
    """LLM 调用失败（网络/超时/HTTP 4xx 5xx/JSON 解析）。携带 kind/status_code/partial_text。"""

    def __init__(
        self,
        message: str,
        *,
        kind: str = "unknown",
        status_code: int | None = None,
        partial_text: str | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.status_code = status_code
        self.partial_text = partial_text


# ---- 重试判定与退避 ------------------------------------------------------------


def _exc_kind(exc: Exception) -> str:
    """按异常类型归类：超时 → timeout，连接/IO → connection。"""
    name = type(exc).__name__.lower()
    if "timeout" in name or "timedout" in name or isinstance(exc, TimeoutError):
        return "timeout"
    return "connection"


def _is_retryable(exc: Exception | None = None, status_code: int | None = None) -> bool:
    """重试判定：HTTP 408/429/5xx 重试（4xx 不重试）；网络层超时/连接错误重试。"""
    if status_code is not None:
        return status_code in (408, 429) or status_code >= 500
    if exc is None:
        return False
    if isinstance(exc, LLMError):
        return exc.kind in {"timeout", "connection", "http_429", "http_5xx"}
    return _exc_kind(exc) in {"timeout", "connection"}


def _network_exc_detail(exc: Exception) -> str:
    """网络/传输异常 → 可诊断的 LLMError 消息：带异常类型名与（如有）HTTP 状态码。

    保证降级告警携带根因类型与状态码（G5 复审：HTTPStatusError(HTTP 410) 不吞栈）。
    """
    code = ""
    resp = getattr(exc, "response", None)
    if resp is not None and getattr(resp, "status_code", None):
        code = f" (HTTP {resp.status_code})"
    return f"LLM 请求失败 [{type(exc).__name__}]: {exc}{code}"


def _sleep_backoff(attempt: int) -> None:
    """指数退避 + 确定性抖动：attempt 从 1 起，base = min(2^attempt, 30)，±10%。

    测试可通过 monkeypatch 替换本函数为零 sleep。
    """
    base = min(2 ** attempt, 30.0)
    jitter = base * ((attempt * 13) % 7 - 3) / 30.0
    time.sleep(max(0.0, base + jitter))


# ---- 统一 JSON 容错提取 --------------------------------------------------------


def _iter_balanced_blocks(text: str, open_ch: str, close_ch: str):
    """扫描文本中所有括号平衡的最外层块（跳过字符串内的括号/转义）。"""
    i = 0
    while True:
        start = text.find(open_ch, i)
        if start == -1:
            return
        depth = 0
        in_str = False
        esc = False
        end = None
        for j in range(start, len(text)):
            ch = text[j]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
                if depth == 0:
                    end = j
                    break
        if end is None:
            return
        yield start, end
        i = end + 1


def _extract_json(text: str) -> Any:
    """健壮 JSON 提取：剥代码围栏 → 直接 loads → 平衡括号扫描（prose 包裹容忍）。

    合并 generator._parse_json（剥 fence）+ judge 平衡括号扫描两种能力；支持
    顶层对象或数组，返回首个合法 JSON 块。
    """
    if not text:
        raise ValueError("空响应，无 JSON")
    t = text.strip()
    # 剥代码围栏 ```json ... ```
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.S)
        t = re.sub(r"\s*```$", "", t, flags=re.S).strip()
    # 直接 loads
    try:
        return json.loads(t)
    except ValueError:
        pass
    # 平衡括号扫描
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        for start, end in _iter_balanced_blocks(t, open_ch, close_ch):
            try:
                return json.loads(t[start : end + 1])
            except ValueError:
                continue
    raise ValueError("响应不含合法 JSON 对象/数组")


# ---- 生产传输 ----------------------------------------------------------------


def _default_transport(
    method: str,
    url: str,
    *,
    json: Any = None,
    headers: dict | None = None,
    timeout: float | None = None,
    **kw,
) -> Any:
    """生产传输：requests.request 包装。requests 未安装时抛 LLMError(no_requests)。"""
    if requests is None:
        raise LLMError("requests 未安装：无法发起在线 LLM 请求", kind="no_requests")
    return requests.request(method, url, json=json, headers=headers, timeout=timeout, **kw)


# ---- 统一客户端 ---------------------------------------------------------------


class LLMClient:
    """OpenAI 兼容端点的统一客户端。所有在线 LLM 路径（生成/裁判/问答/embedding/VL）共用。

    transport 可注入（测试用 fake，保持零网络）；默认生产用 requests。
    """

    def __init__(self, settings, *, transport: Callable | None = None) -> None:
        self.settings = settings
        self._transport = transport or _default_transport

    # -- 底层 POST（统一重试） --

    def _request(self, method: str, url: str, **kw) -> Any:
        return self._transport(method, url, **kw)

    def _classify_status(self, status: int, resp: Any) -> LLMError:
        text = getattr(resp, "text", "")[:200]
        if status == 429:
            kind = "http_429"
        elif 400 <= status < 500:
            kind = "http_4xx"
        else:
            kind = "http_5xx"
        return LLMError(f"LLM 端点 HTTP {status}: {text}", kind=kind, status_code=status, partial_text=text)

    def _post(self, url: str, body: dict, headers: dict, timeout: float, max_retries: int) -> Any:
        """POST + 统一重试（网络层 5xx/429/408 重试；4xx 不重试直接 LLMError）。"""
        attempt = 0
        while True:
            try:
                resp = self._request("POST", url, json=body, headers=headers, timeout=timeout)
            except LLMError:
                raise
            except Exception as e:  # noqa: BLE001 - requests 网络异常，转 LLMError
                if _is_retryable(e) and attempt < max_retries:
                    attempt += 1
                    _sleep_backoff(attempt)
                    continue
                raise LLMError(_network_exc_detail(e), kind=_exc_kind(e)) from e
            status = resp.status_code
            if status >= 400:
                if _is_retryable(None, status) and attempt < max_retries:
                    attempt += 1
                    _sleep_backoff(attempt)
                    continue
                raise self._classify_status(status, resp)
            return resp

    def _extract_message(self, resp: Any) -> str:
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001
            raise LLMError(f"LLM 响应非 JSON: {getattr(resp, 'text', '')[:200]}", kind="parse_failed")
        try:
            msg = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            raise LLMError(
                f"LLM 响应缺少 choices[0].message: {str(data)[:200]}", kind="parse_failed"
            )
        content = msg.get("content")
        if isinstance(content, str) and content.strip():
            return content
        # 工具调用：返回首个 tool_call 的 arguments（字符串直返，dict 序列化——兼容
        # Ollama 返回对象 vs 云端返回 JSON 字符串两种形态）
        tool_calls = msg.get("tool_calls") or []
        if tool_calls:
            args = tool_calls[0].get("function", {}).get("arguments")
            if isinstance(args, str) and args.strip():
                return args
            if isinstance(args, dict):
                return json.dumps(args, ensure_ascii=False)
        raise LLMError(
            f"LLM 响应无 content 且无 tool_calls: {str(data)[:200]}", kind="parse_failed"
        )

    # -- chat（生成/裁判/问答共用） --

    def chat(
        self,
        messages: list[dict],
        *,
        temperature: float = 0.7,
        response_format: dict | None = None,
        tools: list[dict] | None = None,
        tool_choice: Any = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
    ) -> str:
        """POST {base}/chat/completions → 返回 message.content（或首个 tool_call 参数）。

        端点/模型/key 缺省取 Settings（base_url 缺省 s.ollama_base_url，可传 VL 端点）。
        """
        s = self.settings
        timeout = s.llm_timeout if timeout is None else timeout
        max_retries = s.llm_max_retries if max_retries is None else max_retries
        base_url = base_url or s.ollama_base_url
        model = model or s.model
        api_key = s.api_key if api_key is None else api_key

        url = base_url.rstrip("/") + "/chat/completions"
        body: dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature}
        if response_format is not None:
            body["response_format"] = response_format
        if tools is not None:
            body["tools"] = tools
            if tool_choice is not None:
                body["tool_choice"] = tool_choice
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

        resp = self._post(url, body, headers, timeout, max_retries)
        return self._extract_message(resp)

    def chat_json(self, messages: list[dict], *, expect: type | tuple | None = None, **kw) -> Any:
        """chat + 统一容错 JSON 提取。expect 为顶层期望类型（dict/list）；解析失败抛 LLMError(parse_failed)。"""
        raw = self.chat(messages, **kw)
        try:
            data = _extract_json(raw)
        except ValueError as e:
            raise LLMError(str(e), kind="parse_failed", partial_text=raw[:500]) from e
        if expect is not None and not isinstance(data, expect):
            raise LLMError(
                f"期望顶层 {expect}，实得 {type(data).__name__}",
                kind="parse_failed",
                partial_text=raw[:500],
            )
        return data

    # -- embedding（RAG 向量化） --

    def embed(
        self,
        texts: Sequence[str],
        *,
        timeout: float | None = None,
        max_retries: int | None = None,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
    ) -> list[list[float]]:
        """POST {base}/embeddings → 按 index 排序的向量列表（校验返回条数）。"""
        s = self.settings
        timeout = s.llm_timeout if timeout is None else timeout
        max_retries = s.llm_max_retries if max_retries is None else max_retries
        base_url = base_url or s.embed_base
        model = model or s.embed_model
        api_key = s.embed_key if api_key is None else api_key
        texts = list(texts)

        url = base_url.rstrip("/") + "/embeddings"
        body = {"model": model, "input": texts}
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

        resp = self._post(url, body, headers, timeout, max_retries)
        try:
            data = resp.json()
            items = data.get("data") or []
        except Exception:  # noqa: BLE001
            raise LLMError(
                f"embedding 响应非 JSON: {getattr(resp, 'text', '')[:200]}", kind="parse_failed"
            )
        ordered = [it["embedding"] for it in sorted(items, key=lambda it: it.get("index", 0))]
        if len(ordered) != len(texts):
            raise LLMError(
                f"embedding 返回 {len(ordered)} 条，期望 {len(texts)}",
                kind="parse_failed",
                partial_text=getattr(resp, "text", "")[:200],
            )
        return ordered

    # -- 多模态（VL 图像分类） --

    def classify_image(
        self,
        image: str | tuple[str, bytes],
        prompt: str,
        *,
        system: str | None = None,
        timeout: float | None = None,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
    ) -> str:
        """POST {base}/chat/completions，用户消息含 image_url(data URI)。

        image 为 data-URI 字符串，或 (mime, bytes) 元组（自动 base64 编码）。
        端点缺省取 s.vl_base / s.vl_model / s.vl_key。
        """
        s = self.settings
        if isinstance(image, tuple):
            mime, payload = image
            data_uri = f"data:{mime};base64," + base64.b64encode(payload).decode("ascii")
        else:
            data_uri = image
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append(
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_uri}},
                    {"type": "text", "text": prompt},
                ],
            }
        )
        return self.chat(
            messages,
            temperature=0.1,
            timeout=timeout,
            base_url=base_url or s.vl_base,
            model=model or s.vl_model,
            api_key=s.vl_key if api_key is None else api_key,
        )


__all__ = [
    "LLMClient",
    "LLMError",
    "LLM_KINDS",
    "_extract_json",
    "_is_retryable",
    "_sleep_backoff",
]
