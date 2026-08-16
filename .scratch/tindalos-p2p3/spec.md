# Tindalos P2 完成 + P3 增强 实施 spec

> 来源：`docs/cloud-memory-eval-design.md` §3.3（P2 游玩路径收敛点）、§3.4（衰变与整合）、§4.5（API/前端）、§5（P2 多会话推进、P3 可选增强）。
> 范围由用户拍板：**完成所有开发任务** = P2 补齐 + P3 全做。

## P2 补齐：回叙采集链路

1. **web 端点**（ticket 01）：`POST /api/sessions/{campaign_id}`（KP 回叙 → `record_session` → play_status + 冲突决策 JSON）+ `GET /api/sessions/{campaign_id}`（会话列表 + 最新 play_status）。
2. **CLI**（ticket 02 顺带，`cli.py` 唯一属主）：`tindalos session <campaign> --summary ...` 走同一 `record_session`。

## P3-1：sleep-time 离线整合（ticket 02）

- `src/tindalos/sleep.py`（新，零第三方依赖）：单次/循环 consolidate 调度器；幂等（`consolidate` 本身靠 content_hash/status 幂等，重复跑无副作用）。
- CLI：`tindalos consolidate [--campaign <id>]` 手动离线整合（打印统计）。
- `tindalos serve`：启动时可选注入后台整合线程（默认关或低频，worker 定最简）。

## P3-2：前端记忆可视化（ticket 03）

- `#/memories/:campaignId`：briefing 卡片（"上次停在哪"）+ 剧情线状态（longterm synopsis/plotline）+ 四类记忆分区 + 会话时间线（session_index/summary/play_status/conflicts）。
- 消费 `GET /api/memories/{campaign_id}`（含 briefing）+ `GET /api/sessions/{campaign_id}`。

## 验收门

- 后端：`python -m pytest tests/ -q -k "not web_dockerfile_build"` 全绿（390 基线 + 新增）。
- 前端：`cd frontend && npm test` 全绿（99 + 新增）；`npm run build` 通过。
- 全部新路径零 LLM 零网络可测。

## 完成态（2026-08-16）

全部实现并集成验收通过，tickets 01/02/03 均 `Status: resolved`（各自含 `## Answer`）。

- 后端全量：**407 passed / 1 deselected**（新增 17 个测试：test_session_api 4 + test_sleep 13）。
- 前端全量：**vitest 104 passed + `npm run build` 绿**（新增 5 个：memory.test.tsx）。
- 新增文件：`src/tindalos/sleep.py`、`tests/test_sleep.py`、`tests/test_session_api.py`、`frontend/src/site/MemoryView.tsx`、`frontend/tests/memory.test.tsx`。
- 新增 CLI：`tindalos session` / `tindalos consolidate` / `tindalos serve --consolidate-interval`。
- 提交：`9e87a4e`（feat: P2 完成 + P3 全做，本阶段主线程提交）。
