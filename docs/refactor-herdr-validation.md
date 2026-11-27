# Herdr 重构验收记录

本记录供维护者判断本轮改动的范围、验证结果与剩余验收。检查日期为 2026-11-27，基线为 `11e2362c969215b7e2fda05269b931d442836446`，工作分支为 `refactor/herdr-workbench-runtime`。

生成任务装配和工作台操作已完成提取，现有自动检查通过。两轮独立 Standards／Spec 评审未发现已确认的源码违规或违约；首轮指出的计划歧义已澄清并补测。实际浏览器完成了离线生成、编辑保存、实际记录、续团和刷新重开；运行中刷新恢复与取消已核对；390×844 首屏和 390×820 图／详情布局已观察。完整键盘焦点与大图帧率检查因浏览器控制超时尚未执行。

## 改动与方法

`generation_jobs.py` 集中提交准备、执行上下文与续团合并。HTTP 层只调用提交入口；JobRuntime 继续管理容量、取消、预算、CAS、修订与终态事务。原 `workspace.py` 三个函数名以导入别名保留，生成时仍动态读取 `web.default_generate`。

`site/useWorkbenchController.ts` 集中生成、续团、打开、取消、重试键和终态刷新。页面组合原展示组件，表单草稿仍由表单保存，图仍使用原 store，目录与进度仍复用原组件。文案、DOM、样式、组件 key 和禁用条件保持基线。

架构审查使用 Matt `codebase-design` 比较至少两种接口方案；实现沿 Matt `tdd` 在已有 HTTP、任务运行器和页面入口建立行为检查，先在原实现通过，再验证提取后的相同断言。纯提取没有制造人工失败来声称修复缺陷。前端探针使用图 mock，生成装配检查使用替代生成器；真实离线流程另行执行。

Herdr Core 启动架构审查 `t15`、唯一源码实现者 `t16`，随后启动独立规范评审 `t1b` 和验收项评审 `t19`；反馈处理后，以新的独立上下文启动最终评审 `t1c` 和 `t1d`。当前 App／Core 合同与旧 Skill 的区别见[本机 Skill 检查](herdr-skill-check.md)。业务工作在独立克隆中完成，原仓库保留既有 `AGENTS.md` 修改及原数据。

## 自动检查

Python 命令在仓库根目录运行，npm 命令在 `frontend` 运行。原始输出保存在本地忽略目录 `.local-preserved/herdr-20261127`，没有纳入源码提交。

| 检查 | 实际结果 | 证据日志 |
|---|---|---|
| 原实现后端现有入口 | 43 passed | `implementation-baseline-backend-existing.log` |
| 原实现新增装配行为检查 | 修正测试夹具后 17 passed；首次失败没有改动业务代码 | `implementation-baseline-backend-added-fixed.log` |
| 原实现前端现有与新增检查 | 34 passed；首次依赖链接引起 EPERM，改成本地依赖副本后执行成功 | `implementation-baseline-frontend-fixed.log` |
| R1 后相同后端入口 | 60 passed | `implementation-r1-backend.log` |
| 规则优先级组合：原实现与当前实现分别执行 | 各 1 passed，执行规则、保存规则与旧快照一致 | `implementation-rules-priority-baseline.log`、`implementation-rules-priority-current.log` |
| 补测后相关后端入口 | 61 passed；新增装配检查现为 18 项 | `implementation-rules-priority-related.log` |
| 完整后端：`./.venv/bin/python -m pytest -q` | 523 passed，1 skipped，28 warnings，33.70 秒 | `implementation-final-backend-tests.log` |
| 完整前端：`npm test` | 12 个文件，118 passed | `implementation-final-frontend-tests.log` |
| `npm run format:check` | 通过 | `implementation-final-format.log` |
| `npm run check:modules` | 44 个源码文件通过依赖方向和循环检查 | `implementation-final-modules.log` |
| `npm run build` | TypeScript 与 Vite 生产构建通过 | `implementation-final-build.log` |
| `npm run check:bundle` | 8 个延迟路由，原预算通过 | `implementation-final-bundle.log` |
| 长期保存解释器的导入检查 | Python 3.12.16，模块导入与兼容别名通过 | `environment-stable-check.log` |
| 长期保存解释器的真实离线流程 | 1 passed，6 warnings | `environment-stable-offline-workflow.log` |

完整后端检查早于最后新增的规则组合测试；新增测试已在原实现、当前实现及相关 61 项检查中通过，没有将它计入此前的 523 项。后端跳过项为既有 Docker 不可用条件下的容器检查。warning 原样保留，没有屏蔽。两次真实离线业务验证均调用实际生成管线和临时 SQLite，涵盖 PDF 导入、生成、幂等重试、编辑、重载、实际记录、续团、评测批注与修订恢复；它们通过 FastAPI TestClient 执行，不等于浏览器验收或真实模型质量验收。

首屏 JS 为 203855 字节、gzip 为 64314 字节；CSS 为 13504 字节、gzip 为 3756 字节。原预算分别为 JS 230000／75000、CSS 16000／5000 字节。基线首屏 JS 同为 203855 字节，本轮没有据此宣称性能提高。

## 两轮独立评审

首轮评审冻结了相对于基线的完整差异，包括 5 个已跟踪修改和 6 个新增文件；11 个文件逐项校验哈希。评审者使用独立上下文，业务源码只读，没有重复运行测试。模型派发前在本地分类同一冻结范围，并使用当前目的地的一次性同进程披露门禁；只发送本轮源码、合成测试材料和工程记录。

| 评审轴 | 首轮结论 | 处理 |
|---|---|---|
| Standards，`t1b` | 0 项已确认违规，0 项启发式发现 | 兼容别名有明确要求；生成装配与发表、页面操作与表单／图职责符合仓库约定 |
| Spec，`t19` | 0 项已确认违约，1 处 AC02 表述疑点 | 明确沿用基线规则优先级，并补充模组规则与战役规则不同的组合检查 |

疑点来自 AC02 原文“固定……战役规则”：基线先取战役规则，有模组时再以模组规则覆盖执行输入；续团保存结果保留采用快照的战役规则。本轮保留该行为，计划已明确执行规则与保存规则，D032 记录依据。改变这个优先级需要另一项产品或工程决定。

最终评审重新冻结 13 个文件并逐项核对哈希，包含新增组合测试和验收记录。`t1c` 的 Standards 结论仍为 0 项违规、0 项异味，另建议补充浏览器观察的证据定位（P3）；`t1d` 的 Spec 结论为 0 项源码违约、0 项范围外行为，保留浏览器覆盖不足的限制。两位最终评审者均未重复运行测试或操作浏览器。

P3 已通过下文的 Leader 现场观察记录、请求日志与持久化核对补充；实施者观察明确标注为报告来源。评审原文按当时状态保留，之后只补充验收证据与交付状态，业务源码和测试没有变化。四份报告分别保存在本地证据目录的 `review-standards`、`review-spec`、`review-final-standards` 和 `review-final-spec`。

## 实际浏览器观察

据实施者报告，原生 Chrome 在独立临时数据目录、仅绑定 `127.0.0.1` 的服务器上完成离线生成至版本 1、幕详情编辑保存至版本 2，以及实际记录和确认事实保存。这些操作使用实际离线管线，没有生成 mock。Leader 接管 UI 验收后，实施者停止浏览器操作和自己启动的服务器；报告保存在 `implementation-report.md`。

Leader 首次接管时遇到桌面连接启动失败，该次实例只读取了页面、资源和目录。连接恢复后，在新的 `8359` 实例中通过原生 Chrome 完成以下操作，使用合成材料《封闭灯塔》与关闭的模型开关：

1. 离线生成完成版本 1，刷新后从目录重新打开，图画布显示完整节点。
2. 选择第一幕，将摘要改为“采用编辑：先核对灯塔档案，保留码头目击者的证词。”，保存后显示“已保存版本 2”。
3. 保存实际记录“调查员找到了地下室钥匙，目击者仍然存活。”及确认事实 `目击者.生命=存活`，显示“本场记录已保存，续团将引用这些实际记录。”
4. 准备下一场后显示“任务已完成，保存版本 3”，图追加 `r3-` 节点；再次刷新并重新打开，仍为版本 3，采用编辑保留。
5. 在 Chrome 设备工具栏确认宽 390、高 844，观察页头、横向导航、单列表单及并排操作按钮。截图已在本轮工具回执中展示。

可追查的操作、页面文案和任务身份见[本轮 UI 观察记录](refactor-herdr-ui-observations.md)。本地请求日志 `leader-browser-server.log` 与数据库只读核对 `leader-browser-persistence.json` 保存在忽略证据目录。它们证明本轮合成材料的操作与保存结果，不验证真实模型的内容质量。预览服务器已正常停止。

第二轮为实际离线生成器增加可取消等待，运行中多次刷新恢复同一任务。取消后没有发表战役；另一项任务释放等待后完成。数据库只有两项提交和一个完成战役。390×820 下的图、图例、控制按钮和节点详情已观察，详情保存与完整焦点检查尚未完成。等待脚本没有修改业务源码，也不能用于性能测量。

静态 JSX 对照、jsdom 行为检查和离线 API 流程分别支持自己的范围。新增生产镜像冒烟与短时离线容量实验见[剩余工程验证](refactor-remaining-engineering.md)。Q001、真实模型内容质量、独立 KP 使用验收、大图帧率与真实生产容量没有本轮结果。

## 本地开发与交付状态

本轮目录为 `/Users/bytedance/workspace/projects/tindalos-herdr-20261127`。Python 与 uv 长期保存在 workspace 的 `.tools` 下，`.venv` 的解释器链接与 `pyvenv.cfg` 已改为该路径，避免依赖被清理的临时解释器。依赖沿用锁文件，前端依赖为本地副本。

继续开发可在仓库根目录运行 `./.venv/bin/tindalos web --port 8359`；验收实例使用隔离数据目录与关闭的模型开关，运行数据保留在本地忽略目录。正式运行前按 README 配置自己的数据与模型设置。

首项提取提交为 `f911a72a8ff67db966a63408fb5f54122b9c0310`，已推送到原 GitHub 仓库的工作分支。原有 `AGENTS.md` 写作入口修改在两个 checkout 中均保留为未提交改动，未并入本轮源码提交。新增工程交付的最终提交、PR、CI 与 Pages 状态见[交付记录](refactor-herdr-delivery.md)。中文成稿使用 `clear-writing`，实际扫描结果见[写作检查记录](writing-validation.md)。
