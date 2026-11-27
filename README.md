# Tindalos

Tindalos 为人类守秘人（KP）提供可编辑、可保存、可续用的 TRPG 备团材料。工作台贯通模组导入、任务生成、剧本修订、实际场次记录、续团和评测反馈。人类 KP 确认发生了什么，并决定采用哪些创作建议。

[GitHub](https://github.com/SgtBaixiao/tindalos) · [静态展示](https://sgtbaixiao.github.io/tindalos/) · [运行架构](docs/runtime-architecture.md) · [界面验收](docs/ui-refactor-validation.md) · [前端规范](docs/frontend-architecture.md) · [决策日志](docs/refactor-decision-log.md) · [本轮 Herdr 验收](docs/refactor-herdr-validation.md) · [交付记录](docs/refactor-herdr-delivery.md)

静态展示只提供前端素材。导入、生成、编辑保存与场次记录需要 Python 服务。新版本的展示发布状态见 [GitHub Actions](https://github.com/SgtBaixiao/tindalos/actions/workflows/pages.yml)。

## 本地启动

使用 Python 3.12、Node.js 24 和 uv 0.13.9。Python 依赖固定在 `uv.lock`，前端依赖固定在 `frontend/package-lock.json`。

```bash
uv sync --locked --all-extras
cd frontend
npm ci
npm run build
cd ..
uv run --locked --all-extras tindalos web --port 8347
```

打开[本地工作台](http://127.0.0.1:8347/#/workbench)。服务默认绑定回环地址，数据保存在当前目录下的 `data/`。这套方案面向本机使用；公开服务的账号、权限和隔离仍需设计。

## 完成备团与续团

1. 在模组资料库上传 PDF，或在工作台输入模组正文与备团前提。
2. 选择已上传模组，点击生成。默认使用离线模板；使用模型需先完成下节配置。
3. 点击幕、场景、事件、NPC 或线索，检查并编辑内容。保存成功后显示新修订号，刷新后可从“打开已保存剧本”继续编辑。
4. 在“记录本场与评测”填写实际游玩记录。确认事实按每行 `主体.状态=内容` 填写，例如 `Hope.life=alive`。
5. 点击“准备下一场”。系统引用实际场次与当前确认事实，保留原有幕及 NPC，追加备团材料。
6. 评测当前版本，在评测详情页填写 KP 使用反馈。人工反馈关联被评修订，原自动结论保留。

同版本并发编辑会返回冲突，保留未提交的草稿。任务断线后继续执行，重连订阅原任务；显式取消后结果不会写入剧本。服务重启将未完成任务标为中断，需要明确重新提交。

NPC 可编辑目标、已知信息和资源；线索可编辑支持结论、取得条件、替代路径与来源引用。离线模板提供结构样例，尚未实现带原文页码的自动事实抽取和按取得条件计算的调查图，内容可主持性也未通过实际 KP 验收。

## 配置模型

生成器通过 OpenAI 兼容的聊天接口调用模型，密钥只放在环境变量中。

```bash
export TINDALOS_LLM_ENABLED=1
export TINDALOS_API_KEY=your-key
export TINDALOS_API_BASE=https://your-provider.example/v1
export TINDALOS_MODEL=your-model
uv run --locked --all-extras tindalos web --port 8347
```

在工作台勾选“使用模型”。未开启服务端模型开关时，请求显式失败；已开启但某步模型失败时，可使用模板替代，并在结果中记录原因。替代结果需要审阅。

视觉与向量配置、旧 CLI 示例保存在[旧版说明](docs/legacy-readme-20261119.md)，配置项以 [config.py](src/tindalos/config.py) 为准。旧版说明中的测试数量、部署状态和质量结论是历史记录。

## 命令行与验证

命令行保留生成、笔记、知识图谱、评测、修复、记忆、场次及模型连通检查。CLI 生成和 Web 使用相同生成图；CLI 文件操作与 Web 修订库分别管理。四维 CLI 评分是兼容的结构报告，Web trace 另记录证据、跳过项和人工反馈。

```bash
uv run --locked --all-extras tindalos generate examples/sample-module.md --out /tmp/campaign.json
uv run --locked --all-extras tindalos eval /tmp/campaign.json
uv run --locked --all-extras python -m pytest tests/ -q
cd frontend
npm test
npm run format:check
npm run check:modules
npm run build
npm run check:bundle
```

2026-11-27 本机验收：完整前端 118 项通过，格式、模块、类型、生产构建和首屏体积检查通过。后端完整检查、最后补测与生产镜像冒烟分别记录，具体数量及范围见[本轮验收](docs/refactor-herdr-validation.md)和[工程补验](docs/refactor-remaining-engineering.md)。界面更新和前端性能见[界面验收](docs/ui-refactor-validation.md)，存储与生成验证见[工程验收](docs/refactor-validation.md)。

## 数据与部署

每个数据目录只允许一个服务进程，生成执行器默认 2 个工作线程、最多容纳 16 项运行或排队任务。存储使用本机 SQLite WAL，读取与短事务分别管理。SQLite 文件要求本机可靠磁盘，每个目录只有一个服务副本。

停止服务后备份全部数据：

```bash
uv run --locked --all-extras python tools/backup_data.py --data data --out .local-preserved/manual-backup
```

工具取得服务独占锁，对 SQLite 使用备份 API，复制其他素材。备份目的地必须是新目录。自定义站点库也须放在同一数据目录内；恢复时停止服务，保留当前目录，将完整备份复制为新的数据目录。

容器构建入口为 `deploy/Dockerfile`，使用两个依赖锁文件，单进程提供前端和 API。CI 在 PR 和 main 上构建实际生产镜像，并检查离线 HTTP 生成、资源与重启持久化；运行时无外部网络且不映射公开端口。本机未安装 Docker，远端实际结果见[交付记录](docs/refactor-herdr-delivery.md)。

已有 Docker 时可复用检查入口：

```bash
docker build -f deploy/Dockerfile -t tindalos:smoke .
bash scripts/smoke_production.sh tindalos:smoke tindalos-local-smoke
```

冒烟使用隔离容器内数据，完成后删除该容器。此脚本的等待工具 `timeout` 适用于 CI 的 Linux 环境。

## 研究与设计

- [项目理解与原始问题](docs/review-project-understanding.md)
- [调查线索与实际状态设计](docs/deep-functional-design.md)
- [单机任务与存储研究](docs/research-runtime-storage.md)
- [客户端与事实评测研究](docs/research-client-evaluation.md)
- [深化后的研究提示词](docs/deep-research-prompt.md)
- [领域词汇](GLOSSARY.md)与[未定产品问题](docs/refactor-clarification.md)

代码采用 MIT 许可；模组、规则书及第三方素材分别遵循原有许可。
