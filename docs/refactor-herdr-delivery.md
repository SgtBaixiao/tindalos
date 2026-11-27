# Herdr 重构交付记录

本轮在 `SgtBaixiao/tindalos` 完成生成任务装配和工作台操作提取，并补充实际生产镜像的离线 HTTP／重启冒烟。基线为 `11e2362c969215b7e2fda05269b931d442836446`，工作分支为 `refactor/herdr-workbench-runtime`。源码和测试范围见[计划](refactor-herdr-plan.md)，方法与本机结果见[验收记录](refactor-herdr-validation.md)及[工程补验](refactor-remaining-engineering.md)。

## 交付对象

| 对象 | 当前状态 | 核对来源 |
|---|---|---|
| 生成装配与工作台操作提取 | 已提交并推送，提交 `f911a72a8ff67db966a63408fb5f54122b9c0310` | Git 本地提交与 GitHub 工作分支 |
| 生产镜像冒烟、构建范围及文档 | 本机回归通过，待最终提交和远端检查 | [工程补验](refactor-remaining-engineering.md) |
| PR、合并及 main CI | 待执行 | [GitHub Actions](https://github.com/SgtBaixiao/tindalos/actions/workflows/ci.yml) |
| Pages 静态展示 | 待本轮发布核对 | [Pages 流水线](https://github.com/SgtBaixiao/tindalos/actions/workflows/pages.yml) |
| Python 生产服务部署 | 缺少目标主机与运行配置 | 当前 README 面向本机单进程；Pages 只托管前端 |
| 真实模型与独立 KP 使用 | 缺少项目模型配置与实际验收输入 | [未定产品问题](refactor-clarification.md) |

## 已保留的内容

工作目录为 `/Users/bytedance/workspace/projects/tindalos-herdr-20261127`。独立克隆保护原 checkout 和数据；两处原有 `AGENTS.md` 写作入口更新保留为未提交改动，不混入本轮源码提交。依赖锁文件、数据库 schema、业务 API、原有文案和布局保持既有语义。

本机验收数据、SQLite 备份、完整命令日志、独立评审报告和模型派发回执保存在忽略目录 `.local-preserved`。公开文档保留方法、可复跑入口、合成观察和结果边界；[浏览器记录](refactor-herdr-ui-observations.md)可直接在 GitHub 阅读。

## 尚缺的验证

浏览器控制在第二轮窄屏详情打开后连续超时，完整键盘焦点、大图帧率和该次窄屏保存尚未完成。运行中刷新、取消、完成发表，以及桌面编辑、实际记录和续团已经分别核对。真实手机、真实模型内容质量、独立 KP 使用与真实生产容量仍没有结果。

生产服务还需要明确主机、数据目录和模型设置。项目没有公开服务的账号、权限和隔离方案；本轮镜像冒烟不会代替生产部署或人类使用验收。
