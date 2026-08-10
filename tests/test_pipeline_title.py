"""title/extract_premise 提取回归（loop 迭代改进，2026-08-11）。"""
from tindalos.pipeline import extract_premise, title_from_text


def test_title_from_organized_module_h1():
    """organized 格式：# 模组：xxx 优先；不得误判 ## 元信息。"""
    md = "# 模组：留地不留头（To Hell or Connaught）\n## 元信息\n- 年代 1649\n## 背景设定\n..."
    assert title_from_text(md) == "留地不留头（To Hell or Connaught）"


def test_title_from_raw_module():
    md = "To Hell or Connaught 留地不留头\n1649 年的爱尔兰。"
    assert title_from_text(md) == "To Hell or Connaught 留地不留头"


def test_premise_prefers_premise_line():
    md = "## 元信息\n前提：1649 年爱尔兰的克苏鲁模组。\n正文..."
    assert extract_premise(md).startswith("1649 年爱尔兰的克苏鲁模组")
