# Tindalos P1（完整）+ P2（起步）实施 spec

> 来源：`docs/cloud-memory-eval-design.md` §5（P1/P2 落地路线）、§3.2/3.3/3.4（memory_entries schema 与读写收敛点）、§4.5（API）。
> 范围由用户拍板：**P0 收尾 + P1 完整 + P2 起步**。

## P0 收尾（本 effort 附带）

- [x] config.store_dir 读 `TINDALOS_DATA_DIR`（config.py + test_scaffold.py 新增 test）。
- [x] 过时标记清理：README dualJudge 改单模型陈述；.harness/status.json 重写为 p0-complete 终态。
- [x] 前端评测页（ticket 03）——P0-b 的最后一环（eval 路由：runs 列表 + L1–L6 trace 详情 + verdict + annotations + evidence_refs，house-style 令牌）。

## P1 整合管道 + 游玩路径

1. **记忆核心**（ticket 01）：
   - `consolidate(campaign_id, db_path, llm=None, min_episodic=20)`：LLM 两段式（第一遍读 episodic+semantic 提议 ADD/UPDATE/DELETE；第二遍执行）→ 产出 longterm（synopsis/plotline/npc_arcs）并把被整合的 episodic 置 `consolidated`（保留历史，不物理删除）。**无 LLM 时确定性降级**：只把超限 episodic 置 `consolidated` + 确定性拼 synopsis，幂等（同输入两次一致）。
   - `longterm` 写入 helper：memory_type='longterm'，subject_key 取 'synopsis'/'plotline'/'npc_arcs'，更新同键旧条目（置 superseded 或合并），ref_ids 连回源 episodic/semantic。
   - `record_session(campaign_id, session_summary, db_path, llm=None, play_status=None, conflicts=None)`：KP 回叙 → 新增 `play_sessions` 表行（summary / play_status / conflicts JSON / created_at）+ 轻量 consolidate。确定性路径零 LLM。`current_play_status(campaign_id, db_path)` 读最近状态。
   - `GET /api/memories/{campaign_id}`：返回四类记忆 + play_status + 最近 briefing 摘要。
2. **L3 LLM judge 增强**（ticket 02）：CoT 推理 + per-dim `evidence_refs` + 记录 `judge_model`（与生成同模型时标注 self-preference 风险）+ temp=0；预算门沿用；坏输出降级确定性。
3. **`/api/regenerate` 记忆钩子**（ticket 04）：改节点后 `capture_episodic` 覆盖新内容 + 相关 semantic/longterm 置 superseded。

## P2 起步（多会话推进）

1. **post-session briefing**：`briefing(campaign_id, db_path)` 生成"上次停在哪"——读最近 play_session + 当前 play_status + longterm synopsis/plotline 摘要。（✅ 已实现：确定性零 LLM，无会话返回中文占位文案。）
2. **向量检索**：memory_entries.embedding BLOB 写（复用 rag embedder）+ 检索（cosine）；无 key / 无 embedder 时确定性降级 BM25（现状已有）。（✅ 已实现：`embed_entries`/`retrieve_memory`，sqlite BLOB + 纯 Python 余弦，**未上 ChromaDB**——取最简方案，零新依赖；`rag.get_embedder` 已暴露直接复用。）

## 验收门（本 effort 全部达成，2026-08-16 集成验证）

- 后端：`python -m pytest tests/ -q -k "not web_dockerfile_build"` → **390 passed, 1 deselected**（342 基线 → M1+E1 → 379 → R1 → 379 → V1 → 390；deselected 为环境项 Docker）。
- 前端：`cd frontend && npm test` → **99 passed**（原 93 + 评测页 6）；`npm run build`（tsc + vite）通过。
- 全部新路径零 LLM 零网络可测；LLM 路径 FakeLLM 驱动。
- CLI 冒烟：`tindalos memories examples/campaign.json` 退出码 0，四类列出正常。
