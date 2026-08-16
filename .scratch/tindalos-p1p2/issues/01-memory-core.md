# 01 记忆核心：consolidate + longterm + record_session + /api/memories

Type: task
Status: claimed

## 目标

P1 #1 + #2 + §4.5 `/api/memories/{campaign_id}` 端点。全部落在 `src/tindalos/memory_entries.py` + `src/tindalos/web.py`（仅新增端点）+ `tests/test_memory_p1.py`（新文件）。

## 规格（摘自 spec.md）

- `consolidate(campaign_id, db_path, llm=None, min_episodic=20)`：
  - LLM 两段式：第一遍把 episodic+semantic 摘要给 LLM，返回 ADD/UPDATE/DELETE 操作列表；第二遍执行操作并产出 longterm（synopsis/plotline/npc_arcs）。被整合的 episodic 置 `consolidated`（不物理删除）。LLM 返回结构不合法 → 确定性降级。
  - 无 LLM 确定性降级：episodic 超 `min_episodic` 时把最旧部分置 `consolidated` + 确定性拼 synopsis（subject_key='synopsis'）。幂等：同输入两次结果一致（靠 content_hash / status 幂等）。
- `longterm` 写入：memory_type='longterm'，subject_key ∈ {synopsis, plotline, npc_arcs}；更新同键旧条目（旧版置 superseded，supersedes_id 链）；ref_ids 连回源条目。
- `record_session(campaign_id, session_summary, db_path, llm=None, play_status=None, conflicts=None)`：新建 `play_sessions` 表（id/campaign_id/session_index/summary/play_status/conflicts JSON/created_at）；随后触发轻量 consolidate；`current_play_status()` 返回最近 play_status。确定性路径零 LLM。
- `GET /api/memories/{campaign_id}` → `{campaign_id, status, play_status, memories:{episodic:[], semantic:[], shortterm:[], longterm:[]}}`。先读 `src/tindalos/web.py` 现有端点风格 + `render_entries_doc` / `count_entries`。

## 边界

- 不破坏 `tests/test_memory.py` 现有 342 基线的任何用例（schema 兼容，只加列/表）。
- `memory_entries.py` 现有 capture_episodic/capture_semantic_initial/assemble_memory_context 签名不动。
- LLM 路径用 `FakeLLM`（tests 内 stub）驱动，零网络；无 LLM 路径全确定性。
- 涉及 play_sessions 新表时用 `CREATE TABLE IF NOT EXISTS` 迁移式建表（兼容既有 memory_entries.sqlite）。

## 验收

- consolidate 确定性降级幂等测试；FakeLLM 两段式协议测试（ADD/UPDATE/DELETE 生效 + longterm 三键写入）。
- record_session：play_status 更新 + 最近会话可读 + conflicts JSON 往返。
- /api/memories 端点 FastAPI TestClient 测试：四类 + play_status。
- 运行 `python -m pytest tests/test_memory_p1.py tests/test_memory.py -q -k "not web_dockerfile_build"` 全绿；再跑全量确认无回归。
