# Tindalos 模块架构设计（MVP）

> 状态：G1/G2 通过；为 inline 管线各 worker 的实现依据，也是面试可深讲的架构文档。
> 术语一律遵循 `CONTEXT.md`（由 t2 产出）词汇表：KP 主控 / NPC subagent / 幕 Act / 场景 Scene / 事件 Event / 线索 Clue / 世界知识图谱 / 备团笔记 / 分幕创作 / 剧本节点图。禁止新造同义词。

## 1. 背景与目标

Tindalos 是克苏鲁 TRPG 备团系统：KP 主控读取模组文本 → 自适应 NPC 生成（并行）→ 分幕创作（每幕子图）→ 产出备团笔记 + 剧本 JSON → 剧本节点图（React Flow，二期前端）。MVP 目标：以 LangGraph 多智能体管线（StateGraph 主图 + `@task`/`Send` 并行 + SqliteSaver checkpoint + InMemoryStore）端到端跑通，并在 **DeterministicGenerator 离线路径下零网络、零 LLM 完全可测**，配合 4 维 eval 与自进化闭环，使整个系统成为可复现、可归因、可演示的工程样本。

## 2. 模块划分表（codingMode 全为 spec-driven：契约先行，接口即验收）

| 模块 | 职责 | 关键接口（深模块接缝） | 依赖 | 测试接缝（G3 先红） |
|---|---|---|---|---|
| **models** | 领域模型：Campaign→Act→Scene→Event 层级 + NPC/Clue + WorldRelation 六类边 + ScriptGraph；内置跨层引用校验 | `Campaign`（acts/scenes/events 内 id 全局唯一；npc/clue 引用可解析校验）；`WorldRelation(source:str,target:str,type:RelationType,label:str,valid_from:str,valid_to:str\|None,note:str\|None)`；`RelationType`（KNOWS 认识/POINTS_TO 指向/CAUSES 起因/BELONGS_TO 归属/LEARNS 获知/EXPIRES 失效）；`ScriptGraph.from_campaign(campaign)->ScriptGraph`（nodes/edges 视图） | 仅 pydantic | dump→load 往返相等；悬空引用抛 `ValidationError`；`from_campaign` 节点/边数量断言；六类中文标签 |
| **kg** | 世界知识图谱：networkx 六类边 + 时间窗过滤 + 多跳路径线索推理 + 一致性检查；campaign↔world 映射 | `WorldGraph`：`add_entity(entity_id,kind,attrs)`、`add_relation(source,target,type,label,valid_from,valid_to=None)`（同源同型同窗不重复）、`relations_of(entity_id)->list[dict]`、`path(start,end,max_depth=5)->list[list]`（BFS 限深）、`active_relations(as_of=None)->list[dict]`（时间窗过滤）、`consistency_check()->list[str]`（悬空端点/有效窗重叠/valid_to<valid_from 倒置）、`to_json()/from_json()`（与 ScriptGraph 边 schema 对齐）；模块函数 `build_from_campaign(campaign)->WorldGraph`、`campaign_consistency(campaign,world)->list[str]` | models | 六类边增查、时间窗过滤（valid_to 已过 → 不含）、多跳 path、JSON 往返、三类矛盾均检出、build 后无遗漏 |
| **generator** | 生成协议 + 双实现：离线确定性 vs 可选 LLM | `Generator(Protocol)`：`generate_acts(premise,n_acts)->list[dict]`、`generate_npcs(premise,n)->list[NPC dict]`、`generate_scene(act_title,premise,npc_ids)->Scene dict`；`DeterministicGenerator`（模板+固定种子伪随机，结构完整）；`OllamaGenerator`（requests 调 `/chat/completions`，OpenAI 兼容 + function calling 声明，仅 `settings.llm_enabled` 时构造） | models、config | 确定性：同输入同输出；协议三方法产出可过 models 校验 |
| **pipeline** | LangGraph 主图编排：kp_parse→kp_plan→npc_fanout（Send 并行人格注入）→write_act（每幕子图）→compose（Campaign + 备团笔记 + progress 事件） | `state=PipelineState{module_text,premise,acts,npcs,world,campaign,progress:list[str]}`；`build_pipeline(settings)->CompiledStateGraph`；`kg_query(entity_id)` 工具挂 kp 节点（ToolNode，Function Calling 实证）；checkpoint=SqliteSaver(`settings.checkpoint_dir`)、store=InMemoryStore(namespace `('campaigns',cid,'facts')`)；`get_stream_writer` 发 custom 进度 | models、kg、generator、config | 端到端（小模组→合法 campaign）；笔记含幕标题与 NPC 名；progress 顺序 kp→npc→act→compose；同 thread_id 二次运行继承状态；store 新实例可读 |
| **eval_** | 4 维 rubric 评估 + 确定性检查 + 可选 LLM-judge + 失败源归因 | `RUBRIC: dict[str,dict[int,str]]`（structural/consistency/depth/playability，1/5 锚点）；`run_deterministic(campaign,world)->{dims:{dim:{score,evidence[]}},total,checks}`（models 校验 + `campaign_consistency` + 结构计数，低分带字段级 evidence）；`LLMJudge`（llm_enabled 时用，否则 `judge='none'`）；`eval_report(...)->dict`（总表 + attribution） | models、kg | 坏剧本低分且 evidence 命中具体字段；好剧本高分；attribution ∈ 四类；judge='none' 路径 |
| **evolve** | 自进化循环：eval→建议→确定性修复自动应用→重建 world→复评→loop_log | `evolve(campaign,world,pipeline,evaluator,rounds=2,out_path=None)->{campaign,report,loop_log}`；修复 (a) 悬空 npc 引用注册 (b) 空 scene 局部重生成（复用原 scene id，换 events）(c) KG 矛盾标记失效 `valid_to=now` (d) 无 linked 的 clue 补指首幕首事件 (e) LLM 建议仅记 pending 不自动应用；`loop_log` 含 `{round,applied,score_before,score_after,evidence}` | models、kg、pipeline、eval_ | 坏剧本 evolve 后 consistency 升 + 引用可解析 + 无空 scene；loop_log 有 delta；提前终止；两次运行结果一致 |
| **cli** | Typer 五命令入口 `tindalos` | `app`：`generate <module.md\|json> [--llm]`（出 campaign.json + notes.md）、`notes <campaign.json>`、`eval <campaign.json> [--judge]`（4 维分数表+归因+建议）、`evolve <campaign.json> --rounds 2`（出 evolved json + loop_log）、`kg <campaign.json> --entity <id> [--path-to <id>]`；成功退出码 0 / 失败非 0 | models、pipeline、eval_、evolve、config | CliRunner 冒烟：generate 出可 load 的 json、eval 退出 0 且含 structural、evolve 出文件、kg 查询非空 |
| **config** | 手写零依赖配置 | `@dataclass Settings`：`ollama_base_url`（env `OLLAMA_BASE_URL`，默认 `http://localhost:11434/v1`）、`model`（`TINDALOS_MODEL`，默认 `deepseek-r1`）、`llm_enabled`（`TINDALOS_LLM_ENABLED=='1'`）、`checkpoint_dir`（`data/checkpoints`）、`store_dir`（`data/store`）；`get_settings()->Settings` 单例 | 仅标准库 | 默认值断言（llm_enabled False、base_url 默认） |

## 3. 依赖图（模块级）

```
                 config（零依赖，叶子）
                   │
models（零依赖）    ├────── generator ────┐
   │  ▲            │                      │
   │  └──── kg ────┼──── pipeline ◄──────┘
   │               │         │
   ├── eval_ ◄─────┼── evolve ◄──── eval_ + pipeline
   │               │         │
   └── cli ◄───────┴── evolve + eval_ + pipeline + config + models
```

阻塞边（planner 用）：t1 scaffold → t4；t2 models → t3/t4/t5；t3 kg → t4/t5；t4 pipeline → t6/t7；t5 eval_ → t6/t7；t6 evolve → t7。**kg 依赖 models 的类型**（`RelationType`），pipeline 依赖 kg（`kg_query` 工具）与 generator（协议注入），eval_ 依赖 kg（`campaign_consistency`），evolve 依赖 pipeline（局部重生成）与 eval_（复评）。

## 4. 关键设计决策与理由（含备选对比）

1. **Generator 协议：离线可测优先，LLM 为可选升级**。`Generator(Protocol)` 让 pipeline 与实现解耦；`DeterministicGenerator`（模板 + 固定种子）保证 CI 零网络零 LLM 全绿；`OllamaGenerator` 仅 `llm_enabled` 时构造，同一协议下的真实 LLM 调用是"质量升级"而非"功能必需"。备选：直接硬编码 LLM 调用 —— 被拒，CI 不可测、成本不可控、面试不可复现。
2. **Send 并行（npc_fanout）而非 supervisor**。LangGraph 1.x 官方已弃维护 prebuilt supervisor；`Send` 显式扇出保证确定性执行序与 progress 事件可断言，supervisor 引入额外 LLM 往返，破坏"零 LLM 可测"。每幕 write_act 用 `@task`/子图并行，checkpoint 收敛于 compose 前，规避并发写竞态。
3. **时间窗语义："当时为真"，非"现在为真"**。`active_relations(as_of)` 返回 `valid_to` 在 as_of 之后的关系——线索推理针对的是**该时刻的叙事事实**（第一幕为真的关系在第一幕推理时仍可用，尽管后来被 EXPIRES 失效）。这与 evolve 修复 (c)"矛盾标记失效而非删除"一脉相承：KG 保留历史，语义由时间窗表达。备选：物理删除过期边 —— 被拒，破坏线索回溯与失效归因。
4. **双图分层：剧本图 ↔ 世界图**。`ScriptGraph`（models）是**呈现层**——Act/Scene/Event 层级 + NPC/Clue 引用边，schema 供前端 React Flow 直接消费（二期）；`WorldGraph`（kg）是**推理层**——六类语义边 + 时间窗，供 `path()`/`active_relations()`/`consistency_check()` 推理。`build_from_campaign` 与 `ScriptGraph.from_campaign` 是同一 Campaign 的两张投影，边 schema（source/target/type/label/valid_from/valid_to）两端对齐，由 `campaign_consistency` 保证两图与剧本互不漂移。
5. **eval 归因四类判定规则**：每维低分 evidence 先映射确定性检查项——schema 校验失败/id 重复/缺层级 → `structure`；悬空引用/KG 矛盾/clue 无目标 → `data`；纯内容判断（personality 空洞、深度不足）→ `model`；检查项自相矛盾或分数无法区分好坏剧本 → `evaluation`。规则可复现：同一剧本两次 eval 归因必同。四类覆盖了缺陷的可能来源，是自进化修复的分流依据（structure/data 可确定性修，model 走 LLM 建议 pending，evaluation 修 rubric 本身）。
6. **自进化收敛与幂等**：收敛 = 当轮无修复**且**无分数提升 → 提前终止，`rounds` 上限兜底（防死循环）；幂等 = 修复全部由确定性规则驱动（注册/复用 id 重生成/标记失效/补指），LLM 建议只记录不自动应用 → 同输入两次运行结果一致，`loop_log` 即自进化简历实证。

## 5. 实现约束清单

- **零网络测试**：pytest 全程禁止真实网络；默认路径 DeterminististicGenerator；`OllamaGenerator`/`LLMJudge` 仅在 `llm_enabled=True` 时构造；judge 禁用返回 `'none'`。所有冒烟/单测在 sandbox 内跑（`.sandbox/Dockerfile` + `requirements.txt`），pytest 全绿为门禁。
- **config 零依赖手写**：仅 `dataclass` + `os.environ`，禁 pydantic-settings；`get_settings()` 单例。
- **文件布局**：`src/tindalos/{config,models,kg,generator,pipeline,evolve,cli}.py` + `src/tindalos/eval_/{__init__,rubric,deterministic,judge,report}.py` + `tests/`（test_scaffold/models/kg/backend/eval/evolve/cli.py）+ `docs/architecture.md` + `CONTEXT.md`。
- **模型纪律**：Campaign 校验只允许跨层 id 唯一性与引用可解析类规则，不写业务启发式（防 eval/evolve 无法注入坏剧本做红测试）。
- **接口即契约**：本文件签名与 `plan.json` spec 一致，worker 不得改名改参；后端脚本入口 `[project.scripts] tindalos=tindalos.cli:app`。

## 6. 风险与缓解

| 风险 | 缓解 |
|---|---|
| Send 并行 + SqliteSaver 并发写 checkpoint 冲突 | 并行节点只读 state，写收敛于 compose 单点；确定性任务无竞态 |
| 时间窗语义被误用为"删除"（valid_to 后不再返回） | `active_relations` 语义单测锁定"当时为真"；文档第 4.3 条为验收基准 |
| LLM 输出 schema 不稳定（judge/generator） | function calling 声明强制 JSON；judge 5 键校验失败降级 deterministic-only，不污染评估 |
| evolve 重生成破坏 id 唯一 | 局部重生成复用原 scene id 只换 events；收敛提前终止 + rounds 上限双保险 |
| 前端二期消费 schema 漂移 | ScriptGraph JSON schema 即契约，`from_campaign` 数量断言 + `to_json/from_json` 往返测试锁定 |
| `campaign_consistency` 漏检导致坏剧本溜过 | kg 的 `consistency_check` 三类矛盾 + eval 确定性检查清单双层覆盖，证据字段级可定位 |
