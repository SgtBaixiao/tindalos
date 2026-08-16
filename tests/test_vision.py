"""vision 多模态识别测试：FakeTransport 注入，零网络零 LLM。

覆盖（t17-vision）：
- 离线路径（无 vl_key）：kind=unknown / needs_confirmation / meta.hint 启发式候选；
- 在线成功：模型返回 {kind,name,caption,confidence} → VisionResult 各字段正确，
  且 transport.calls[0] 收到 data URI；
- 模型置信度驱动确认：0.9 → 免确认；0.3 → 需确认；缺失 → 0.0 → 需确认；clamp 到 [0,1]；
- kind 越界：归一为 unknown、需确认；
- name 规整：去首尾 markdown；
- MIME 嗅探：PNG / JPEG 魔数 → data URI 前缀正确；
- 大小限制：>4MB 不发起任何请求、返回离线 fallback、degraded_reason 以"在线识别失败"开头；
- 在线异常降级：不泄露上游错误正文（密钥串不进 degraded_reason）；
- 5xx 重试耗尽后降级；4xx 不重试（1 次调用即降级）；
- classify_images 批量逐张返回 list；
- VisionResult.to_dict() 键集契约。
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from tindalos.config import get_settings
from tindalos.pdfio import PdfImageInfo
from tindalos.vision import VisionResult, classify_image, classify_images


# ---- FakeTransport（复用 test_llm.py 模式：响应/异常动作队列 + .calls 记录） ----


class FakeResp:
    def __init__(self, status=200, json_data=None, text=""):
        self.status_code = status
        self._json_data = json_data
        self.text = text or (json.dumps(json_data, ensure_ascii=False) if json_data is not None else "")

    def json(self):
        return self._json_data


class FakeTransport:
    """可编程 fake：单一动作队列（响应或异常），耗尽后重放最后一个动作；每次调用记录 payload。"""

    def __init__(self):
        self.calls = []
        self._actions = []
        self._last = None

    def respond(self, resp):
        self._actions.append(resp)
        return self

    def raises(self, exc):
        self._actions.append(exc)
        return self

    def __call__(self, method, url, *, json=None, headers=None, timeout=None):
        self.calls.append({"method": method, "url": url, "json": json, "headers": headers, "timeout": timeout})
        if not self._actions:
            if self._last is None:
                raise AssertionError("FakeTransport 未配置响应")
            action = self._last
        else:
            action = self._actions.pop(0)
            self._last = action
        if isinstance(action, Exception):
            raise action
        return action


def _ok_chat(content: str) -> FakeResp:
    """OpenAI 兼容 chat/completions 成功响应，content 为模型返回文本。"""
    return FakeResp(200, {"choices": [{"message": {"content": content}}]})


def _info(img_path) -> PdfImageInfo:
    return PdfImageInfo(
        page_no=1, index=0, bbox={"x": 0, "y": 0, "w": 1, "h": 1},
        width=1, height=1, saved_path=str(img_path),
    )


def _real_png(path: Path) -> Path:
    """用 PIL 生成一张真实 1x1 PNG（离线启发式与在线读文件共用）。"""
    from PIL import Image

    Image.new("RGB", (1, 1), (255, 0, 0)).save(path)
    return path


def _data_uri(call: dict) -> str:
    """从 transport 记录的请求里取首条用户消息的 image_url。"""
    return call["json"]["messages"][1]["content"][0]["image_url"]["url"]


# ---- fixtures ----------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """重试退避已收敛到 llm._sleep_backoff：测试全零延迟（防真实 sleep）。"""
    import tindalos.llm as _llm

    monkeypatch.setattr(_llm, "_sleep_backoff", lambda attempt: None)


@pytest.fixture
def fake_transport(monkeypatch):
    """把 llm._default_transport 换成 FakeTransport：vision 内部构造的 LLMClient 也走 fake。"""
    import tindalos.llm as _llm

    t = FakeTransport()
    monkeypatch.setattr(_llm, "_default_transport", t)
    return t


@pytest.fixture
def no_vl_key(monkeypatch):
    """确保无 VL key：删除相关 env + 重置配置单例（测后 monkeypatch 自动恢复）。"""
    monkeypatch.delenv("TINDALOS_DASHSCOPE_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.setattr("tindalos.config._settings", None)


@pytest.fixture
def with_vl_key(monkeypatch):
    """设置 VL key + 重置配置单例（测后 monkeypatch 自动恢复）。"""
    monkeypatch.setenv("TINDALOS_DASHSCOPE_KEY", "sk-test-vl")
    monkeypatch.setattr("tindalos.config._settings", None)


# ---- 离线路径 ----------------------------------------------------------------


def test_offline_fallback_when_no_key(tmp_path, no_vl_key):
    img = _real_png(tmp_path / "img.png")
    result = classify_image(_info(img))
    assert result.kind == "unknown"
    assert result.needs_confirmation is True
    assert result.confidence == 0.0
    assert result.name is None
    assert "hint" in result.meta  # 启发式候选标签存在


# ---- 在线成功 ----------------------------------------------------------------


def test_online_success_fields(tmp_path, with_vl_key, fake_transport):
    img = _real_png(tmp_path / "img.png")
    fake_transport.respond(
        _ok_chat('{"kind": "portrait", "name": "老船长", "caption": "一位老船长", "confidence": 0.9}')
    )
    result = classify_image(_info(img))
    assert result.kind == "portrait"
    assert result.name == "老船长"
    assert result.caption == "一位老船长"
    assert result.confidence == 0.9
    assert result.needs_confirmation is False
    assert _data_uri(fake_transport.calls[0]).startswith("data:image/png;base64,")


def test_confidence_high_no_confirmation(tmp_path, with_vl_key, fake_transport):
    img = _real_png(tmp_path / "img.png")
    fake_transport.respond(_ok_chat('{"kind": "map", "confidence": 0.9}'))
    assert classify_image(_info(img)).needs_confirmation is False


def test_confidence_low_requires_confirmation(tmp_path, with_vl_key, fake_transport):
    img = _real_png(tmp_path / "img.png")
    fake_transport.respond(_ok_chat('{"kind": "map", "confidence": 0.3}'))
    result = classify_image(_info(img))
    assert result.needs_confirmation is True
    assert result.confidence == 0.3


def test_confidence_missing_defaults_zero(tmp_path, with_vl_key, fake_transport):
    img = _real_png(tmp_path / "img.png")
    fake_transport.respond(_ok_chat('{"kind": "cover"}'))
    result = classify_image(_info(img))
    assert result.confidence == 0.0
    assert result.needs_confirmation is True


def test_confidence_clamped_to_unit(tmp_path, with_vl_key, fake_transport):
    img = _real_png(tmp_path / "img.png")
    fake_transport.respond(_ok_chat('{"kind": "scene", "confidence": 9.9}'))
    fake_transport.respond(_ok_chat('{"kind": "scene", "confidence": -2}'))
    assert classify_image(_info(img)).confidence == 1.0
    assert classify_image(_info(img)).confidence == 0.0


# ---- kind 越界 ---------------------------------------------------------------


def test_kind_out_of_range_normalized(tmp_path, with_vl_key, fake_transport):
    img = _real_png(tmp_path / "img.png")
    fake_transport.respond(_ok_chat('{"kind": "foo", "confidence": 0.9}'))
    result = classify_image(_info(img))
    assert result.kind == "unknown"
    assert result.needs_confirmation is True


# ---- name 规整 ---------------------------------------------------------------


def test_name_strips_markdown(tmp_path, with_vl_key, fake_transport):
    img = _real_png(tmp_path / "img.png")
    fake_transport.respond(_ok_chat('{"kind": "portrait", "name": "  **老船长**  ", "confidence": 0.9}'))
    result = classify_image(_info(img))
    assert result.name == "老船长"


# ---- MIME 嗅探 ---------------------------------------------------------------


def test_mime_sniff_png(tmp_path, with_vl_key, fake_transport):
    img = tmp_path / "img.dat"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    fake_transport.respond(_ok_chat('{"kind": "scene", "confidence": 0.8}'))
    classify_image(_info(img))
    assert _data_uri(fake_transport.calls[0]).startswith("data:image/png;base64,")


def test_mime_sniff_jpeg(tmp_path, with_vl_key, fake_transport):
    img = tmp_path / "img.dat"
    img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 32)
    fake_transport.respond(_ok_chat('{"kind": "scene", "confidence": 0.8}'))
    classify_image(_info(img))
    assert _data_uri(fake_transport.calls[0]).startswith("data:image/jpeg;base64,")


# ---- 大小限制 ----------------------------------------------------------------


def test_oversize_skips_online(tmp_path, with_vl_key, fake_transport):
    img = tmp_path / "big.bin"
    img.write_bytes(b"\x00" * (4 * 1024 * 1024 + 1))
    result = classify_image(_info(img))
    assert fake_transport.calls == []  # 未发起任何请求
    assert result.kind == "unknown"
    assert result.needs_confirmation is True
    assert result.meta["degraded_reason"].startswith("在线识别失败")


# ---- 在线异常降级 ------------------------------------------------------------


def test_online_error_degrades_without_leaking_body(tmp_path, with_vl_key, fake_transport):
    img = _real_png(tmp_path / "img.png")
    fake_transport.raises(ConnectionError("sk-super-secret 泄露密钥"))
    result = classify_image(_info(img))
    assert result.kind == "unknown"
    assert result.needs_confirmation is True
    reason = result.meta["degraded_reason"]
    assert reason.startswith("在线识别失败")
    assert "sk-super-secret" not in reason  # 绝不内嵌上游错误正文


def test_5xx_exhausts_then_fallback(tmp_path, with_vl_key, fake_transport):
    img = _real_png(tmp_path / "img.png")
    fake_transport.respond(FakeResp(500, {}, "boom"))
    result = classify_image(_info(img))
    assert result.kind == "unknown"
    assert result.meta["degraded_reason"] == "在线识别失败（http_5xx）"
    assert len(fake_transport.calls) == 1 + get_settings().llm_max_retries  # 重试耗尽


def test_4xx_no_retry_then_fallback(tmp_path, with_vl_key, fake_transport):
    img = _real_png(tmp_path / "img.png")
    fake_transport.respond(FakeResp(404, {}, "not found"))
    result = classify_image(_info(img))
    assert result.kind == "unknown"
    assert result.meta["degraded_reason"] == "在线识别失败（http_4xx）"
    assert len(fake_transport.calls) == 1  # 4xx 不重试


# ---- 批量 --------------------------------------------------------------------


def test_classify_images_returns_list(tmp_path, with_vl_key, fake_transport):
    a = _real_png(tmp_path / "a.png")
    b = _real_png(tmp_path / "b.png")
    fake_transport.respond(_ok_chat('{"kind": "map", "confidence": 0.95}'))
    fake_transport.respond(_ok_chat('{"kind": "cover", "confidence": 0.8}'))
    results = classify_images([_info(a), _info(b)])
    assert isinstance(results, list)
    assert len(results) == 2
    assert results[0].kind == "map"
    assert results[1].kind == "cover"
    assert results[0].needs_confirmation is False


# ---- _ensure_min_side 小图放大（Qwen3-VL ≥28px 供应商要求） --------------------


def _png_bytes(w: int, h: int, color=(10, 20, 30)) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="PNG")
    return buf.getvalue()


def test_ensure_min_side_upscales_small_png():
    from PIL import Image

    from tindalos.vision import MIN_ONLINE_IMAGE_SIDE, _ensure_min_side

    out, mime = _ensure_min_side(_png_bytes(4, 2), "image/png")
    assert mime == "image/png"  # 重编码为 PNG
    with Image.open(io.BytesIO(out)) as im:
        assert min(im.size) == MIN_ONLINE_IMAGE_SIDE  # 短边放大到 64
        assert im.size[0] / im.size[1] == pytest.approx(2.0)  # 宽高比保持


def test_ensure_min_side_passthrough_large_png():
    from tindalos.vision import _ensure_min_side

    data = _png_bytes(300, 200)
    out, mime = _ensure_min_side(data, "image/jpeg")
    assert out == data  # 字节原样
    assert mime == "image/jpeg"  # 原 mime 保留（即使非 PNG）


def test_ensure_min_side_passthrough_non_image():
    from tindalos.vision import _ensure_min_side

    data = b"\x00\x01\x02not a raster image"
    out, mime = _ensure_min_side(data, "image/png")
    assert out == data
    assert mime == "image/png"  # 解不开原样放行，不阻断在线识别


def test_ensure_min_side_small_gif_becomes_png():
    from PIL import Image

    from tindalos.vision import MIN_ONLINE_IMAGE_SIDE, _ensure_min_side

    # 魔数嗅探会标成 GIF，但短边 <64 的小图经放大后重编码为 PNG
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (255, 0, 0)).save(buf, format="GIF")
    out, mime = _ensure_min_side(buf.getvalue(), "image/gif")
    assert mime == "image/png"
    with Image.open(io.BytesIO(out)) as im:
        assert min(im.size) == MIN_ONLINE_IMAGE_SIDE


# ---- to_dict 契约 ------------------------------------------------------------


def test_to_dict_key_set():
    r = VisionResult(
        image_path="a.png", page_no=3, kind="map", name=None, caption="",
        confidence=0.5, needs_confirmation=True, meta={"hint": "宽幅——可能地图/横幅"},
    )
    assert set(r.to_dict()) == {
        "image_path", "page_no", "kind", "name", "caption",
        "confidence", "needs_confirmation", "meta",
    }
