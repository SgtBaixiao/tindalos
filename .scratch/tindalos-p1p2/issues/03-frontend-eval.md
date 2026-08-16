# 03 前端评测页（P0-b 收尾）

Type: task
Status: claimed

## 目标

P0-b 最后一环：`frontend/src/site/` 加评测页，消费已存在的 `GET /api/eval/runs` + `GET /api/eval/runs/{run_id}`。**只动 frontend/**，不碰后端 Python。

## 规格

- 路由：在 `SiteApp.tsx` 现有 routes（home/workbench/library/qa/history/replay）旁加 `eval`（评测页），沿用侧边导航/主题布局。
- 页面结构：
  - 列表视图：GET `/api/eval/runs` → 渲染运行列表（run_id、时间、verdict、LLM 预算等可用字段，先读 web.py 该端点返回结构）。
  - 详情视图：GET `/api/eval/runs/{id}` → 渲染 L1–L6 分层 trace、各层证据/annotations、verdict、judge 信息。若字段含 evidence_refs / judge_model 一并展示。
- 设计：遵守 `frontend/src/theme.css` house-style 令牌（暖色极简 + 暖墨深色板），复用现有站点组件模式与数据获取方式（先看 HistoryView/WorkbenchView 怎么 fetch/渲染）。
- 空态/错误态：列表空、请求失败要有占位。
- 测试：跟随现有前端测试模式（vitest + 组件渲染 + mock fetch），新增评测页用例。`cd frontend && npm test` 保持全绿（现 93）。

## 验收

- eval 路由可渲染列表与详情，字段与后端响应一致。
- 组件测试覆盖列表/详情/空态。
- `cd frontend && npm test` 全绿；`npm run build` 通过（tsc 无类型错误）。
