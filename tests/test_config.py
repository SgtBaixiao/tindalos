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


# ---- 统一客户端新增字段（vl / embed / 健壮解析 / 宽容布尔） ----


def test_llm_enabled_tolerant_boolean(monkeypatch):
    """TINDALOS_LLM_ENABLED 宽容解析：1/true/yes/on 视为开，其余视为关。"""
    for truthy in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv("TINDALOS_LLM_ENABLED", truthy)
        assert Settings().llm_enabled is True, truthy
    for falsy in ("0", "false", "off", "no", "", "abc"):
        monkeypatch.setenv("TINDALOS_LLM_ENABLED", falsy)
        assert Settings().llm_enabled is False, falsy


def test_numeric_garbage_raises_clear_error(monkeypatch):
    """数值字段垃圾值抛带变量名的 ValueError（而非裸 TypeError 击穿 CLI）。"""
    monkeypatch.setenv("TINDALOS_LLM_TIMEOUT", "abc")
    try:
        Settings()
        raise AssertionError("应当抛 ValueError")
    except ValueError as e:
        assert "TINDALOS_LLM_TIMEOUT" in str(e)
    monkeypatch.delenv("TINDALOS_LLM_TIMEOUT")
    monkeypatch.setenv("TINDALOS_LLM_MAX_RETRIES", "x")
    try:
        Settings()
        raise AssertionError("应当抛 ValueError")
    except ValueError as e:
        assert "TINDALOS_LLM_MAX_RETRIES" in str(e)


def test_numeric_env_valid_values(monkeypatch):
    """数值字段 env 生效。"""
    monkeypatch.setenv("TINDALOS_LLM_TIMEOUT", "30.5")
    monkeypatch.setenv("TINDALOS_LLM_MAX_RETRIES", "4")
    monkeypatch.setenv("TINDALOS_LLM_CONTEXT", "5000")
    s = Settings()
    assert s.llm_timeout == 30.5
    assert s.llm_max_retries == 4
    assert s.llm_context_chars == 5000


def test_model_default_inferred_from_endpoint(monkeypatch):
    """默认 model 按端点推断：本地 Ollama → deepseek-r1，云端 → deepseek-chat，显式 env 优先。"""
    # 本地（无任何 env）
    monkeypatch.delenv("TINDALOS_API_BASE", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.delenv("TINDALOS_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("TINDALOS_MODEL", raising=False)
    assert Settings().model == "deepseek-r1"
    # 云端（有 key）
    monkeypatch.setenv("TINDALOS_API_KEY", "sk-test")
    assert Settings().model == "deepseek-chat"
    # 显式 TINDALOS_MODEL 优先
    monkeypatch.setenv("TINDALOS_MODEL", "glm-4-plus")
    assert Settings().model == "glm-4-plus"


def test_vl_defaults(monkeypatch):
    """VL 默认：SiliconFlow 兼容 base、Qwen3-VL-8B、key 空。"""
    monkeypatch.delenv("TINDALOS_VL_BASE", raising=False)
    monkeypatch.delenv("TINDALOS_VL_MODEL", raising=False)
    monkeypatch.delenv("TINDALOS_VL_KEY", raising=False)
    monkeypatch.delenv("TINDALOS_DASHSCOPE_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    s = Settings()
    assert s.vl_base == "https://api.siliconflow.cn/v1"
    assert s.vl_model == "Qwen/Qwen3-VL-8B-Instruct"
    assert s.vl_key == ""


def test_vl_base_full_endpoint_suffix_stripped(monkeypatch):
    """旧 TINDALOS_VL_BASE 带 /chat/completions 全端点写法自动剥除后缀。"""
    monkeypatch.setenv("TINDALOS_VL_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions")
    assert Settings().vl_base == "https://dashscope.aliyuncs.com/compatible-mode/v1"


def test_vl_env_overrides(monkeypatch):
    """VL env 覆盖生效；key 规范名 TINDALOS_VL_KEY 优先，旧名逐级回退。"""
    monkeypatch.setenv("TINDALOS_VL_BASE", "https://vllm.example.test/v1")
    monkeypatch.setenv("TINDALOS_VL_MODEL", "qwen2.5-vl-7b")
    # 规范名 TINDALOS_VL_KEY 优先于 DashScope 旧名
    monkeypatch.setenv("TINDALOS_VL_KEY", "sk-vl")
    monkeypatch.setenv("TINDALOS_DASHSCOPE_KEY", "sk-dash")
    s = Settings()
    assert s.vl_base == "https://vllm.example.test/v1"
    assert s.vl_model == "qwen2.5-vl-7b"
    assert s.vl_key == "sk-vl"
    # 未设规范名 → 回退旧名 TINDALOS_DASHSCOPE_KEY
    monkeypatch.delenv("TINDALOS_VL_KEY", raising=False)
    assert Settings().vl_key == "sk-dash"
    # 再回退 DASHSCOPE_API_KEY
    monkeypatch.delenv("TINDALOS_DASHSCOPE_KEY", raising=False)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-legacy-dash")
    assert Settings().vl_key == "sk-legacy-dash"


def test_embed_env_overrides(monkeypatch):
    """embedding env 覆盖生效；key 独立覆盖优先，缺省回退 VL key。"""
    monkeypatch.setenv("TINDALOS_EMBED_BASE", "https://embed.example.test/v1")
    monkeypatch.setenv("TINDALOS_EMBED_MODEL", "text-embedding-v3")
    monkeypatch.setenv("TINDALOS_EMBED_KEY", "sk-embed")
    monkeypatch.setenv("TINDALOS_DASHSCOPE_KEY", "sk-vl")
    s = Settings()
    assert s.embed_base == "https://embed.example.test/v1"
    assert s.embed_model == "text-embedding-v3"
    assert s.embed_key == "sk-embed"
    # 未设独立 embed key → 回退 VL key
    monkeypatch.delenv("TINDALOS_EMBED_KEY", raising=False)
    assert Settings().embed_key == "sk-vl"
