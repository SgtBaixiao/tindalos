"""零依赖配置模块：手动 dataclass Settings（禁止 pydantic-settings）。

全部字段仅依赖标准库（dataclass / os.environ / pathlib），环境变量在实例化时读取。
数值字段经 `_env_float/_env_int` 健壮解析：垃圾值抛带变量名的 ValueError（明确报错，
不静默吞掉）；布尔字段经 `_env_bool` 宽容解析（1/true/yes/on 均视为开）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


# ---- 环境解析辅助 -------------------------------------------------------------


def _env_bool(name: str, default: bool = False) -> bool:
    """宽容布尔解析：1/true/yes/on 视为真（大小写不敏感）；空值用默认。"""
    val = os.environ.get(name)
    if val is None or not val.strip():
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    """健壮浮点解析：垃圾值抛带变量名的 ValueError（否则 CLI 只见裸 TypeError）。"""
    val = os.environ.get(name)
    if val is None or not val.strip():
        return default
    try:
        return float(val)
    except ValueError:
        raise ValueError(f"环境变量 {name}={val!r} 不是合法数字") from None


def _env_int(name: str, default: int) -> int:
    """健壮整数解析：同 _env_float。"""
    val = os.environ.get(name)
    if val is None or not val.strip():
        return default
    try:
        return int(val)
    except ValueError:
        raise ValueError(f"环境变量 {name}={val!r} 不是合法整数") from None


def _strip_chat_suffix(base: str) -> str:
    """兼容旧 `TINDALOS_VL_BASE` 带 `/chat/completions` 全端点的写法：
    LLMClient 统一按 base + `/chat/completions` 拼路径，全端点写法须剥掉后缀。"""
    suffix = "/chat/completions"
    if base.endswith(suffix):
        return base[: -len(suffix)]
    return base


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


def _default_model() -> str:
    """模型名默认值按端点推断：显式 TINDALOS_MODEL 优先；本地 Ollama（localhost/
    127.0.0.1）默认 deepseek-r1（README 本地默认，Ollama 无 deepseek-chat 这个 tag，
    旧默认会 404 → 整体回退确定性模板）；其余（云端）默认 deepseek-chat。"""
    if m := os.environ.get("TINDALOS_MODEL"):
        return m
    base = _default_api_base()
    if "localhost" in base or "127.0.0.1" in base:
        return "deepseek-r1"
    return "deepseek-chat"


# ---- VL（视觉）配置 ------------------------------------------------------------

# 默认端点/模型跟随当前主力供应商（SiliconFlow 硅基流动；OpenAI 兼容）。
# 换供应商只改 TINDALOS_VL_BASE / TINDALOS_VL_MODEL 两个 env，无需改代码。
_VL_DEFAULT_BASE = "https://api.siliconflow.cn/v1"
_EMBED_DEFAULT_BASE = "https://api.siliconflow.cn/v1"


def _vl_key() -> str:
    """VL key：规范名 TINDALOS_VL_KEY 优先；TINDALOS_DASHSCOPE_KEY / DASHSCOPE_API_KEY
    为历史厂商命名兼容回退（曾用于阿里 DashScope，键值现仍被 SiliconFlow 复用）。"""
    return (
        os.environ.get("TINDALOS_VL_KEY")
        or os.environ.get("TINDALOS_DASHSCOPE_KEY")
        or os.environ.get("DASHSCOPE_API_KEY", "")
    )


def _vl_model() -> str:
    return os.environ.get("TINDALOS_VL_MODEL", "Qwen/Qwen3-VL-8B-Instruct")


def _vl_base() -> str:
    return _strip_chat_suffix(os.environ.get("TINDALOS_VL_BASE", _VL_DEFAULT_BASE))


def _embed_key() -> str:
    return os.environ.get("TINDALOS_EMBED_KEY") or _vl_key()


def _embed_model() -> str:
    return os.environ.get("TINDALOS_EMBED_MODEL", "BAAI/bge-m3")


def _embed_base() -> str:
    return os.environ.get("TINDALOS_EMBED_BASE", _EMBED_DEFAULT_BASE)


@dataclass
class Settings:
    """Tindalos 运行配置：LLM 端点、模型开关与本地数据目录。

    API 模式（2026-08-11）：ollama_base_url 现指向任意 OpenAI 兼容端点
    （本地 Ollama / DeepSeek / 智谱 GLM / 通义 Qwen / 月暗 Kimi 均可），
    api_key 提供时自动带 Authorization: Bearer 头。

    统一客户端（2026-08-16）：VL / embedding 六字段也收进 Settings（不再由
    vision.py / rag.py 直读 os.environ），并受 `llm_enabled` 门控联动。
    """

    # OpenAI 兼容端点（本地 Ollama 或任一国产云 API：https://api.deepseek.com/v1 等）
    ollama_base_url: str = field(default_factory=_default_api_base)
    # API key（云端点必填；本地 Ollama 可空）。读取 TINDALOS_API_KEY，缺省回退 DEEPSEEK_API_KEY
    api_key: str = field(
        default_factory=lambda: os.environ.get("TINDALOS_API_KEY") or os.environ.get("DEEPSEEK_API_KEY", "")
    )
    # LLM 模型名（按端点推断：本地 Ollama → deepseek-r1，云端 → deepseek-chat；
    # 显式 TINDALOS_MODEL 优先。其余国产云模型如 glm-4-plus / qwen-plus 靠 env 指定）
    model: str = field(default_factory=_default_model)
    # LLM 总开关：TINDALOS_LLM_ENABLED ∈ 1/true/yes/on（大小写不敏感）才启用（默认离线确定性路径）
    llm_enabled: bool = field(default_factory=lambda: _env_bool("TINDALOS_LLM_ENABLED", False))
    # LLM 请求超时（秒，需容纳慢模型冷启动/长推理）
    llm_timeout: float = field(default_factory=lambda: _env_float("TINDALOS_LLM_TIMEOUT", 180))
    # Eval 预检预算上限（USD）：LLM 层（L3/L4）调用前按 worst-case 估算，
    # 超限降级跳过（设计文档 §4.3 预算门）。EVAL_MAX_USD 默认 $2
    eval_max_usd: float = field(default_factory=lambda: _env_float("EVAL_MAX_USD", 2.0))
    # LLM 请求重试次数（网络抖动 / 5xx / 429 时重试；4xx 不重试）
    llm_max_retries: int = field(default_factory=lambda: _env_int("TINDALOS_LLM_MAX_RETRIES", 2))
    # 模组全文注入上限（字符）：generate 时把模组正文作为背景上下文给 LLM，
    # 让剧本真正基于模组内容（loop 迭代改进，2026-08-11）；0 = 不注入
    llm_context_chars: int = field(default_factory=lambda: _env_int("TINDALOS_LLM_CONTEXT", 12000))
    # 风格与设计规范注入开关（TINDALOS_STYLE_GUIDE，默认 '1' = 开）：
    # 生成时把 references/style-guide.md（洛氏恐怖风格 + KP 把控 + 剧情设计，
    # 源自守秘人规则书与官方模组）注入所有 LLM prompt，强化语言风格与任务把控；
    # 文件缺失或开关关闭时静默跳过，不影响生成。
    style_guide_enabled: bool = field(default_factory=lambda: _env_bool("TINDALOS_STYLE_GUIDE", True))
    # 风格规范文件路径（相对仓库根或绝对路径）
    style_guide_path: Path = field(
        default_factory=lambda: Path(
            os.environ.get("TINDALOS_STYLE_GUIDE_PATH", "references/style-guide.md")
        )
    )
    # LangGraph SqliteSaver checkpoint 目录
    checkpoint_dir: Path = field(default_factory=lambda: Path("data/checkpoints"))
    # 跨会话记忆 store 落盘目录（memory.build_store 消费：可写时 SqliteStore 落盘）
    store_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("TINDALOS_DATA_DIR", "data")) / "store"
    )

    # --- 视觉（VL）端点：SiliconFlow Qwen3-VL 多模态识别（OpenAI 兼容） ---
    # 端点 base（旧 TINDALOS_VL_BASE 带 /chat/completions 后缀的写法自动剥除）；
    # key 用规范名 TINDALOS_VL_KEY（DashScope 旧名 TINDALOS_DASHSCOPE_KEY 兼容回退）
    vl_base: str = field(default_factory=_vl_base)
    vl_model: str = field(default_factory=_vl_model)
    vl_key: str = field(default_factory=_vl_key)
    # --- 向量 embedding 端点：SiliconFlow bge-m3（独立 base/model/key 覆盖） ---
    embed_base: str = field(default_factory=_embed_base)
    embed_model: str = field(default_factory=_embed_model)
    embed_key: str = field(default_factory=_embed_key)


_settings: Settings | None = None


def get_settings() -> Settings:
    """返回进程级单例 Settings；首次调用时按当前环境变量构造一次。"""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
