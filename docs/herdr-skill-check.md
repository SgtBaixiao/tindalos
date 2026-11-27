# 本机 Herdr 与写作 Skill 检查

本记录供维护者确认本轮用了哪些入口，以及为什么启动方式与旧 Skill 示例不同。检查日期为 2026-11-27。

七个 Herdr Skill 的入口文件可读：`herdr-dispatch`、`herdr-handoff`、`herdr-implement`、`herdr-leader`、`herdr-patrol`、`herdr-recover`、`herdr-worktree`。入口 Markdown 中直接链接的本地资源存在；Claude 对应入口通过软链接指向 `.agents/skills`。本次 Codex 的可见 Skill 清单也包含这些名称，并已实际读取正文及本轮需要的实施、checkout 参考。

新写作入口为 `clear-writing`。本轮已读取其正文、技术写作和编辑记录参考，使用实际安装的 `clarity_check.py` 检查长期文档。它的编辑规则要求保留必要限定，把删稿过程另存；普通短回复不强制建编辑记录。原仓库已有的写作入口迁移在本轮独立 checkout 原样保留。

## 当前运行入口与兼容性

| 对象 | 实际版本或状态 | 取证入口 |
|---|---|---|
| Herdr App | 0.1.18 | 已安装应用的 `Info.plist` 与 `herdr-app --skill` |
| Herdr Core | 0.1.22 | `herdr-core --version`、`--help`、`--skill` |
| 旧 `herdr` CLI | 当前 PATH 中不存在 | 命令路径查询 |
| 七个 Herdr Skill | 正文使用旧 pane、agent、worktree 合同 | 实际入口 `SKILL.md` 与实施参考 |
| 当前 Core | 使用 terminal 地址、`run`、`read`、`wait` 与 `send` | 当前官方手册明确声明它不是旧终端复用器 |
| 当前 App | 使用 space、tab 与 `do`，显示 Core 终端 | 当前官方命令表与窗口回读 |

当前会话在 Core 中识别为 `t14`，但 shell 工具没有继承 `HERDR_ENV` 或 `HC_TERMINAL_ID`。因此，旧 Skill 的环境检查与命令不能直接套用。本轮从实际运行入口读取官方合同，使用 Core 的可交互 Agent 和 App 的工作区显示能力。

原仓库与 GitHub `main` 均为本轮基线 `11e2362c969215b7e2fda05269b931d442836446`。原仓库仅有既有 `AGENTS.md` 写作入口更新。本轮采用独立本地克隆与唯一实现写入者，保留原数据；原仓库不承担本轮业务写入。

## 本轮实际使用

- 架构审查：Core 启动独立交互式 Codex，读取 Matt `codebase-design`，比较接口方案并输出报告。业务源码只读，补充探针在临时源码副本执行。
- 实施：Core 启动另一交互式 Codex，在独立 checkout 按 R1、R2 与 AC01—AC11 修改源码和行为测试；App 工作区回读确认终端正在运行。
- 评审：实现后的完整差异按 Matt `code-review` 分别审查仓库规范和本轮计划，结果与修复另存验收记录。
- 写作：使用 `clear-writing` 保真与表达自检，长期中文文档实际运行扫描并记录结果。

未修改 Skill 安装、宿主配置、通道注册表或系统定时任务。文件存在与资源可读只证明本机安装完整的相应部分；Herdr 实施是否完成、测试与浏览器流程是否通过，以[本轮验收记录](refactor-herdr-validation.md)为准。
