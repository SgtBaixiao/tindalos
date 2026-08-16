"""PDF 解析管线测试：analyze_pdf / 图像提取（含 CMYK 容错），零网络。

用 fitz（PyMuPDF）在 tmp_path 构造含图 PDF，pypdfium2 提取（与生产一致）——
全部本地文件操作，无网络无 LLM。覆盖：
- RGB PNG + CMYK JPEG 双图：analyze_pdf 不抛异常、两图均提取、落盘 .png 模式为 RGB；
- 确定性复现审计缺陷：给真实 PdfImage 注入 CMYK 模式 PIL（to_pil 返回 CMYK），
  验证 convert("RGB") 保图落盘、模式为 RGB、无临时残骸；
- 单图失败容错：to_pil 抛错 / 落盘抛错 → 仅跳过该图并告警（注明页码/索引），
  其余图正常提取、无孤儿 .png、整体不抛；
- _save_pil_png 落盘失败清理临时文件不重抛孤儿；
- 无图 PDF → 空 list；损坏/极小 PDF → 容错返回空结果；saved_path 契约。
"""

from __future__ import annotations

import io
import logging
from pathlib import Path

import fitz
import pypdfium2 as pdfium
import pytest
from PIL import Image
from pypdfium2 import _helpers as h

from tindalos.pdfio import (
    PdfImageInfo,
    PdfParseInfo,
    _extract_page_images,
    _save_pil_png,
    analyze_pdf,
)


class _FakeBitmap:
    """伪 PDFium bitmap：to_pil 返回预置 PIL 图像，close 为 no-op（注入用）。"""

    def __init__(self, pil) -> None:
        self._pil = pil

    def to_pil(self):
        return self._pil

    def close(self) -> None:
        pass


class _BrokenBitmap:
    """to_pil 抛错的 bitmap：模拟单张损坏图像。"""

    def to_pil(self):
        raise RuntimeError("broken image data")

    def close(self) -> None:
        pass


def _make_rgb_png(size=(64, 64), color=(10, 200, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _make_cmyk_jpeg(size=(32, 32), color=(200, 50, 50)) -> bytes:
    """CMYK 编码 JPEG（Pillow 存 JPEG 保留 CMYK，重开 mode == "CMYK"）。"""
    buf = io.BytesIO()
    Image.new("RGB", size, color).convert("CMYK").save(buf, format="JPEG")
    return buf.getvalue()


def _build_pdf_with_images(
    pdf_path: Path, images: list[tuple[bytes, tuple[float, float, float, float]]]
) -> None:
    """用 fitz（仅测试构造）嵌入图像生成 PDF：images 为 (字节流, rect)。"""
    doc = fitz.open()
    page = doc.new_page(width=400, height=200)
    for data, rect in images:
        page.insert_image(fitz.Rect(*rect), stream=data)
    doc.save(str(pdf_path))
    doc.close()


# ---- CMYK 容错 ----


def test_cmyk_jpeg_fixture_is_really_cmyk() -> None:
    """夹具 sanity：构造的 JPEG 确为 CMYK 编码（否则测试失去意义）。"""
    with Image.open(io.BytesIO(_make_cmyk_jpeg())) as im:
        assert im.mode == "CMYK"


def test_rgb_and_cmyk_images_both_extracted_as_rgb(tmp_path: Path) -> None:
    """RGB PNG + CMYK JPEG：analyze_pdf 不抛异常，两图均提取，落盘模式为 RGB。"""
    pdf = tmp_path / "imgs.pdf"
    _build_pdf_with_images(
        pdf,
        [
            (_make_rgb_png((64, 64), (10, 200, 30)), (0, 0, 64, 64)),
            (_make_cmyk_jpeg((32, 32), (200, 50, 50)), (100, 0, 132, 32)),
        ],
    )
    info = analyze_pdf(pdf, tmp_path / "out")
    assert len(info.images) == 2
    for img in info.images:
        saved = Path(img.saved_path)
        assert saved.is_file()
        assert saved.parent.resolve() == (tmp_path / "out").resolve()
        with Image.open(saved) as im:
            assert im.mode == "RGB"  # CMYK 不落盘为 CMYK 形态


def test_injected_cmyk_pil_converted_to_rgb(tmp_path: Path, monkeypatch) -> None:
    """审计复现：真实 PdfImage 注入 CMYK 模式 PIL → convert("RGB") 保图落盘。"""
    pdf = tmp_path / "one.pdf"
    _build_pdf_with_images(pdf, [(_make_rgb_png((32, 32), (1, 2, 3)), (0, 0, 32, 32))])
    out_dir = tmp_path / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    cmyk = Image.new("RGB", (20, 10), (120, 60, 40)).convert("CMYK")
    monkeypatch.setattr(h.PdfImage, "get_bitmap", lambda self: _FakeBitmap(cmyk))
    doc = pdfium.PdfDocument(str(pdf))
    try:
        page = doc[0]
        try:
            infos = _extract_page_images(0, page, out_dir)
        finally:
            page.close()
    finally:
        doc.close()
    assert len(infos) == 1
    saved = Path(infos[0].saved_path)
    assert saved.is_file()
    with Image.open(saved) as im:
        assert im.mode == "RGB"
    assert infos[0].width == 20 and infos[0].height == 10
    assert infos[0].page_no == 0 and infos[0].index == 1
    assert not list(out_dir.glob("*.tmp"))  # 无临时残骸


# ---- 单图失败容错 ----


def test_to_pil_failure_skips_only_that_image(tmp_path: Path, monkeypatch, caplog) -> None:
    """单张图像 to_pil 失败：仅跳过该图并告警（含页码/索引），其余正常，无孤儿。"""
    pdf = tmp_path / "two.pdf"
    _build_pdf_with_images(
        pdf,
        [
            (_make_rgb_png((32, 32), (1, 2, 3)), (0, 0, 32, 32)),
            (_make_rgb_png((48, 48), (4, 5, 6)), (100, 0, 148, 48)),
        ],
    )
    out_dir = tmp_path / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    orig = h.PdfImage.get_bitmap
    state = {"n": 0}

    def _flaky(self):
        state["n"] += 1
        return _BrokenBitmap() if state["n"] == 1 else orig(self)

    monkeypatch.setattr(h.PdfImage, "get_bitmap", _flaky)
    doc = pdfium.PdfDocument(str(pdf))
    try:
        page = doc[0]
        try:
            with caplog.at_level(logging.WARNING, logger="tindalos.pdfio"):
                infos = _extract_page_images(0, page, out_dir)
        finally:
            page.close()
    finally:
        doc.close()
    assert len(infos) == 1
    assert infos[0].index == 2  # 第 1 张被跳过，第 2 张保留
    msgs = [r.message for r in caplog.records]
    assert any("第 0 页" in m and "第 1 张" in m and "转 PIL 失败" in m and "跳过" in m for m in msgs)
    assert sorted(p.name for p in out_dir.glob("img-p0-*.png")) == ["img-p0-2.png"]


def test_save_failure_skips_image_and_no_orphan(tmp_path: Path, monkeypatch, caplog) -> None:
    """单张图像落盘失败：仅跳过并告警，不留孤儿 .png，其余图正常。"""
    import tindalos.pdfio as pdfio

    pdf = tmp_path / "two.pdf"
    _build_pdf_with_images(
        pdf,
        [
            (_make_rgb_png((32, 32), (1, 2, 3)), (0, 0, 32, 32)),
            (_make_rgb_png((48, 48), (4, 5, 6)), (100, 0, 148, 48)),
        ],
    )
    out_dir = tmp_path / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    real_save = pdfio._save_pil_png
    state = {"n": 0}

    def _flaky(pil, dest):
        state["n"] += 1
        if state["n"] == 1:
            raise OSError("磁盘已满")
        real_save(pil, dest)

    monkeypatch.setattr(pdfio, "_save_pil_png", _flaky)
    doc = pdfium.PdfDocument(str(pdf))
    try:
        page = doc[0]
        try:
            with caplog.at_level(logging.WARNING, logger="tindalos.pdfio"):
                infos = _extract_page_images(0, page, out_dir)
        finally:
            page.close()
    finally:
        doc.close()
    assert len(infos) == 1
    assert infos[0].index == 2
    msgs = [r.message for r in caplog.records]
    assert any("第 0 页" in m and "第 1 张" in m and "保存 PNG 失败" in m and "跳过" in m for m in msgs)
    assert sorted(p.name for p in out_dir.glob("img-p0-*.png")) == ["img-p0-2.png"]


def test_save_pil_png_cleans_tmp_on_failure(tmp_path: Path, monkeypatch) -> None:
    """落盘失败时清理临时文件并重抛，不留下孤儿 .png。"""
    dest = tmp_path / "img-p0-1.png"
    pil = Image.new("RGB", (16, 16), (1, 2, 3))

    def _broken_save(*args, **kwargs):
        raise OSError("磁盘已满")

    monkeypatch.setattr(pil, "save", _broken_save)
    with pytest.raises(OSError):
        _save_pil_png(pil, dest)
    assert not dest.exists()
    assert not list(tmp_path.glob("*.tmp"))


# ---- 常规契约 ----


def test_pdf_without_images_returns_empty_list(tmp_path: Path) -> None:
    """无图 PDF：返回空 list，不崩溃。"""
    pdf = tmp_path / "text.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "plain text page, no images")
    doc.save(str(pdf))
    doc.close()
    info = analyze_pdf(pdf, tmp_path / "out")
    assert info.images == []
    assert len(info.texts) == 1
    assert info.pages == 1


def test_saved_path_and_page_no_correct(tmp_path: Path) -> None:
    """saved_path 指向 out_dir 下、文件真实存在、page_no 正确。"""
    pdf = tmp_path / "pages.pdf"
    doc = fitz.open()
    for color in ((1, 2, 3), (4, 5, 6)):
        page = doc.new_page()
        page.insert_image(fitz.Rect(0, 0, 32, 32), stream=_make_rgb_png((32, 32), color))
    doc.save(str(pdf))
    doc.close()
    out_dir = tmp_path / "out"
    info = analyze_pdf(pdf, out_dir)
    assert len(info.images) == 2
    for img in info.images:
        assert isinstance(img, PdfImageInfo)
        saved = Path(img.saved_path)
        assert saved.is_file()
        assert saved.parent.resolve() == out_dir.resolve()
        assert img.width == 32 and img.height == 32
        assert img.page_no in (0, 1)
    assert {i.page_no for i in info.images} == {0, 1}


def test_analyze_pdf_without_out_dir(tmp_path: Path) -> None:
    """out_dir 缺省：仅文本，不提取图像（契约不变）。"""
    pdf = tmp_path / "imgs.pdf"
    _build_pdf_with_images(pdf, [(_make_rgb_png((32, 32), (1, 2, 3)), (0, 0, 32, 32))])
    info = analyze_pdf(pdf)
    assert info.images == []
    assert info.pages == 1
    assert len(info.texts) == 1


# ---- 边界：损坏/极小 PDF 容错 ----


def test_corrupted_pdf_returns_empty_not_crash(tmp_path: Path) -> None:
    """损坏 PDF：不抛异常，返回仅含 sha256 的空结果。"""
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"this is not a pdf at all")
    info = analyze_pdf(bad, tmp_path / "out")
    assert isinstance(info, PdfParseInfo)
    assert info.texts == [] and info.images == [] and info.pages == 0 and info.chars == 0
    assert info.sha256  # 摘要仍可计算


def test_minimal_tiny_pdf_does_not_crash(tmp_path: Path) -> None:
    """极小（单页空白）PDF：不抛异常。"""
    pdf = tmp_path / "tiny.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(str(pdf))
    doc.close()
    info = analyze_pdf(pdf, tmp_path / "out")
    assert info.pages == 1
    assert info.images == []
