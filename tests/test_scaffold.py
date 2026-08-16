"""tindalos 包骨架契约测试（task t1-scaffold）。

覆盖验收：
- import tindalos 成功（沙箱内 python -c 'import tindalos' 的等价验证）
- __version__ == '0.1.0'
- get_settings() 默认值正确（llm_enabled False、ollama_base_url 默认、model 默认、checkpoint/store 目录）
- get_settings() 单例（多次调用返回同一实例）
- 环境变量覆盖（OLLAMA_BASE_URL / TINDALOS_MODEL / TINDALOS_LLM_ENABLED）
"""

from pathlib import Path

import tindalos
import tindalos.config as config_module


def test_import_tindalos():
    """验收：import tindalos 成功且暴露 __version__。"""
    assert tindalos is not None
    assert hasattr(tindalos, "__version__")


def test_version():
    """验收：__version__ 为 '0.1.0'。"""
    assert tindalos.__version__ == "0.1.0"


def test_settings_defaults():
    """验收：config 默认值断言——llm_enabled False、base_url 默认。"""
    config_module._settings = None  # 重置单例，保证按当前环境构造
    s = config_module.get_settings()
    assert s.llm_enabled is False
    assert s.ollama_base_url == "http://localhost:11434/v1"
    assert s.model == "deepseek-chat"  # 云端默认（2026-08-11 改为 DeepSeek）
    assert s.checkpoint_dir == Path("data/checkpoints")
    assert s.store_dir == Path("data/store")


def test_settings_data_dir_override(monkeypatch):
    """TINDALOS_DATA_DIR 生效：store_dir 落到 <dir>/store（与 history/web 一致）。"""
    monkeypatch.setenv("TINDALOS_DATA_DIR", str(Path("var/data").resolve()))
    config_module._settings = None
    s = config_module.get_settings()
    assert s.store_dir == Path("var/data").resolve() / "store"


def test_settings_env_override(monkeypatch):
    """环境变量覆盖生效（default_factory 在实例化时读 env）。"""
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://example.test/v1")
    monkeypatch.setenv("TINDALOS_MODEL", "test-model")
    monkeypatch.setenv("TINDALOS_LLM_ENABLED", "1")
    config_module._settings = None
    s = config_module.get_settings()
    assert s.ollama_base_url == "http://example.test/v1"
    assert s.model == "test-model"
    assert s.llm_enabled is True


def test_settings_singleton():
    """get_settings() 单例：两次调用返回同一实例。"""
    config_module._settings = None
    a = config_module.get_settings()
    b = config_module.get_settings()
    assert a is b
