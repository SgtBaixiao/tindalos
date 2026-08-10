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

## 为什么做这个项目（求职叙事）

克苏鲁 TRPG 备团是垂直场景，但造它的技术动机是通用的：

> **问题**：多智能体 Agent 系统（主控 → 并行子 agent → 分幕产出）怎么设计才能**离线可测、可评估、能自修复**？
> LLM 输出不可靠时如何优雅降级？产出物怎么让人类**可视、可编辑**（而不是一坨文本）？

Tindalos 用一条完整链路回答：LangGraph 多智能体编排（KP 主控 → Send 并行 NPC → 每幕子图）
+ 世界知识图谱（跨会话记忆与线索推理） + 4 维 eval + **自进化闭环**（eval → 确定性修复 → 复评，
坏剧本 3.2→5.0 收敛） + React Flow 剧本节点图（点击抽屉编辑）。

**可直接讲的技术亮点**（面试口头版）：

1. **多智能体拓扑经过实证选型**：对比 supervisor（官方弃维护）后采用自定义 StateGraph 主图 + `@task`/`Send` 并行 + 每幕子图——确定性编排 + 并行扇出，语义与 KP→NPC 的实际工作流同构；sqlite checkpoint 跨进程恢复、Store 跨会话记忆、custom 进度流（前端进度带的数据源）全部本机实测跑通。
2. **离线确定性可测是设计铁律**：`Generator` 协议（Deterministic 离线 / Ollama 可选）——全部 140 个测试零网络零 LLM，在无网络加固沙箱内跑；LLM 失败按设计降级（根因告警带 HTTP 状态码，不吞栈）。
3. **eval 不是脚本，是闭环**：4 维 rubric（结构/一致性/深度/可玩性，1-5 锚点）+ 确定性检查清单（schema 合法/id 唯一/引用可解析/KG 无矛盾…）+ LLM-judge 可选降级 + **失败源归因四类**（structure/data/model/evaluation）；`evolve` 把建议变成确定性修复（注册悬空 NPC/重生成空场景/失效重叠关系/补线索链接），loop_log 记录每轮 applied/delta/evidence，收敛提前终止、幂等。
4. **开发过程本身就是自进化的实证**：本仓库经 harness 门管线构建（G0–G7），双轴评审抓到并修复 6+ 处测试没覆盖的真实缺陷（含默认 checkpointer 崩溃、重叠永久关系窗、事件 id 无限循环、退役模型 410 假成功）——180+ 测试全绿。

**面试可追问的答案**（深挖弹药）：

- *为什么不用 supervisor？* 官方已标记弃维护；KP→NPC 是确定流程 + 并行子任务，StateGraph + Send 更贴切，可控可测。
- *为什么知识图谱不上 Neo4j？* 数百节点本地场景 NetworkX + JSON 图足够；图的价值在**可解释多跳推理、时间有效窗（当时为真 vs 现在为真）、实体中心记忆**，不在存储引擎。Kuzu 已弃维护，graphiti/cognee 是业界参照（LongMemEval +18.5%）。
- *eval 的分数可信吗？* 确定性检查可复现；LLM-judge 双模型取 min 保守聚合（dualJudge）；归因先查评测的锅（CORE-bench 42%→95% 案例），再谈模型。
- *自进化会不会越修越坏？* 确定性修复白名单 + 收敛提前终止 + 幂等（同输入两次运行结果一致）+ rounds 上限；LLM 建议只记 pending 不自动应用（人审待定）。

**一键演示路径**：

```bash
bash examples/llm-demo.sh                      # LLM 模式全链路（generate→eval→evolve）
PYTHONPATH=src python -m tindalos.cli generate examples/sample-module.md --out examples/campaign.json
PYTHONPATH=src python -m tindalos.cli eval examples/campaign.json
PYTHONPATH=src python -m tindalos.cli evolve examples/campaign-broken.json --rounds 3 --out /tmp/evolved.json  # 坏剧本 3.2→5.0
cd frontend && npm run dev                     # 剧本节点图（点击节点 → 抽屉编辑）
```

仓库其他材料：领域词汇表 `CONTEXT.md`、模块架构设计 `docs/architecture.md`（面试可深讲）、
前端设计系统继承 house-style（暖色极简 + 暖墨深色板，见 `frontend/src/theme.css`）。
