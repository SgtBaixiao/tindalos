#!/usr/bin/env bash
# Tindalos LLM 模式一键演示（t9-llm）
#
#   generate examples/sample-module.md --llm → eval → evolve
#   每步打印产物路径与总分数；LLM 不可用/失败时按设计降级为确定性生成（stderr 有 UserWarning）。
#
# 环境（可覆盖）：
#   TINDALOS_LLM_ENABLED=1            开启 LLM 总开关（必需）
#   TINDALOS_MODEL=qwen2.5:0.5b   模型名（已实测可用；deepseek-v3.1:671b-cloud 已于 2026-07-15 退役）
#   OLLAMA_BASE_URL=http://localhost:11434/v1 OpenAI 兼容端点
#   TINDALOS_LLM_TIMEOUT=300          单次请求超时（秒，容纳慢模型）
#
# Windows 双写法（cmd.exe / PowerShell 等价命令，复制粘贴可用）：
#   set TINDALOS_LLM_ENABLED=1
#   set TINDALOS_MODEL=qwen2.5:0.5b
#   set OLLAMA_BASE_URL=http://localhost:11434/v1
#   set PYTHONIOENCODING=utf-8
#   set PYTHONPATH=src
#   python -c "from tindalos.cli import app; app()" generate examples\sample-module.md --llm --out examples\llm-demo-out\campaign-llm.json
#   python -c "from tindalos.cli import app; app()" eval examples\llm-demo-out\campaign-llm.json --out examples\llm-demo-out\eval-llm.json
#   python -c "from tindalos.cli import app; app()" evolve examples\llm-demo-out\campaign-llm.json --rounds 1 --out examples\llm-demo-out\campaign-llm-evolved.json
#
# 在 Git Bash / WSL 下直接执行本脚本；cmd.exe 请用上方等价命令（.sh 需 bash）。

set -u

# 仓库根（脚本位于 examples/ 下）
cd "$(dirname "$0")/.." || exit 1

# --- bash 环境（cmd 等价写法见文件头）---
export TINDALOS_LLM_ENABLED=1
export TINDALOS_MODEL="${TINDALOS_MODEL:-deepseek-v3.1:671b-cloud}"
export OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://localhost:11434/v1}"
export TINDALOS_LLM_TIMEOUT="${TINDALOS_LLM_TIMEOUT:-300}"
export PYTHONIOENCODING=utf-8
export PYTHONPATH=src

MODULE="examples/sample-module.md"
OUT_DIR="examples/llm-demo-out"
CAMPAIGN="$OUT_DIR/campaign-llm.json"
EVAL_REPORT="$OUT_DIR/eval-llm.json"
EVOLVED="$OUT_DIR/campaign-llm-evolved.json"
mkdir -p "$OUT_DIR"

echo "== 0. 环境 =="
echo "OLLAMA_BASE_URL=$OLLAMA_BASE_URL"
echo "TINDALOS_MODEL=$TINDALOS_MODEL（覆盖：TINDALOS_MODEL=<可用模型>）"
if curl -s --max-time 5 "$OLLAMA_BASE_URL/models" >/dev/null 2>&1; then
  available=$(curl -s --max-time 5 "$OLLAMA_BASE_URL/models" \
    | python -c "import sys,json;print(' '.join(m['id'] for m in json.load(sys.stdin).get('data',[])))" 2>/dev/null)
  echo "Ollama 可达；可用模型：${available:-（列表解析失败）}"
  case " $available " in
    *" $TINDALOS_MODEL "*) echo "模型 $TINDALOS_MODEL 在可用列表中。";;
    *) echo "警告：模型 $TINDALOS_MODEL 不在可用列表，将降级为确定性生成（可设 TINDALOS_MODEL 换可用模型）。" >&2;;
  esac
else
  echo "警告：无法连接 Ollama（$OLLAMA_BASE_URL），LLM 将降级为确定性生成。" >&2
fi

run_cli() { python -c "from tindalos.cli import app; app()" "$@"; }

echo
echo "== 1. generate（--llm）=="
run_cli generate "$MODULE" --llm --out "$CAMPAIGN" || exit 1
echo "产物：$CAMPAIGN"
echo "  · 备团笔记：$OUT_DIR/notes.md"

echo
echo "== 2. eval（4 维评分）=="
run_cli eval "$CAMPAIGN" --out "$EVAL_REPORT" || exit 1
TOTAL=$(python -c "import json;print(json.load(open('$EVAL_REPORT',encoding='utf-8'))['total'])")
echo "产物：$EVAL_REPORT"
echo "总分数 total = $TOTAL"

echo
echo "== 3. evolve（自进化）=="
run_cli evolve "$CAMPAIGN" --rounds 1 --out "$EVOLVED" || exit 1
echo "产物：$EVOLVED"

echo
echo "== 完成：LLM 演示闭环（generate → eval → evolve）=="
echo "总分数 total = $TOTAL"
echo "产物：$CAMPAIGN / $EVAL_REPORT / $EVOLVED / $OUT_DIR/notes.md"
