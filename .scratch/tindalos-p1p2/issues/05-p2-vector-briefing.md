# 05 P2 起步：post-session briefing + 向量检索

Type: task
Status: resolved

## 目标

P2 #2 + #3。落在 `src/tindalos/memory_entries.py`（embedding 写 + 检索函数）+ `src/tindalos/rag.py`（复用 embedder，仅小改）+ `src/tindalos/web.py`（/api/memories 返回 briefing 增强，若需）+ `tests/test_memory_p2.py`（新文件）。**须在 01 完成后开始**。

## 规格（摘自设计文档 §3.5 P2 / §3.2 embedding 列）

- **briefing**：`briefing(campaign_id, db_path)` → "上次停在哪" 一段文本：最近 play_session 摘要 + 当前 play_status + longterm synopsis/plotline 概要。无会话时返回占位文案。确定性零 LLM。
- **向量检索**：
  - 写：`embed_entries(campaign_id, db_path, embedder=None)` 给 memory_entries.embedding 列填 BLOB（embedder 复用 rag 的 OpenAI 兼容端点；无 key / embedder 失败 → 不写 embedding，仅置标记）。
  - 读：`retrieve_memory(campaign_id, query, db_path, k=5)` 优先 cosine 检索有 embedding 的条目；无 embedding 条目 → 确定性降级用现有 BM25（rag.search 或 assemble_memory_context 已有逻辑）。
  - ChromaDB 可选集成：仅当环境装好且不破坏零依赖铁律时；否则纯 sqlite BLOB + numpy cosine 即满足（worker 判断，倾向最简）。
  - 零 LLM 测试：embedder stub 返回固定向量。

## 验收

- briefing：有最近会话 → 含摘要+状态；无会话 → 占位。
- retrieve_memory：stub embedding 下 cosine 返回相关条目；无 embedding 降级 BM25 仍返回结果。
- `python -m pytest tests/test_memory_p2.py tests/test_memory.py -q` 全绿。

## Answer

已实现并全绿（`tests/test_memory_p2.py` 11 个新测试；全量 `tests/ -q -k "not web_dockerfile_build"` 390 passed / 1 deselected；前端 vitest 99 + build 绿）。

- `memory_entries.py` 追加 `briefing(campaign_id, db_path=None) -> str`（最近 play_session 摘要 + play_status + longterm synopsis/plotline 概要，无会话中文占位，确定性零 LLM）、`embed_entries(campaign_id, db_path=None, embedder=None) -> int`（embedding BLOB 写，embedder 契约 `(text) -> list[float]`，无 embedder 零写不崩，幂等跳过已填行）、`retrieve_memory(campaign_id, query, db_path=None, k=5, embedder=None) -> list[dict]`（有 embedding → 纯 Python 嵌套列表余弦 top-k；无 → BM25 确定性降级，复用 `BM25Index`；返回含 id/memory_type/content/score）。
- `web.py` `GET /api/memories/{campaign_id}` 响应加 `briefing` 字段（四类 + play_status 原结构不动，M1 测试断言不受影响）。
- `rag.py` **零改动**——`get_embedder` 已在 `__all__` 暴露，直接复用。

**与 ticket 的偏差**（按 worker 判断 / ticket 明示可选）：
1. **放弃 ChromaDB，取最简方案**：sqlite BLOB + 纯 Python 嵌套列表余弦，零新依赖；BLOB 用 `struct '<f'`（float32 IEEE754 小端），与 numpy float32 `tobytes()` 字节兼容。
2. `retrieve_memory` 追加可选 `embedder=None` 参数（ticket 签名未含）：cosine 需要查询向量，该参数让测试 stub 与真实调用（复用 `rag.get_embedder`）都能驱动查询嵌入。
3. 已知边界（未纳入本票）：regenerate 改内容时不清除旧 embedding，下次 `embed_entries` 只补 NULL 行——不崩，但旧向量不自动刷新，属后续优化。
