"""tindalos doctor：LLM 连通性自检（task #18）。只读，不写任何数据、不改任何配置。

四路探测：
  ① 配置检查（静态，不联网）：LLM 开关 / 各端点 key 与 base 是否存在 → 提示；
  ② 主 LLM chat ping：/chat/completions 最小请求；
  ③ 视觉 VL classify：1px 测试 PNG → (mime, bytes) → LLMClient.classify_image；
  ④ 向量 embedding：/embeddings 最小请求。

每路失败按 无 key / key 无效(4xx) / 端点不通(连接/超时) / requests 未安装 等
给出**层级化中文提示**（绝不内嵌上游错误正文，防泄露）；退出码为位掩码，可按位判断哪路失败：

  bit0=chat 失败  bit1=vision 失败  bit2=embed 失败   （0 = 全部通过）

用法：`tindalos doctor`（cli.py 接线，见 cli.py doctor_command）。transport= 参数供测试
注入 FakeTransport（零网络）；生产缺省走 requests。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Optional

from tindalos.config import Settings, get_settings
from tindalos.llm import LLMClient, LLMError

# 退出码位掩码
BIT_CHAT = 1 << 0
BIT_VISION = 1 << 1
BIT_EMBED = 1 << 2

_TIMEOUT = 15.0  # 每路探测短超时，快速返回；CLI 可用 --timeout 覆盖


def _tiered_hint(exc: BaseException, key_present: bool, base: str) -> str:
    """按失败类型给层级化中文提示（绝不内嵌上游错误正文/密钥）。"""
    if isinstance(exc, LLMError):
        k = exc.kind
        if k == "no_requests":
            return "requests 库未安装：pip install requests"
        if k == "http_4xx":
            if not key_present:
                return f"端点 {base} 返回 4xx 且未配置 API key——先设置对应 key 环境变量"
            return f"端点 {base} 返回 4xx——API key 无效/过期/额度不足，检查 key 环境变量"
        if k == "http_429":
            return f"端点 {base} 429 限流——稍后重试或检查配额"
        if k == "http_5xx":
            return f"端点 {base} 5xx——服务端故障，稍后重试"
        if k in ("connection", "timeout"):
            return f"连不上 {base}——检查网络 / 对应 BASE 环境变量 / 本地 Ollama 服务是否在跑"
        if k == "parse_failed":
            return f"端点 {base} 响应无法解析——服务可达但响应异常"
        return f"请求失败（{k}）"
    return f"意外错误：{type(exc).__name__}: {exc}"


def _config_check(settings: Settings, out: Callable[[str], None]) -> None:
    """① 静态配置检查：开关与各端点 key/base 是否存在（不联网）。"""
    out(f"  LLM 开关   : {'开（TINDALOS_LLM_ENABLED=1）' if settings.llm_enabled else '关（离线确定性路径；如需 LLM 设 TINDALOS_LLM_ENABLED=1）'}")
    out(f"  主端点     : {settings.ollama_base_url}" + ("（未配 key，本地 Ollama 可无 key）" if not settings.api_key else "（已配 key）"))
    out(f"  视觉端点   : {settings.vl_base}" + ("（未配 key）" if not settings.vl_key else "（已配 key）"))
    out(f"  向量端点   : {settings.embed_base}" + ("（未配 key）" if not settings.embed_key else "（已配 key）"))


def _probe(settings: Settings, transport: Any, timeout: float, base: str, key_present: bool, fn: Callable[[LLMClient], tuple[bool, str]]) -> tuple[bool, str]:
    """通用单路探测：LLMClient(settings, transport) 注入 → 捕获一切异常给层级提示。"""
    client = LLMClient(settings, transport=transport)
    try:
        return fn(client, timeout)
    except Exception as e:  # noqa: BLE001 —— 连通性自检必须捕获一切失败并给提示
        return False, _tiered_hint(e, key_present, base)


def _chat_probe(client: LLMClient, timeout: float) -> tuple[bool, str]:
    """② 主 LLM chat ping。"""
    text = client.chat([{"role": "user", "content": "ping"}], timeout=timeout)
    ok = bool(text and text.strip())
    return ok, f"chat → {text.strip()[:40]!r}"


def _vision_probe(client: LLMClient, timeout: float) -> tuple[bool, str]:
    """③ 视觉 VL classify：64×64 测试 PNG → (mime, bytes) 走 LLMClient.classify_image。

    64×64 满足主流 VL 模型最小边长要求（Qwen3-VL 需 ≥28px；2×2 会被 SiliconFlow 400
    拒绝）。doctor 直连 transport，不经 vision._ensure_min_side 预处理，故用真实尺寸。
    """
    try:
        from PIL import Image
    except ImportError:
        return False, "PIL 未安装：pip install Pillow"
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        path = Path(f.name)
    try:
        Image.new("RGB", (64, 64), (60, 80, 120)).save(path, format="PNG")
        data = path.read_bytes()
        text = client.classify_image(("image/png", data), "请识别这张图像。", timeout=timeout)
        ok = bool(text and text.strip())
        return ok, f"VL → {text.strip()[:40]!r}"
    finally:
        try:
            path.unlink(missing_ok=True)
        except OSError:  # noqa: S110 —— 临时文件清理失败不阻断自检
            pass


def _embed_probe(client: LLMClient, timeout: float) -> tuple[bool, str]:
    """④ 向量 embedding 最小请求。"""
    vecs = client.embed(["ping"], timeout=timeout)
    ok = len(vecs) == 1 and len(vecs[0]) > 0
    return ok, f"embed → {len(vecs)} 条 × {len(vecs[0])} 维"


def run_doctor(settings: Optional[Settings] = None, *, transport: Any = None, timeout: float = _TIMEOUT) -> int:
    """执行四路自检，返回位掩码退出码（0 = 全部通过）。只读。

    transport= 注入 FakeTransport（测试零网络）；缺省生产 requests。输出打到 stdout。
    """
    settings = settings if settings is not None else get_settings()
    # Windows 控制台默认 GBK 无法编码 ✓/✗ → 强制 UTF-8（与 scripts/verify_llm.py 同款）
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 —— 非 Windows / 无 reconfigure 时忽略
        pass
    out = print
    out("Tindalos LLM 连通性自检（只读，不写数据）\n")

    out("① 配置检查")
    _config_check(settings, out)

    out("\n② 主 LLM chat")
    ok_c, note_c = _probe(settings, transport, timeout, settings.ollama_base_url, bool(settings.api_key), _chat_probe)
    out(f"  {'✓' if ok_c else '✗'} {note_c}")

    out("\n③ 视觉 VL classify")
    ok_v, note_v = _probe(settings, transport, timeout, settings.vl_base, bool(settings.vl_key), _vision_probe)
    out(f"  {'✓' if ok_v else '✗'} {note_v}")

    out("\n④ 向量 embedding")
    ok_e, note_e = _probe(settings, transport, timeout, settings.embed_base, bool(settings.embed_key), _embed_probe)
    out(f"  {'✓' if ok_e else '✗'} {note_e}")

    code = (0 if ok_c else BIT_CHAT) | (0 if ok_v else BIT_VISION) | (0 if ok_e else BIT_EMBED)
    if code == 0:
        out("\n结果：全部通过")
    else:
        failed = []
        if code & BIT_CHAT:
            failed.append("chat")
        if code & BIT_VISION:
            failed.append("vision")
        if code & BIT_EMBED:
            failed.append("embed")
        out(f"\n结果：有失败（退出码 {code}：" + "、".join(failed) + "）")
    return code
