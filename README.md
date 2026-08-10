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

## 求职叙事

> TODO：待补充——为什么做这个项目、技术亮点（LangGraph 多智能体编排 / 离线确定性可测 / 4 维 eval 自进化闭环）、可演示路径。
