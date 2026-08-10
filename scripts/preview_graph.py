#!/usr/bin/env python3
"""examples/preview.svg 生成器：campaign JSON → 剧本结构 SVG（README/GitHub 渲染预览）。

布局：幕（Act）为列 → 场景（Scene）纵向堆叠 → 事件（Event）横向；NPC/线索为侧栏卡片。
配色对齐前端设计系统（house-style 继承 + 铜锈绿信息色）：
  act=#D64545(红) scene=#E8620C(橙) event=#6B7A55(铜锈绿) npc=#3E4A5A(墨蓝) clue=#4A3B28(墨棕)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

COLORS = {"act": "#D64545", "scene": "#E8620C", "event": "#6B7A55", "npc": "#3E4A5A", "clue": "#4A3B28"}
LABELS = {"act": "幕", "scene": "场景", "event": "事件", "npc": "NPC", "clue": "线索"}


def esc(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def node_card(x: float, y: float, w: float, h: float, kind: str, title: str, sub: str = "") -> list[str]:
    c = COLORS[kind]
    lines = [
        f'<rect x="{x:.0f}" y="{y:.0f}" width="{w:.0f}" height="{h:.0f}" rx="6" fill="#FAF6EF" '
        f'stroke="#E5DCC8" stroke-width="1"/>',
        f'<rect x="{x:.0f}" y="{y:.0f}" width="{w:.0f}" height="5" fill="{c}"/>',
        f'<text x="{x+10:.0f}" y="{y+30:.0f}" font-size="13" font-weight="700" fill="#2B2620" '
        f'font-family="Century Gothic, sans-serif">{esc(title[:22])}</text>',
    ]
    if sub:
        lines.append(
            f'<text x="{x+10:.0f}" y="{y+48:.0f}" font-size="10" fill="#5C5345" '
            f'font-family="sans-serif">{esc(sub[:34])}</text>'
        )
    return lines


def build(campaign: dict) -> str:
    W = 1280
    acts = campaign.get("acts", [])
    npcs = campaign.get("npcs", {})
    clues = campaign.get("clues", [])
    rows = max(1, max((len(a.get("scenes", [])) for a in acts), default=1))
    col_w, row_h = 300, 120
    hdr_h, pad, side_w = 70, 24, 230
    H = hdr_h + rows * row_h + pad * 2 + 40
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" font-family="sans-serif">',
        f'<rect width="{W}" height="{H}" fill="#FAF6EF"/>',
        f'<text x="{pad}" y="34" font-size="20" font-weight="800" fill="#2B2620" '
        f'font-family="Century Gothic, sans-serif">Tindalos · 模组《{esc(campaign.get("title", ""))}》剧本结构</text>',
        f'<text x="{pad}" y="56" font-size="12" fill="#8A7F6D">'
        f'{len(acts)} 幕 · {sum(len(a.get("scenes", [])) for a in acts)} 场景 · '
        f'{sum(len(s.get("events", [])) for a in acts for s in a.get("scenes", []))} 事件 · '
        f'{len(npcs)} NPC · {len(clues)} 线索 · 关系 {len(campaign.get("relations", []))} 条</text>',
    ]
    for ai, act in enumerate(acts):
        x = pad + ai * (col_w + 16)
        parts += node_card(x, hdr_h, col_w, 58, "act", act.get("title", ""), act.get("summary", ""))
        for si, scene in enumerate(act.get("scenes", [])):
            y = hdr_h + 72 + si * row_h
            parts += node_card(x, y, col_w, 62, "scene", scene.get("title", ""),
                               f"{scene.get('setting', {}).get('time', '')} · {scene.get('setting', {}).get('place', '')}")
            evs = scene.get("events", [])
            ev_w = (col_w - 28) / max(1, len(evs))
            for ei, ev in enumerate(evs):
                ex = x + 14 + ei * ev_w
                parts += node_card(ex, y + 70, ev_w - 4, 40, "event", f"#{ei+1} {ev.get('title', '')[:10]}")
            if ai == 0 and si == 0:
                parts += [f'<line x1="{x+col_w}" y1="{y+30}" x2="{x+col_w+16}" y2="{y+30}" '
                          f'stroke="#D64545" stroke-width="2"/>']
    # 侧栏：NPC 与线索
    sx = pad + len(acts) * (col_w + 16) + 20
    parts.append(f'<text x="{sx}" y="{hdr_h+16}" font-size="13" font-weight="800" fill="#3E4A5A">NPC（{len(npcs)}）</text>')
    for i, (nid, npc) in enumerate(npcs.items()):
        y = hdr_h + 26 + i * 34
        parts += node_card(sx, y, 200, 28, "npc", npc.get("name", nid), npc.get("archetype", ""))
    cy = hdr_h + 26 + len(npcs) * 34 + 10
    parts.append(f'<text x="{sx}" y="{cy}" font-size="13" font-weight="800" fill="#4A3B28">线索（{len(clues)}）</text>')
    for i, c in enumerate(clues):
        y = cy + 12 + i * 30
        parts += node_card(sx, y, 200, 24, "clue", c.get("name", ""), "")
    parts.append("</svg>")
    return "\n".join(parts)


def main() -> int:
    src = Path(sys.argv[1] if len(sys.argv) > 1 else "examples/campaign.json")
    out = Path(sys.argv[2] if len(sys.argv) > 2 else "examples/preview.svg")
    campaign = json.loads(src.read_text(encoding="utf-8"))
    out.write_text(build(campaign), encoding="utf-8")
    print(f"preview.svg: {out} ({out.stat().st_size // 1024}KB, acts={len(campaign.get('acts', []))})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
