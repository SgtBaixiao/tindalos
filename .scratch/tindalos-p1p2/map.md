# Tindalos P1+P2 起步 — wayfinder 地图

## Destination

按 `docs/cloud-memory-eval-design.md` §5 落地路线，把 Tindalos 从 P0（零 LLM，已完成）推进到 **P1（整合管道 + 游玩路径）完整 + P2（多会话推进）起步**，并收尾 P0 遗留前端评测页。

- **P0 收尾**：config.store_dir 读 TINDALOS_DATA_DIR（已完成，ticket 00）；前端评测页（ticket 03）；过时标记清理（README dualJudge、.harness status，已完成）。
- **P1**：记忆整合 `consolidate` + `longterm` + `record_session` 游玩路径 + `/api/memories`（ticket 01）；L3 LLM judge CoT/evidence_refs 增强（ticket 02）；`/api/regenerate` 记忆一致性钩子（ticket 04）。
- **P2 起步**：post-session briefing + 向量检索（ChromaDB/embedding，零 key 确定性降级）（ticket 05）。

## Notes

- **领域词汇**（CONTEXT.md，输出禁用同义词）：KP 主控 / NPC subagent / 幕 Act / 场景 Scene / 事件 Event / 线索 Clue / 世界知识图谱 WorldGraph / 备团笔记 / 分幕创作 / 剧本节点图 ScriptGraph。
- **设计来源**：`docs/cloud-memory-eval-design.md` —— §3.2 memory_entries 单表 schema（id/campaign_id/memory_type/content/importance/source_episode/ref_ids/subject_key/status/valid_from/valid_to/supersedes_id/consolidated_into/content_hash/embedding/created_at/updated_at/last_accessed_at）、§3.3 读写时机收敛点、§3.4 衰变与整合（不物理删除，置 superseded/consolidated）、§5 P1/P2、§4.5 API。
- **铁律**：零 LLM 零网络可测。LLM 路径用 FakeLLM stub 测试；降级路径（无 LLM）确定性且全绿。
- **测试基线**：后端 **390 passed / 1 deselected**（`python -m pytest tests/ -q -k "not web_dockerfile_build"`，Docker 未运行属环境项）；前端 vitest **99 绿** + `npm run build` 通过。
- **python**：Python 3.14.0，`PYTHONPATH=src` 或 pytest 走 conftest src-layout。
- **语言**：用户中文交流；代码注释/文档中文。
- **安全**：绝不在 GitHub 提交 API keys（只走环境变量）；规则书 PDF 不入库。
- **文件隔离**：Round-1 worker 触碰的文件互不重叠（memory/web / eval_ / frontend），可并行。
- **提交**：worker 不自行 git commit；集成验收后由主线程统一提交。

## Decisions so far

- [记忆核心](issues/01-memory-core.md) — consolidate（LLM 两段式 ADD/UPDATE/DELETE + 确定性降级只标 consolidated）+ longterm（synopsis/plotline/npc_arcs）+ record_session（play_sessions 表 + play_status + 冲突决策 JSON）+ GET /api/memories/{campaign_id} 四类返回。
- [judge 增强](issues/02-judge-cot.md) — CoT + per-dim evidence_refs + judge_model 记录 + temp=0；预算门沿用；坏输出确定性降级。
- [前端评测页](issues/03-frontend-eval.md) — SiteApp 路由 eval：runs 列表 + trace 详情（L1-L6 层 + verdict + annotations + budget），house-style 令牌。
- [regenerate 钩子](issues/04-regenerate-hook.md) — 改节点后 capture_episodic 覆盖 + 相关语义/长期 superseded。
- [P2 起步](issues/05-p2-vector-briefing.md) — post-session briefing（上次停在哪）+ 向量检索（sqlite BLOB + 纯 Python 余弦；**未上 ChromaDB**，取最简方案零新依赖；零 key 降级 BM25）。✅ 完成（2026-08-16）。

## 完成状态（2026-08-16 集成验证通过）

本期全部落地：P0 收尾（config.store_dir / README / .harness / 前端评测页）＋ P1 完整（记忆核心 M1 / judge CoT E2 / regenerate 钩子 R1）＋ P2 起步（briefing + 向量检索 V1）。
后端 390 passed / 1 deselected（环境 Docker 项），前端 99 passed + build 绿，CLI `tindalos memories` 冒烟退出码 0。
已 `git commit` 至 main，消息 `feat: P1 整合管道 + P2 起步（记忆核心 / judge CoT / regenerate 钩子 / briefing / 向量检索）`。

## Not yet specified

- P3 可视化、sleep-time 离线整合（本期不做）。

## Out of scope

- P0 的 harness 门管线重建（已用 wayfinder 流程承接）。
- Docker 镜像/部署验收（环境无 Docker Desktop，测试项跳过）。
