"""零依赖配置模块：手动 dataclass Settings（禁止 pydantic-settings）。

全部字段仅依赖标准库（dataclass / os.environ / pathlib），环境变量在实例化时读取。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Settings:
    """Tindalos 运行配置：LLM 端点、模型开关与本地数据目录。

    API 模式（2026-08-11）：ollama_base_url 现指向任意 OpenAI 兼容端点
    （本地 Ollama / DeepSeek / 智谱 GLM / 通义 Qwen / 月暗 Kimi 均可），
    api_key 提供时自动带 Authorization: Bearer 头。
    """

    # OpenAI 兼容端点（本地 Ollama 或任一国产云 API：https://api.deepseek.com/v1 等）
    ollama_base_url: str = field(
        default_factory=lambda: os.environ.get("TINDALOS_API_BASE", os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1"))
    )
    # API key（云端点必填；本地 Ollama 可空）。读取 TINDALOS_API_KEY，缺省回退 DEEPSEEK_API_KEY
    api_key: str = field(
        default_factory=lambda: os.environ.get("TINDALOS_API_KEY") or os.environ.get("DEEPSEEK_API_KEY", "")
    )
    # LLM 模型名（DeepSeek 云：deepseek-chat；智谱：glm-4-plus；Qwen：qwen-plus 等）
    model: str = field(default_factory=lambda: os.environ.get("TINDALOS_MODEL", "deepseek-chat"))
    # LLM 总开关：TINDALOS_LLM_ENABLED == '1' 才启用（默认离线确定性路径）
    llm_enabled: bool = field(
        default_factory=lambda: os.environ.get("TINDALOS_LLM_ENABLED", "0") == "1"
    )
    # LLM 请求超时（秒，需容纳慢模型冷启动/长推理）
    llm_timeout: float = field(
        default_factory=lambda: float(os.environ.get("TINDALOS_LLM_TIMEOUT", "180"))
    )
    # LLM 请求重试次数（网络抖动 / 5xx / 429 时重试；4xx 不重试）
    llm_max_retries: int = field(
        default_factory=lambda: int(os.environ.get("TINDALOS_LLM_MAX_RETRIES", "2"))
    )
    # 模组全文注入上限（字符）：generate 时把模组正文作为背景上下文给 LLM，
    # 让剧本真正基于模组内容（loop 迭代改进，2026-08-11）；0 = 不注入
    llm_context_chars: int = field(
        default_factory=lambda: int(os.environ.get("TINDALOS_LLM_CONTEXT", "12000"))
    )
    # LangGraph SqliteSaver checkpoint 目录
    checkpoint_dir: Path = field(default_factory=lambda: Path("data/checkpoints"))
    # 跨会话记忆 store 落盘目录（memory.build_store 消费：可写时 SqliteStore 落盘）
    store_dir: Path = field(default_factory=lambda: Path("data/store"))


_settings: Settings | None = None


def get_settings() -> Settings:
    """返回进程级单例 Settings；首次调用时按当前环境变量构造一次。"""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
