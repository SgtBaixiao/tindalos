"""风格规范注入测试（t11-style-guide）：references/style-guide.md 注入所有 LLM prompt。

覆盖：
- 开关开启 + 文件存在：_ctx 含风格规范标题与正文；
- 文件缺失：静默跳过（不注入、不报错）；
- 开关关闭：不注入；
- 与模组背景共存：风格规范在前、模组背景在后；
- 截断上限：超长规范截断至 6000 字符（控制 token 成本）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tindalos.config import Settings
from tindalos.generator import OllamaGenerator


class _FakeResp:
    def __init__(self, payload) -> None:
        self._payload = payload
        self.status_code = 200

    def raise_for_status(self) -> None:
        pass

    def json(self):
        return self._payload


class _FakeRequests:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers or {}, "timeout": timeout})
        return _FakeResp({"choices": [{"message": {"content": '{"ok": 1}'}}]})


def _settings(style_enabled: bool, style_path: str) -> Settings:
    s = Settings()
    s.ollama_base_url = "http://localhost:11434/v1"
    s.llm_enabled = True
    s.style_guide_enabled = style_enabled
    s.style_guide_path = Path(style_path)
    return s


def _ctx(style_enabled: bool, style_path: str) -> str:
    fake = _FakeRequests()
    g = OllamaGenerator(_settings(style_enabled, style_path))
    g._requests = fake
    g._chat("hi")
    return fake.calls[0]["json"]["messages"][0]["content"]


class TestStyleGuideInjection:
    def test_injected_when_enabled_and_present(self, tmp_path: Path) -> None:
        guide = tmp_path / "style-guide.md"
        guide.write_text("洛氏恐怖风格：感官压制，克制描写。", encoding="utf-8")
        content = _ctx(True, str(guide))
        assert "Tindalos 风格与设计规范" in content
        assert "洛氏恐怖风格" in content
        assert content.startswith("hi【Tindalos 风格与设计规范")

    def test_skipped_when_file_missing(self, tmp_path: Path) -> None:
        missing = tmp_path / "does-not-exist.md"
        content = _ctx(True, str(missing))
        assert content == "hi"  # 无任何注入，与旧版行为一致

    def test_skipped_when_disabled(self, tmp_path: Path) -> None:
        guide = tmp_path / "style-guide.md"
        guide.write_text("不应被注入的内容。", encoding="utf-8")
        content = _ctx(False, str(guide))
        assert content == "hi"

    def test_coexists_with_module_context(self, tmp_path: Path) -> None:
        guide = tmp_path / "style-guide.md"
        guide.write_text("风格规范正文。", encoding="utf-8")
        fake = _FakeRequests()
        g = OllamaGenerator(_settings(True, str(guide)))
        g._requests = fake
        g.set_module_context("1649 年爱尔兰，克伦威尔登陆。", title="留地不留头")
        g._chat("hi")
        content = fake.calls[0]["json"]["messages"][0]["content"]
        assert content.index("Tindalos 风格与设计规范") < content.index("模组《留地不留头》")
        assert "1649 年爱尔兰" in content

    def test_truncated_to_6000_chars(self, tmp_path: Path) -> None:
        guide = tmp_path / "style-guide.md"
        guide.write_text("规" * 9000, encoding="utf-8")
        content = _ctx(True, str(guide))
        assert "规" * 6000 in content
        assert len(content) < 7000  # 9000 字被截断至 6000 + 标题

    def test_default_settings_point_to_repo_guide(self) -> None:
        """默认配置应指向仓库内的 references/style-guide.md 且存在（随项目分发）。"""
        s = Settings()
        repo_guide = Path(__file__).resolve().parent.parent / s.style_guide_path
        assert repo_guide.is_file()
