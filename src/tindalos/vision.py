"""多模态识别管线（wayfinder ticket 04）：qwen3-vl-plus（DashScope）+ 置信度门槛 + 人工确认。

在线路径（`TINDALOS_DASHSCOPE_KEY` 或 `DASHSCOPE_API_KEY` 存在时）：
  DashScope OpenAI 兼容 `chat/completions` 端点 + qwen-vl 模型，对提取出的图像
  分类（人物像/地图/场景/封面）+ 人物像提取名字 + 一句话说明，返回结构化 JSON。
  该路径需要把本地 PNG 以 data URI（base64）发给远端——**密钥只走环境变量**。

离线路径（无 key / 调用失败 / 超时）：诚实降级——`kind="unknown"`、
`confidence=0`、`needs_confirmation=true`，同时给出**启发式候选标签**（宽高比 + 体量），
由前端人工确认 UI 兜底归类。与全仓"LLM 失败按设计降级"哲学一致：云端识别是增强层，
不是硬依赖。

输出契约 VisionResult：{image_path, page_no, kind, name, caption, confidence,
needs_confirmation}；kind ∈ portrait/map/scene/cover/unknown。
"""

from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

try:  # requests 为可选（离线无网络时缺省降级）
    import requests
except ImportError:  # pragma: no cover - 依赖探测已确认安装，此分支仅防御
    requests = None  # type: ignore[assignment]

from tindalos.pdfio import PdfImageInfo

KIND_VALUES = ("portrait", "map", "scene", "cover", "unknown")


def _vl_key() -> str:
    return os.environ.get("TINDALOS_DASHSCOPE_KEY") or os.environ.get("DASHSCOPE_API_KEY", "")


def _vl_model() -> str:
    return os.environ.get("TINDALOS_VL_MODEL", "qwen3-vl-plus")


def _vl_endpoint() -> str:
    return os.environ.get("TINDALOS_VL_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions")


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


def _data_uri(img_path: str | Path) -> str:
    """本地 PNG → data URI（在线 VL 识别的图像载荷）。"""
    data = Path(img_path).read_bytes()
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


_SYSTEM_PROMPT = (
    "你是 TRPG 模组扫描识图助手。给定一张从克苏鲁/奇幻模组 PDF 提取的图像，"
    "用 JSON 回答：{kind, name, caption}。"
    "kind 只能是 portrait（人物肖像/头像）、map（地图）、scene（场景/物件/插图）、cover（封面）之一；"
    "kind 为 portrait 时，name 填画面人物的名字（若画面有文字名帖/引言可判断，否则 null）；"
    "caption 用一句中文描述画面内容（≤40 字）。只输出 JSON，不要多余文字。"
)


def _parse_vl_json(text: str) -> dict[str, Any]:
    """剥离代码围栏/前后缀，提取第一个 JSON 对象。"""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("VL 响应无 JSON 对象")
    return json.loads(text[start : end + 1])


def classify_image_online(img_path: str | Path, *, timeout: float = 60.0) -> dict[str, Any]:
    """在线路径：qwen3-vl 单图分类（DashScope OpenAI 兼容端点）。失败抛异常（调用方降级）。"""
    if requests is None:
        raise RuntimeError("requests 未安装，无法走在线 VL 识别")
    key = _vl_key()
    if not key:
        raise RuntimeError("未配置 TINDALOS_DASHSCOPE_KEY / DASHSCOPE_API_KEY")
    body = {
        "model": _vl_model(),
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": _data_uri(img_path)}},
                    {"type": "text", "text": "请识别这张图像并给出 JSON。"},
                ],
            },
        ],
        "temperature": 0.1,
    }
    resp = requests.post(_vl_endpoint(), json=body, headers={"Authorization": f"Bearer {key}"}, timeout=timeout)
    if resp.status_code != 200:
        raise RuntimeError(f"VL API {resp.status_code}: {resp.text[:200]}")
    content = resp.json()["choices"][0]["message"]["content"]
    parsed = _parse_vl_json(content)
    kind = parsed.get("kind")
    if kind not in KIND_VALUES:
        kind = "unknown"
    return {
        "kind": kind,
        "name": parsed.get("name"),
        "caption": str(parsed.get("caption", ""))[:120],
        "confidence": 0.9 if kind != "unknown" else 0.0,
        "needs_confirmation": kind == "unknown",
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
    if not _vl_key():
        return fallback
    try:
        res = classify_image_online(info.saved_path)
    except Exception as e:  # noqa: BLE001 - 在线失败诚实降级离线（含密钥缺失/超时/坏 JSON）
        fallback.meta["degraded_reason"] = str(e)[:120]
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
