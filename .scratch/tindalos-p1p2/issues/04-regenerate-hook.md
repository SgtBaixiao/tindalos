# 04 /api/regenerate 记忆一致性钩子

Type: task
Status: resolved

## 目标

P1 #3。落在 `src/tindalos/regenerate.py` + `src/tindalos/web.py`（regenerate 路由）+ `src/tindalos/memory_entries.py`（仅调用现有 capture 函数，不重构）+ `tests/test_regenerate_memory.py`（新文件）。**须在 01 完成后开始**。

## 规格（摘自设计文档 §3.3 修复钩子 / 风险表）

- regenerate 改节点成功后：
  1. 对改动节点的内容走 `capture_episodic`（event-id 幂等 upsert 覆盖新内容）。
  2. 相关 semantic（subject_key 命中该节点）与 longterm（ref_ids 命中）条目置 `superseded`，新版本 active（supersedes_id 链）。
- web.py `POST /api/regenerate`（或现有 regenerate 路由）在返回成功时附带记忆一致性副作用，失败/回滚不写记忆。
- 确定性路径零 LLM；用内存临时 DB 测试。

## 验收

- regenerate 后：情景条目为新内容；相关语义/长期旧条目 superseded + 新条目 active。
- 回滚（校验失败）时记忆不变。
- `python -m pytest tests/test_regenerate_memory.py tests/test_regenerate.py -q` 全绿。

## Answer

已实现并全绿（`tests/test_regenerate_memory.py` 10 个新测试 + `test_regenerate.py` 21 个全绿；
全量 `tests/ -q -k "not web_dockerfile_build"` 379 passed / 1 deselected / 0 failures）。

- `memory_entries.py` 追加唯一公开函数 `supersede_entries(campaign_id, db_path=None, ids=None, subject_keys=None) -> int`；
- `regenerate.py` 在 `regenerate_node` 成功路径接 `_apply_memory_hook`（keyword-only `db_path`，默认 None → 纯重生成契约不变）；
  钩子 (a) `capture_episodic` 幂等覆盖；(b) 相关 semantic/longterm 内容漂移时 superseded + 写新版 + `supersedes_id` 链回填；
  内容无漂移（content_hash 相同）保持 active；无法确定性重算（自定义 longterm key）跳过；失败仅告警不阻断。
- `web.py` regenerate 路由传 `db_path=_data_dir()/"store"/"memory_entries.sqlite"`，返回结构不变。

**与 ticket 的偏差**：ticket 规格要求 supersedes_id 链，但 `supersede_entries` 签名不含该参数（brief 规定），
钩子在 `supersede_entries` 之后用直接 `UPDATE ... SET supersedes_id = ?` 回填旧行 → 链成立（旧.supersedes_id == 新.id）。
