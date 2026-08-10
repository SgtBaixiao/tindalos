"""零依赖配置模块：手动 dataclass Settings（禁止 pydantic-settings）。

全部字段仅依赖标准库（dataclass / os.environ / pathlib），环境变量在实例化时读取。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Settings:
    """Tindalos 运行配置：LLM 端点、模型开关与本地数据目录。"""

    # OpenAI 兼容端点（Ollama 本地服务）
    ollama_base_url: str = field(
        default_factory=lambda: os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")
    )
    # LLM 模型名
    model: str = field(default_factory=lambda: os.environ.get("TINDALOS_MODEL", "deepseek-r1"))
    # LLM 总开关：TINDALOS_LLM_ENABLED == '1' 才启用（默认离线确定性路径）
    llm_enabled: bool = field(
        default_factory=lambda: os.environ.get("TINDALOS_LLM_ENABLED", "0") == "1"
    )
    # LangGraph SqliteSaver checkpoint 目录
    checkpoint_dir: Path = field(default_factory=lambda: Path("data/checkpoints"))
    # LangGraph InMemoryStore 持久化目录
    store_dir: Path = field(default_factory=lambda: Path("data/store"))


_settings: Settings | None = None


def get_settings() -> Settings:
    """返回进程级单例 Settings；首次调用时按当前环境变量构造一次。"""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
