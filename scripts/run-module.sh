#!/usr/bin/env bash
# Tindalos 本地全流程（API 模式）——不依赖任何 GitHub 工作流。
#
# 用法（Windows Git Bash / WSL）：
#   TINDALOS_API_KEY=sk-xxx bash scripts/run-module.sh "<模组.pdf|.md>" [输出目录]
#
# 流程：PDF → 文本提取 → DeepSeek 结构化整理 → LLM 生成剧本 → eval → evolve → 报告
# 环境变量（全部可覆盖）：
#   TINDALOS_API_KEY / TINDALOS_API_BASE（默认 https://api.deepseek.com/v1）
#   TINDALOS_MODEL（默认 deepseek-chat）· TINDALOS_LLM_CONTEXT（默认 16000）
#   TINDALOS_LLM_ENABLED=1（脚本自动置位）
set -u
cd "$(dirname "$0")/.." || exit 1

MODULE="${1:?用法: bash scripts/run-module.sh <pdf|md> [outdir]}"
OUT="${2:-data/output}"
if [ -z "${TINDALOS_API_KEY:-}" ]; then
  echo "错误: 请设置 TINDALOS_API_KEY（云端 API，DeepSeek 等）——本地 API 才需要；Ollama 端点可留空并设 TINDALOS_API_BASE" >&2
  exit 2
fi
mkdir -p "$OUT"
export TINDALOS_LLM_ENABLED=1
export TINDALOS_LLM_CONTEXT="${TINDALOS_LLM_CONTEXT:-16000}"
export PYTHONIOENCODING=utf-8
export PYTHONPATH=src

STEP=0
step() { STEP=$((STEP+1)); echo; echo "=== [$STEP] $1 ==="; }

# 1) PDF → 原始文本
RAW="$OUT/raw.md"
if [[ "$MODULE" == *.pdf ]]; then
  step "PDF 文本提取（PyMuPDF）"
  python - "$MODULE" "$RAW" <<'PY'
import fitz, re, sys
pdf = fitz.open(sys.argv[1])
pages = []
for i in range(len(pdf)):
    keep = [ln.strip() for ln in pdf[i].get_text().splitlines()
            if not re.fullmatch(r"\d{1,3}", ln.strip()) and not re.fullmatch(r"[•·\-—=]+", ln.strip())]
    if keep: pages.append("\n".join(keep))
text = re.sub(r"\n{3,}", "\n\n", "\n\n".join(pages))
open(sys.argv[2], "w", encoding="utf-8").write(text)
print(f"  提取 {len(pages)} 页 / {len(text)} 字符 → $RAW")
PY
else
  cp "$MODULE" "$RAW"
fi

# 2) 云端 LLM 结构化整理（非本地模型）
ORG="$OUT/module-organized.md"
step "LLM 结构化整理（${TINDALOS_MODEL:-deepseek-chat}，云端 API）"
python scripts/organize_module.py "$RAW" "$ORG" --title "$(basename "$MODULE" | sed 's/\.[^.]*$//')"

# 3) LLM 生成剧本（模组全文注入）
CAMP="$OUT/campaign.json"
step "LLM 生成剧本（KP→NPC→分幕，基于模组全文）"
python -m tindalos.cli generate "$ORG" --llm --out "$CAMP"
python - <<PY
import json
c = json.load(open(r"$CAMP", encoding="utf-8"))
sc = sum(len(a["scenes"]) for a in c["acts"]); ev = sum(len(s["events"]) for a in c["acts"] for s in a["scenes"])
print(f"  {c['title']}: {len(c['acts'])} 幕 / {sc} 场景 / {ev} 事件 / {len(c['npcs'])} NPC")
PY

# 4) eval（确定性 4 维 + LLM judge）
step "评估（确定性 4 维 + LLM judge）"
python -m tindalos.cli eval "$CAMP" --judge 2>/dev/null | python -c "import sys,json; d=json.load(sys.stdin); print('  total:', d['total'], '| judge:', d.get('judge'), '|', {k:v['score'] for k,v in d['table'].items()})"

# 5) evolve 自进化
step "自进化（eval → 修复 → 复评）"
python -m tindalos.cli evolve "$CAMP" --rounds 2 --out "$OUT/evolved.json" 2>&1 | grep -E "round|进化结果"

# 6) 记忆
step "跨会话记忆"
python -m tindalos.cli memories "$CAMP" 2>&1 | head -4

echo
echo "═══════════════════════════════════════════════"
echo "完成！产物目录: $OUT"
echo "  campaign:     $CAMP"
echo "  notes:        $OUT/notes.md（备团笔记，含记忆节）"
echo "  evolved:      $OUT/evolved.json"
echo "  前端预览:     cd frontend && npm run dev（离线静态）；tindalos serve 开实时模式"
echo "═══════════════════════════════════════════════"
