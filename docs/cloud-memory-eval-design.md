# Tindalos 数据层扩展设计：云数据库 · 记忆系统 · Eval 体系

> 状态：设计已定稿（2026-08-16），进入 P0 落地。
> 本文合成三份调研（`research-cloud-db.md` / `research-memory.md` / `research-eval.md`）为一份可执行设计，
> 面向单用户个人工具（香港 2C4G VPS、Docker 单容器、大陆访问、DeepSeek LLM、数据本地化）的真实约束。
> 术语遵循 `CONTEXT.md` 词汇表；与既有 ADR 冲突处显式标注。

---

## 0. 一页决策摘要（TL;DR）

| 议题 | 结论 | 一句话理由 |
|---|---|---|
| **云数据库** | **不值得默认上云**；维持全本地 SQLite 为基线；仅当需要跨设备/团队协作或异地备份时才升级 | 单用户低流量，上云引入的延迟/合规/密钥/运维代价远超收益；本地 SQLite 已够用且零运维 |
| **升级路径** | ① 本地 SQLite + 对象存储备份（默认）；② 同 VPS 自托管 Postgres+pgvector（memory/checkpoint 需跨会话健壮时）；③ Turso（需 SQLite 边缘复制时，唯一有香港地域的托管库）；④ Supabase（多设备/团队协同时才考虑） | LangGraph 官方存储族只支持 Postgres；托管库中仅 Turso 有 `hkg` 香港地域 |
| **记忆系统** | 两轴×四类模型（情景/语义 = 内容类型；短期/长期 = 留存期）。记忆 = 追加式事件流 + 去重合并事实库 + 会话级有界 buffer + 整合维护的综合层 | 现有 `memory.py` 是「快照重建」非「记忆系统」，需重构为分层追加式 |
| **Eval 体系** | 不可变 trace（`eval_runs` 表）+ L1–L6 分层（确定性→图谱→LLM judge→faithfulness→KP 可用性→回归）+ 级联门 + 闭环到 evolve | 让 eval 有可观察 trace、全面分层、可回归 |
| **落地** | P0（零 LLM，CI 全绿）→ P1（整合管道 + 游玩路径）→ P2（多会话推进）→ P3（可选增强） | 克制：每阶段有明确交付与验证 |

---

## 1. 现状盘点（真实代码）

| 模块 | 现状 | 本次设计对应的缺口 |
|---|---|---|
| `history.py` | SQLite `site.db`，表 `modules` + `campaigns`（`snapshot_json` 整份快照，覆盖式 `INSERT OR REPLACE`，每调用一连接） | 无 eval 表；campaign 不保留版本历史（回归/对比不友好） |
| `memory.py` | t14 聚合版：`build_memory_facts` 从 Campaign 重建一份 facts 文档（NPC 印象/关键事件/世界摘要），整体覆盖写入；`build_store` = SqliteStore/InMemoryStore | 「快照重建」而非记忆系统：不累积、不追加、不分层、无检索、无整合 |
| `eval_/` | 4 维 rubric（structural/consistency/depth/playability）+ `run_deterministic`（15 项 checks）+ `LLMJudge` + `eval_report`；无 trace 持久化 | 无不可变 trace；L3 LLM 层无 CoT/evidence_refs；无 L4 faithfulness；无回归 |
| `rag.py` | `search()`（vector top-20 + BM25 top-20 → RRF → top_k）、`qa()`（含 `_dedup_sources` + 克制 prompt）；`_llm_answer` 需 API key 否则降级 local | **可复用于 L4 faithfulness**（用模组语料判生成声明是否被支持） |
| `kg.py` | `WorldGraph.consistency_check()` + `campaign_consistency()` | 未用于「线索可达性/事件图可达性」这类图上可达性检查（L2 扩展项） |
| `web.py` | FastAPI：`/api/generate|campaigns|regenerate|rag/*|history/*` | 无 eval 端点、无记忆查询端点 |
| `config.py` | 零依赖手写 `Settings`（env 驱动） | 需新增 `EVAL_MAX_USD`、`MEMORY_*` 等配置，保持零依赖 |

---

## 2. 云数据库决策

### 2.1 诚实评估：上云解决什么、引入什么

**解决（对单用户个人工具）**：多设备同步、异地备份、web 可访问的 memory/eval 查询。
**引入**：大陆访问海外云延迟（新加坡/东京 80–200ms，香港本地 30–50ms）、数据出境合规（模组原文可能含版权，不宜出境）、密钥管理、运维复杂度、成本。

**判断：不值得默认上云。** 单用户低流量场景，本地 SQLite + 定期备份已覆盖全部真实需求；上云是「为解决假想问题而引入真实代价」。

### 2.2 候选方案横向对比（结论表）

| 方案 | 类型 | 免费额度 | 亚太地域 | 对 Tindalos 适用性 | 结论 |
|---|---|---|---|---|---|
| **本地 SQLite**（现状） | 文件库 | 无限 | 本机 | 全部数据本地化，零运维 | ✅ **默认基线** |
| **同 VPS Postgres+pgvector** | 关系+向量 | 随 VPS | 香港 | `langgraph-checkpoint-postgres` / `PostgresStore`（官方），RAG 向量也可迁移 | ✅ 条件升级 |
| **Turso/libSQL** | SQLite 边缘 | ~5GB/月 | **唯一有 `hkg` 香港** | embedded replicas，SQLite 兼容 | 🟡 仅需边缘复制时 |
| **Supabase** | Postgres 全家桶 | 500MB | 新加坡（无香港） | 多设备/团队协作、Auth/Storage/Realtime 才有价值 | 🟡 仅多设备/协同时 |
| **Neon** | serverless PG | 512MB | 新加坡 | 无香港、serverless 对常驻服务无增益 | 🔴 排除 |
| **Upstash** | Redis/Vector/QStash | 按量 | 新加坡/东京（无香港） | `langgraph-checkpoint-upstash-redis` 仅 checkpoint 无 store/向量 | 🔴 排除（缺 store/向量） |
| **Cloudflare D1+R2+Vectorize** | 边缘 SQLite | 5M rows/10GB R2 | 全球边缘 | D1 无 Python driver、无 socket、REST-only | 🔴 排除（驱动/事务限制） |
| **PlanetScale** | MySQL | 无免费档 | — | MySQL 非 LangGraph 存储族 | 🔴 排除 |
| **Qdrant Cloud / Pinecone** | 纯向量库 | — | 新加坡/东京 | 对几十~上百 chunks 过度设计，丢 BM25 混合检索 | 🔴 排除 |

> 〔注〕Qdrant/Pinecone 免费额度数字待 agent D 回填，但两者已因「纯向量库对 Tindalos 规模过度设计」出局，回填不改变结论。

### 2.3 合规要点

- 模组 PDF 提取原文可能含版权 → **只留本地**，不入任何境外云。
- 中国大陆数据出境：个人信息出境需评估；模组文本非「重要数据」，但规避风险最佳路径就是**不出境**。
- 香港 VPS 是「本地」与「境外」的最佳平衡：免备案、大陆 30–50ms、数据仍在境内法域（香港）。

### 2.4 落地决策（绑定 VPS 部署）

- **默认**：全本地 SQLite + 每日 `sqlite3 .backup` 到对象存储/rsync 异地。
- **条件升级**（出现跨会话 memory/checkpoint 健壮性需求或 RAG 规模增长时）：同 VPS 起 `postgres:16-pgvector` 容器，`PostgresStore` 替换 `SqliteStore`——**接口同构，无感替换**（`build_store` 返回 store 对象，读写入口不变）。

---

## 3. 记忆系统设计

### 3.1 概念纠正：两轴×四类，非「四类存储」

记忆**不是**四个独立存储桶，而是两条正交轴：

```
内容类型（存什么）    留存期（留多久）
┌─────────────┐      ┌─────────────┐
│  情景 episodic │      │  短期 short │
│  语义 semantic │      │  长期 long  │
└─────────────┘      └─────────────┘
```

- **情景记忆** = 追加式事件流（每次 compose/游玩后追加，按 event id 幂等）。
- **语义记忆** = 去重合并的事实库（按 `subject_key` 覆盖更新，版本化不删）。
- **短期记忆** = 会话级有界 buffer：情景近因投影 + 当前场景 + 活跃 NPC + 未决线索。
- **长期记忆** = 整合维护的综合层（synopsis / plotline / npc_arcs），由 consolidation 从情景/语义折叠而来。
- **语义 ⊂ 长期**：语义事实是长期知识的主体；长期层额外持有跨条综合。

### 3.2 `memory_entries` 数据模型（单表承载四类）

```sql
CREATE TABLE memory_entries (
  id              TEXT PRIMARY KEY,        -- 'evm:...' / 'sem:...' / 'stm:...' / 'ltm:...'
  campaign_id     TEXT NOT NULL,
  memory_type     TEXT NOT NULL,           -- episodic | semantic | shortterm | longterm
  content         TEXT NOT NULL,           -- 单条 ≤ 200 字（克制）
  importance      REAL DEFAULT 0.5,        -- 0~1
  source_episode  TEXT,                    -- 生成时的 act/scene/event 溯源
  ref_ids         JSON,                    -- 关联条目 id（整合链）
  subject_key     TEXT,                    -- 语义去重键（episodic 可空）
  status          TEXT DEFAULT 'active',   -- active | superseded | consolidated
  valid_from      TEXT,
  valid_to        TEXT,
  supersedes_id   TEXT,
  consolidated_into TEXT,
  content_hash    TEXT,                    -- 幂等写入判重
  embedding       BLOB,                    -- 可选向量（P2）
  created_at      TEXT,
  updated_at      TEXT,
  last_accessed_at TEXT
);
```

### 3.3 读写时机（收敛点）

```
写入（P1 主路径）            compose 成功后单点写入（收敛点，无并发竞态）
                             └→ capture_episodic（追加式 upsert，event id 幂等）
                             └→ capture_semantic_initial（确定性抽取，subject_key 去重）
                             └→ light consolidate（episodic ≥20 条或显式触发）

写入（P2 游玩路径）          record_session：KP 回叙 → play_status 更新 + 冲突决策 + consolidate

读取                         assemble_memory_context：write_act 前注入相关记忆
                             （BM25 检索 + 近因加权；短期 = 未决线程 + 近景 + 活跃 NPC + 长期 brief）

修复钩子                     /api/regenerate 改节点后：capture_episodic 覆盖新内容
                             + 相关语义/长期条目置 superseded
```

### 3.4 衰变与整合

- **不主动物理删除**：旧版本置 `superseded` / `consolidated`，保留历史。
- **情景**：原始条目设上限（如 200 条/战役），超出整合后置 `consolidated`；检索时近因指数加权（`decay^age_days`）。
- **语义**：仅被矛盾新事实覆盖（版本化，valid_from/valid_to）。
- **长期**：consolidation（LLM 两段式 ADD/UPDATE/DELETE；无 LLM 时确定性降级只标 consolidated）。

### 3.5 落地路线（P0–P3）

| 阶段 | 内容 | 验证 |
|---|---|---|
| **P0（零 LLM，重构现状）** | `memory_entries` schema + `capture_episodic`/`capture_semantic_initial` 幂等写入；compose 后接入；`assemble_memory_context` 注入 write_act；BM25 检索复用 rag；`tindalos memories` 升级为按四类列出 | CI 零 LLM 全绿；幂等（同 campaign 两次写入不重复） |
| **P1（整合管道）** | `consolidate`（LLM 两段式 + 确定性降级）；`longterm`（synopsis/plotline/npc_arcs）；`record_session` 游玩路径；`/api/regenerate` 钩子 | LLM 启用时整合链正确；降级路径仍全绿 |
| **P2（多会话推进）** | KP 回叙/live 采集实际游玩 → play_status 更新 + 冲突决策；post-session briefing（"上次停在哪"）；可选向量检索 | 会话续接问答正确 |
| **P3（可选增强）** | sleep-time 离线整合；前端记忆可视化 | 前端四类记忆与剧情线状态 |

---

## 4. Eval 体系设计

### 4.1 不可变 trace（可观察的根基）

- **`eval_runs` 表**：一次评测的完整旅程——`run_id`、时间、被测对象（campaign/module）、被测生成参数（llm/deterministic、种子）、各层结果 JSON、总 verdict。
- **`eval_annotations` 表**：评分事件（score/explanation），指回被评的 span/条目（`subject_ref`）。
- trace 不可变（append-only），可回放、可对比、可归因。前端可点开任意一次 run 看各层证据。

### 4.2 L1–L6 分层评测（从便宜到贵、客观到主观、失败即短路）

| 层 | 检查什么 | 工具 | 成本 | 短路条件 |
|---|---|---|---|---|
| **L1 确定性/结构** | schema 合法、id 唯一、引用可解析、每幕≥1场景、每场景≥1事件、NPC 有 personality、clue 有 linked | `run_deterministic`（已实现 15 checks）+ 新增 `entry_per_scene` 等 | 零 | 未过 → 短路，建议 `evolve` |
| **L2 图谱一致性** | KG 端点注册、有效窗重叠/倒置、剧本↔图谱双向投影、线索可达性 | `WorldGraph.consistency_check()` + `campaign_consistency`（已实现）+ 新增图上可达性检查 | 零 | 未过 → 短路 |
| **L3 内容质量** | 4 维综合分（structural/consistency 由 L1/L2 客观覆盖，LLM 重点 depth/playability）+ per-scene 细评（抽样 30%）+ per-event 质量门 | 增强 `LLMJudge`（CoT + evidence_refs + temp=0） | LLM | 预算超限降级 |
| **L4 忠实度 faithfulness** | 生成内容是否忠于模组原文，检出臆造/漂移 | **复用 `rag.search`**：拆声明 → 用模组语料判每条是否被支持（Ragas 式） | LLM/向量 | 预算超限降级 |
| **L5 KP 可用性** | 备团笔记是否可直接带团：线索链是否可达结局、NPC 卡片是否自洽、时间线是否一致 | LLM judge + 确定性检查 | LLM | 仅手动触发 |
| **L6 回归** | 历史快照重打分：同一 campaign 新生成 vs 旧版本，分数/维度对比 | 重放 `eval_runs` 历史 | 零（重放） | — |

### 4.3 级联门与预算（克制机制）

- **级联门**：先跑 L1/L2（零成本确定性），未过阈值不进入 L3+ LLM 层——最省钱的机制。
- **预检预算**：LLM 层调用前按 worst-case token 估算，超 `EVAL_MAX_USD`（默认 $2）拒绝或降级。
- **裁判用小模型**：L3/L4 默认用便宜模型；L5 可换强模型；记录 `judge_model`（与生成同模型时标注 self-preference 风险）。
- **缓存**：judge 调用按 `prompt_fingerprint` 缓存（`eval_cache` 表），同数据集复跑几乎免费。
- **CoT 只在 L3/L4/L5 开**，L1/L2 纯确定性，对冲 token 成本（CoT +30~60%）。

### 4.4 闭环到 evolve

- L1/L2 失败 → 自动建议 `evolve` 确定性修复（现有修复集 a–d），人工 gate 后写回。
- L3/L4 内容问题 → 记 pending 建议（LLM 不自动应用），人工确认后转生成配置/风格规范变更。
- L6 回归差 → 触发生成参数回归排查（budget 约束 + 人工 gate）。

### 4.5 API 与前端

- 新增端点：`GET /api/eval/runs`（列表）、`GET /api/eval/runs/{run_id}`（单次 trace）、`POST /api/eval/run`（手动触发）、`GET /api/memories/{campaign_id}`（四类记忆）。
- 前端：评测页展示 trace 树 + 各层证据 + 分数；记忆页展示四类记忆与剧情线状态（P3 可视化）。

---

## 5. 统一落地路线（编程顺序）

> 顺序原则：**零 LLM 优先（CI 可测）、复用既有模块、克制不过度设计**。每步以测试为门。

### P0-a. 记忆系统基础（零 LLM，先做）

1. `memory_entries` schema + SQLite 落盘（`data/store/memory_entries.sqlite`，复用 `TINDALOS_DATA_DIR` 语义）。
2. `capture_episodic(campaign)` 追加式 upsert（event id 幂等，content_hash 判重）。
3. `capture_semantic_initial(campaign)` 确定性抽取（NPC 事实/地点事实，subject_key 去重）。
4. compose 后接入写入口（收敛点单点）。
5. `assemble_memory_context(campaign_id, query)`：BM25 检索（复用 rag）+ 近因加权，注入 write_act。
6. `tindalos memories` 升级：按四类列出。
7. **验证**：幂等测试（同 campaign 两次捕获不重复）+ CI 零 LLM 全绿。

### P0-b. Eval trace 基础设施（零 LLM，先做）

1. `eval_store.py`：`eval_runs` + `eval_annotations` 表 + append-only 写入。
2. `run_eval` 编排：L1 → L2 →（级联门）→ L3（LLM，预算门）→ L4（faithfulness）→ L5 → L6。
3. L4 faithfulness：拆声明 → `rag.search` 判定支持度（复用现有检索）。
4. `GET /api/eval/runs/{id}` trace 端点 + 前端评测页。
5. **验证**：trace 完整可回放；L1/L2 短路路径零 LLM 全绿。

### P1. 整合管道 + 游玩路径

1. `consolidate`（LLM 两段式 + 确定性降级）+ `longterm`（synopsis/plotline/npc_arcs）。
2. `record_session` 游玩路径。
3. `/api/regenerate` 钩子（改节点后记忆一致性）。
4. L3 LLM judge 增强（CoT + evidence_refs + 预算门）。

### P2. 多会话推进

1. KP 回叙采集 → play_status 更新 + 冲突决策。
2. post-session briefing。
3. 可选向量检索（复用 rag embedder + ChromaDB）。

### P3. 可选增强

- sleep-time 离线整合；前端记忆可视化。

---

## 6. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 记忆系统复杂度失控（四类 + 整合管道） | 严格按 P0 零 LLM → P1 集成；每阶段有幂等测试与 CI 门禁 |
| LLM 层成本不可控（judge/consolidation） | 级联门 + `EVAL_MAX_USD` 预检预算 + 缓存 + 采样 + 便宜裁判模型 |
| faithfulness 误判（生成内容在模组外但有依据） | L4 判「不被模组支持」而非「与模组无关」；分数按声明支持度聚合，提供 evidence_refs 供人工复核 |
| 记忆与生成漂移（regenerate 后旧记忆残留） | `/api/regenerate` 钩子：覆盖情景 + 语义/长期置 superseded |
| 云迁移（未来 PostgresStore 替换） | `build_store` 返回 store 对象、读写入口类型无关 → 无感替换 |
| eval trace 表膨胀 | trace 不可变但可清理归档；`eval_cache` 单独表，超限可清 |

---

## 附录：一手来源

- 云数据库：`research-cloud-db.md` §0–§7（LangGraph Postgres 官方文档、中国数据出境规定、香港 VPS 延迟实测、Upstash/Cloudflare/PlanetScale 调研）。
- 记忆：`research-memory.md`（mem0 两段式、Generative Agents 反思、两轴模型、P0–P3 路线）。
- Eval：`research-eval.md`（LangSmith/Langfuse trace 模型、Ragas faithfulness、G-Eval CoT、promptfoo PR 门禁、L1–L6 分层）。
- 现状代码：`src/tindalos/{memory,eval_,rag,kg,history,web,config}.py`。
