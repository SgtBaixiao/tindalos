# 生产镜像检查与离线容量实验

本报告供维护者和 Leader 核对剩余工程工作、复跑检查及安排远端 CI。日期为 2026-11-27，分支为 `refactor/herdr-workbench-runtime`，起点为 `f911a72a8ff67db966a63408fb5f54122b9c0310`。材料来自当前源码、真实回环 HTTP、临时 SQLite 与原始命令日志。

生产镜像 CI job 和可复用 smoke 脚本已添加，本地 3 项回归通过，Docker/Moby 的 35 项路径检查通过。离线容量实验的 64 次提交中，16 次被接受、48 次返回 429；16 项任务全部完成，保存为 16 个唯一战役，重启后读取结果一致。本机没有 Docker，镜像构建、容器启动和远端 CI 尚未执行，由 Leader 后续验证。

## 生产镜像 CI 检查

原 `deploy/Dockerfile` 已采用 Node.js 24 构建前端，以 Python 3.12 和 uv 0.13.9 按 `uv.lock` 安装后端，再复制前端产物。当前没有需要修改 Dockerfile 或业务源码的已确认问题。原 CI 检查后端、前端及 CLI，新增 `production-image` job 使用同一份生产 Dockerfile，沿用 `pull_request` 和推送 `main` 的触发条件。

CI 先构建本地镜像，再调用 Shell 脚本创建命名容器。容器使用 `--network none`，没有宿主端口映射、卷挂载或继承宿主环境，模型开关显式设为 `0`。合成运行数据写到容器的 `/tmp/tindalos-smoke-data`，HTTP 探针通过 `docker exec` 在容器内访问回环地址。

探针只依赖 Python 标准库，先检查健康响应中的单进程、SQLite WAL、2 个 worker 和容量 16，再检查首页、实际引用的 JS/CSS 内容及 MIME 类型、SPA 回退。空目录确认后，它提交合成灯塔材料，显式指定 `llm=false`，等待真实离线任务完成，并读取战役、修订和目录。检查同时要求生成模式为 `deterministic`，幕、场景及 NPC 非空，原始材料与修订号一致，同键重试复用同一任务。

首阶段将完整任务与战役响应保存为 receipt；脚本重启同一容器，第二阶段读取相同身份并逐项比较完整响应。这个检查验证进程重启后的持久读取。运行数据保存在该容器的可写层，没有测试容器删除后通过卷重建，也没有把它当成备份恢复检查。

每阶段 HTTP 总预算为 60 秒，单请求超时最多 3 秒；两次 `docker exec` 各由 `timeout 90s` 限制，容器重启的停止等待为 10 秒，CI job 总预算为 20 分钟。Shell 的 EXIT／INT／TERM trap 输出日志并移除本轮创建的容器；CI 另有 `if: always()` 清理步骤，覆盖 smoke 失败或中途退出。这里的清理逻辑已做语法与代码核对，实际 Docker 清理仍待远端执行。

新增实现位于以下文件。

| 文件 | 用途 |
|---|---|
| `.github/workflows/ci.yml` | 构建实际生产镜像、调用 smoke、始终清理容器 |
| `scripts/smoke_production.sh` | 命名容器、隔离数据、停启与清理 |
| `scripts/smoke_production.py` | 健康、SPA／资源、离线任务与重启读取 |
| `tests/test_production_smoke.py` | 真实回环 HTTP 的成功、缺失资源和等待上限回归 |

Leader 可在具有 Docker 与 GNU `timeout` 的 Linux 环境从仓库根目录运行：

```bash
docker build --progress=plain -f deploy/Dockerfile -t tindalos-production:ci .
bash scripts/smoke_production.sh tindalos-production:ci tindalos-production-smoke-local
```

两个命令都应以退出码 0 结束；日志应有 `generate` 和 `verify-persisted` 两份 JSON，任务状态均为 `completed`，身份相同，模式为 `deterministic`，修订为 1。脚本结束后，命名容器应不存在。镜像仅在本机构建，不发布到 registry。

## Docker 构建上下文

`.dockerignore` 补充了 `mods`、`.scratch`、内部工作 JSON，以及递归 SQLite 主文件、WAL／SHM、Python 缓存和环境配置规则。`references/*` 只为 `references/style-guide.md` 放行；生产所需源码、锁文件、README 和配置样例继续保留。

采用 Docker/Moby 官方 `github.com/moby/patternmatcher v0.6.0` 的 `ignorefile.ReadAll` 与 `MatchesOrParentMatches` 核对路径。首轮检查发现 `.env` 规则没有排除 `deploy/.env`，退出码为 1；改为 `**/.env` 与 `**/.env.*`，保留 `!**/.env.example` 后通过。检查只传入路径，没有读取任何凭据或本地素材内容。

最终 35 项检查中，25 项应排除的路径全部被排除，10 项生产输入和样例配置全部保留。检查覆盖运行数据、本轮证据、工作目录、模组图片、references 原文、嵌套环境配置、SQLite 文件及前端依赖。匹配器的来源、版本和逐路径结果保存在证据中；这证明已测路径的规则结果，没有替代 Docker 实际打包和镜像构建。

```bash
cd .local-preserved/herdr-remaining-20261127/dockerignore-audit
GOCACHE=/private/tmp/tindalos-go-cache go run . ../../../.dockerignore
```

该辅助 Go module 只在忽略证据目录中使用，没有加入产品依赖。首次下载因沙箱网络限制失败，获准下载后运行成功。

## 当前运行器的短时离线容量实验

实验在独立 Uvicorn 进程上运行未替换的真实离线生成管线，监听 `127.0.0.1`，每轮使用空临时 SQLite。32 个 HTTP 客户端在 barrier 后并发提交，每个客户端顺序发出 2 次请求，共 64 次，全部使用不同幂等键，不重试 429。输入为带序号的合成灯塔材料，每份 174 个字符、174 字节，64 个输入 SHA-256 均不同。没有注入延迟，没有模型调用。

| 设置 | 实际值 |
|---|---|
| 环境 | macOS 26.5.1 arm64，Python 3.12.16，SQLite 3.53.1 |
| JobRuntime | 2 个 worker，运行加排队容量 16，任务预算 600 秒 |
| 生成图 | 每任务并发上限 4，2 幕，3 个 NPC |
| 客户端 | 32 个并发 HTTP 客户端，每个 2 次独立提交 |
| 实验限制 | 请求超时 5 秒，负载阶段预算 45 秒，启动与正常停止各 10 秒 |

实验结果按提交响应、任务终态和持久战役分别计数。

| 观察项 | 实际结果 |
|---|---:|
| 提交请求 | 64 |
| HTTP 202 接受 | 16 |
| HTTP 429 拒绝 | 48，全部包含 `Retry-After: 1` |
| 其他响应／传输错误 | 0 |
| 完成任务／唯一任务 ID | 16／16 |
| 唯一持久战役／目录战役 | 16／16 |
| SQLite 战役行／唯一 ID／修订行 | 16／16／16 |
| 失败、取消、中断及未完成任务 | 各 0 |
| 重启后任务与完整战役响应一致 | 16 项均一致 |
| 提交阶段耗时 | 0.1526556669268757 秒 |
| 负载阶段耗时，包含提交与等待完成 | 2.1754857499618083 秒 |
| 总耗时，包含启动、两次停止及重启读取 | 4.396740291966125 秒 |

全部完成战役的生成模式为 `deterministic`。SQLite 只读核对的 `journal_mode` 为 `wal`，`integrity_check` 为 `ok`；API 读取与数据库行数相符。服务器停止后以同一临时数据库重启，16 项任务和完整战役响应不变，随后关闭服务器并删除临时数据，原始样本 JSON 与日志保留。

这是一轮短时合成离线突发提交，不是持续负载或真实模型吞吐量测试，也不能认证生产容量。容量 16 限制某一时刻运行加排队的任务数；任务释放槽位后仍可接受新请求，因此不能把本次接受数量当作所有负载下固定的接收上限。负载阶段计时包含客户端准备和终态轮询，不能当成单次纯生成耗时。

从仓库根目录可按以下命令复跑。Harness 保存在忽略目录，作为实验材料，没有加入长期回归测试。

```bash
./.venv/bin/python .local-preserved/herdr-remaining-20261127/capacity_experiment.py \
  --out .local-preserved/herdr-remaining-20261127/capacity-rerun.json
```

## 本地核验与证据

本地测试以已授权的 HTTP seam 为观察入口，不替换生成器或 JobRuntime。先添加真实回环测试，再实现 CLI：首轮因沙箱禁止绑定端口失败，放行回环执行后的 red 为缺少 smoke CLI；实现后成功，再补资源缺失与 HTTP 等待上限回归。两类首次失败的日志分别保留，没有把沙箱失败当成产品缺陷。

证据根目录为 `.local-preserved/herdr-remaining-20261127`。下表列出本轮实际结果，不重新累计此前完整后端或前端测试。

| 检查／命令 | 实际结果 | 原始证据 |
|---|---|---|
| `./.venv/bin/python -m pytest -q tests/test_production_smoke.py` | 3 passed，6.05 秒，退出码 0 | `smoke-regression.log` |
| 本地现有 `frontend/dist` 的真实 HTTP smoke | 2 个阶段通过，JS／CSS 各 1 个；离线生成 3.480 秒，重启读取 0.930 秒 | `local-production-assets.log`、`local-production-assets.json` |
| Docker/Moby 范围检查 | 35 项通过：25 排除、10 保留，退出码 0 | `dockerignore-audit.json`、`dockerignore-audit.stderr.log` |
| 容量实验 | 64 次提交，16 完成，48 次 429，退出码 0 | `capacity.json`、`capacity.log`、`capacity.server.log` |
| Python 语法／Shell 语法／CI YAML | 2 个 Python 文件、1 个 Shell 文件、1 份 workflow 通过；触发条件不变 | `static-checks.log` |
| `git diff --check` | 通过，退出码 0 | `static-checks.log` |

本地生产前端验证使用已有 Vite 产物，没有重建前端。两阶段 receipt 和请求日志也在证据目录中；它验证应用可提供该产物，不能证明 Docker 前端构建已成功。新增 smoke 测试使用合成 HTML／JS／CSS，容量实验则不加载前端，两者范围分别保留。

本执行者未改业务源码、Dockerfile、依赖锁、README 或 Leader 负责的验收／决策／写作记录。原有未提交 `AGENTS.md` 的 SHA-256 仍为 `9fcb2557e8701637296be560aa725ae5db339cad6c68c0ce3da82c520120d8af`，按字节保留。CI 与隔离验证的工程选择及其依据已写入本报告，由 Leader 汇总到最终记录。

## 交付范围与待验证项

本轮新增或修改的可入库文件共 6 个：`.dockerignore`、`.github/workflows/ci.yml`、`scripts/smoke_production.py`、`scripts/smoke_production.sh`、`tests/test_production_smoke.py`、本报告。忽略目录中的 harness、receipt、原始样本、Go module 和日志作为本机证据保留。Leader 的 UI 与最终说明另行维护，不计入这 6 个文件。

Docker 实际构建仍可能遇到基础镜像拉取、registry／包源网络、Linux wheel 安装或启动差异。本机的 Node.js 24、Python 3.12 与回环 HTTP 检查不能排除这些风险；新增 CI 将直接检查它们。没有 Docker 构建、远端 CI、公开部署或真实模型的本轮成功声明。

本报告使用 `clear-writing`，事实与命令对照原始日志。实际运行以下扫描，九类合计 0 命中，退出码 0，无需按提示修改，无豁免项；原始输出保存为 `clarity.log`，初次输出另存为 `clarity-initial.log`。作者自检与脚本扫描没有替代真实读者测试，本轮未做读者测试。

```bash
python3 /Users/bytedance/.agents/skills/clear-writing/scripts/clarity_check.py \
  docs/refactor-remaining-engineering.md
```
