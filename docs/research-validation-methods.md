# 备团可靠性要用连续游玩验证

本文给维护者和独立守秘人（KP）一套验收方法。固定一个已有模组 PDF、文件哈希及模型和提示词版本，沿“模组→备团→一场实际游玩→重启续团”保留证据。目标是检验生成内容能否支持主持、正确延续事实，并兑现玩家选择。以下程序是针对本项目的应用推断，尚未执行盲测或 benchmark。

源码契约走读只确定测量入口，不重复代码评审。本次基于提交 `feca892` 的这些位置：

| 契约事实 | 可复核位置 |
|---|---|
| 分支检查只要求某事件有至少两条出边；可达性检查沿边遍历 | [分支](/Users/bytedance/Documents/ChatGPT/Tindalos/source/src/tindalos/eval_/deterministic.py:224)、[可达性](/Users/bytedance/Documents/ChatGPT/Tindalos/source/src/tindalos/eval_/runner.py:121) |
| 忠实度支持判据是正检索分数加至少一个双字词元重叠 | [支持判据](/Users/bytedance/Documents/ChatGPT/Tindalos/source/src/tindalos/eval_/runner.py:197) |
| 生成事件写入情景记忆；游玩回叙另表保存；续团读取记忆条目 | [生成捕获](/Users/bytedance/Documents/ChatGPT/Tindalos/source/src/tindalos/memory_entries.py:194)、[回叙](/Users/bytedance/Documents/ChatGPT/Tindalos/source/src/tindalos/memory_entries.py:887)、[续团读取](/Users/bytedance/Documents/ChatGPT/Tindalos/source/src/tindalos/pipeline.py:352) |

## 用原文证据验收生成内容

第一手依据：Anthropic 的 [evals 原文][evals]区分运行记录与环境最终结果，并要求分别检查断言支持、关键事实覆盖和来源质量。[effective agents 原文][agents]强调从环境取得真实反馈。因此，结构满分不能证明忠实度或实际可选路线。

验证程序：独立 KP 在看生成稿前，先从选定模组列出关键事实及分支条件。生成后逐项对照，并在全文前、中、后部分抽取普通断言，避免只检查开头。每项证据记录“原句、PDF 阅读器页码、印刷页码、生成节点、支持／矛盾／原文未定”。解析器页码从零开始，必须转换并对照原页；检索块编号不能替代页码。原文未定的补充内容须标明推断及 KP 是否批准。

关键事实覆盖率按正确保留且有原文证据的项数，除以事先列定的关键事实数计算；断言支持率的分母是实际抽查断言数，另列未定补充。失败标准：任一关键事实矛盾、关键条件遗漏，或把无依据补充写成原文事实，即不通过。独立证据是原始 PDF 页与人工证据表，模型裁判只辅助定位。

## 用盲测确认选择和已发生事实

第一手依据：[multi-agent 原文][multi]允许不同有效路径，要求判断结果与过程；[EventStorming 作者网站][storm]支持跨角色共同探索领域。[Fowler 的事件溯源说明][events]提供由事件序列重建状态的方法。下面的知情范围和权威规则是项目应用约定。

验证程序：开发者与独立 KP 先做事件风暴，按时间摆出“玩家尝试、KP 裁定、已发生事件”，将备团计划另放。再画状态机，把条件满足及裁定确认作为状态迁移前提。信息同时标记真值、有效时间和知情者：

| 归属 | 需要区分的内容 |
|---|---|
| 世界 | 实际真值及变化时间 |
| KP | 主持所需秘密与裁定 |
| NPC | 每个 NPC 的已知信息和错误信念 |
| 玩家 | 每位玩家获知的信息，区分公开说明与私信 |

未参与开发的 KP 用匿名备团材料主持一场，不看模型身份和评分。逐个决策点记录已告知、条件可满足且有区别后果的选项数，除以备团承诺选项数；区别后果包括信息、代价或世界变化。没有承诺选项的点记未覆盖。实际选择单列，未选路线从当时状态反事实演练，标记为模拟。

另一 KP 依据录音时间戳、逐字记录和裁定复核。初始世界以模组为准，已发生变化以游玩记录和显式裁定为准；摘要与模型推断不得推翻记录。例如“计划交付线索、本场未交付”的待测条件，续团须保留玩家尚未知情的状态。失败标准：承诺路线依赖玩家未知秘密、条件不可满足、KP 无条件依据地强制导回计划，或续团把未发生计划写成事实，任一出现即不通过。

## 用对照和故障检验续团记忆

第一手依据：LongMemEval [原论文][paper]第 4 页列出五类记忆能力，第 6 页区分问答与检索测量；[官方仓库][benchmark]提供答案所在场次及轮次标注和只含证据的 oracle 对照。[官方裁判][judge]对时间间隔题容许天／周／月数量相差 1，也接受含旧信息但给出更新答案的响应。本项目的事件先后和当前裁定须严格复核，不能直接套用这套评分。[LangGraph 状态][state]解释更新合并，[持久化][persist]区分线程检查点与跨线程存储，[interrupt][interrupt]说明恢复会重跑节点，副作用需幂等。

验证程序：在存储副本上，将本场真实记录整理成事实、跨记录推理、时间先后、知识更新及未知问题。真正跨场推理须等续团产生新记录后测。每题预定答案、知情者和必需证据编号。重启并用新线程续团，分别比较实际检索与人工直接提供全部证据的答案，以区分漏检和读错。召回@6 按前六条覆盖的必需证据数除以全部必需证据数计算；问答正确率另算，泄密和权威性错误逐条计数。

对同一用例做变形测试：同义改写回叙、添加无关记录应保持关键状态；只修改一项显式裁定，应只改变依赖它的事实。加入时间更近的模型推断，也不得改变已确认事件。再注入写入失败、模型超时、写入后进程中断及重复提交，并用另一战役的特殊事实检查隔离。每种条件从同一冻结副本独立恢复，重复三次，保留每次结果，不挑最好一次。本地题只借鉴评测方法，不能称为 LongMemEval 成绩。

失败标准：恢复后出现重复或丢失的已确认事实、跨战役混入、秘密越权，或低权威推断覆盖真实记录，即不通过；故障降级必须可见。独立证据是冻结的输入、调用记录、故障前后存储快照及人工重建状态。单个连续用例只支持当前模组和已测路径的结论，不能外推总体可靠率。

## 第一手来源

检索与阅读日期：2026-11-19。网页按章节定位，论文按印刷页码定位。

- [Demystifying evals for AI agents][evals]：The structure of an evaluation、Evaluating research agents、Step 2／Step 5。
- [Building effective agents][agents]：Agents。
- [How we built our multi-agent research system][multi]：Effective evaluation of agents。
- [EventStorming][storm]：作者网站首页的工作坊定义。
- [Event Sourcing][events]：How it Works、Complete Rebuild；Fowler 标明此文为草稿。
- [LongMemEval 论文][paper]：§3.1–§3.3；[官方 benchmark][benchmark]：Dataset Format、Testing Your System；另读[官方问答裁判脚本][judge]的时间题和知识更新题判据。
- LangGraph：[状态][state]的 State／Reducers，[持久化][persist]的 Checkpointers／Stores，[interrupt][interrupt]的 Side effects called before interrupt must be idempotent。

[evals]: https://www.anthropic.com/research/demystifying-evals-for-ai-agents
[agents]: https://www.anthropic.com/research/building-effective-agents
[multi]: https://www.anthropic.com/research/multi-agent-research-system
[storm]: https://www.eventstorming.com/
[events]: https://martinfowler.com/eaaDev/EventSourcing.html
[paper]: https://arxiv.org/pdf/2410.10813
[benchmark]: https://github.com/xiaowu0162/LongMemEval
[judge]: https://github.com/xiaowu0162/LongMemEval/blob/main/src/evaluation/evaluate_qa.py
[state]: https://docs.langchain.com/oss/python/langgraph/graph-api
[persist]: https://docs.langchain.com/oss/python/langgraph/persistence
[interrupt]: https://docs.langchain.com/oss/python/langgraph/interrupts

## 写作核对记录

已按 doc-writing 定主题和结构、de-ai-tech-writing 核对技术论证，再按 haohao-shuohua 分别回读事实、表达和节奏。源码行为、论文页码及来源 URL 已逐项核对；验证程序、三次重复和失败门槛均标为项目应用约定。节奏扫描中的英文专名和完整短判断，按文意保留。

实际运行命令：`python3 -B /Users/bytedance/.agents/skills/haohao-shuohua/scripts/quick_scan.py /Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/research-validation-methods.md`。扫描结果：半角标点、破折号串、B2 变体、元话语、序号标题、免责尾巴各 0 处，退出码 0。无需处理扫描命中，无豁免项。
