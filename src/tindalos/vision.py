"""多模态识别管线（wayfinder ticket 04）：qwen3-vl-plus（DashScope）+ 置信度门槛 + 人工确认。

在线路径（`get_settings().vl_key` 存在时）：
  DashScope OpenAI 兼容 `chat/completions` 端点 + qwen-vl 模型，对提取出的图像
  分类（人物像/地图/场景/封面）+ 人物像提取名字 + 一句话说明，返回结构化 JSON。
  请求统一走 `LLMClient.classify_image`（自动重试 + 错误分类 + data URI 组装）；
  本地图像以魔数嗅探 MIME 后按 (mime, bytes) 交给客户端 base64 编码——**密钥只走 Settings**。

离线路径（无 key / 调用失败 / 超时）：诚实降级——`kind="unknown"`、
`confidence=0`、`needs_confirmation=true`，同时给出**启发式候选标签**（宽高比 + 体量），
由前端人工确认 UI 兜底归类。与全仓"LLM 失败按设计降级"哲学一致：云端识别是增强层，
不是硬依赖。

输出契约 VisionResult：{image_path, page_no, kind, name, caption, confidence,
needs_confirmation}；kind ∈ portrait/map/scene/cover/unknown。confidence 由模型给出
（缺失/非数字按 0），确认门槛 0.7：kind 未知或置信度低于门槛均需人工确认。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from tindalos import llm
from tindalos.config import get_settings
from tindalos.llm import LLMError
from tindalos.pdfio import PdfImageInfo

KIND_VALUES = ("portrait", "map", "scene", "cover", "unknown")

# 在线识别图像大小上限（4MB）：超出则跳过在线识别走离线降级（防大图拖垮请求/成本）
MAX_ONLINE_IMAGE_BYTES = 4 * 1024 * 1024
# 发送前最小短边（px）：Qwen3-VL（SiliconFlow）要求边长 ≥28px，且模块 PDF 可能抽出
# 小图标/装饰图——短边低于此值的小图在发送前按短边等比放大到该值（LANCZOS）。
# 取 64 而非 28 留足供应商差异余量（DashScope 老 qwen-vl 无此限制，放大后仍兼容）。
MIN_ONLINE_IMAGE_SIDE = 64
# 确认门槛：模型置信度低于该值即需人工确认（VisionResult docstring 引用 0.7）
CONFIDENCE_THRESHOLD = 0.7
# name 字段截断上限（字符）：防模型回吐超长名字污染前端
MAX_NAME_LEN = 80


def _probe_metadata(img_path: str | Path, info: PdfImageInfo | None = None) -> dict[str, Any]:
    """启发式候选（离线路径兜底）：宽高比 → 粗略类型倾向；仅供人工确认排序，不算识别。"""
    from PIL import Image

    img_path = Path(img_path)
    try:
        with Image.open(img_path) as im:
            w, h = im.size
    except Exception:  # noqa: BLE001
        w, h = (getattr(info, "width", 0), getattr(info, "height", 0))
    hint: list[str] = []
    if w and h:
        ratio = w / h
        if 0.6 <= ratio <= 1.4:
            hint.append("近方/竖幅——可能人物像")
        elif ratio > 2.4:
            hint.append("宽幅——可能地图/横幅")
        else:
            hint.append("横幅——可能场景")
    return {"hint": "；".join(hint) if hint else "无启发线索"}


_SYSTEM_PROMPT = (
    "你是 TRPG 模组扫描识图助手。给定一张从克苏鲁/奇幻模组 PDF 提取的图像，"
    "用 JSON 回答：{kind, name, caption, confidence}。"
    "kind 只能是 portrait（人物肖像/头像）、map（地图）、scene（场景/物件/插图）、cover（封面）之一；"
    "kind 为 portrait 时，name 填画面人物的名字（若画面有文字名帖/引言可判断，否则 null）；"
    "caption 用一句中文描述画面内容（≤40 字）；"
    "confidence 是 0~1 之间的小数，表示你对分类的把握程度，必须给出。"
    "只输出 JSON，不要多余文字。"
)


def _sniff_mime(data: bytes) -> str:
    """魔数嗅探真实 MIME：PNG/JPEG/GIF/WEBP/BMP；其余防御性默认 image/png。

    顺序敏感：PNG 魔数须最前（\x89 不与后续冲突）；WEBP 需校验 RIFF....WEBP
    四字签；GIF 分 87a/89a 两版。
    """
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"BM"):
        return "image/bmp"
    return "image/png"


def _parse_confidence(value: Any) -> float:
    """解析模型 confidence：缺失/非数字 → 0.0；数值 clamp 到 [0,1]。"""
    try:
        c = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, c))


# 首尾 markdown 包裹（** 粗体 / * 斜体 / ` 行内代码）剥离；`**` 须在 `*` 前匹配整段
_MD_TRIM = re.compile(r"^(\*\*|\*|`)+|(\*\*|\*|`)+$")
_WS_FOLD = re.compile(r"\s+")


def _normalize_name(name: Any) -> str | None:
    """name 规整：strip → 去首尾 markdown → 折叠内部连续空白 → 截断 ≤80 字符。"""
    if name is None:
        return None
    s = str(name).strip()
    s = _MD_TRIM.sub("", s).strip()
    s = _WS_FOLD.sub(" ", s)
    return s[:MAX_NAME_LEN]


def _degraded_reason(exc: Exception) -> str:
    """上游错误 → 简短泛化信息：绝不内嵌错误正文（防泄露密钥/内部信息）。

    LLMError 带 kind（connection/http_5xx…）按其归类；其余按异常类型名。
    """
    if isinstance(exc, LLMError) or hasattr(exc, "kind"):
        return f"在线识别失败（{getattr(exc, 'kind') or 'unknown'}）"
    return f"在线识别失败（{type(exc).__name__}）"


def _parse_vl_json(text: str) -> dict[str, Any]:
    """健壮 JSON 提取（复用 llm._extract_json 的 fence 剥除 + 平衡括号扫描）；失败/非对象返回空 dict。"""
    try:
        obj = llm._extract_json(text)
    except ValueError:
        return {}
    return obj if isinstance(obj, dict) else {}


def _ensure_min_side(data: bytes, mime: str) -> tuple[bytes, str]:
    """Qwen3-VL（SiliconFlow）要求图像边长 ≥28px；模块 PDF 可能抽出小图标/装饰图，
    发送前把短边低于 MIN_ONLINE_IMAGE_SIDE 的小图按短边等比放大（LANCZOS）重编码 PNG。

    已达标 / PIL 解不开（含魔数误判的非光栅）原样放行——绝不因预处理失败阻断在线识别。
    """
    import io

    from PIL import Image

    try:
        with Image.open(io.BytesIO(data)) as im:
            w, h = im.size
            if min(w, h) >= MIN_ONLINE_IMAGE_SIDE:
                return data, mime
            scale = MIN_ONLINE_IMAGE_SIDE / min(w, h)
            up = im.convert("RGB").resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
    except Exception:  # noqa: BLE001 —— 小图放大是增强层：解不开就原样发，不阻断
        return data, mime
    buf = io.BytesIO()
    up.save(buf, format="PNG")
    return buf.getvalue(), "image/png"


def classify_image_online(img_path: str | Path, *, timeout: float = 60.0) -> dict[str, Any]:
    """在线路径：qwen3-vl 单图分类（统一 LLMClient.classify_image，自动重试 + 错误分类）。

    本地图像以魔数嗅探 MIME 后按 (mime, bytes) 交给客户端组装 data URI；
    短边过小的小图先经 _ensure_min_side 放大（供应商最小尺寸要求），再按 PNG 发送；
    超过 4MB 抛 ValueError（"图像超过 4MB，跳过在线识别"），由 classify_image 捕获转离线降级。
    失败抛 LLMError（调用方降级）。
    """
    path = Path(img_path)
    if path.stat().st_size > MAX_ONLINE_IMAGE_BYTES:
        raise ValueError("图像超过 4MB，跳过在线识别")
    data = path.read_bytes()
    mime = _sniff_mime(data)
    data, mime = _ensure_min_side(data, mime)
    prompt = "请识别这张图像并给出 JSON。"
    text = llm.LLMClient(get_settings()).classify_image(
        (mime, data), prompt, system=_SYSTEM_PROMPT, timeout=timeout
    )
    parsed = _parse_vl_json(text)
    kind = parsed.get("kind")
    if kind not in KIND_VALUES:
        kind = "unknown"
    confidence = _parse_confidence(parsed.get("confidence"))
    return {
        "kind": kind,
        "name": _normalize_name(parsed.get("name")),
        "caption": str(parsed.get("caption", ""))[:120],
        "confidence": confidence,
        "needs_confirmation": (kind == "unknown") or (confidence < CONFIDENCE_THRESHOLD),
    }


@dataclass
class VisionResult:
    """单图识别结果。confidence < 门槛（默认 0.7）或 needs_confirmation 时需人工确认。"""

    image_path: str
    page_no: int
    kind: str = "unknown"
    name: str | None = None
    caption: str = ""
    confidence: float = 0.0
    needs_confirmation: bool = True
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "image_path": self.image_path,
            "page_no": self.page_no,
            "kind": self.kind,
            "name": self.name,
            "caption": self.caption,
            "confidence": self.confidence,
            "needs_confirmation": self.needs_confirmation,
            "meta": self.meta,
        }


def classify_image(info: PdfImageInfo) -> VisionResult:
    """单图分类：有 key 走在线（失败自动降级离线），无 key 走离线启发式 + 人工确认。"""
    fallback = VisionResult(
        image_path=info.saved_path,
        page_no=info.page_no,
        kind="unknown",
        name=None,
        caption="",
        confidence=0.0,
        needs_confirmation=True,
        meta=_probe_metadata(info.saved_path, info),
    )
    if not get_settings().vl_key:
        return fallback
    try:
        res = classify_image_online(info.saved_path)
    except Exception as e:  # noqa: BLE001 - 在线失败诚实降级离线（含超大图/超时/坏 JSON/密钥缺失）
        fallback.meta["degraded_reason"] = _degraded_reason(e)
        return fallback
    return VisionResult(
        image_path=info.saved_path,
        page_no=info.page_no,
        kind=res["kind"],
        name=res.get("name"),
        caption=res.get("caption", ""),
        confidence=float(res.get("confidence", 0.0)),
        needs_confirmation=bool(res.get("needs_confirmation", False)),
        meta={},
    )


def classify_images(infos: Sequence[PdfImageInfo]) -> list[VisionResult]:
    """批量分类（逐张；未来可并行）。"""
    return [classify_image(i) for i in infos]


__all__ = ["VisionResult", "classify_image", "classify_images", "classify_image_online", "KIND_VALUES"]
