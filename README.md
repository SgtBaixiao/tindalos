# Tindalos

克苏鲁 TRPG 备团系统：KP 主控 → 自适应 NPC 生成 → 分幕剧本 → 备团笔记 → 剧本节点图。

## 技术栈

- **LangGraph** — 多智能体管线编排（StateGraph 主图 + Send 并行扇出 + SqliteSaver checkpoint + InMemoryStore）
- **NetworkX** — 世界知识图谱（六类语义边 + 时间窗推理 + 多跳路径线索推理）
- **Pydantic v2** — 领域模型与跨层校验（Campaign → Act → Scene → Event 层级 + NPC/Clue/关系）
- **Typer** — CLI 入口（`tindalos` 命令，由 `src/tindalos/cli.py` 提供）

## 开发

```bash
# 测试（沙箱内执行，零网络零 LLM）
python -m pytest tests/ -q

# src 布局免安装 import（沙箱只读无网络，无法 pip install -e）
python -c "import sys; sys.path.insert(0, 'src'); import tindalos; print(tindalos.__version__)"
```

> pytest 通过 `tests/conftest.py` 自动把 `src/` 加入 `sys.path`（src 布局，免安装）；`python -c` 不加载 conftest，需显式 `PYTHONPATH=src`。

## Usage

CLI 入口为 `tindalos`（Typer，`src/tindalos/cli.py`，经 `[project.scripts]` 暴露）。安装后直接使用；未安装时可用 `PYTHONPATH=src python -c "from tindalos.cli import app; app()" <args>` 等价调用。五命令示例：

```bash
# ① 生成 campaign：模组文本（.md 或含 premise/title 的 .json）→ 分幕剧本 + 同目录 notes.md 备团笔记
#    默认 DeterministicGenerator；--llm 且 TINDALOS_LLM_ENABLED=1 时改用 OllamaGenerator
PYTHONPATH=src python -c "from tindalos.cli import app; app()" generate module.md --out campaign.json
PYTHONPATH=src python -c "from tindalos.cli import app; app()" generate module.json --out campaign.json --llm

# ② 重生成备团笔记（campaign JSON → notes.md）
tindalos notes campaign.json --out notes.md

# ③ 评测：4 维分数表 + 归因 + 建议；JSON 到 stdout（无 --out）或 --out 文件
#    --judge 启用 LLM 裁判（需 TINDALOS_LLM_ENABLED=1，否则降级 judge=none）
tindalos eval campaign.json
tindalos eval campaign.json --judge --out eval.json

# ④ 自进化：eval → 确定性修复 → 复评，循环 rounds 轮（打印 loop_log）
tindalos evolve campaign.json --rounds 2 --out campaign.evolved.json

# ⑤ 世界知识图谱查询：实体关系 / 多跳路径
#    成功退出码 0；输入缺失 / 实体未知 → 非 0 退出码
tindalos kg campaign.json --entity npc-1
tindalos kg campaign.json --entity npc-1 --path-to clue-act-1
```

## LLM 模式（可选）

默认全程离线确定性（零网络零 LLM）；开启后 `generate --llm` 改用 Ollama（OpenAI 兼容
`/chat/completions`）生成幕/NPC/场景草案，`eval --judge` 启用 LLM 裁判。

### 开启方式（环境变量）

```bash
# Git Bash / Linux
export TINDALOS_LLM_ENABLED=1
export TINDALOS_MODEL=qwen2.5:0.5b   # 模型名（默认 deepseek-r1，按本地 Ollama 实际模型覆盖）
export OLLAMA_BASE_URL=http://localhost:11434/v1 # OpenAI 兼容端点（默认即此）
export TINDALOS_LLM_TIMEOUT=300                  # 单次请求超时秒数（默认 180）
export TINDALOS_LLM_MAX_RETRIES=2                # 网络抖动/5xx/429 重试次数（默认 2）

# Windows cmd.exe（双写法）
# set TINDALOS_LLM_ENABLED=1 && set TINDALOS_MODEL=qwen2.5:0.5b && set PYTHONIOENCODING=utf-8
```

`--llm` 仅在 `TINDALOS_LLM_ENABLED=1` 时生效；未开启时 CLI 向 stderr 告警并回退确定性生成器。

### 用法

```bash
PYTHONPATH=src python -c "from tindalos.cli import app; app()" generate examples/sample-module.md --llm --out campaign-llm.json
PYTHONPATH=src python -c "from tindalos.cli import app; app()" eval campaign-llm.json --judge --out eval-llm.json
```

一键演示（generate → eval → evolve，打印每步产物路径与总分数）：

```bash
bash examples/llm-demo.sh
```

### 降级语义（LLM 失败不阻塞管线）

- LLM 调用失败（连接失败/超时/4xx/5xx 重试耗尽）或回复无法解析（坏 JSON/缺少必要字段）
  → 该次生成回退 `DeterministicGenerator`，并向 stderr 发 `UserWarning`（如
  `OllamaGenerator 生成失败（generate_npcs：无有效 JSON），回退 DeterministicGenerator`）；
- 容错细节：`OllamaGenerator` 剥离 `\`\`\`json / \`\`\`` 围栏（含未闭合围栏）与前后缀说明文字，
  支持顶层数组与工具调用（`tool_calls[].function.arguments`）解析，并对模型输出的
  int id / 非法事件 kind 等做规整；
- 因此即使模型名错误/服务不可用，`generate --llm` 仍成功退出（产出确定性 campaign），
  评测/自进化照常执行——LLM 是可选的“增强层”，不是硬依赖。

## 求职叙事

> TODO：待补充——为什么做这个项目、技术亮点（LangGraph 多智能体编排 / 离线确定性可测 / 4 维 eval 自进化闭环）、可演示路径。
