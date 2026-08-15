"""零依赖配置模块：手动 dataclass Settings（禁止 pydantic-settings）。

全部字段仅依赖标准库（dataclass / os.environ / pathlib），环境变量在实例化时读取。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _default_api_base() -> str:
    """OpenAI 兼容端点默认值（实例化时按环境解析一次）。

    优先级：TINDALOS_API_BASE → OLLAMA_BASE_URL → 检测到云端 API key 时默认
    DeepSeek → 兜底本地 Ollama。修复回归：只设 TINDALOS_API_KEY、未设 base 时，
    旧实现兜底到 localhost:11434/v1，生成器连 Ollama 失败后整体回退确定性模板
    （2 幕/4 场景/12 事件）——而 organize_module.py / README 默认 DeepSeek。
    """
    if base := os.environ.get("TINDALOS_API_BASE"):
        return base
    if base := os.environ.get("OLLAMA_BASE_URL"):
        return base
    if os.environ.get("TINDALOS_API_KEY") or os.environ.get("DEEPSEEK_API_KEY"):
        return "https://api.deepseek.com/v1"
    return "http://localhost:11434/v1"


@dataclass
class Settings:
    """Tindalos 运行配置：LLM 端点、模型开关与本地数据目录。

    API 模式（2026-08-11）：ollama_base_url 现指向任意 OpenAI 兼容端点
    （本地 Ollama / DeepSeek / 智谱 GLM / 通义 Qwen / 月暗 Kimi 均可），
    api_key 提供时自动带 Authorization: Bearer 头。
    """

    # OpenAI 兼容端点（本地 Ollama 或任一国产云 API：https://api.deepseek.com/v1 等）
    ollama_base_url: str = field(default_factory=_default_api_base)
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
    # 风格与设计规范注入开关（TINDALOS_STYLE_GUIDE，默认 '1' = 开）：
    # 生成时把 references/style-guide.md（洛氏恐怖风格 + KP 把控 + 剧情设计，
    # 源自守秘人规则书与官方模组）注入所有 LLM prompt，强化语言风格与任务把控；
    # 文件缺失或开关关闭时静默跳过，不影响生成。
    style_guide_enabled: bool = field(
        default_factory=lambda: os.environ.get("TINDALOS_STYLE_GUIDE", "1") == "1"
    )
    # 风格规范文件路径（相对仓库根或绝对路径）
    style_guide_path: Path = field(
        default_factory=lambda: Path(
            os.environ.get("TINDALOS_STYLE_GUIDE_PATH", "references/style-guide.md")
        )
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
