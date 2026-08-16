# 01 P2 回叙采集链路 — web 端点

Type: task
Status: resolved

## 目标

P2「KP 回叙/live 采集实际游玩 → play_status 更新 + 冲突决策」的 web 面。落在 `src/tindalos/web.py` + `tests/test_session_api.py`（新文件）。**独占 web.py**，不碰 cli.py / sleep.py / frontend / memory_entries.py（02 独占 cli.py）。

## 规格（设计文档 §3.3 P2 游玩路径 / §4.5）

- `POST /api/sessions/{campaign_id}`：body `{summary: str, play_status?: str, conflicts?: list[dict]}` → 调 `memory_entries.record_session(campaign_id, summary, db_path=_, play_status=_, conflicts=_)`（已存在，勿改）→ 返回该会话结果（session_index / play_status / conflicts / created_at 等，先读 record_session 返回结构）。校验：summary 非空；db_path 与 `/api/memories` 端点同款 `_data_dir()/"store"/"memory_entries.sqlite"`。
- `GET /api/sessions/{campaign_id}` → `{campaign_id, current_play_status, sessions: [...]}`（用 `list_play_sessions` + `current_play_status`，均已在 memory_entries.py）。
- 风格与现有端点一致（先读 web.py 现有端点 + memories 端点怎么取 db_path / 用 Pydantic 模型还是 dict）。
- 零 LLM：record_session 的轻量 consolidate 走确定性路径；测试全确定性。

## 验收

- POST 后：play_status 更新、session_index 递增、conflicts JSON 往返正确；空 summary 4xx。
- GET 空 campaign → 空列表 + None play_status。
- 不破坏现有 390 基线任何用例。
- `python -m pytest tests/test_session_api.py -q` 全绿；再全量确认无回归。
- **不要 git commit。**

## Answer

已实现并全绿（`tests/test_session_api.py` 4 个新测试；全量 407 passed / 1 deselected）。

- `POST /api/sessions/{campaign_id}`：body `{summary（必填）, play_status?, conflicts?: list[dict]|None}`，summary 空/纯空白 → 400，缺字段 → 422；db_path 与 /api/memories 同款；返回 `{session_id, session_index, summary, play_status, conflicts, created_at, consolidate}`（conflicts JSON 已解析回对象）。
- `GET /api/sessions/{campaign_id}` → `{campaign_id, current_play_status, sessions:[...]}`（升序，conflicts 解析回对象；空 campaign → 空列表 + None，不 404）。

**与 ticket 的偏差**：conflicts 模型收紧为 `list[dict[str, Any]] | None`（与 §3.3 对象列表语义一致）；POST 返回额外含 summary 与 consolidate（record_session 原生返回）。
