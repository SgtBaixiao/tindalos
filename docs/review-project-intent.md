# Tindalos 产品意图研究

本文供项目评审者判断 Tindalos 面向谁、要解决什么问题、设计要求如何验收，以及哪些要求仍有歧义。研究基于 `main` 的 `feca892bfbaf7b7a9ca04dc87b2908ad03d7fc9f`，阅读日期为 2026-11-19。

本次只阅读领域词汇表、两份指定设计文档、全部四份参考文档，以及示例目录中的备团笔记、演示脚本和样例数据；必要的历史查询也限定在这些路径。未阅读 README、后端实现、前端实现或测试，未运行生成、评测、修复或演示脚本。下文的“要求”“计划”均指设计材料；文档中的“现状”“已实现”归为作者自述，样例中的结果归为保存的产物，均不构成本次对当前实现的验收。

## 主要判断

- 产品的直接服务对象是掌握剧情裁定权的 KP 主控，核心交付是分幕剧本与现场可用的备团笔记。（设计事实；[CONTEXT.md:7–8](/Users/bytedance/Documents/ChatGPT/Tindalos/source/CONTEXT.md:7)、[CONTEXT.md:28–29](/Users/bytedance/Documents/ChatGPT/Tindalos/source/CONTEXT.md:28)）
- 产品范围按单用户个人工具设定，默认采用本地 SQLite；多设备和团队协作只是数据库升级的条件。（设计事实；[docs/cloud-memory-eval-design.md:3–5](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/cloud-memory-eval-design.md:3)、[docs/cloud-memory-eval-design.md:14–15](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/cloud-memory-eval-design.md:14)）
- 产品同时承担工程演示用途：离线确定性运行、缺陷归因与可复现的修复过程属于明确目标。（设计事实；[docs/architecture.md:3–8](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/architecture.md:3)、[docs/architecture.md:45–46](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/architecture.md:45)）
- 叙事质量要求已经超出数据合法性，包括检定失败后的替代路径、线索回收、NPC 动机和恐怖递进；后期设计增加忠实度与 KP 可用性评测来检查这些内容。（归纳；[references/style-guide.md:9–36](/Users/bytedance/Documents/ChatGPT/Tindalos/source/references/style-guide.md:9)、[docs/cloud-memory-eval-design.md:166–170](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/cloud-memory-eval-design.md:166)）
- 设计从 MVP 的自动确定性修复，发展到分层记忆、不可变评测记录及人工确认后写回；两份设计的权限边界和阶段划分需要一起核对。（版本归纳；[docs/architecture.md:19](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/architecture.md:19)、[docs/cloud-memory-eval-design.md:88–91](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/cloud-memory-eval-design.md:88)、[docs/cloud-memory-eval-design.md:158–185](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/cloud-memory-eval-design.md:158)）

## 术语沿用领域词汇表

下表只解释后文需要的概念，不新增领域命名。

| 术语 | 在产品中的含义 | 来源 |
|---|---|---|
| KP 主控 | 读取模组、裁定剧本走向的人类权威，也是备团笔记的最终读者。 | [CONTEXT.md:7–8](/Users/bytedance/Documents/ChatGPT/Tindalos/source/CONTEXT.md:7) |
| NPC subagent | 按 NPC 独立注入人格的并行单元，产出人格和各幕分工。 | [CONTEXT.md:10–11](/Users/bytedance/Documents/ChatGPT/Tindalos/source/CONTEXT.md:10) |
| 幕／场景／事件 | 剧本按幕组织；场景包含时间、地点和事件序列；事件分为进入、触发、结局。 | [CONTEXT.md:13–20](/Users/bytedance/Documents/ChatGPT/Tindalos/source/CONTEXT.md:13) |
| 线索 | 调查员可获得的信息，记录事件绑定、NPC 绑定和发现位置。 | [CONTEXT.md:22–23](/Users/bytedance/Documents/ChatGPT/Tindalos/source/CONTEXT.md:22) |
| 世界知识图谱 | 用六类语义关系、时间窗和多跳路径表达叙事事实，供推理与一致性检查使用。 | [CONTEXT.md:25–26](/Users/bytedance/Documents/ChatGPT/Tindalos/source/CONTEXT.md:25) |
| 剧本节点图 | 同一剧本的呈现投影，包含幕、场景、事件、NPC、线索五类节点及包含、出场、关联三类边。 | [CONTEXT.md:34–35](/Users/bytedance/Documents/ChatGPT/Tindalos/source/CONTEXT.md:34) |
| 忠实度（faithfulness） | 检查生成声明是否得到模组语料支持，并提供可复核的证据引用。 | [docs/cloud-memory-eval-design.md:169](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/cloud-memory-eval-design.md:169)、[docs/cloud-memory-eval-design.md:241](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/cloud-memory-eval-design.md:241) |

## 产品服务 KP 的备团与续团

KP 主控保留剧情裁定权。参考材料描述的现场节奏是 KP 主控给出环境与遭遇，玩家宣告行动，再交代后果；管线把模组解析和幕结构规划抽象为创作步骤。因此，设计中的多智能体生成应服务于备团与裁定，不宜据此推定产品承诺全自动带团。（归纳；[CONTEXT.md:7](/Users/bytedance/Documents/ChatGPT/Tindalos/source/CONTEXT.md:7)、[references/kp-guidance.md:11–13](/Users/bytedance/Documents/ChatGPT/Tindalos/source/references/kp-guidance.md:11)、[docs/architecture.md:17](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/architecture.md:17)）

以下按使用场景区分初始设计、扩展计划和后期文档自述。

| 使用场景 | 预期帮助与边界 | 来源 |
|---|---|---|
| 将模组整理为可用剧本 | 读取模组文本，自适应生成 NPC，并行分幕创作，交付剧本 JSON 与备团笔记。 | [docs/architecture.md:8](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/architecture.md:8) |
| 现场查阅与线索推理 | 备团笔记提供幕标题、NPC 名和剧情要点；世界知识图谱支持实体关系、历史时点和多跳路径查询。 | [CONTEXT.md:25–29](/Users/bytedance/Documents/ChatGPT/Tindalos/source/CONTEXT.md:25)、[docs/architecture.md:15、20](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/architecture.md:15) |
| 检查并修复备团材料 | 评测结构、一致性、深度和可玩性，定位失败字段与缺陷来源，再修复结构或数据问题。 | [docs/architecture.md:18–20、45](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/architecture.md:18) |
| 跨会话续团 | 扩展计划采集 KP 回叙或实际游玩情况，更新游玩状态与冲突决策，生成“上次停在哪”的会后简报。 | [docs/cloud-memory-eval-design.md:148–149](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/cloud-memory-eval-design.md:148)、[docs/cloud-memory-eval-design.md:216–227](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/cloud-memory-eval-design.md:216) |
| 浏览、问答与局部修改 | 后期文档自述已有混合检索、问答、历史和局部重生成接口；扩展设计拟复用这些入口，未提供完整交互规格。 | [docs/cloud-memory-eval-design.md:26–31](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/cloud-memory-eval-design.md:26)、[docs/cloud-memory-eval-design.md:132–134](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/cloud-memory-eval-design.md:132) |
| 展示工程能力 | 架构材料明确面向面试讲解；演示脚本组织生成、评测、修复三个步骤并打印产物。 | [docs/architecture.md:3、8](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/architecture.md:3)、[examples/llm-demo.sh:4–5、63–83](/Users/bytedance/Documents/ChatGPT/Tindalos/source/examples/llm-demo.sh:4) |

## 功能要求覆盖创作与质量检查

### MVP 明确规定的功能

这些是架构文档的契约要求，不能据表中接口名推定当前已经交付。

| 功能 | 文档要求 | 来源 |
|---|---|---|
| 分幕创作与人格分工 | 主流程依次解析模组、规划幕、并行派生 NPC、按幕创作、合成剧本与备团笔记。NPC 按人格原型注入 personality，并产出各幕分工；NPC 与幕是两个独立的并行维度。 | [CONTEXT.md:10、31](/Users/bytedance/Documents/ChatGPT/Tindalos/source/CONTEXT.md:10)、[docs/architecture.md:17、42](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/architecture.md:17) |
| 结构化交付与引用校验 | 剧本包含幕、场景、事件、NPC、线索和世界关系；跨层 id 唯一，NPC 与线索引用可解析，序列化往返保持相等。 | [docs/architecture.md:14](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/architecture.md:14) |
| 双图投影 | 剧本节点图供 React Flow 消费；世界知识图谱负责语义推理。两图均由同一剧本投影，需检查与剧本的一致性。前端在 MVP 文档中列为二期。 | [CONTEXT.md:34](/Users/bytedance/Documents/ChatGPT/Tindalos/source/CONTEXT.md:34)、[docs/architecture.md:8、44、64](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/architecture.md:8) |
| 世界知识图谱查询 | 支持认识、指向、起因、归属、获知、失效六类关系；按时点筛选有效关系，搜索多跳路径，检出悬空端点、重叠时间窗和倒置时间窗。路径搜索默认最大深度为 5。 | [docs/architecture.md:14–15、43](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/architecture.md:14) |
| 两种生成路径 | 同一生成协议提供离线确定性生成与可选 LLM 生成；确定性路径要求同输入同输出，LLM 路径只有显式启用后才构造。 | [docs/architecture.md:16、41、50](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/architecture.md:16) |
| 进度与状态续接 | 管线发出解析、NPC、幕、合成顺序的进度；同 thread_id 再次运行继承状态。检查点在合成前收敛，避免并发写入。 | [docs/architecture.md:17、42、60](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/architecture.md:17) |
| 四维评测与归因 | 结构、一致性、深度、可玩性按 rubric 打分，低分给出字段级证据。失败来源分为结构、数据、模型、评测规则四类；同一剧本再次评测应归因一致。 | [docs/architecture.md:18、45](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/architecture.md:18) |
| 自进化修复 | 注册悬空 NPC；局部重生成空场景并保留场景 id；标记矛盾关系失效；给无绑定线索补目标。重建世界知识图谱、复评并记录轮次、应用动作、分数变化和证据；LLM 建议只记待处理。 | [docs/architecture.md:19、46](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/architecture.md:19) |
| 命令行入口 | 五个入口分别承担生成、导出备团笔记、评测、修复、世界知识图谱查询；成功退出码为 0，失败为非 0。 | [docs/architecture.md:20](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/architecture.md:20) |

### 扩展设计要求分层记忆

扩展文档将快照重建列为需要重构的现状，目标改为追加事件、版本化事实与会话上下文。下表中的记忆行为都属于该设计。（[docs/cloud-memory-eval-design.md:16、27、88–91](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/cloud-memory-eval-design.md:16)）

| 功能 | 文档要求 | 来源 |
|---|---|---|
| 情景与语义记忆 | 情景按事件 id 幂等追加；语义按 subject_key 去重合并，保留旧版本。写入在剧本合成成功后单点完成。 | [docs/cloud-memory-eval-design.md:88–89、122–125](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/cloud-memory-eval-design.md:88) |
| 短期与长期记忆 | 短期上下文包含近因事件、当前场景、活跃 NPC 与未决线索；长期层保存综合摘要、剧情线与 NPC 发展轨迹。 | [docs/cloud-memory-eval-design.md:90–92](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/cloud-memory-eval-design.md:90) |
| 检索与创作注入 | 分幕创作前用 BM25 检索相关记忆，并按近因加权；短期上下文还包含长期简报。 | [docs/cloud-memory-eval-design.md:129–130、204](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/cloud-memory-eval-design.md:129) |
| 整合与历史保留 | 旧版本标记为被替代或已整合，不主动物理删除；情景条目超上限后整合，语义以时间窗记录新旧事实。LLM 整合提供确定性降级。 | [docs/cloud-memory-eval-design.md:136–141](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/cloud-memory-eval-design.md:136) |
| 重生成后更新记忆 | 局部改节点后更新情景内容，并将受影响的语义、长期条目标记为被替代，避免旧记忆残留。 | [docs/cloud-memory-eval-design.md:132–134、242](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/cloud-memory-eval-design.md:132) |
| 查询与展示 | 命令行按四类列出记忆，拟新增按剧本查询记忆的 API；记忆页面展示四类记忆与剧情线状态，完整可视化列为 P3。 | [docs/cloud-memory-eval-design.md:147、189–190、229–231](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/cloud-memory-eval-design.md:147) |

### 扩展设计要求保存评测证据

每次评测拟保存不可变运行记录，包含被测剧本或模组、生成参数与种子、各层结果和总判定；评分注释指回具体条目。前端应能展开一次评测查看证据，支持回放、对比与归因。（[docs/cloud-memory-eval-design.md:158–160](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/cloud-memory-eval-design.md:158)）

| 评测层 | 检查目标与限制 | 来源 |
|---|---|---|
| L1 结构 | 数据合法、id 唯一、引用可解析、每幕至少一个场景、每场景至少一个事件、NPC 有人格、线索有绑定；失败短路并建议修复。 | [docs/cloud-memory-eval-design.md:166](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/cloud-memory-eval-design.md:166) |
| L2 图谱一致性 | 检查端点、有效时间窗、剧本与世界知识图谱的双向投影，以及线索可达性；失败短路。 | [docs/cloud-memory-eval-design.md:167](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/cloud-memory-eval-design.md:167) |
| L3 内容质量 | 保留四维评分，LLM 重点检查深度与可玩性；抽样 30% 场景，并设事件质量门。裁判设置包括 CoT、证据引用与 temperature=0。 | [docs/cloud-memory-eval-design.md:168](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/cloud-memory-eval-design.md:168) |
| L4 忠实度 | 拆分生成声明，再用模组检索判断支持度，检出无依据生成或漂移；证据供人工复核。 | [docs/cloud-memory-eval-design.md:169、241](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/cloud-memory-eval-design.md:169) |
| L5 KP 可用性 | 检查备团笔记能否直接带团，包括通往结局的线索链、NPC 卡片自洽和时间线一致；只允许手动触发。 | [docs/cloud-memory-eval-design.md:170](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/cloud-memory-eval-design.md:170) |
| L6 回归 | 比较同一剧本的新旧快照与维度分数，重放历史评测记录。 | [docs/cloud-memory-eval-design.md:171](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/cloud-memory-eval-design.md:171) |

扩展设计拟新增评测列表、单次记录、手动运行和剧本记忆查询四类 API，评测页展示记录树、证据与分数。结构或数据修复需要人工确认后写回；内容问题保留为待处理建议，人工确认后再转为生成配置或风格规范变更。（[docs/cloud-memory-eval-design.md:183–190](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/cloud-memory-eval-design.md:183)）

## 质量要求以现场可用为目标

### 叙事要能回应玩家行动

参考文档把“可描述、可回应、有后果”写成每个场景的要求。检定需对应明确目标，失败应产生代价和后续出路。这些要求影响实际带团质量，无法用事件数量或引用合法性单独证明。（前两句为设计事实，末句为归纳；[references/kp-guidance.md:11–21、37、45–48](/Users/bytedance/Documents/ChatGPT/Tindalos/source/references/kp-guidance.md:11)、[docs/cloud-memory-eval-design.md:166、170](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/cloud-memory-eval-design.md:166)）

| 质量要求 | 明确判据 | 来源 |
|---|---|---|
| 任务持续推进 | 每幕有任务推进；时间表规范要求每幕至少一个事件、一个新 NPC 和一条线索或危机，灾难后留后遗症阶段。 | [references/kp-guidance.md:45](/Users/bytedance/Documents/ChatGPT/Tindalos/source/references/kp-guidance.md:45)、[references/style-guide.md:18–19](/Users/bytedance/Documents/ChatGPT/Tindalos/source/references/style-guide.md:18) |
| 检定节奏与难度 | 普通日常免检定，冲突或戏剧性时刻才检定；常规、困难、极难对应技能值、技能值一半、技能值五分之一。对手技能至少 50 时困难，至少 90 时极难。 | [references/kp-guidance.md:19–32](/Users/bytedance/Documents/ChatGPT/Tindalos/source/references/kp-guidance.md:19) |
| 成败后果完整 | 结局事件同时给成功与失败后果；关键线索有替代获得路径，避免一次失败令故事停滞。 | [references/kp-guidance.md:37、47](/Users/bytedance/Documents/ChatGPT/Tindalos/source/references/kp-guidance.md:37)、[references/style-guide.md:13](/Users/bytedance/Documents/ChatGPT/Tindalos/source/references/style-guide.md:13) |
| 线索前后呼应 | 每条线索至少呼应一次，调查阶段的细节在转折或高潮回收；支持跨场景互证、多级发现和搜索时间成本。 | [references/scenario-design.md:38–45、64–68](/Users/bytedance/Documents/ChatGPT/Tindalos/source/references/scenario-design.md:38) |
| NPC 行为可信 | 每个 NPC 有动机、信息功能和可辨识细节；重要反派给行为约束，策略由动机推导。 | [references/scenario-design.md:51–58](/Users/bytedance/Documents/ChatGPT/Tindalos/source/references/scenario-design.md:51)、[references/style-guide.md:11、35](/Users/bytedance/Documents/ChatGPT/Tindalos/source/references/style-guide.md:11) |
| 恐怖递进与留白 | 事件描述先写感官，再写行动与后果；每幕恐怖递增，终幕前以痕迹和侧写为主，不暴露怪物全貌，神话线索不自我解说。 | [references/lovecraftian-style.md:69–72](/Users/bytedance/Documents/ChatGPT/Tindalos/source/references/lovecraftian-style.md:69) |
| 氛围连续 | 全剧选 1–2 个环境或物象母题，每幕至少出现一次变奏；用具体器物、痕迹、尺寸、年代与人物细节建立可信度。 | [references/scenario-design.md:76–80](/Users/bytedance/Documents/ChatGPT/Tindalos/source/references/scenario-design.md:76)、[references/style-guide.md:20、26](/Users/bytedance/Documents/ChatGPT/Tindalos/source/references/style-guide.md:20) |
| 理智损失可演绎 | 使用 san 0/1、san 1/1d8 等成败损失格式，并描述幻觉、遗忘、执念等具体症状及其行为后果。 | [references/scenario-design.md:67、104–110](/Users/bytedance/Documents/ChatGPT/Tindalos/source/references/scenario-design.md:67)、[references/style-guide.md:29](/Users/bytedance/Documents/ChatGPT/Tindalos/source/references/style-guide.md:29) |

### 工程要求保证复现与降级

| 质量要求 | 明确判据 | 来源 |
|---|---|---|
| 离线可复现 | 默认生成与 CI 测试零网络、零 LLM；固定输入输出一致，LLM 属于显式启用的质量升级。 | [docs/architecture.md:16、41、50](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/architecture.md:16) |
| 模型校验保持边界 | 模型只校验跨层 id 唯一与引用可解析，不加入业务启发式，以便评测和修复能接受有内容缺陷的剧本。 | [docs/architecture.md:53](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/architecture.md:53) |
| 失败不能污染评测 | LLM 结构不稳定时校验输出；裁判校验失败降级为纯确定性评测。演示说明还要求 LLM 不可用或失败时降级生成并给出警告。 | [docs/architecture.md:62](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/architecture.md:62)、[examples/llm-demo.sh:4–5、54–57](/Users/bytedance/Documents/ChatGPT/Tindalos/source/examples/llm-demo.sh:4) |
| 修复有界且幂等 | 没有修复且没有分数提升时提前终止，轮数有上限；局部重生成保留场景 id，确定性修复同输入再次运行结果一致。 | [docs/architecture.md:19、46、63](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/architecture.md:19) |
| 历史可追溯 | 世界知识图谱保留过期关系，用历史时点推理；扩展记忆保留被替代或整合条目；评测记录追加写入。 | [docs/architecture.md:43](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/architecture.md:43)、[docs/cloud-memory-eval-design.md:138–141、158–160](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/cloud-memory-eval-design.md:138) |
| 接口与配置稳定 | 接口签名作为验收契约；配置坚持标准库手写、环境变量驱动，不使用 pydantic-settings；剧本节点图需防止呈现 schema 漂移。 | [docs/architecture.md:51、54、64](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/architecture.md:51) |

### 成本与部署按个人工具约束

| 约束 | 设计决定与口径 | 来源 |
|---|---|---|
| LLM 评测预算 | L1/L2 不通过即停止后续 LLM 层；调用前估算最坏 token 用量，默认预算上限为 $2，超限拒绝或降级；缓存按 prompt_fingerprint 保存。预算适用周期未写明。 | [docs/cloud-memory-eval-design.md:175–178](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/cloud-memory-eval-design.md:175) |
| 裁判与生成分开记录 | L3/L4 默认低成本模型，L5 可用强模型；记录裁判模型，若与生成模型相同则标注自偏好风险。 | [docs/cloud-memory-eval-design.md:177](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/cloud-memory-eval-design.md:177) |
| 记忆规模 | 单条内容不超过 200 字，重要性取值 0–1、默认 0.5；轻整合条件为至少 20 条情景条目或显式触发；每剧本 200 条原始情景条目只是上限示例。 | [docs/cloud-memory-eval-design.md:101–102、125、139](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/cloud-memory-eval-design.md:101) |
| 运行环境与备份 | 设计按香港 2C4G VPS、Docker 单容器、大陆访问和 DeepSeek LLM 制定；默认本地 SQLite，每日备份到对象存储或 rsync 异地。 | [docs/cloud-memory-eval-design.md:5、69](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/cloud-memory-eval-design.md:5) |
| 数据范围 | 文档要求模组 PDF 提取原文只留本地、不入境外云，同时把香港 VPS 列为部署环境。“本地”与推理、备份传输的边界需澄清，见后文。 | [docs/cloud-memory-eval-design.md:5、63–70](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/cloud-memory-eval-design.md:5) |

云商地域、额度、网络延迟与合规判断在扩展文档中承担选型依据。本次把它们作为作者的方案前提记录，不将表中的延迟数字转为产品性能保证，也不独立确认云商或法律事实；其原始调研指向三份范围外的文档。（[docs/cloud-memory-eval-design.md:40–59、63–65、250–252](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/cloud-memory-eval-design.md:40)）

## 后续计划按 P0 至 P3 推进

扩展设计状态写的是“设计已定稿，进入 P0 落地”。下表按该文档的统一路线整理，所有阶段均为计划，不以文档存在或提交信息代替完成证据。（[docs/cloud-memory-eval-design.md:3、194–231](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/cloud-memory-eval-design.md:3)）

| 阶段 | 计划产物与验证 | 来源 |
|---|---|---|
| P0-a：记忆基础 | SQLite 记忆条目、情景与初始语义幂等捕获、合成后写入、检索上下文注入分幕创作、按四类查询。验证同一剧本再次捕获不重复及零 LLM CI。 | [docs/cloud-memory-eval-design.md:198–206](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/cloud-memory-eval-design.md:198) |
| P0-b：评测记录 | 追加式运行与评分注释、分层评测编排、忠实度检索、单次记录 API 与评测页。验证记录可回放，L1/L2 短路路径零 LLM。 | [docs/cloud-memory-eval-design.md:208–214](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/cloud-memory-eval-design.md:208) |
| P1：整合与游玩路径 | LLM 两段式整合及确定性降级、长期综合层、会话记录入口、局部重生成后记忆更新，以及 L3 裁判增强。 | [docs/cloud-memory-eval-design.md:216–221](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/cloud-memory-eval-design.md:216) |
| P2：多会话推进 | KP 回叙采集、游玩状态和冲突决策、会后简报、可选向量检索；阶段表要求会话续接问答正确。 | [docs/cloud-memory-eval-design.md:149、223–227](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/cloud-memory-eval-design.md:149) |
| P3：可选增强 | sleep-time 离线整合和前端记忆可视化，展示四类记忆与剧情线状态。 | [docs/cloud-memory-eval-design.md:150、229–231](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/cloud-memory-eval-design.md:150) |
| 条件数据库升级 | 跨会话存储健壮性需求或 RAG 增长时，考虑同 VPS 的 Postgres+pgvector；边缘复制才考虑 Turso，多设备或团队协作才考虑 Supabase。 | [docs/cloud-memory-eval-design.md:15、50–52、69–70](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/cloud-memory-eval-design.md:15) |

## 设计版本与歧义需要分别处理

### 两份设计不能合并成当前状态

Git 历史显示，领域词汇表与 MVP 架构在 `12a9c5f`（2026-08-10）出现，扩展设计在 `a32c5fd`（2026-08-16）新增；指定的词汇表和架构从初始提交到研究基线没有内容差异。日期与变更归属来自下列只读查询，设计内容按对应文件行号核验。

```bash
git log --format='%h %ad' --date=iso-strict -- CONTEXT.md docs/architecture.md docs/cloud-memory-eval-design.md
git diff 12a9c5f feca892 -- CONTEXT.md docs/architecture.md
```

历史查询返回两次设计提交：`a32c5fd 2026-08-16T15:20:22+08:00`、`12a9c5f 2026-08-10T13:07:25+08:00`；第二条查询无差异。初始内容可用 `git show 12a9c5f:docs/architecture.md` 核验，扩展设计可用 `git show a32c5fd:docs/cloud-memory-eval-design.md` 核验。对应设计状态分别见 [docs/architecture.md:1–8](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/architecture.md:1) 与 [docs/cloud-memory-eval-design.md:1–6](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/cloud-memory-eval-design.md:1)。

| 议题 | 材料差异与评审含义 | 来源 |
|---|---|---|
| 自动修复的写回权限 | MVP 写明确定性修复自动应用；扩展设计要求 L1/L2 修复人工确认后写回。权限边界发生变化，需要确定新规则适用哪些入口。 | [docs/architecture.md:19、46](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/architecture.md:19)、[docs/cloud-memory-eval-design.md:183–185](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/cloud-memory-eval-design.md:183) |
| 状态续接与持久记忆 | MVP 规定 SqliteSaver 检查点、InMemoryStore，同时要求 store 新实例可读；扩展文档自述改为 SqliteStore/InMemoryStore 快照重建，并计划分层追加记忆。相同会话续接、存储持久性和实际游玩记忆应分别验收。 | [docs/architecture.md:17](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/architecture.md:17)、[docs/cloud-memory-eval-design.md:27、88–91、147–150](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/cloud-memory-eval-design.md:27) |
| 评测能力范围 | MVP 为四维评分与四类归因；扩展设计自述缺少持久记录、忠实度和回归，随后提出 L1–L6。四维分数的存在不能代表六层评测完成。 | [docs/architecture.md:18、45](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/architecture.md:18)、[docs/cloud-memory-eval-design.md:28、158–171](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/cloud-memory-eval-design.md:28) |
| 前端与服务范围 | MVP 将 React Flow 放在二期；扩展文档自述已有生成、重生成、RAG、历史接口，又明确拟新增评测与记忆接口。它体现设计进展，但接口自述和静态预览均不能证明当前完整交互。 | [docs/architecture.md:8、44](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/architecture.md:8)、[docs/cloud-memory-eval-design.md:31、189–190](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/cloud-memory-eval-design.md:31)、[examples/preview.svg:1–4](/Users/bytedance/Documents/ChatGPT/Tindalos/source/examples/preview.svg:1) |
| 默认模型与演示说明 | MVP 默认模型为 deepseek-r1；演示注释推荐 qwen2.5:0.5b，并称 deepseek-v3.1:671b-cloud 已退役，但脚本实际默认仍是后一个名字；扩展设计按 DeepSeek LLM 制定。材料没有统一的模型配置基线，本次未查供应商可用性。 | [docs/architecture.md:21](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/architecture.md:21)、[examples/llm-demo.sh:8–10、31–34](/Users/bytedance/Documents/ChatGPT/Tindalos/source/examples/llm-demo.sh:8)、[docs/cloud-memory-eval-design.md:5](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/cloud-memory-eval-design.md:5) |

### 叙事规范仍有边界不清的地方

| 议题 | 明确的文字与待澄清边界 | 来源 |
|---|---|---|
| 六段骨架的数量 | 详细参考称“六段式”，表格实际列七阶段；运行时规范也称“六段骨架”，随后列出包含尾声的七阶段。这是材料内部计数不一致，不能据此规定必须六幕或七幕。 | [references/scenario-design.md:7–19](/Users/bytedance/Documents/ChatGPT/Tindalos/source/references/scenario-design.md:7)、[references/style-guide.md:17](/Users/bytedance/Documents/ChatGPT/Tindalos/source/references/style-guide.md:17) |
| 事件类型与事件数量 | 词汇表规定三种事件类型；参考规范进一步要求场景三事件制；扩展 L1 只写每场景至少一个事件，并计划增加进入事件检查。类型、固定数量与质量门的判据需明确。 | [CONTEXT.md:19](/Users/bytedance/Documents/ChatGPT/Tindalos/source/CONTEXT.md:19)、[references/style-guide.md:33](/Users/bytedance/Documents/ChatGPT/Tindalos/source/references/style-guide.md:33)、[docs/cloud-memory-eval-design.md:166](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/cloud-memory-eval-design.md:166) |
| 触发事件是否都必须检定 | KP 指南要求只在冲突或戏剧性时刻检定；同文又称触发事件应带检定条件。剧情参考把任务简报和关键问答也列为触发事件，需要区分一般触发条件与掷骰检定。 | [references/kp-guidance.md:19–21、46](/Users/bytedance/Documents/ChatGPT/Tindalos/source/references/kp-guidance.md:19)、[references/scenario-design.md:27–32](/Users/bytedance/Documents/ChatGPT/Tindalos/source/references/scenario-design.md:27) |
| 双图共享 schema 的含义 | 词汇表明确剧本节点图只有包含、出场、关联三类边，世界知识图谱有六类语义边；架构要求边 schema 对齐，却未在此解释类型映射。应明确共享字段与各自边类型的边界。 | [CONTEXT.md:25、34](/Users/bytedance/Documents/ChatGPT/Tindalos/source/CONTEXT.md:25)、[docs/architecture.md:44](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/architecture.md:44) |

### 记忆与评测的接口语义尚待定细

| 议题 | 明确的文字与待澄清边界 | 来源 |
|---|---|---|
| 两个正交轴与四类枚举 | 概念按内容类型和留存期两轴说明，还写明语义属于长期；数据表只给四选一的 memory_type。一个长期语义事实如何同时表达两轴，以及四类查询是否重叠，材料未说明。 | [docs/cloud-memory-eval-design.md:76–100](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/cloud-memory-eval-design.md:76) |
| 追加历史与同事件覆盖 | 情景按事件 id 追加、去重，旧版本不物理删除；重生成后又要求覆盖情景新内容。相同事件 id 内容变更时如何保留旧版本、溯源和幂等键，需要定义。 | [docs/cloud-memory-eval-design.md:88、103–111、123、132–140](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/cloud-memory-eval-design.md:88) |
| 整合中的 DELETE | 历史保留要求不物理删除，LLM 整合却使用 ADD/UPDATE/DELETE；DELETE 是否仅改变状态，以及确定性降级只标记整合后怎样产生长期摘要，均需细化。 | [docs/cloud-memory-eval-design.md:138–141](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/cloud-memory-eval-design.md:138) |
| 阶段归属 | 读写时机图把合成写入列为 P1、会话记录列为 P2；阶段表和统一路线则把合成捕获放 P0，会话记录放 P1。P2 又加入实际回叙和冲突决策，需明确 P1 会话入口与 P2 完整续团的区别。 | [docs/cloud-memory-eval-design.md:122–127、147–149、198–225](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/cloud-memory-eval-design.md:122) |
| P0 的零 LLM 范围 | P0-b 标为零 LLM，却列完整六层编排和忠实度工作；该阶段验证只保证 L1/L2 短路零 LLM。应区分基础设施离线验收与 L3–L5 内容判定的启用条件。 | [docs/cloud-memory-eval-design.md:168–170、208–214](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/cloud-memory-eval-design.md:168) |
| L5 手动触发 | 分层表规定 L5 只手动触发，统一运行序列又包含 L5。请求参数、默认跳过行为和评测记录中的跳过状态未在材料中规定。 | [docs/cloud-memory-eval-design.md:170、189、211](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/cloud-memory-eval-design.md:170) |
| 回归所需版本 | 现状自述剧本快照覆盖写入，不保留历史；L6 目标比较新旧快照，评测记录又只列对象、参数、结果等字段。不可变被测快照、rubric／裁判版本和回放范围尚需明确。 | [docs/cloud-memory-eval-design.md:26、158–160、171](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/cloud-memory-eval-design.md:26) |
| 门槛与保留策略 | 材料规定 L1/L2 级联阈值、默认 $2 预算及评测记录可归档清理，但没有给数值阈值、预算周期或保留期限。它们是验收规格缺口。 | [docs/cloud-memory-eval-design.md:175–178、244](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/cloud-memory-eval-design.md:175) |
| 本地化与外部传输 | 文档同时要求原文只留本地、使用 DeepSeek LLM、香港 VPS 和异地备份。推理与备份各能接触哪些数据、“本地”指 KP 设备还是 VPS、备份目的地是否受限制，材料没有划清。 | [docs/cloud-memory-eval-design.md:5、63–70](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/cloud-memory-eval-design.md:5) |

## 保存的样例支持哪些判断

样例可以说明交付形态和当时保存的结果。它们缺少本次重新运行的记录，不能代表研究基线的运行结果；下表将产物事实与评审含义分开。

| 样例 | 可核验的产物事实 | 评审含义与来源 |
|---|---|---|
| 最小模组与普通剧本 | 最小模组包含 1925 年雾港、老船长邀请、灯塔异声、失踪渔夫和航海日志；普通剧本的前提只保存标题。 | 样例说明输入可有具体人物与线索，但保存的前提不能证明原文细节已全部用于生成。[examples/sample-module.md:1–6](/Users/bytedance/Documents/ChatGPT/Tindalos/source/examples/sample-module.md:1)、[examples/campaign.json:2–10](/Users/bytedance/Documents/ChatGPT/Tindalos/source/examples/campaign.json:2) |
| 普通备团笔记 | 按幕、场景和事件列出内容，含 NPC、世界关系和记忆；记忆段以幕数、NPC 数、线索数、关系数及结局事件概括世界状态。 | 展示备团笔记和快照摘要的交付形态；“已推进”字样本身不能证明实际玩家已游玩。[examples/notes.md:9–69](/Users/bytedance/Documents/ChatGPT/Tindalos/source/examples/notes.md:9)；后期文档另将实际游玩采集列为 P2：[docs/cloud-memory-eval-design.md:149](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/cloud-memory-eval-design.md:149) |
| 记忆用样例 | 该 JSON 保存幕、场景、事件、NPC、线索和世界关系，与普通剧本采用相同交付层级。 | 文件名不能作为分层记忆条目或续团正确性的证明。[examples/mem-test.json:5–19、200–290](/Users/bytedance/Documents/ChatGPT/Tindalos/source/examples/mem-test.json:5)；分层记忆目标见 [docs/cloud-memory-eval-design.md:94–116](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/cloud-memory-eval-design.md:94) |
| 缺陷剧本与修复产物 | 缺陷样例含悬空 NPC、空场景和重叠关系；修复产物记录注册 NPC、重生成事件、关系失效，以及 3.2→5.0 的分数变化；下一轮无修复、分数不变。 | 支持确定性修复的演示意图与日志格式。该产物裁判为 none，不能据此得出内容质量满分。[examples/campaign-broken.json:50–54、99–107、300–307](/Users/bytedance/Documents/ChatGPT/Tindalos/source/examples/campaign-broken.json:50)、[examples/campaign-evolved.json:399–449](/Users/bytedance/Documents/ChatGPT/Tindalos/source/examples/campaign-evolved.json:399) |
| LLM 演示的评分与内容 | 保存的评测总分为 5.0、四维均为 5，注明全部来自确定性规则、judge 为 none；同目录剧本的幕编号写“夜”“黎明”，NPC 包括李清照、孟晚舟、黄文秀。 | 编号与罗马数字约定不一致；人物与最小模组中的时间、地点和已给角色不对应。该评分产物不能验收忠实度或洛氏风格。[examples/llm-demo-out/eval-llm.json:4–25、48–59](/Users/bytedance/Documents/ChatGPT/Tindalos/source/examples/llm-demo-out/eval-llm.json:4)、[examples/llm-demo-out/campaign-llm.json:7–10、105–108、200–238](/Users/bytedance/Documents/ChatGPT/Tindalos/source/examples/llm-demo-out/campaign-llm.json:7)、[CONTEXT.md:13](/Users/bytedance/Documents/ChatGPT/Tindalos/source/CONTEXT.md:13)、[examples/sample-module.md:3–6](/Users/bytedance/Documents/ChatGPT/Tindalos/source/examples/sample-module.md:3) |
| LLM 演示目录的一致性 | 备团笔记仍列“初现端倪”及顾长歌等人，首场景事件描述为空；同目录剧本列“雾港之夜”及另一组 NPC，修复产物沿用这份剧本且无修复动作。 | 同目录的笔记、剧本与评测没有可核验的统一运行标识，不应拼成一次完整验收。[examples/llm-demo-out/notes.md:10–18、42–46](/Users/bytedance/Documents/ChatGPT/Tindalos/source/examples/llm-demo-out/notes.md:10)、[examples/llm-demo-out/campaign-llm.json:7–10、200–238](/Users/bytedance/Documents/ChatGPT/Tindalos/source/examples/llm-demo-out/campaign-llm.json:7)、[examples/llm-demo-out/campaign-llm-evolved.json:358–373](/Users/bytedance/Documents/ChatGPT/Tindalos/source/examples/llm-demo-out/campaign-llm-evolved.json:358) |
| 《留地不留头》与静态预览 | 预览明确标注 2 幕、14 场景、42 事件、3 NPC、2 线索、3 条关系；剧本有详细环境、调查和终局策略，终局同时写成败后果。 | 展示真实模组的整理规模与文风样例，规模数字取自产物，不代表吞吐能力或交互验收。[examples/preview.svg:1–4](/Users/bytedance/Documents/ChatGPT/Tindalos/source/examples/preview.svg:1)、[examples/hell-or-connaught-campaign.json:24–54、90–94、735–742](/Users/bytedance/Documents/ChatGPT/Tindalos/source/examples/hell-or-connaught-campaign.json:24) |
| 大样例的结构与内容关系 | 多个场景正文提及克伦威尔、巴斯托、麦克马洪等角色，但注册的三个 NPC 是威廉、塞缪尔和托马斯；各幕分工为空，两条线索均为泛化名称。两个相邻场景都描述麦克马洪在弥撒中涌蛇死亡。 | 内容到注册 NPC、线索与时间线的映射仍需质量检查；这是保存样例的观察，不据此定位当前代码缺陷。[examples/hell-or-connaught-campaign.json:38–61、470–483、519–534、757–818](/Users/bytedance/Documents/ChatGPT/Tindalos/source/examples/hell-or-connaught-campaign.json:38)；对应设计判据见 [CONTEXT.md:10、16、22](/Users/bytedance/Documents/ChatGPT/Tindalos/source/CONTEXT.md:10)、[docs/cloud-memory-eval-design.md:167、170](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/cloud-memory-eval-design.md:167) |

## 阅读范围与清洗记录

### 证据来源

| 材料 | 本次用途与证据地位 | 可核验位置 |
|---|---|---|
| 领域词汇表 | 领域术语及实体边界的约束来源。 | [CONTEXT.md:1–35](/Users/bytedance/Documents/ChatGPT/Tindalos/source/CONTEXT.md:1) |
| MVP 架构 | 初始功能、接口与质量契约；文首的 G1/G2 状态为材料自述。 | [docs/architecture.md:1–65](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/architecture.md:1) |
| 数据层扩展设计 | 单用户约束、数据库选择、记忆、六层评测和 P0–P3 计划；“现状盘点”只作为设计作者的自述。 | [docs/cloud-memory-eval-design.md:1–32、194–253](/Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/cloud-memory-eval-design.md:1) |
| 四份参考文档 | KP 把控、洛氏风格、剧情参考与运行时规范；属于仓库整理的参考材料，未独立验证所引规则书和模组原文。 | [references/kp-guidance.md:1–5](/Users/bytedance/Documents/ChatGPT/Tindalos/source/references/kp-guidance.md:1)、[references/lovecraftian-style.md:1–5](/Users/bytedance/Documents/ChatGPT/Tindalos/source/references/lovecraftian-style.md:1)、[references/scenario-design.md:1–5](/Users/bytedance/Documents/ChatGPT/Tindalos/source/references/scenario-design.md:1)、[references/style-guide.md:1–5](/Users/bytedance/Documents/ChatGPT/Tindalos/source/references/style-guide.md:1) |
| 示例目录 | 备团笔记、演示说明、保存的剧本、评分与修复日志，以及静态预览；只证明这些产物中写了什么。 | 上节逐项引用。 |
| 相关 Git 历史 | 确定材料先后与是否更新；未使用提交信息中的“完成”“测试全绿”等表述证明实现状态。 | 上文列出的限定路径查询及 `12a9c5f`、`a32c5fd`。 |

参考文档指向的规则书／模组提取路径在本次目录清单中不存在，扩展文档引用的三份调研未进入授权阅读范围，因此没有补造原文或沿引用扩展阅读。引用标签保留路径与原始行号区间，点击链接定位区间起始行；历史查询可在本研究基线重放。

### 成稿清洗

已按 doc-writing 定主题与框架、按 de-ai-tech-writing 组织技术正文，再显式按 haohao-shuohua 清洗。事实核对保留人类裁定权、自动／人工写回边界、阶段归属、数字、术语、原始路径和样例评分口径；三遍回读分别检查事实、表达残留与句子节奏。清洗将实现断言改为设计要求或产物观察，拆开长句，并把并列要求放入表格，没有增加产品承诺。

实际运行的命令如下，脚本退出码为 0。

```bash
python3 /Users/bytedance/.agents/skills/haohao-shuohua/scripts/quick_scan.py /Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/review-project-intent.md
```

| 扫描项 | 命中数 | 处理结果 |
|---|---|---|
| 半角标点 | 0 | 无需修改，无豁免。 |
| 破折号串 | 0 | 无需修改，无豁免。 |
| B2 变体 | 0 | 无建议项，无豁免。 |
| 元话语 | 0 | 无需修改，无豁免。 |
| 序号标题 | 0 | 无需修改，无豁免。 |
| 免责尾巴 | 0 | 无建议项，无豁免。 |

节奏粗筛另有 1 处提示：版本对照表中的连续英文存储名。该处属于技术名称列举，语义清楚，保留术语不改；这项豁免不涉及机械扫描的六类检查。清洗后的事实复核将线索定义调整为原文的绑定字段说明，将 LLM 样例描述改为“同目录”以保留运行归属的不确定性。引用检查确认 153 处来源链接覆盖 20 个授权材料文件，路径及起始行号均有效。

主线保持为产品意图及其验收边界。功能、质量、路线、版本对照和样例观察均服务于这条主线；清洗后，设计承诺、作者自述与保存产物的关系更明确。
