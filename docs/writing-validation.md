# 本轮文档的表达与事实核对

记录日期为 2026-11-19。本轮先使用 doc-writing 确定读者、主题和框架，技术论证由 de-ai-tech-writing 核对，成稿后显式使用 haohao-shuohua 清洗并扫描。文档服务于项目理解、重构决定和本地验收，保留研究阶段与实施阶段各自的状态。

## 事实核对保留证据的边界

固定原始提交、日期、测试数量、性能样本、命令、字段、专业术语及来源链接。重点核对用户授权与执行者选择、计划与实际事实、修订与任务身份、自动检查与人工使用判断之间的关系。

研究建议没有改写成用户已确认的产品需求；模拟模型延迟没有改写成真实模型性能；SQLite 备份可读取没有改写成完成断电恢复；结构评分没有改写成可主持性。本轮修改与测试记录能支持运行说明，尚无真实模型和独立 KP 试用的成绩。

本轮保存原始 README，另写当前入口，目的是让历史事实与当前运行方式都能查阅。架构与验证记录分别解释保存机制和观察结果，减少源码字段直接堆入正文的段落。清洗只处理表达，不增加用户需求、部署承诺和未运行的验证。

## 机械扫描已实际执行

从仓库根目录运行以下命令。17 份成稿首次扫描的六类命中均为 0，退出码为 0，无需按扫描结果修改，无豁免项。

```bash
python3 -B /Users/bytedance/.agents/skills/haohao-shuohua/scripts/quick_scan.py \
  README.md GLOSSARY.md docs/runtime-architecture.md docs/refactor-validation.md \
  docs/refactor-decision-log.md docs/refactor-clarification.md \
  docs/adr/0001-local-runtime.md docs/adr/0002-source-and-session.md \
  docs/adr/0003-job-restart.md docs/research-runtime-storage.md \
  docs/research-client-evaluation.md docs/review-project-understanding.md \
  docs/review-project-intent.md docs/deep-functional-design.md \
  docs/deep-research-prompt.md docs/research-investigation-design.md \
  docs/research-validation-methods.md
```

| 扫描类别 | 初次命中 | 交付处理 |
|---|---:|---|
| 半角标点 | 0 | 中文正文通过；代码、命令与链接保留 |
| 破折号串 | 0 | 无需修改 |
| B2 变体 | 0 | 无需修改 |
| 元话语 | 0 | 无需修改 |
| 序号标题 | 0 | 无需修改 |
| 免责尾巴 | 0 | 保留实测范围与未验证能力 |

决策和验证记录补充 D019、定向回归结果后，连同本文件再次扫描。最终命中仍为 0，退出码为 0。

扫描范围未包含未改写的历史 README 和既有研究原文，它们按历史材料保留。原架构文档仅增加当前说明的入口，其余历史正文保持原样。开发约定属于供 Agent 执行的规则，单独核对链接与事实，不用成稿章节规则改写。没有通过修改扫描器或隐藏命中来取得结果。

## 回读围绕维护者实际要判断的内容

事实回读核对源码行为、原始样本和验收记录；表达回读检查不明指代、同义重复和无依据的成功结论；节奏回读只补必要停顿，保留配置表、命令和证据索引。

各份文档的主线分别是项目预期、具体设计依据、当前运行机制与可复查的验收。当前入口指向决策、架构和验证资料，历史内容保留单独入口。尚未取得的原始主持材料继续留在澄清与验收记录中。

## 界面重构文档的收尾核对

本轮新增前端模块约定和界面验收，更新 README 与决策 D020 至 D025。事实核对固定首屏字节数、gzip level 9、110 项前端测试、上一轮后端结果、渲染计数、视口尺寸及本地发布状态。完整测试数量变化包含旧启动动画测试删除，未改写为额外测试全部通过。

主题围绕维护者如何修改和验证前端，模块表解释依赖，验收表报告观察结果。表达清洗保留专业术语与命令，将首屏资源减少与浏览器帧率分开说明，未增加真实模型或手机验收结论。事实、残留表达和节奏分别回读，主线没有偏离。

实际运行以下命令，六份文件的半角标点、破折号串、B2 变体、元话语、序号标题和免责尾巴各命中 0 处，退出码 0，无豁免项。最终体积更新后再次执行同一命令核对。

```bash
python3 -B /Users/bytedance/.agents/skills/haohao-shuohua/scripts/quick_scan.py \
  README.md docs/frontend-architecture.md docs/ui-refactor-validation.md \
  docs/refactor-decision-log.md docs/refactor-clarification.md docs/writing-validation.md
```

## 上传前的说明核对

用户已授权上传新版本，新增决策 D026，README 的展示状态改为指向 GitHub Actions。此前验收记录描述的是当时的本地工作树，保留原有日期与验证范围。源码与证据纳入提交，运行数据、备份及密钥保留在本地。

本轮再次使用 doc-writing、de-ai-tech-writing 和 haohao-shuohua 核对三份修改文档。实际执行以下命令，六类命中均为 0，退出码 0，无豁免项。事实核对保留用户上传原话、main 分支、两轮重构范围及自动部署状态的证据入口，未将上传授权改写为部署成功。

```bash
python3 -B /Users/bytedance/.agents/skills/haohao-shuohua/scripts/quick_scan.py \
  README.md docs/refactor-decision-log.md docs/writing-validation.md
```

2026-11-20 补充 D027，记录上传认证问题、用户对专用部署密钥的授权，以及 GitHub 要求本人重新验证密码的状态。沿用上述三份文件扫描命令，六类命中均为 0，退出码 0，无豁免项。提交已生成、远端尚未更新和密钥尚未添加分别核对，没有将准备状态记为完成。

用户完成身份验证后，D027 更新为密钥已添加。核对仓库名称、Read/write 权限、公钥指纹及 SSH 443 端口远端读取结果，私钥继续保存在忽略目录。再次运行上述扫描命令，六类命中均为 0，退出码 0，无豁免项；上传和自动检查保留各自证据入口。

## 2026-11-27 Herdr 重构文档

本轮使用新安装的 `clear-writing`，已读取正文、技术写作与编辑记录参考。此前记录按发生日期保留，不将旧 Skill 的结果改写为本轮扫描结果。

读者是维护者、实现者与评审者；文档分别供选择工程方案、确认实际 Skill 入口、核对验收与继续开发使用。材料来自本轮原始命令输出、冻结源码、两轮独立评审和实际 UI 观察。保留 AC 编号、规则优先级、测试数量、字节数、命令及验收缺口；独立评审指出的疑点修正为明确的原行为说明，浏览器结论绑定现场观察与保存记录，没有补造产品决定。

实际扫描命令如下，结果保存在本地 `.local-preserved/herdr-20261127/leader-final-clarity.log`。事实回读同时核对基线、新增测试与原始日志。

```bash
python3 /Users/bytedance/.agents/skills/clear-writing/scripts/clarity_check.py \
  docs/refactor-herdr-plan.md docs/herdr-skill-check.md \
  docs/refactor-herdr-validation.md docs/refactor-decision-log.md \
  docs/writing-validation.md
```

补充浏览器流程后，扫描发现确认事实 `目击者.生命=存活` 中的半角点 1 处。该字段是受保护的实际输入，改为代码标记后保留原值。最终五份文档再次实际扫描，均为 clean，九类合计 0 命中，退出码 0，无豁免项。扫描器默认提示模式的退出码不能单独代表无问题，本轮同时核对逐项计数。扫描与作者自检没有替代真实读者测试。

## 2026-11-27 剩余工程与交付

读者仍是维护者和评审者。新增工程报告与公开 UI 观察记录，README 更新本轮入口；既有历史扫描记录保留。事实核对覆盖 3 项真实回环回归、35 项 Moby 路径断言、32 客户端的 64 次离线提交，以及 390×844 和 390×820 两次不同视口。取消和完成任务身份分别核对，没有把等待脚本作为性能证据，也没有把本机资源 smoke 作为容器构建成功。

实际执行以下扫描，七份文档九类合计 0 命中，退出码 0，无豁免项。原始输出保存在 `.local-preserved/herdr-remaining-20261127/leader-clarity.log`。后续交付状态更新继续使用同一命令核对；模型质量、独立 KP、浏览器焦点与生产服务部署保持实际状态。

```bash
python3 /Users/bytedance/.agents/skills/clear-writing/scripts/clarity_check.py \
  README.md docs/refactor-herdr-validation.md docs/refactor-herdr-ui-observations.md \
  docs/refactor-herdr-delivery.md docs/refactor-remaining-engineering.md \
  docs/refactor-decision-log.md docs/writing-validation.md
```

正文没有为改善表达增加产品决定或成功结论。作者自检与字面扫描分别记录，未进行真实读者测试。
