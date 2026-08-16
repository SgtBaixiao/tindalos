"""统一 LLM 客户端单元测试：FakeTransport 注入，零网络零 LLM。

覆盖：chat 内容/工具调用返回、chat_json 容错提取（围栏/prose 包裹）、embed
按 index 排序与条数校验、classify_image data URI 载荷组装、重试矩阵
（5xx/429/408 重试、4xx 不重试、网络异常重试到耗尽）、_extract_json 边界。
"""

import json

import pytest

from tindalos.config import Settings
from tindalos.llm import LLMClient, LLMError, _extract_json, _is_retryable


class FakeResp:
    def __init__(self, status=200, json_data=None, text=""):
        self.status_code = status
        self._json_data = json_data
        self.text = text or (json.dumps(json_data, ensure_ascii=False) if json_data is not None else "")

    def json(self):
        return self._json_data


class FakeTransport:
    """可编程 fake：单一动作队列（响应或异常），耗尽后重放最后一个动作；
    每次调用记录 payload。"""

    def __init__(self):
        self.calls = []
        self._actions = []
        self._last = None

    def respond(self, resp):
        self._actions.append(resp)
        return self

    def raises(self, exc):
        self._actions.append(exc)
        return self

    def __call__(self, method, url, *, json=None, headers=None, timeout=None):
        self.calls.append({"method": method, "url": url, "json": json, "headers": headers, "timeout": timeout})
        if not self._actions:
            if self._last is None:
                raise AssertionError("FakeTransport 未配置响应")
            action = self._last
        else:
            action = self._actions.pop(0)
            self._last = action
        if isinstance(action, Exception):
            raise action
        return action


@pytest.fixture
def no_sleep(monkeypatch):
    import tindalos.llm as llm

    monkeypatch.setattr(llm, "_sleep_backoff", lambda attempt: None)


def make_client(transport=None):
    return LLMClient(Settings(llm_enabled=True, model="deepseek-chat"), transport=transport)


def ok_chat(content):
    return FakeResp(200, {"choices": [{"message": {"content": content}}]})


def ok_tools(arguments):
    return FakeResp(200, {"choices": [{"message": {"tool_calls": [{"function": {"arguments": arguments}}]}}]})


# ---- chat ----


def test_chat_returns_content():
    t = FakeTransport().respond(ok_chat("你好"))
    c = make_client(t)
    assert c.chat([{"role": "user", "content": "hi"}]) == "你好"
    call = t.calls[0]
    assert call["url"].endswith("/chat/completions")
    assert call["json"]["model"] == "deepseek-chat"
    assert call["json"]["messages"][0]["content"] == "hi"


def test_chat_bearer_header_when_key():
    s = Settings(llm_enabled=True, model="deepseek-chat", api_key="sk-test")
    t = FakeTransport().respond(ok_chat("x"))
    LLMClient(s, transport=t).chat([{"role": "user", "content": "hi"}])
    assert t.calls[0]["headers"]["Authorization"] == "Bearer sk-test"


def test_chat_no_bearer_when_no_key():
    t = FakeTransport().respond(ok_chat("x"))
    make_client(t).chat([{"role": "user", "content": "hi"}])
    assert t.calls[0]["headers"] == {}


def test_chat_tool_calls_returns_arguments():
    args = json.dumps({"act_id": "a1"}, ensure_ascii=False)
    t = FakeTransport().respond(ok_tools(args))
    out = make_client(t).chat([{"role": "user", "content": "make"}], tools=[{"type": "function"}])
    assert out == args


def test_chat_tool_calls_dict_arguments_serialized():
    """Ollama 形态：tool_calls arguments 为 dict → 序列化为 JSON 字符串。"""
    t = FakeTransport().respond(ok_tools({"act_id": "a1"}))
    out = make_client(t).chat([{"role": "user", "content": "make"}], tools=[{"type": "function"}])
    assert json.loads(out)["act_id"] == "a1"


def test_network_error_message_keeps_type_and_status():
    """网络层异常 → LLMError 消息携带类型名与（如有）HTTP 状态码（G5 回归，不吞栈）。"""
    class _E(Exception):
        pass
    err = _E("boom")
    t = FakeTransport().raises(err)
    with pytest.raises(LLMError) as ei:
        make_client(t).chat([{"role": "user", "content": "hi"}])
    assert "LLM 请求失败" in str(ei.value)
    assert "_E" in str(ei.value)  # 类型名
    assert ei.value.kind == "connection"


def test_chat_missing_choices_raises_parse_failed():
    t = FakeTransport().respond(FakeResp(200, {}))
    with pytest.raises(LLMError) as ei:
        make_client(t).chat([{"role": "user", "content": "hi"}])
    assert ei.value.kind == "parse_failed"


# ---- chat_json 容错提取 ----


def test_chat_json_plain_object():
    t = FakeTransport().respond(ok_chat('{"a": 1}'))
    assert make_client(t).chat_json([{"role": "user", "content": "x"}]) == {"a": 1}


def test_chat_json_fenced():
    t = FakeTransport().respond(ok_chat('```json\n{"a": 2}\n```'))
    assert make_client(t).chat_json([{"role": "user", "content": "x"}]) == {"a": 2}


def test_chat_json_prose_wrapped():
    t = FakeTransport().respond(ok_chat('好的，以下是结果：{"a": 3}，请查收。'))
    assert make_client(t).chat_json([{"role": "user", "content": "x"}]) == {"a": 3}


def test_chat_json_array():
    t = FakeTransport().respond(ok_chat("[1, 2, 3]"))
    assert make_client(t).chat_json([{"role": "user", "content": "x"}], expect=list) == [1, 2, 3]


def test_chat_json_expect_mismatch_raises():
    t = FakeTransport().respond(ok_chat("[1, 2]"))
    with pytest.raises(LLMError) as ei:
        make_client(t).chat_json([{"role": "user", "content": "x"}], expect=dict)
    assert ei.value.kind == "parse_failed"


def test_chat_json_no_json_raises():
    t = FakeTransport().respond(ok_chat("抱歉，我无法回答。"))
    with pytest.raises(LLMError) as ei:
        make_client(t).chat_json([{"role": "user", "content": "x"}])
    assert ei.value.kind == "parse_failed"


# ---- 重试矩阵 ----


def test_retry_5xx_then_success(no_sleep):
    t = FakeTransport().respond(FakeResp(500, {}, "boom")).respond(ok_chat("ok"))
    out = make_client(t).chat([{"role": "user", "content": "hi"}])
    assert out == "ok"
    assert len(t.calls) == 2


def test_retry_429_then_success(no_sleep):
    t = FakeTransport().respond(FakeResp(429, {}, "limit")).respond(ok_chat("ok"))
    assert make_client(t).chat([{"role": "user", "content": "hi"}]) == "ok"
    assert len(t.calls) == 2


def test_retry_408_then_success(no_sleep):
    t = FakeTransport().respond(FakeResp(408, {}, "timeout")).respond(ok_chat("ok"))
    assert make_client(t).chat([{"role": "user", "content": "hi"}]) == "ok"


def test_no_retry_on_4xx(no_sleep):
    t = FakeTransport().respond(FakeResp(404, {}, "not found"))
    with pytest.raises(LLMError) as ei:
        make_client(t).chat([{"role": "user", "content": "hi"}])
    assert ei.value.kind == "http_4xx"
    assert len(t.calls) == 1  # 4xx 不重试


def test_5xx_exhausts_raises(no_sleep):
    t = FakeTransport().respond(FakeResp(500, {}, "boom"))
    with pytest.raises(LLMError) as ei:
        make_client(t).chat([{"role": "user", "content": "hi"}])
    assert ei.value.kind == "http_5xx"
    assert len(t.calls) == 1 + Settings().llm_max_retries  # 1 + max_retries 次尝试


def test_network_error_retry_then_success(no_sleep):
    t = FakeTransport().raises(TimeoutError("boom")).respond(ok_chat("ok"))
    assert make_client(t).chat([{"role": "user", "content": "hi"}]) == "ok"


def test_network_error_exhausts_raises(no_sleep):
    t = FakeTransport().raises(ConnectionError("down"))
    with pytest.raises(LLMError) as ei:
        make_client(t).chat([{"role": "user", "content": "hi"}])
    assert ei.value.kind == "connection"


# ---- embed ----


def test_embed_ordered_by_index():
    t = FakeTransport().respond(
        FakeResp(200, {"data": [
            {"index": 1, "embedding": [1.0, 2.0]},
            {"index": 0, "embedding": [3.0, 4.0]},
        ]})
    )
    out = make_client(t).embed(["a", "b"])
    assert out == [[3.0, 4.0], [1.0, 2.0]]
    call = t.calls[0]
    assert call["url"].endswith("/embeddings")
    assert call["json"]["input"] == ["a", "b"]


def test_embed_count_mismatch_raises():
    t = FakeTransport().respond(FakeResp(200, {"data": []}))
    with pytest.raises(LLMError) as ei:
        make_client(t).embed(["a", "b"])
    assert ei.value.kind == "parse_failed"


# ---- classify_image（多模态） ----


def test_classify_image_builds_data_uri_from_bytes():
    t = FakeTransport().respond(ok_chat('{"kind": "map"}'))
    s = Settings(llm_enabled=True, vl_key="sk-vl", vl_model="qwen3-vl-plus")
    c = LLMClient(s, transport=t)
    out = c.classify_image(("image/png", b"\x00\x01"), "这是什么？", system="你是识图助手")
    assert out == '{"kind": "map"}'
    call = t.calls[0]
    assert call["url"].endswith("/chat/completions")
    body = call["json"]
    assert body["model"] == "qwen3-vl-plus"
    assert call["headers"]["Authorization"] == "Bearer sk-vl"
    msgs = body["messages"]
    assert msgs[0]["role"] == "system"
    user = msgs[1]["content"]
    assert user[0]["type"] == "image_url"
    assert user[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert user[1]["text"] == "这是什么？"
    assert body["temperature"] == 0.1


def test_classify_image_accepts_existing_data_uri():
    t = FakeTransport().respond(ok_chat("{}"))
    make_client(t).classify_image("data:image/png;base64,AA==", "看看")
    url = t.calls[0]["json"]["messages"][0]["content"][0]["image_url"]["url"]
    assert url == "data:image/png;base64,AA=="


# ---- 错误分类辅助 ----


def test_is_retryable_matrix():
    assert _is_retryable(None, 500) is True
    assert _is_retryable(None, 503) is True
    assert _is_retryable(None, 429) is True
    assert _is_retryable(None, 408) is True
    assert _is_retryable(None, 404) is False
    assert _is_retryable(None, 401) is False
    assert _is_retryable(None, 200) is False
    assert _is_retryable(TimeoutError("x")) is True
    assert _is_retryable(ConnectionError("x")) is True
    assert _is_retryable(LLMError("x", kind="http_429")) is True
    assert _is_retryable(LLMError("x", kind="http_4xx")) is False


def test_extract_json_edges():
    assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert _extract_json("前缀 [1,2] 后缀") == [1, 2]
    assert _extract_json('嵌套 {"a": {"b": "}"}} 尾') == {"a": {"b": "}"}}
    with pytest.raises(ValueError):
        _extract_json("没有任何 JSON")
    with pytest.raises(ValueError):
        _extract_json("")
    assert _extract_json('{"str": "含{花括号}"}') == {"str": "含{花括号}"}
