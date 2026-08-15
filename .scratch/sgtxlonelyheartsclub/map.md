# SgtXLonelyHeartsClub — wayfinder 地图

## Destination

把个人网站 **SgtXLonelyHeartsClub** 建成一个随时可访问的服务：复刻 aicodingdictionary.com 的纸墨极简设计/动效；首页为各栏目入口；集成 Tindalos 剧本工作台；支持 PDF 上传解析（头像/地点/地图 + 多模态识别）；RAG 支持模组材料全文搜索 + COC/DND 规则书问答（规则无关）；历史记录子页面可重放生成剧本。

## Notes

- **领域**：个人网站 + LLM 应用（COC/DND TRPG 剧本生成）。设计复刻参考站，后端 FastAPI 统一服务。
- **设计资产**：`.scratch/sgtxlonelyheartsclub/design-spec.md` —— 参考站完整设计规格（纸墨双色 / cubic-bezier(.16,1,.3,1) / stagger 公式 / 1/3 面板 / data-* 状态钩子），前端 ticket 必读。
- **已定决策（grilling Round 1-2 产出）**：
  - 部署：静态前端 + FastAPI 统一后端（serve.py 生成内核迁入）
  - 定位：展示 + 实用为主
  - RAG 范围：A 模组全文搜索 + C 规则书问答；**先 A 后 C**
  - 规则：RAG 层规则无关（COC+DND 材料皆可入库可搜可答）；生成管线保持 COC，models 预留 rules 字段；DND 生成后续独立 effort
  - 多模态：qwen3-vl-plus 全自动识别 + 置信度门槛 + 人工确认 UI
  - 历史记录：上传模组 + 生成剧本（可重放）
  - 首页栏目：全要（剧本工作台 / 模组资料库 / 规则问答 / 历史记录）——**不含求职**
- **技能**：grilling / prototype / research / domain-modeling；子 agent 对分配任务自主调用。用户中文交流，输出用中文。
- **安全**：绝不在 GitHub 提交 API keys（只走环境变量）；16MB 规则书 PDF 不入库（.gitignore）；参考站资源不入库（版权）。
- **现状锚点**：serve.py `/api/generate` POST-only SSE vs 前端 EventSource GET-only（缺口已定位，见 08）；MAX_BODY=1MB（PDF 上传需独立通道）。

## Decisions so far

- [PDF 文本与图像提取合规方案](issues/03-PDF文本与图像提取合规方案.md) — Web 服务弃用 PyMuPDF（AGPL §13 网络义务与 MIT 冲突）：文本改 pypdf（BSD）、图像/坐标/栅格化改 pypdfium2（Apache/BSD）；本地 run-module.sh 可留 fitz（无分发无网络）。阻塞的 04 已解锁。
- [集成缺口修复](issues/08-集成缺口修复.md) — 前端 live 改 fetch POST /api/generate 读 SSE 流（复用 SseStreamParser），serve.py 契约零改动；ProgressBand 失败/错误回退静态 progress.jsonl 语义保留；vite dev proxy /api→127.0.0.1:8347；82 前端测试绿 + 真实 TCP 实测 SSE 逐条推进至 done:true campaign。前端 live 演示已解锁。

## Not yet specified

- **部署目标**：后端常驻位置（VPS / PaaS / 云函数）——影响鉴权 / HTTPS / 对象存储 / 数据持久化。前置：02 后端架构 + 用户提供部署环境。
- **RAG 规则书语料**：16MB 原版 `克苏鲁的呼唤第七版守秘人规则书.pdf` 不入库，需轻量切片版本或官方可分发文本。
- **人工确认 UI 形态**：多模态识别低置信度项的确认识交互（列表 / 画布点选 / 拖拽关联）。
- **移动端体验**：参考站 90dvh 底部抽屉；栏目入口图的移动端降级方案。
- **多语言/文案**：网站主体语言、术语表是否双语。

## Out of scope

- **求职栏目**：用户明确排除——个人网站，与求职无关。
- **生成管线双规则**（COC+DND 判定参数化重构）：DND 生成作为后续独立 effort，本 effort 只做 RAG 层规则无关 + models 预留 rules 字段。
- **参考站代码搬运**：只复刻设计与动效语言，不复刻其内容/域名/品牌；不提交其静态资源。
