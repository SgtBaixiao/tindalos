Type: grilling
Status: open
Blocked by:

## Question

**FastAPI 统一后端架构**的模块划分与迁移策略（部署决策已定：静态 + 后端；后端 = FastAPI 统一服务）。

**现状锚点**：`src/tindalos/serve.py` 是 stdlib http.server——`do_POST` 处理 `/api/generate`（SSE 流，`data:{stage,message}…data:{done,campaign}`）与 `/api/regenerate`（JSON）；`do_GET` 只处理 `/api/campaigns/<id>`；`MAX_BODY=1_048_576`（1MB）；campaign 仅存内存 `state.campaigns[id]`；无 auth、CORS `*`、默认 127.0.0.1:8347。

**子问题**：
1. **生成内核迁移**：serve.py 的 SSE 契约（`/api/generate`、`/api/regenerate`、`/api/campaigns/<id>`）如何原样迁入 FastAPI，前端 `live.ts` 零改动？SSE 在 FastAPI 用 StreamingResponse 还是 sse-starlette？
2. **PDF 上传通道**：multipart 上传绕开 1MB JSON body 限制；文件暂存/解析触发（同步阻塞 vs 后台任务 + 任务状态轮询）；上传后的解析管线如何与 03/04 对接。
3. **RAG 检索端点**：搜索、问答、实体关联的 REST/SSE 契约初稿（由 05/06 细化）。
4. **历史记录存储**：SQLite（模组记录 + campaign 元数据 + eval 分数）还是文件系统 + 索引？为 07 定接口。
5. **静态托管与鉴权**：生产上 FastAPI 是否托管构建后的前端静态文件（单进程部署）；「随时可访问」的鉴权（个人站点：轻量 token？Cloudflare Access？）——连到 Not-yet-specified 的部署目标 fog。
6. **配置**：如何复用 `src/tindalos/config.py` 的 Settings（API key 只走环境变量）。

**产出**：后端模块边界图 + API 契约清单 + 迁移顺序。阻塞 07（历史记录依赖其存储接口）。

➡️ 推荐：单一 FastAPI app，包内划分 routers（generate / files / rag / history / static）；SSE 用 sse-starlette 保持契约；campaign 落 SQLite 而非纯内存（历史记录与可重放都依赖持久化）。
