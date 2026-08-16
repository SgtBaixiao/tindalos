# 02 P3-1 sleep-time 离线整合 + CLI 回叙

Type: task
Status: resolved

## 目标

P3「sleep-time 离线整合」+ 承接 CLI 回叙。落在 `src/tindalos/sleep.py`（**新文件**）+ `src/tindalos/cli.py` + `tests/test_sleep.py`（新文件）。**独占 cli.py + sleep.py**，不碰 web.py / frontend / memory_entries.py（01 独占 web.py）。

## 规格（设计文档 §3.4 / §5 P3）

- `sleep.py`（零第三方依赖，可 import 即测）：
  - 离线整合：给定 db_path，枚举有哪些 campaign（读 memory_entries 表 DISTINCT campaign_id）→ 对每个调 `memory_entries.consolidate(campaign_id, db_path, llm=None)`（已存在，勿改）。返回每个 campaign 的整合统计（如 consolidated 条数 / 出错列表）。幂等：consolidate 本身靠 content_hash/status 幂等，重复跑结果一致。
  - 循环模式：`interval_seconds` 轮询 + 停止事件（线程安全）。单次模式 `once=True` 跑一轮即返回。
- `cli.py`（B 独占，可任意改）：
  - `tindalos consolidate [--campaign <id>] [--db <path>]`：手动离线整合（指定 campaign 或全部），打印统计。db 默认 `_data_dir()/"store"/"memory_entries.sqlite"`（先读 cli.py 现有 memories 命令怎么拿 settings/store）。
  - `tindalos serve`：可选后台整合线程（如 `--consolidate-interval` 秒，默认 0=关；开时 daemon 线程 + 停止钩子），最简实现即可。
  - `tindalos session <campaign> --summary "..." [--play-status ...]`：CLI 回叙 → `record_session`（对应 01 的 web 端点，同一函数）。
- 测试 `tests/test_sleep.py`（零网络零 LLM）：临时/内存 DB 插入 campaign + episodic 超 `min_episodic` → 跑一轮 → 旧 episodic 置 consolidated + longterm synopsis 写入；再跑一轮无变化（幂等）；`--campaign` 过滤；serve 线程开关路径不崩。

## 验收

- consolidate 单次跑正确且幂等；CLI consolidate / session 命令退出码与输出正常。
- serve 无 flag 时行为不变（不新增后台线程）。
- `python -m pytest tests/test_sleep.py tests/test_cli.py -q` 全绿；再全量确认无回归（390 基线保持）。
- **不要 git commit。**

## Answer

已实现并全绿（`tests/test_sleep.py` 13 个新测试；全量 407 passed / 1 deselected）。

- `src/tindalos/sleep.py`（零第三方依赖）：`list_campaign_ids(db_path)` / `consolidate_campaign(campaign_id, db_path, llm=None, min_episodic=20) -> dict`（单 campaign 异常兜底）/ `run_consolidation(db_path, campaign_ids=None, ...) -> {campaigns, total_consolidated, errors}` / `ConsolidationLoop(interval_seconds, ...)`（stopped 属性 + run_once/start/stop(timeout)）。
- `cli.py`：`tindalos consolidate [-c <id>] [--db <path>]`（手动离线整合，打印统计；空库提示）；`tindalos session <campaign> --summary "..." [--play-status ...]`（回叙 → record_session，打印第 N 场）；`tindalos serve --consolidate-interval <秒>`（默认 0=不启动，>0 启动 daemon 线程 + 退出 join）。

**与 ticket 的偏差**：未加 `once=True` 参数——`run_consolidation()` 天然单轮即返回，`ConsolidationLoop.run_once()` 供手动单轮，循环线程内部复用，行为一致。
