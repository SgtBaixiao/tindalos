"""配置回归测试：云端 API 模式默认端点解析（调试会话发现的真实缺陷）。

背景：只设 TINDALOS_API_KEY、不设 TINDALOS_API_BASE 时，旧实现把 ollama_base_url
兜底到本地 Ollama（localhost:11434/v1），导致生成器连 Ollama 失败后整体回退确定性
模板（2 幕/4 场景/12 事件）——而 organize_module.py / README 都宣称默认 DeepSeek。
修复：解析顺序改为 TINDALOS_API_BASE → OLLAMA_BASE_URL → 检测到 key 时默认 DeepSeek
→ 兜底本地 Ollama（保留「无任何 env = 本地 Ollama」的旧路径）。
"""

from tindalos.config import Settings


def test_key_set_no_base_defaults_to_deepseek(monkeypatch):
    """只设 key、不设 base → 应解析到 DeepSeek（本缺陷的回归锚点）。"""
    monkeypatch.delenv("TINDALOS_API_BASE", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.setenv("TINDALOS_API_KEY", "sk-test")
    assert Settings().ollama_base_url == "https://api.deepseek.com/v1"


def test_no_key_no_base_defaults_to_local_ollama(monkeypatch):
    """无 key 无 base → 仍默认本地 Ollama（旧路径不受影响）。"""
    monkeypatch.delenv("TINDALOS_API_BASE", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.delenv("TINDALOS_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert Settings().ollama_base_url == "http://localhost:11434/v1"


def test_explicit_api_base_wins(monkeypatch):
    """显式 TINDALOS_API_BASE 优先级最高（换端点即换模型）。"""
    monkeypatch.setenv("TINDALOS_API_BASE", "https://api.moonshot.cn/v1")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("TINDALOS_API_KEY", "sk-test")
    assert Settings().ollama_base_url == "https://api.moonshot.cn/v1"


def test_ollama_base_url_wins_over_inferred_deepseek(monkeypatch):
    """显式 OLLAMA_BASE_URL 优先于「有 key 推断云端」。"""
    monkeypatch.delenv("TINDALOS_API_BASE", raising=False)
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("TINDALOS_API_KEY", "sk-test")
    assert Settings().ollama_base_url == "http://localhost:11434/v1"


def test_deepseek_key_fallback_when_tindalos_key_missing(monkeypatch):
    """DEEPSEEK_API_KEY 缺省回退同样触发云端默认。"""
    monkeypatch.delenv("TINDALOS_API_BASE", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.delenv("TINDALOS_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-legacy")
    assert Settings().ollama_base_url == "https://api.deepseek.com/v1"
