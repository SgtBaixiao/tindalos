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

> `sitecustomize.py` 让仓库根目录下的脚本/模块运行（`python xxx.py`、`python -m pytest`）免安装直接 import src 布局包；`python -c` 因解释器启动时序不加载 cwd 下的 sitecustomize，需显式加入 `src`。

## 求职叙事

> TODO：待补充——为什么做这个项目、技术亮点（LangGraph 多智能体编排 / 离线确定性可测 / 4 维 eval 自进化闭环）、可演示路径。
