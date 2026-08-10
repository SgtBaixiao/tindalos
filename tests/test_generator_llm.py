"""OllamaGenerator 离线 mock 测试（沙箱可跑，零网络）。

覆盖（t9-llm）：
- _parse_json：```json / ``` 围栏、未闭合围栏、前后缀说明文字、顶层数组、坏 JSON；
- _chat：content 与 tool_calls 分支、arguments 提取、超时/连接错误重试、4xx 不重试、
  5xx 重试、timeout/max_retries 构造参数（settings 默认 + 显式覆盖）；
- generate_acts/npcs/scene：fenced 数组解析、工具调用产物、字段规整（缺 id/非法 kind）、
  坏回复/网络错误降级 + UserWarning 告警；
- build_generator 开关（llm_enabled ↔ Ollama / Deterministic）。
"""

from __future__ import annotations

import json

import pytest
import requests

from tindalos.config import Settings
from tindalos.generator import (
    DeterministicGenerator,
    Generator,
    OllamaGenerator,
    _SCENE_TOOL,
    _parse_json,
    build_generator,
)


# ---------------------------------------------------------------- fakes


def _settings(model: str = "test-model") -> Settings:
    s = Settings()
    s.ollama_base_url = "http://localhost:11434/v1"
    s.model = model
    s.llm_enabled = True
    return s


class _FakeResp:
    def __init__(self, payload=None, status: int = 200) -> None:
        self._payload = payload
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            exc = requests.exceptions.HTTPError(f"{self.status_code} error")
            exc.response = self
            raise exc

    def json(self):
        return self._payload


class _FakeRequests:
    """按调用序号返回预置结果（Exception 或 payload dict）；结果不足时复用最后一个。"""

    def __init__(self, *results) -> None:
        self._results = list(results)
        self.calls: list[dict] = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers or {}, "timeout": timeout})
        idx = min(len(self.calls) - 1, len(self._results) - 1)
        r = self._results[idx]
        if isinstance(r, Exception):
            raise r
        return r if isinstance(r, _FakeResp) else _FakeResp(r)


def _msg(content: str | None = None, tool_calls=None) -> dict:
    m: dict = {"role": "assistant"}
    if tool_calls is not None:
        m["tool_calls"] = tool_calls
    else:
        m["content"] = content
    return {"choices": [{"message": m}]}


def _tool_call(arguments) -> list[dict]:
    return [{"function": {"name": "generate_scene", "arguments": arguments}}]


def _make_generator(fake: _FakeRequests, **kwargs) -> OllamaGenerator:
    g = OllamaGenerator(_settings(), retry_delay=0, **kwargs)
    g._requests = fake
    return g


# ---------------------------------------------------------------- _parse_json


class TestParseJson:
    def test_plain_object(self) -> None:
        assert _parse_json('{"id": "s1"}') == {"id": "s1"}

    def test_array_multielement(self) -> None:
        doc = _parse_json('[{"id": "a1"}, {"id": "a2"}, {"id": "a3"}]')
        assert doc == [{"id": "a1"}, {"id": "a2"}, {"id": "a3"}]

    def test_fenced_json_block(self) -> None:
        doc = _parse_json('```json\n{"id": "s1", "title": "场景"}\n```')
        assert doc["id"] == "s1"

    def test_fenced_no_language(self) -> None:
        doc = _parse_json('以下是结果：\n```\n{"id": "s1"}\n```\n完毕')
        assert doc["id"] == "s1"

    def test_unclosed_fence(self) -> None:
        doc = _parse_json('```json\n{"id": "s1"}\n（缺少闭合围栏）')
        assert doc["id"] == "s1"

    def test_prose_wrapped_object(self) -> None:
        doc = _parse_json('好的，这是场景：\n{"id": "s1", "setting": {"time": "午夜"}}\n希望有帮助。')
        assert doc["setting"]["time"] == "午夜"

    def test_prose_wrapped_array(self) -> None:
        doc = _parse_json(
            '幕结构如下：\n[{"id": "act-1", "title": "一"}, {"id": "act-2", "title": "二"}]\n以上。'
        )
        assert len(doc) == 2

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError):
            _parse_json("")

    def test_bad_json_raises(self) -> None:
        with pytest.raises(ValueError):
            _parse_json("这不是 JSON 内容，只是一段文字。")


# ---------------------------------------------------------------- _chat


class TestChat:
    def test_content_branch(self) -> None:
        fake = _FakeRequests(_msg("hello"))
        g = _make_generator(fake)
        assert g._chat("hi") == "hello"
        assert fake.calls[0]["url"] == "http://localhost:11434/v1/chat/completions"
        assert fake.calls[0]["timeout"] == g.timeout

    def test_tool_call_arguments_string(self) -> None:
        args = json.dumps(
            {"id": "s1", "title": "场景", "setting": {"time": "深夜"}, "events": [], "npc_ids": []},
            ensure_ascii=False,
        )
        fake = _FakeRequests(_msg("", tool_calls=_tool_call(args)))
        g = _make_generator(fake)
        out = g._chat("generate scene", tools=[_SCENE_TOOL])
        assert json.loads(out)["id"] == "s1"
        assert fake.calls[0]["json"]["tools"] == [_SCENE_TOOL]

    def test_tool_call_arguments_dict(self) -> None:
        fake = _FakeRequests(_msg("", tool_calls=_tool_call({"id": "s1", "title": "场景"})))
        g = _make_generator(fake)
        assert json.loads(g._chat("gen", tools=[_SCENE_TOOL]))["id"] == "s1"

    def test_tool_call_missing_arguments_raises(self) -> None:
        fake = _FakeRequests(_msg("", tool_calls=[{"function": {}}]))
        g = _make_generator(fake)
        with pytest.raises(ValueError):
            g._chat("gen", tools=[_SCENE_TOOL])

    def test_retries_on_timeout_then_success(self) -> None:
        to = requests.exceptions.ReadTimeout("read timeout")
        fake = _FakeRequests(to, _msg("ok"))
        g = _make_generator(fake, max_retries=2)
        assert g._chat("hi") == "ok"
        assert len(fake.calls) == 2

    def test_retries_on_connection_error(self) -> None:
        conn = requests.exceptions.ConnectionError("refused")
        fake = _FakeRequests(conn, conn, _msg("ok"))
        g = _make_generator(fake, max_retries=2)
        assert g._chat("hi") == "ok"
        assert len(fake.calls) == 3

    def test_retries_exhausted_raises(self) -> None:
        to = requests.exceptions.ReadTimeout("read timeout")
        fake = _FakeRequests(to, to, to)
        g = _make_generator(fake, max_retries=2)
        with pytest.raises(requests.exceptions.ReadTimeout):
            g._chat("hi")
        assert len(fake.calls) == 3

    def test_http_4xx_no_retry(self) -> None:
        fake = _FakeRequests(_FakeResp({"error": "model not found"}, status=404))
        g = _make_generator(fake, max_retries=3)
        with pytest.raises(requests.exceptions.HTTPError):
            g._chat("hi")
        assert len(fake.calls) == 1

    def test_http_5xx_retried(self) -> None:
        fake = _FakeRequests(_FakeResp({"error": "boom"}, status=500), _msg("ok"))
        g = _make_generator(fake, max_retries=2)
        assert g._chat("hi") == "ok"
        assert len(fake.calls) == 2

    def test_constructor_defaults_from_settings(self) -> None:
        s = _settings()
        s.llm_timeout = 42
        s.llm_max_retries = 5
        g = OllamaGenerator(s)
        assert g.timeout == 42
        assert g.max_retries == 5

    def test_constructor_explicit_overrides_settings(self) -> None:
        g = OllamaGenerator(_settings(), timeout=9, max_retries=0)
        assert g.timeout == 9
        assert g.max_retries == 0


# ---------------------------------------------------------------- generate_*


class TestGenerate:
    def test_generate_acts_fenced_array(self) -> None:
        payload = _msg(
            '```json\n'
            '[{"id": "act-1", "title": "初现端倪", "roman": "I"}, '
            '{"id": "act-2", "title": "深入漩涡", "roman": "II"}]\n'
            '```'
        )
        g = _make_generator(_FakeRequests(payload))
        acts = g.generate_acts("前提", 2)
        assert [a["title"] for a in acts] == ["初现端倪", "深入漩涡"]
        assert acts[0]["scene_titles"]

    def test_generate_acts_wrapped_object(self) -> None:
        payload = _msg('返回如下：\n{"acts": [{"id": "act-1", "title": "幕一", "roman": "I"}]}\n完毕')
        g = _make_generator(_FakeRequests(payload))
        assert g.generate_acts("前提", 2)[0]["title"] == "幕一"

    def test_generate_acts_normalizes_missing_id(self) -> None:
        payload = _msg('[{"title": "无名幕"}]')
        g = _make_generator(_FakeRequests(payload))
        acts = g.generate_acts("前提", 2)
        assert len(acts) == 1
        assert acts[0]["title"] == "无名幕"
        assert acts[0]["id"].startswith("act-")
        assert acts[0]["roman"]

    def test_generate_acts_coerces_int_id(self) -> None:
        """回归：真实模型返回 int id（如 "id": 1）时下游 re.search 会崩，必须转 str 并加类型前缀防跨实体冲突。"""
        payload = _msg('[{"id": 1, "title": "雾港之晨"}, {"id": 2, "title": "雾港之夜"}]')
        g = _make_generator(_FakeRequests(payload))
        acts = g.generate_acts("前提", 2)
        assert [a["id"] for a in acts] == ["act-1", "act-2"]
        assert all(isinstance(a["id"], str) for a in acts)

    def test_generate_acts_coerces_summary_and_scene_titles(self) -> None:
        """回归：summary 为列表 / scene_titles 为字符串时规整为 str / list[str]。"""
        payload = _msg('[{"id": "act-1", "title": "幕一", "summary": ["a", "b"], "scene_titles": "唯一场景"}]')
        g = _make_generator(_FakeRequests(payload))
        acts = g.generate_acts("前提", 2)
        assert acts[0]["summary"] == "['a', 'b']"
        assert acts[0]["scene_titles"] == ["唯一场景"]

    def test_generate_acts_fallback_on_bad_json_warns(self) -> None:
        g = _make_generator(_FakeRequests(_msg("抱歉，我无法生成 JSON。")))
        with pytest.warns(UserWarning, match="回退 DeterministicGenerator"):
            acts = g.generate_acts("前提", 2)
        assert acts[0]["id"].startswith("act-")

    def test_generate_acts_fallback_on_network_error_warns(self) -> None:
        g = _make_generator(_FakeRequests(requests.exceptions.ConnectionError("down")), max_retries=1)
        with pytest.warns(UserWarning, match="回退"):
            acts = g.generate_acts("前提", 2)
        assert acts

    def test_generate_npcs_parses_and_normalizes(self) -> None:
        payload = _msg('[{"id": "npc-1", "name": "林晚", "archetype": "调查员"}, {"name": "沈一舟"}]')
        g = _make_generator(_FakeRequests(payload))
        npcs = g.generate_npcs("前提", 3)
        assert [n["id"] for n in npcs] == ["npc-1", "npc-2"]
        assert npcs[0]["name"] == "林晚"

    def test_generate_npcs_prefixes_bare_numeric_id(self) -> None:
        """回归：裸数字 npc id（如 1）加 npc- 前缀，避免与 act id 跨实体冲突。"""
        payload = _msg('[{"id": 1, "name": "林晚"}]')
        g = _make_generator(_FakeRequests(payload))
        npcs = g.generate_npcs("前提", 2)
        assert npcs[0]["id"] == "npc-1"

    def test_generate_npcs_coerces_list_acts_roles(self) -> None:
        """回归：真实模型把 acts_roles 输出为列表时规整为 dict，避免 pydantic 校验失败。"""
        payload = _msg(
            '[{"name": "林晚", "acts_roles": ["商人", "谈判"], "personality": "谨慎"}]'
        )
        g = _make_generator(_FakeRequests(payload))
        npcs = g.generate_npcs("前提", 2)
        assert npcs[0]["acts_roles"] == {}
        assert npcs[0]["personality"] == ["谨慎"]

    def test_generate_npcs_fallback_warns(self) -> None:
        g = _make_generator(_FakeRequests(_msg("[]")))
        with pytest.warns(UserWarning):
            npcs = g.generate_npcs("前提", 3)
        assert npcs[0]["name"]

    def test_generate_scene_uses_tool_call(self) -> None:
        doc = {
            "id": "s1",
            "title": "灯塔之夜",
            "setting": {"time": "深夜", "place": "灯塔"},
            "events": [
                {"id": "e1", "title": "抵达", "kind": "entry", "description": "抵达灯塔。", "next_event_ids": ["e2"]},
                {"id": "e2", "title": "发现", "kind": "trigger"},
                {"id": "e3", "title": "升级", "kind": "outcome"},
            ],
            "npc_ids": ["npc-1"],
        }
        fake = _FakeRequests(_msg("", tool_calls=_tool_call(json.dumps(doc, ensure_ascii=False))))
        g = _make_generator(fake)
        scene = g.generate_scene("第I幕", "前提", ["npc-1"])
        assert scene["id"] == "s1"
        assert [e["kind"] for e in scene["events"]] == ["entry", "trigger", "outcome"]

    def test_generate_scene_content_json_invalid_kind_coerced(self) -> None:
        doc = {"id": "s1", "title": "场景", "events": [{"title": "事件", "kind": "action"}]}
        g = _make_generator(_FakeRequests(_msg(json.dumps(doc, ensure_ascii=False))))
        scene = g.generate_scene("第I幕", "前提", [])
        assert scene["events"][0]["kind"] == "entry"

    def test_generate_scene_fallback_warns_on_bad_json(self) -> None:
        g = _make_generator(_FakeRequests(_msg("```json\n{broken\n```")))
        with pytest.warns(UserWarning):
            scene = g.generate_scene("第I幕", "前提", [])
        assert scene["id"].startswith("scene-")

    def test_generate_scene_fallback_on_empty_events(self) -> None:
        """回归：场景事件为空时下游 scenes[0]["events"][-1] 会 IndexError，必须回退。"""
        doc = {"id": "s1", "title": "场景", "events": []}
        g = _make_generator(_FakeRequests(_msg(json.dumps(doc, ensure_ascii=False))))
        with pytest.warns(UserWarning):
            scene = g.generate_scene("第I幕", "前提", [])
        assert scene["id"].startswith("scene-")

    def test_generate_scene_fallback_warns_on_missing_title(self) -> None:
        g = _make_generator(_FakeRequests(_msg('{"id": "s1", "events": []}')))
        with pytest.warns(UserWarning):
            scene = g.generate_scene("第I幕", "前提", [])
        assert scene["id"].startswith("scene-")

    def test_build_generator_switch(self) -> None:
        assert isinstance(build_generator(_settings()), OllamaGenerator)
        off = _settings()
        off.llm_enabled = False
        assert isinstance(build_generator(off), DeterministicGenerator)

    def test_implements_generator_protocol(self) -> None:
        assert isinstance(_make_generator(_FakeRequests(_msg("ok"))), Generator)

    def test_generate_acts_410_warns_with_root_cause(self) -> None:
        """回归（G5 复审）：HTTPError(410 模型退役) 时告警必须携带类型与状态码，不吞栈。"""
        import httpx
        err = httpx.HTTPStatusError(
            "Model retired", request=httpx.Request("POST", "http://localhost:11434/v1/chat/completions"),
            response=httpx.Response(410, request=httpx.Request("POST", "http://localhost:11434/v1/chat/completions")),
        )
        g = _make_generator(_FakeRequests(err), max_retries=1)
        with pytest.warns(UserWarning) as rec:
            acts = g.generate_acts("前提", 2)
        assert acts  # 降级回退仍产出
        joined = " ".join(str(w.message) for w in rec.list)
        assert "HTTPStatusError" in joined and "410" in joined and "Model retired" in joined

    def test_norm_npcs_coerces_list_archetype(self) -> None:
        """回归（真实模组实验发现）：LLM 返回 archetype 为列表（如 ['Ghost']）时，
        _norm_npcs 必须规整为 str，否则 pydantic 校验失败中断整条管线（非优雅降级）。"""
        payload = _msg('[{"name": "伯纳德", "archetype": ["Ghost"], "personality": ["阴沉"], "description": "佣兵"}]')
        g = _make_generator(_FakeRequests(payload))
        npcs = g.generate_npcs("1649 年爱尔兰", 1)
        assert len(npcs) == 1
        assert isinstance(npcs[0]["archetype"], str), f"archetype 必须是 str: {npcs[0]['archetype']!r}"
        assert npcs[0]["archetype"] == "Ghost" or npcs[0]["archetype"] == "Ghost, 佣兵"


    def test_api_key_sets_authorization_header(self) -> None:
        """云端 API：settings.api_key 存在时请求必须带 Authorization: Bearer 头（2026-08-11 接入）。"""
        s = _settings()
        s.api_key = "sk-test1234567890"
        fake = _FakeRequests(_msg('[{"name": "伯纳德", "archetype": "佣兵"}]'))
        g = _make_generator(fake)
        g.settings = s
        g.generate_npcs("前提", 1)
        assert fake.calls, "无请求发出"
        assert fake.calls[0]["headers"].get("Authorization") == "Bearer sk-test1234567890"

    def test_no_api_key_no_auth_header(self) -> None:
        s = _settings()
        s.api_key = ""
        fake = _FakeRequests(_msg('[{"name": "伯纳德"}]'))
        g = _make_generator(fake)
        g.settings = s
        g.generate_npcs("前提", 1)
        assert "Authorization" not in fake.calls[0]["headers"]

    def test_module_context_injected_into_prompt(self) -> None:
        """模组全文注入：set_module_context 后，请求 prompt 必须携带背景块（loop 迭代改进）。"""
        s = _settings()
        s.api_key = ""
        fake = _FakeRequests(_msg('[{"name": "伯纳德", "archetype": "佣兵"}]'))
        g = _make_generator(fake)
        g.settings = s
        g.set_module_context("1649 年爱尔兰。克伦威尔登陆。地下神庙有旧印。", title="留地不留头")
        g.generate_npcs("前提", 1)
        prompt = fake.calls[0]["json"]["messages"][0]["content"]
        assert "留地不留头" in prompt and "1649 年爱尔兰" in prompt and "背景资料" in prompt

    def test_module_context_truncated_to_limit(self) -> None:
        s = _settings()
        s.api_key = ""
        s.llm_context_chars = 20
        fake = _FakeRequests(_msg('[{"name": "伯纳德"}]'))
        g = _make_generator(fake)
        g.settings = s
        g.set_module_context("A" * 500, title="T")
        g.generate_npcs("前提", 1)
        prompt = fake.calls[0]["json"]["messages"][0]["content"]
        assert len(g._module_context) <= 20

    def test_norm_acts_canonicalizes_llm_ids(self) -> None:
        """回归（云端 DeepSeek 实验发现）：LLM 返回 act_1/ActOne 等任意 id → 规范为 act-{n}，
        保证前端/测试/引用的 id 契约（act-N-scene-N-ev-N）不依赖 LLM 输出。"""
        payload = _msg('[{"id": "act_1", "title": "德罗赫达的阴影", "scene_titles": ["A"]},'
                       '{"id": "ActTwo", "title": "蛇的回归", "scene_titles": ["B"]}]')
        g = _make_generator(_FakeRequests(payload))
        acts = g.generate_acts("前提", 2)
        assert [a["id"] for a in acts] == ["act-1", "act-2"]
