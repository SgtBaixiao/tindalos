#!/usr/bin/env python3
"""模组数据整理器：原始文本 → 结构化模组文档（LLM API 驱动）。

用法：
  python scripts/organize_module.py <raw.md> <out.md> [--title 留地不留头]

输出结构（markdown）：
  元信息 / 背景设定 / 时间线与历史 / 地点（描述+检定）/ NPC（身份/动机/关键线索）/
  事件链（起始/发展/高潮/结局）/ 调查线索 / 关键检定清单 / 附录与笔记

API 复用 Tindalos 配置：TINDALOS_API_BASE / TINDALOS_API_KEY / TINDALOS_MODEL
（缺省 DEEPSEEK_API_KEY；本地 ollama 端点也可用）。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests

STRUCTURE_PROMPT = """你是克苏鲁 TRPG 模组整理师。把下面的模组原始文本整理成结构化 markdown 文档，要求：

## 输出结构（严格遵守，中文）
# 模组：<标题>
## 元信息
- 背景年代 / 地区 / 作者 / 译者
## 背景设定（3-6 段，保留史实与克苏鲁元素）
## 时间线与历史（列表：时间 → 事件）
## 地点（每处：名称 / 描述 / 相关检定与线索）
## NPC（每个：姓名 / 身份 / 动机 / 关键信息）
## 事件链（起始 → 发展 → 高潮 → 结局，含关键转折）
## 调查线索（编号列表：线索 → 指向）
## 关键检定清单（技能 → 用途）
## 附录与笔记

## 整理纪律
1. 忠实原文：保留所有地点/NPC/检定/线索/数字，不增删事实；
2. 原文是散落叙述时按上述章节归位，可适当合并重复；
3. 保留克苏鲁专用词（旧印/莎布尼古拉斯等）与原文专有名词；
4. 输出纯 markdown，不要额外解释。

## 原始文本
"""


def main() -> int:
    if len(sys.argv) < 3:
        print("用法: python scripts/organize_module.py <raw.md> <out.md> [--title 标题]")
        return 2
    raw_path, out_path = Path(sys.argv[1]), Path(sys.argv[2])
    title = "留地不留头"
    if "--title" in sys.argv:
        title = sys.argv[sys.argv.index("--title") + 1]

    base = os.environ.get("TINDALOS_API_BASE", "https://api.deepseek.com/v1")
    key = os.environ.get("TINDALOS_API_KEY") or os.environ.get("DEEPSEEK_API_KEY", "")
    model = os.environ.get("TINDALOS_MODEL", "deepseek-chat")
    if not key:
        print("错误: 未设置 TINDALOS_API_KEY / DEEPSEEK_API_KEY")
        return 2

    raw = raw_path.read_text(encoding="utf-8")
    print(f"整理 {raw_path.name}（{len(raw)} 字符）→ {out_path.name}（模型 {model}）...")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": STRUCTURE_PROMPT},
            {"role": "user", "content": f"模组标题：{title}\n\n{raw}"},
        ],
        "temperature": 0.3,
        "max_tokens": 8192,
    }
    try:
        resp = requests.post(
            f"{base.rstrip('/')}/chat/completions",
            json=payload,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
            timeout=300,
        )
        resp.raise_for_status()
    except Exception as e:  # noqa: BLE001
        print(f"API 调用失败: {type(e).__name__}: {e}")
        return 1
    content = resp.json()["choices"][0]["message"]["content"]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content.strip() + "\n", encoding="utf-8")
    print(f"完成: {out_path}（{len(content)} 字符）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
