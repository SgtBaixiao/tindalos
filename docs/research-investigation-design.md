# 调查备团要能解释线索与变化

本文为 Tindalos 的 TRPG AI 备团重构回答一个问题：怎样把原始模组变成 KP 能主持、能处理玩家偏离和检定失败的材料。建议优先深化线索推理与世界状态：KP 应能回答“这条信息支持什么判断”“行动失败后还剩什么机会”“NPC 为什么现在这样做”。

## 一手来源给出了哪些依据

Alexander 的 Three Clue Rule 要求为希望玩家得出的每项结论准备至少三条线索，并接受玩家提出的合理调查方法；节点设计进一步提供不同调查顺序。[S2]、[S4] Pelgrane 区分通往另一场景的引导信息（lead）与帮助理解真相的线索（clue）。GUMSHOE 在满足场景与调查能力等条件时，必要信息的取得不依赖掷骰，但理解和行动仍需玩家完成。[S5]、[S6]

这些观点有分歧：Alexander 在 2008 年批评 GUMSHOE 会形成线性取线索体验；Pelgrane 的 SRD 则明确要求避免固定顺序，后来的主持材料强调多入口、多出口。[S2]、[S6]、[S8] 因此应分别借鉴获取规则与调查结构。CoC《The Haunting》也同时包含检定、明显线索和失败代价，不能把 GUMSHOE 的规则直接标成 CoC 原文。[S1]

本轮读到的是规则、作者设计观点与经验回顾，未取得包含样本、对照和原始记录的效果实验。“三条”是设计经验，不能据此承诺不卡关，更不能推断 AI 已改善主持体验。

## 先还原事件，再分析玩家如何得知

专业设计师会先从幕后事件建立因果关系，再反向追查事件留下的证据。GUMSHOE SRD 明确描述这一过程，Kulp 的个人备团示例也从反派计划推到线索与可选解决方式。[S6]、[S7] 应从以下原始材料逐项分析。

| 原始素材 | 具体分析 | KP 需要得到的内容 |
|---|---|---|
| 幕后秘密、历史、事件时间 | 谁做了什么，原因是什么，哪些事实已确定 | 真相与因果关系，原文未定事项 |
| 地图、地点说明、玩家资料 | 信息在哪里，如何发现，谁能接触 | 调查入口、获取条件与离开后的去向 |
| 证词、文件、物证 | 信息支持哪项结论，需要哪些前提，有何反证 | 证据与结论的关系，可能的误读 |
| NPC 描写、能力、主持注释 | NPC 知道什么，想要什么，行动受何限制 | 动机、资源、触发条件与变化 |

随后分别检查玩家能否发现信息、理解信息、据此行动。把一条线索改写三次不能增加调查机会；三份证据都锁在同一入口后，也会同时失去。若结论必须结合两个前提，每个前提都要检查获取路径。[S4 作者答复](https://thealexandrian.net/wordpress/7999/roleplaying-games/node-based-scenario-design-part-4-sample-scenario)

《The Haunting》的讣告提到 Corbitt 希望葬在地下室，后续诉讼结果未记录。[S1] 它支持“地下室值得调查”，尚不足以证明“异常源头已经确定”。备团必须保留这一推理距离，也区分文件的记载与幕后真相。

## 建议深化现有备团内容

以下是本文提出的设计建议，尚未接受产品或游玩验证。

### 让每条线索解释它支持的结论

| 项目 | 约定 |
|---|---|
| 触发 | KP 无法说明线索用途，或漏检、误读会阻断调查 |
| 输入 | 原文证据及位置、目标结论、获取条件、玩家当前已知信息 |
| 产出 | 结论与证据对照，以及当前场景可用的调查入口、失败代价和后续行动 |
| 失败情形 | 把相关性当证明，多个入口实际共用同一障碍，或直接替玩家宣布真相 |

对照中写清“任一证据即可支持”还是“必须结合多个前提”。先找原文已有的替代路径；不足时标明缺口，补充线索须列为创作建议。CoC 的无检定发现、可重试研究与孤注一掷分别保留，失败后的备选方法由 KP 选择，仍允许玩家放弃或调查失败。

### 用动机与可变状态决定 NPC 反应

| 项目 | 约定 |
|---|---|
| 触发 | 玩家跳过场景、拖延、公开指控，或以未预设方式接触 NPC |
| 输入 | NPC 目标、所知事实、资源、当前态度、时间与已发生行动 |
| 产出 | 条件成立时 NPC 会尝试什么、消耗什么、玩家能察觉什么，以及状态更新 |
| 失败情形 | NPC 获得未知信息、资源无限，或玩家怎样行动都被带回同一结果 |

Alexander 建议准备对手目标与玩家不介入时的行动；SRD 也要求给对手反应写触发条件。[S3]、[S6] 据此准备少量条件反应，避免穷举玩家分支。未知动机保持未定。时间变化必须留下可观察后果，KP 才能据此裁定偏离，而非临时改真相。

### 逐项区分原文事实与创作建议

| 项目 | 约定 |
|---|---|
| 触发 | 原文不足以回答问题，或 AI 提议补线索、补动机、解释未定材料 |
| 输入 | 原文段落及版本、已有因果关系、KP 已认可的改编 |
| 产出 | 每项内容标为原文事实、推断或创作建议，并附依据、适用条件和受影响内容 |
| 失败情形 | 补写内容冒充原文，人物说法被升级为事实，或推断悄悄修改幕后真相 |

例如模组明确把看起来像占星图的纸张留作未来冒险种子。[S1] 可以建议其意义，但须保持“原文未定”。在 KP 采纳前，补写内容不能成为其他结论的确定前提。

## 用真实模组建立可复核评估

选定官方 Quick-Start 所收《The Haunting》，先冻结版本与原文页码，再由未参与生成的 KP 独立列出结论、线索和状态约束。以下已完成原文核对，可作为验收检查项；它们尚未验证 Tindalos 的输出。

| 原文位置 | 复核情境 | 备团必须保留的约束 |
|---|---|---|
| 第 18—19 页，报社与图书馆 | 社交检定失败；研究失败 | 前者拒绝进入，孤注一掷失败升级后果；后者每次可多耗半天重试 |
| 第 30 页，Corbitt 主持注释 | 玩家入屋后直奔其本体 | 追踪施法消耗与每小时恢复，不能默认资源已回满 |
| 第 28 页，地下室纸张 | 玩家询问材料的真实意义 | 保留未来种子的未定性质，新增解释标为建议 |

用同一输入生成当前备团与改版备团，冻结输出及生成配置。独立 KP 盲核每项事实出处、必需结论的获取路径和状态矛盾，报告原始计数与分母，逐项附页码、输出位置、判定理由。建议验收门槛是无来源冒充、无状态矛盾，每项必需结论有可执行路径；未解决的阻断缺口使该项不通过。门槛属于本次评估提议。

再由另一位 KP 用固定情境走查偏离、失败与意外问询，记录能否仅凭备团裁定、查找时间及回翻原文次数。情境注入是人工测试。拿到真实场次记录后，按时间点重播，只允许使用当时已知信息，记录线索实际取得、玩家推断、停滞和 KP 干预。同谜重跑存在记忆偏差，单场只能暴露问题，不能证明因果收益。

独立验收证据应包括阅源者的期待清单、冻结的两版输出、带署名与时间的逐项审阅，以及场次时间戳。模型自己评分不算独立证据；本轮尚无这些产品验收或游玩材料。

## 缺少的私有素材

后续需要用户实际使用的模组版本与玩家资料、原始输入和 AI 输出、KP 手工修改及改编决定，以及经参与者同意使用的录音或逐字场次记录。还需 KP 的主持经验、人数与时长等场次条件。本轮未读取这些私有材料，不能编造用户访谈、玩家偏离、检定失败或满意度，也不能判断哪个建议收益最大。

## 出处与阅读范围

检索与阅读日期：2026-11-19。使用 web 搜索、打开正文、定位段落及 PDF 文本；GUMSHOE 102 两页另以截图复核。仅转述与问题直接相关的内容，未复制模组玩家资料或完整场景。范围止于调查设计与验收方法。

- S1：[Chaosium，Call of Cthulhu 7th Edition Quick-Start Rules](https://www.chaosium.com/content/FreePDFs/CoC/CHA23131%20Call%20of%20Cthulhu%207th%20Edition%20Quick-Start%20Rules.pdf)。读取的在线文本为 40th Anniversary、CHA23131；定位采用书内印刷页码。阅读 Skill Rolls and Difficulty Levels、Pushing the Roll，以及《The Haunting》的主持秘密、调查地点、地下室、Corbitt 说明与 Extension。第 18—19、28、30 页用于上述检查。官方规则及模组事实。web 原 PDF 截图受文件大小限制，实际阅读来自索引提供的 PDF 文本，未逐页图像核验；后续冻结评估版本时应再次核对下载件。
- S2：[Justin Alexander，Three Clue Rule](https://thealexandrian.net/wordpress/1118/roleplaying-games/three-clue-rule)，2008-05-08。阅读发现与推断的失败点、三线索原则、Permissive Clue-Finding、Red Herrings 与 Nothing Is Foolproof。设计观点；其对 GUMSHOE 的评价不能代替官方规则。文中转引的早期 playtest 叙述无本轮可复查原始记录，未作实验支持。
- S3：[Justin Alexander，Don’t Prep Plots](https://thealexandrian.net/wordpress/4147/roleplaying-games/dont-prep-plots)，2009-03-23。阅读事件链与局势的对比、Robust Design、Goal-Oriented Opponents 及 Don’t Plan Specific Contingencies。设计观点。
- S4：[Node-Based Scenario Design，Part 1](https://thealexandrian.net/wordpress/7949/roleplaying-games/node-based-scenario-design-part-1-the-plotted-approach)、[Part 3](https://thealexandrian.net/wordpress/7985/roleplaying-games/node-based-scenario-design-part-3-inverting-the-three-clue-rule)、[Part 4](https://thealexandrian.net/wordpress/7999/roleplaying-games/node-based-scenario-design-part-4-sample-scenario)、[Part 9](https://thealexandrian.net/wordpress/8049/roleplaying-games/node-based-scenario-design-part-9-types-of-nodes)。阅读线性瓶颈、倒置原则及示例、节点类型；“两个必要前提分别准备线索”依据 Part 4 中 Alexander 于 2023-08-29 的作者答复。示例和推理是设计论证，倒置原则不构成概率保证。
- S5：[Gareth Ryder-Hanrahan，Clues vs. Leads](https://pelgranepress.com/2020/02/21/clues-vs-leads/)，2020-02-21；[A Taxonomy of Investigations](https://pelgranepress.com/2023/03/08/a-taxonomy-of-investigations/)，2023-03-08。阅读 lead、core lead、clue、core clue 的区分。官方设计指导；本报告沿用后期澄清，未把所有线索都要求为通往下一场景的箭头。
- S6：[Pelgrane Press，GUMSHOE SRD Creative Commons Version](https://pelgranepress.com/gumshoe/files/GUMSHOE%20SRD%20CC%20version.pdf)，65 页的 2013 年版本。阅读第 31—32 页调查逻辑与 Gathering Clues、第 58—61 页 Records are your Friend、Core、Placing Information、Antagonist Reaction、Hybrid Scenes 和 Scene Diagrams。官方规则及设计指导；系统内的取信息约定与跨系统效果证据分别处理。
- S7：[Kevin Kulp，GUMSHOE 102 for GMs](https://pelgranepress.com/gumshoe/files/GUMSHOE-102-for-GMs.pdf)，2013 年，两页均阅读。反派计划、调查推理、多种终局和以名誉作为交涉手段；作者明确说明这是个人备团过程。文中链接的 play-by-post 未纳入本轮阅读与游玩证据。
- S8：[Robin D. Laws，GUMSHOE Scenarios Then and Now](https://pelgranepress.com/2016/10/04/gumshoe-scenarios-then-and-now/)，2016-10-04。阅读 Lead-In／Lead-Out、调查资源花费与无桌面用途的背景段落。作者经验回顾，没有提供可估计效果的对照数据。

[S1]: https://www.chaosium.com/content/FreePDFs/CoC/CHA23131%20Call%20of%20Cthulhu%207th%20Edition%20Quick-Start%20Rules.pdf
[S2]: https://thealexandrian.net/wordpress/1118/roleplaying-games/three-clue-rule
[S3]: https://thealexandrian.net/wordpress/4147/roleplaying-games/dont-prep-plots
[S4]: https://thealexandrian.net/wordpress/7985/roleplaying-games/node-based-scenario-design-part-3-inverting-the-three-clue-rule
[S5]: https://pelgranepress.com/2020/02/21/clues-vs-leads/
[S6]: https://pelgranepress.com/gumshoe/files/GUMSHOE%20SRD%20CC%20version.pdf
[S7]: https://pelgranepress.com/gumshoe/files/GUMSHOE-102-for-GMs.pdf
[S8]: https://pelgranepress.com/2016/10/04/gumshoe-scenarios-then-and-now/

## 写作检查记录

按 doc-writing 定主题与结构，按 de-ai-tech-writing 区分事实、观点和建议，再按 haohao-shuohua 分别回读事实、表达与节奏。保留专名、规则条件、页码和 URL，统一占星材料的译名；将未解决的调查阻断明确列为验收不通过。正文未加入效果数字，各节均服务于调查备团问题。

实际执行命令：

```bash
python3 -B /Users/bytedance/.agents/skills/haohao-shuohua/scripts/quick_scan.py /Users/bytedance/Documents/ChatGPT/Tindalos/source/docs/research-investigation-design.md
```

扫描记录（2026-11-19）：半角标点 0 处、破折号串 0 处、B2 变体 0 处、元话语 0 处、序号标题 0 处、免责尾巴 0 处；默认硬闸为半角标点，退出码 0。无人工豁免。此扫描检查文字形式，不构成设计或游玩验收证据。
