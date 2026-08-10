# Tindalos 领域词汇表

> 本文件是 Tindalos 全仓库（`src/`、`tests/`、`docs/`、`.harness/`）的统一领域语言来源。任何文档与代码必须使用下表术语，禁止新造同义词；定义严谨、边界清晰，为评审与 eval 的判定基准。

## Language

**KP 主控**：读取模组文本并裁定剧本走向的人类权威角色；管线中以 `kp_parse`（解析 premise）与 `kp_plan`（拟定幕结构草案）节点抽象其职能，是分幕创作与备团笔记的最终读者。
_Avoid_: GM, 主持人, DM, game master

**NPC subagent**：并行派生的独立角色人格注入单元；每人一个 LangGraph 并行分支（`Send` 扇出），按 archetype 注入 personality 并产出 `acts_roles`（幕 id→角色）分工，输出必须可过 models 校验。
_Avoid_: 角色生成器, bot, agent（泛指）

**幕 Act**：剧本的一级结构单元，以罗马数字编号（`roman`），含一组场景（`scenes`）与本幕出场 NPC（`npc_ids`）；对应 kp_plan 的幕结构草案与 write_act 的每幕子图。
_Avoid_: chapter, 章, part

**场景 Scene**：幕内时间/地点设定（`setting.time`/`setting.place`）与其事件序列（`events`）的组合；`npc_ids` 声明本场景出场的 NPC，是 NPC 引用边（appears）的锚点。
_Avoid_: location, 地点, 布景

**事件 Event**：场景内剧情推进的最小节点，`kind` 为 entry（进入）/ trigger（触发）/ outcome（结局）三选一；`conditions` 为触发条件，`next_event_ids` 声明后继事件。
_Avoid_: beat, step, plot point

**线索 Clue**：调查员可获得的信息单元；`linked_event_ids` 绑定到具体事件、`linked_npc_ids` 绑定到相关 NPC，`found_at` 记录发现位置；悬空绑定（引用未注册 id）视为数据缺陷。
_Avoid_: hint, evidence, 证据（证据是现实术语，线索是剧本结构术语）

**世界知识图谱**：以 WorldRelation 六类语义边（认识/指向/起因/归属/获知/失效）组织实体间事实的推理层（kg.WorldGraph）；带时间窗语义（"当时为真"，非"现在为真"）与多跳路径推理（path），供线索推理与一致性检查使用。
_Avoid_: ontology, knowledge base, graph db

**备团笔记**：管线 compose 阶段为 KP 生成的 markdown 交付物，含幕标题、NPC 名与剧情要点，供 KP 现场主持参考；结构与内容由 eval 的 playability 维度评估。
_Avoid_: notes, prep doc, 笔记（未限定"备团"）

**分幕创作**：按幕拆分的剧本写作流程——每幕一个子图并行产出场景与事件序列，checkpoint 在 compose 前收敛；与 NPC 扇出（按人并行）是两个正交的并行维度。
_Avoid_: act writing, 幕写作, 分幕生成

**剧本节点图**：呈现层投影（models.ScriptGraph），把 Campaign 映射为 act/scene/event/npc/clue 五类节点（`{id,type,label,data}`）与 contains/appears/links 三类边的 JSON，供前端 React Flow 直接消费；与 WorldGraph 是同一 Campaign 的两张投影。
_Avoid_: node graph, 节点图（不带"剧本"限定语）, flow graph
