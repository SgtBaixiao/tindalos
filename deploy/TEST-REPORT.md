# Tindalos 全链路测试报告

> 测试时间：2026-08-16 ｜ 环境：Windows 11 + Docker Desktop ｜ 镜像：tindalos:latest（多阶段构建）
> 测试内容：从 Docker 镜像到前端/后端/LLM/持久化的完整链路。本报告可同时作为 **VPS 验收清单** 使用。

---

## 0. 一句话结论

**全链路通过，发现并修复 1 个真实生产 bug（SQLite 历史库在容器重建后丢失）。** 修复后经 `down/up` 重建容器验证：模组、剧本、历史记录、RAG 索引**全部持久化存活**。

---

## 1. 测试结果总览

| # | 链路环节 | 结果 | 关键证据 |
|---|---|---|---|
| 1 | Docker 镜像构建（多阶段：node:22-slim → python:3.12-slim） | ✅ PASS | ~872MB，构建成功 |
| 2 | 容器启动 + 健康检查 | ✅ PASS | `/api/health` → `{"ok":true,"version":"0.1.0"}`（2s 就绪） |
| 3 | 前端加载（SgtXLonelyHeartsClub 页面） | ✅ PASS | `GET /` → HTTP 200，`<html lang="zh-CN">` |
| 4 | 单进程统一服务（前端静态 + `/api/*` + `/files/*`） | ✅ PASS | 同端口 8347 全通 |
| 5 | PDF 模组上传 | ✅ PASS | `mod-f3da57801f`：27 页 / 24757 字符 / 18 张插图 / sha256 入库 |
| 6 | 模组列表 / 详情 / 文本提取 | ✅ PASS | `GET /api/modules`、`GET /api/modules/<id>`（status=indexed）、`GET /files/modules/<id>/text.txt` |
| 7 | RAG 建索引（ingest） | ✅ PASS | `{"indexed": true, "chunks": 92}`，幂等可重跑 |
| 8 | RAG 检索 | ✅ PASS | 查询「老吴江 伊德海拉 迎亲队伍」→ 6 条命中 |
| 9 | 剧本生成（离线确定性） | ✅ PASS | 2 幕 / 4 场景 / 12 事件，0.3s，`campaign-82257648` |
| 10 | 剧本生成（真实 DeepSeek） | ✅ PASS | 34.3s，8 场景 / 24 事件，真实 NPC（以利亚·霍普、哑巴乔纳斯·克里克、玛格丽特·韦德） |
| 11 | RAG QA（真实 LLM，带出处） | ✅ PASS | 有依据的回答，引用 11 条来源 |
| 12 | 局部重生成（regenerate） | ✅ PASS | `npc-1`、`act-1-scene-1` 均 applied；未知节点 → 400；缺失剧本 → 404 |
| 13 | 剧本列表 / 详情（快照可重放）/ 历史 | ✅ PASS | `/api/campaigns`、`/api/campaigns/<id>`、`/api/history/campaigns`、`/api/history/modules` |
| 14 | LLM 故障兜底 | ✅ PASS | LLM 调用失败 → 按设计回退离线确定性生成，服务不崩（UserWarning） |
| 15 | **容器重建后持久化** | ✅ PASS | `down` + `up` 后：模组 / 剧本 / 历史 / RAG 索引**全部存活**（见 §3） |

---

## 2. 发现并修复的生产 Bug（重点）

### Bug：SQLite 历史库在容器重建后丢失

- **现象**：容器 `down/up` 重建后，上传的模组、生成的剧本、历史记录全部消失。
- **根因**：`src/tindalos/history.py` 的 `db_path()` 用 `Path(__file__).resolve().parents[2] / "data" / "site.db"`。
  本地**可编辑安装**（editable pip install）时 `parents[2]` = 仓库根，落在正常位置；
  但 Docker 里是**非编辑安装**（`pip install ".[web,llm]"`），`parents[2]` 解析到
  `/usr/local/lib/python3.12` → `site.db` 写进了**容器的 ephemeral 文件系统**，容器一重建就丢。
  模组文件 / RAG 索引 / store 都在卷里（它们用 `TINDALOS_DATA_DIR`），唯独历史库走错路径。
- **修复**（3 处改动）：
  1. `src/tindalos/history.py`：`db_path()` 改为 `Path(os.environ.get("TINDALOS_DATA_DIR", "data")) / "site.db"`，
     与 web/rag/store 统一走 `TINDALOS_DATA_DIR` 语义（容器内 `/app/data` 即卷挂载点）。
  2. `tests/test_history.py`：`test_db_path_default` 断言同步为 `Path("data") / "site.db"`。
  3. `deploy/docker-compose.yml`：environment 显式加 `TINDALOS_DATA_DIR: "/app/data"`。
- **回归测试**：43 项本地单测全部 PASS。
- **验证**：重建容器后确认 `/app/data/site.db`（32KB）在卷内；重启验证全部数据存活。

### 踩坑教训：`docker compose restart` 不重新读取 `.env`

改 `deploy/.env` 里的 API key 后，曾出现 LLM 调返回 fallback——日志显示
`UnicodeEncodeError`（`latin-1` 编码不出 key）。排查发现请求里的 key 仍是**占位符**：
因为 `restart` 只重启进程，环境变量在容器**创建时**固化。必须用 `docker compose up -d`
（重建容器）才生效。已在 `deploy/README.md` 故障排查表修正并加醒目提示。

---

## 3. 持久化验证明细（down/up 重建容器）

| 数据类别 | 重建前（snapshot_before） | 重建后 | 结果 |
|---|---|---|---|
| 模组 | `mod-f3da57801f`（indexed, 27 页） | 存活，详情 status=indexed | ✅ |
| 剧本 | `campaign-82257648`（12 事件） | 存活，详情可重放 12 事件 | ✅ |
| 历史记录 | campaigns + modules 各 1 条 | 存活 | ✅ |
| RAG 索引 | 92 chunks，6 命中 | **无需重 ingest**，6 命中 | ✅ |
| site.db 落点 | — | `/app/data/site.db`（卷内） | ✅ |

> 说明：`down/up` 前曾因修复前的 bug 丢失过一批历史行（旧库在 ephemeral 文件系统里被丢弃），
> 属**一次性迁移丢失**，修复后不再发生。

---

## 4. 已知限制与诚实报告

| 项目 | 现状 | 影响 |
|---|---|---|
| 插图识别 | 上传解析出 18 张插图，但 `kind=unknown, name=null, confidence=0.0, needs_confirmation=true` | 「人物头像 / 地图联动」的视觉理解能力**未接线**，属后续迭代（前端已展示插图占位） |
| LLM 依赖 | 生成质量依赖 DeepSeek key 有效 + 网络可达 + 账户有余额 | key 无效 / 断网 / 429 / 5xx 时**按设计回退**离线确定性模板（服务不崩，但输出模板化） |
| 生成耗时 | 真实 LLM 全量生成 ~34s | 前端 SSE 流式逐阶段展示，可接受；单用户场景无压力 |
| 无 HTTPS | IP 直连走 HTTP，明文 | 个人使用可接受；建议后续绑域名 + 反代 + Let's Encrypt |
| 单进程 | 一个容器服务全部 | 单用户个人站足够；无横向扩展需求 |

---

## 5. VPS 复现核对清单（照做即可验收）

在服务器上按 `deploy/README.md` 部署后，逐条核对：

```bash
# 1. 健康检查
curl http://<服务器IP>:8347/api/health
#   期望: {"ok":true,"version":"0.1.0"}

# 2. 前端
#   浏览器打开 http://<服务器IP>:8347 ，应看到 SgtXLonelyHeartsClub 首页

# 3. 上传一个 COC7 模组 PDF → 应解析出页码/字符/插图，状态变 indexed

# 4. RAG 检索
curl -X POST http://<服务器IP>:8347/api/rag/search \
  -H "Content-Type: application/json" \
  -d '{"query":"某模组关键词","k":5}'
#   期望: 返回 ≥3 条命中

# 5. 生成剧本（离线快，验证链路）
#   前端「生成」页 → 应得到完整剧本（2 幕左右，事件可展开）

# 6. 生成剧本（真实 LLM，验证 key 生效）
#   deploy/.env 填好真实 DeepSeek key 后 → 生成内容应出现具体人名/地点（非模板化占位）

# 7. 持久化验收（关键！）
docker compose -f deploy/docker-compose.yml down
docker compose -f deploy/docker-compose.yml up -d --build
#   再查: 已上传模组、已生成剧本、历史记录、RAG 检索 → 全部还在
```

**验收即通过**：若第 7 步重建后数据还在，说明持久化修复在 VPS 上同样生效。

---

## 6. 相关产物

| 产物 | 路径 |
|---|---|
| 部署手册（含安全/备份/故障排查） | `deploy/README.md` |
| 一键部署脚本 | `deploy/deploy.sh` |
| 编排配置 | `deploy/docker-compose.yml` |
| 构建文件 | `deploy/Dockerfile` |
| 环境变量模板（提交） | `deploy/.env.example` |
| 环境变量（含真实 key，**gitignored 不入库**） | `deploy/.env` |
