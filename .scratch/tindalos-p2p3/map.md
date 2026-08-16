# Tindalos P2 完成 + P3 增强 — wayfinder 地图

## Destination

把路线图（`docs/cloud-memory-eval-design.md` §5）**跑完**：P2 补齐「回叙采集链路」（KP 回叙 → play_status + 冲突决策）+ **P3**（sleep-time 离线整合 + 前端记忆可视化）。P0/P1/P2 起步已完成（见 `.scratch/tindalos-p1p2/`）。

- **P2 剩余**：web + CLI 回叙采集（ticket 01）— ✅ 已解决。
- **P3**：sleep-time 离线整合（ticket 02）；前端记忆可视化（ticket 03）— ✅ 已解决。

## Notes

- **领域词汇**（CONTEXT.md，输出禁用同义词）：KP 主控 / NPC subagent / 幕 Act / 场景 Scene / 事件 Event / 线索 Clue / 世界知识图谱 WorldGraph / 备团笔记 / 分幕创作 / 剧本节点图 ScriptGraph。
- **设计来源**：`docs/cloud-memory-eval-design.md` —— §3.3 读写收敛点（P2 游玩路径 record_session）、§3.4 衰变与整合、§4.5 API、§5 P2/P3。
- **已存在（不要重做）**：`record_session`/`list_play_sessions`/`current_play_status`/`briefing`/`consolidate`/`retrieve_memory` 已在 `memory_entries.py`（P1+P2 起步）；近因衰变（decay^天数）已在 `assemble_memory_context`；`GET /api/memories/{campaign_id}` 已含 briefing。
- **铁律**：零 LLM 零网络可测。LLM 路径 FakeLLM stub；降级路径确定性且全绿。
- **测试基线（本阶段完成态）**：后端 **407 passed / 1 deselected**（`python -m pytest tests/ -q -k "not web_dockerfile_build"`，Docker 环境项）；前端 **vitest 104 + build 绿**。
- **python**：Python 3.14.0。
- **语言**：用户中文交流；代码注释/文档中文。
- **安全**：绝不在 GitHub 提交 API keys（只走环境变量）；规则书 PDF 不入库。
- **文件隔离（Round 1 并行）**：01 独占 `web.py`+`tests/test_session_api.py`；02 独占 `cli.py`+`sleep.py`(新)+`tests/test_sleep.py`；03 独占 `frontend/`。互不重叠。
- **提交**：worker 不自行 git commit；集成验收后由主线程统一提交。

## Decisions so far

- [回叙采集链路](issues/01-session-api.md) — `POST /api/sessions/{campaign_id}` + `GET /api/sessions/{campaign_id}` + CLI `tindalos session`，走现有 `record_session`。
- [sleep-time 离线整合](issues/02-sleep-consolidate.md) — `sleep.py` 零依赖调度器 + CLI `tindalos consolidate` + `serve` 注入后台整合线程；`cli.py` 顺带收 CLI `session` 命令。
- [前端记忆可视化](issues/03-memory-visualization.md) — `#/memories` 路由：briefing 卡片 + 剧情线（synopsis/plotline）+ 四类记忆 + 会话时间线（含 conflicts）。

## Not yet specified

- 云数据库迁移（PostgresStore，未来按需）；L5/L6 手动触发完善。

## Out of scope

- P0/P1/P2 起步既有功能（已完成）。
- Docker 镜像/部署验收（环境无 Docker Desktop，测试项跳过）。
