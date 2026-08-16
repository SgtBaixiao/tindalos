"""tindalos doctor 连通性自检测试：FakeTransport 注入，零网络零 LLM。

覆盖：三路全通 → 退出码 0；chat 网络失败 → bit0 + 连接提示；VL 4xx（无 key）→ bit1
+ 点名 vl_base 的 key 提示；embed no_requests → bit2 + 安装提示；位掩码叠加；层级提示
绝不内嵌上游错误正文（防泄露）。
"""

import json

import pytest

from tindalos.config import Settings
from tindalos.doctor import BIT_CHAT, BIT_EMBED, BIT_VISION, _tiered_hint, run_doctor
from tindalos.llm import LLMError


class FakeResp:
    def __init__(self, status=200, json_data=None, text=""):
        self.status_code = status
        self._json_data = json_data
        self.text = text or (json.dumps(json_data, ensure_ascii=False) if json_data is not None else "")

    def json(self):
        return self._json_data


def ok_chat(content):
    return FakeResp(200, {"choices": [{"message": {"content": content}}]})


class DoctorTransport:
    """按请求形状路由的 fake：chat(纯文本) → pong；multimodal(列表 content) → VL JSON；
    /embeddings → 向量。每个槽位可用 FakeResp 或异常覆盖（异常被 raise）。记录调用。"""

    def __init__(self, chat=None, vision=None, embed=None):
        self.calls = []
        self._chat = chat
        self._vision = vision
        self._embed = embed

    def __call__(self, method, url, *, json=None, headers=None, timeout=None):
        self.calls.append({"method": method, "url": url, "json": json, "headers": headers, "timeout": timeout})
        if url.endswith("/embeddings"):
            action = self._embed
            if action is None:
                action = FakeResp(200, {"data": [{"object": "embedding", "index": i, "embedding": [0.1] * 4} for i in range(len((json or {}).get("input", [])))]})
            if isinstance(action, Exception):
                raise action
            return action
        msgs = (json or {}).get("messages", [])
        multimodal = any(isinstance(m.get("content"), list) for m in msgs)
        if multimodal:
            action = self._vision
            if action is None:
                action = ok_chat('{"kind": "map", "confidence": 0.9}')
        else:
            action = self._chat
            if action is None:
                action = ok_chat("pong")
        if isinstance(action, Exception):
            raise action
        return action


@pytest.fixture
def no_sleep(monkeypatch):
    import tindalos.llm as llm

    monkeypatch.setattr(llm, "_sleep_backoff", lambda attempt: None)


def settings(**over):
    base = dict(
        llm_enabled=True,
        model="deepseek-chat",
        ollama_base_url="http://mock/chat",
        vl_model="qwen3-vl-plus",
        vl_base="http://mock/vl",
        embed_model="text-embedding-v4",
        embed_base="http://mock/embed",
    )
    base.update(over)
    return Settings(**base)


# ---- 三路全通 / 位掩码 ----------------------------------------------------------


def test_doctor_all_pass_exit_zero(capsys):
    t = DoctorTransport()
    code = run_doctor(settings(), transport=t, timeout=2)
    assert code == 0
    out = capsys.readouterr().out
    assert "全部通过" in out
    # 恰好三次最小请求：chat ×2（chat + VL）、embedding ×1
    urls = [c["url"] for c in t.calls]
    assert len(urls) == 3
    assert sum(1 for u in urls if u.endswith("/chat/completions")) == 2
    assert sum(1 for u in urls if u.endswith("/embeddings")) == 1


def test_doctor_chat_failure_sets_bit0(no_sleep, capsys):
    t = DoctorTransport(chat=ConnectionError("连不上"))  # 网络层异常 → kind=connection
    code = run_doctor(settings(), transport=t, timeout=2)
    out = capsys.readouterr().out
    assert code == BIT_CHAT
    assert "连不上" in out
    assert "http://mock/chat" in out


def test_doctor_vision_4xx_sets_bit1(capsys):
    t = DoctorTransport(vision=FakeResp(401, {}, "unauthorized"))
    code = run_doctor(settings(), transport=t, timeout=2)
    assert code == BIT_VISION
    out = capsys.readouterr().out
    assert "4xx" in out
    assert "http://mock/vl" in out  # 点名视觉端点，而非主端点


def test_doctor_embed_no_requests_sets_bit2(capsys):
    t = DoctorTransport(embed=LLMError("requests 未安装", kind="no_requests"))
    code = run_doctor(settings(), transport=t, timeout=2)
    assert code == BIT_EMBED
    assert "pip install requests" in capsys.readouterr().out


def test_doctor_multiple_failures_accumulate_bits(no_sleep, capsys):
    t = DoctorTransport(chat=ConnectionError("down"), embed=LLMError("no", kind="no_requests"))
    code = run_doctor(settings(), transport=t, timeout=2)
    assert code == BIT_CHAT | BIT_EMBED
    out = capsys.readouterr().out
    assert "chat" in out and "embed" in out
    assert "全部通过" not in out


# ---- 层级提示不泄露 ------------------------------------------------------------


def test_tiered_hint_never_leaks_upstream_text():
    secret = "sk-super-secret-12345"
    exc = LLMError(f"LLM 端点 HTTP 401: {secret}", kind="http_4xx", status_code=401)
    hint = _tiered_hint(exc, key_present=True, base="http://mock/chat")
    assert secret not in hint
    assert "key" in hint.lower() or "API key" in hint


def test_tiered_hint_4xx_no_key_advises_setting_key():
    exc = LLMError("HTTP 401", kind="http_4xx")
    assert "未配置 API key" in _tiered_hint(exc, key_present=False, base="http://mock/chat")


def test_tiered_hint_non_llm_error_reports_type():
    hint = _tiered_hint(RuntimeError("boom"), key_present=True, base="http://mock/chat")
    assert "RuntimeError" in hint
    assert "boom" in hint  # 本地异常（非上游响应）可带原文，便于诊断
