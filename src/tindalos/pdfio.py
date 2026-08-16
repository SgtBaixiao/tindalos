"""PDF 解析管线（wayfinder ticket 03）：pypdfium2（Apache-2.0/BSD-3）提取文本+图像+栅格化。

合规背景：Web 服务弃用 PyMuPDF（AGPL §13 网络义务与 MIT 冲突），改 pypdfium2
（PDFium 绑定，Apache/BSD 双许可）——文本、图像对象、页面栅格化三者皆可覆盖；
本地脚本 run-module.sh 仍可留 fitz（无分发无网络）。

数据契约（供 web.py / vision.py / rag.py 消费）：
  - PageText      {page_no, text}
  - PdfImageInfo  {page_no, index, bbox(x,y,w,h), width, height, saved_path}
  - PdfParseInfo  {path, sha256, pages, chars, texts, images}
页面坐标单位为 PDF pt（72/in）。图像以 PNG 落盘到调用方给定的输出目录
（`data/modules/<module_id>/images/img-p<page>-<n>.png`），文本按页拼接。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import pypdfium2 as pdfium
from pypdfium2 import _helpers as h


@dataclass
class PageText:
    """单页提取文本。page_no 为 0 基页码。"""

    page_no: int
    text: str


@dataclass
class PdfImageInfo:
    """PDF 内嵌图像对象：页码 + 页内 bbox + 尺寸 + 落盘 PNG 路径。

    bbox 为 PDF pt（原点左下），前端展示时按页高翻转 y 得到 CSS 坐标系。
    """

    page_no: int
    index: int
    bbox: dict[str, float]
    width: int
    height: int
    saved_path: str
    kind: str = "image"


@dataclass
class PdfParseInfo:
    """一次解析的完整产物：文档信息 + 逐页文本 + 提取图像清单。"""

    path: str
    sha256: str
    pages: int
    chars: int
    texts: list[PageText] = field(default_factory=list)
    images: list[PdfImageInfo] = field(default_factory=list)

    def full_text(self, *, max_chars: int | None = None) -> str:
        """按页序拼接全文；max_chars 截断（供 RAG 入库/生成上下文，防超长）。"""
        joined = "\n\n".join(t.text for t in self.texts)
        if max_chars is not None and len(joined) > max_chars:
            return joined[:max_chars]
        return joined


def sha256_of(path: str | Path) -> str:
    """文件 sha256（历史记录去重 / 模组登记主键）。"""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def extract_pages(path: str | Path) -> list[PageText]:
    """逐页提取文本（PDFium textpage，get_text_range 读取顺序）。"""
    doc = pdfium.PdfDocument(str(path))
    try:
        out: list[PageText] = []
        for i in range(len(doc)):
            page = doc[i]
            try:
                tp = page.get_textpage()
                try:
                    out.append(PageText(page_no=i, text=tp.get_text_range()))
                finally:
                    tp.close()
            finally:
                page.close()
        return out
    finally:
        doc.close()


def _extract_page_images(page_no: int, page: h.PdfPage, out_dir: Path) -> list[PdfImageInfo]:
    """提取单页内嵌图像对象 → PNG 落盘。bbox 记录页内坐标（PDF pt）。"""
    infos: list[PdfImageInfo] = []
    idx = 0
    for obj in page.get_objects():
        if not isinstance(obj, h.PdfImage):
            continue
        idx += 1
        rect = obj.get_bounds()  # (x0, y0, x1, y1) PDF pt，原点左下
        if rect is None:
            continue
        x0, y0, x1, y1 = (float(v) for v in rect)
        try:
            bitmap = obj.get_bitmap()
        except Exception:  # noqa: BLE001 - 个别损坏/嵌套对象跳过
            continue
        try:
            pil = bitmap.to_pil()
            w, hh = pil.size
        finally:
            bitmap.close()
        name = f"img-p{page_no}-{idx}.png"
        dest = out_dir / name
        # 二进制写，不涉及编码
        pil.save(dest, format="PNG")
        infos.append(
            PdfImageInfo(
                page_no=page_no,
                index=idx,
                bbox={"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0},
                width=w,
                height=hh,
                saved_path=str(dest),
            )
        )
    return infos


def extract_images(path: str | Path, out_dir: str | Path) -> list[PdfImageInfo]:
    """全文档图像提取 → PNG 落盘 out_dir（自动创建）。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = pdfium.PdfDocument(str(path))
    try:
        infos: list[PdfImageInfo] = []
        for i in range(len(doc)):
            page = doc[i]
            try:
                infos.extend(_extract_page_images(i, page, out_dir))
            finally:
                page.close()
        return infos
    finally:
        doc.close()


def render_page(path: str | Path, page_no: int, *, scale: float = 1.5) -> "object":
    """栅格化单页 → PIL Image（页面缩略图 / 地图背景）。

    scale 控制 DPI（1.0 = 72dpi；2.0 = 144dpi）。返回的 PIL 图像由调用方保存。
    """
    doc = pdfium.PdfDocument(str(path))
    try:
        page = doc[page_no]
        try:
            bitmap = page.render(scale=scale)
            try:
                return bitmap.to_pil()
            finally:
                bitmap.close()
        finally:
            page.close()
    finally:
        doc.close()


def analyze_pdf(path: str | Path, out_dir: str | Path | None = None) -> PdfParseInfo:
    """一站式解析：sha256 + 逐页文本 + 图像提取（out_dir 提供时）。"""
    path = Path(path)
    texts = extract_pages(path)
    infos = PdfParseInfo(
        path=str(path),
        sha256=sha256_of(path),
        pages=len(texts),
        chars=sum(len(t.text) for t in texts),
        texts=texts,
    )
    if out_dir is not None:
        infos.images = extract_images(path, out_dir)
    return infos


def iter_pages(path: str | Path) -> Iterable[int]:
    """页数（轻量：供多模态/人工确认 UI 分页）。"""
    doc = pdfium.PdfDocument(str(path))
    try:
        yield from range(len(doc))
    finally:
        doc.close()


__all__ = [
    "PageText",
    "PdfImageInfo",
    "PdfParseInfo",
    "analyze_pdf",
    "extract_pages",
    "extract_images",
    "render_page",
    "sha256_of",
]
