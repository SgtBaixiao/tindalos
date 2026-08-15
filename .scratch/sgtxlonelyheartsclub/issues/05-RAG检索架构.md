Type: grilling
Status: open
Blocked by:

## Question

**RAG 检索架构落地**细节（选型已在前序 research 定案，本 ticket 定落地方案）。

**已定选型**：向量库 ChromaDB（嵌入式，→Qdrant upgrade path）；embedding 用阿里云 `text-embedding-v4`（1024 维，0.5 元/M；DeepSeek 无 embedding 端点）；分块为父子分块；检索为 jieba + rank-bm25（稀疏）⊕ 向量 → RRF(k=60) → SiliconFlow `bge-reranker-v2-m3` 重排 Top 3-5；**不引入 LangChain/LlamaIndex**。

**子问题**：
1. **分块策略**：父子分块的块大小、重叠、父子链接方式；元数据 `{module, chapter, section, page}` 的抽取来源（organize_module.py 整理后的结构 → 章节锚点）。
2. **入库流程**：上传 PDF → 文本提取（03）→ 整理（organize_module.py 复用？）→ 分块 → 嵌入 → ChromaDB 写入；增量/去重（文件 hash）。
3. **混合检索编排**：BM25 与向量各自的召回量、RRF 融合参数、rerank 触发时机与阈值；中文 query 的 jieba 分词接入。
4. **实体关联**：SQLite `entity_mention` 表（人名/地名 ↔ chunk）——与 04 识别结果、06 数据模型的关系；实体检索入口（搜人名直接定位段落）。
5. **问答模式**：规则书问答（场景 C）的检索增强 prompt 模板；COC/DND 规则无关性如何在检索层体现（不按规则过滤，靠元数据 `rules` 区分）。
6. **性能与成本**：ChromaDB 持久化目录、嵌入缓存、batch 嵌入。

**产出**：入库/检索两条流水线的数据流 + 关键接口签名 + 元数据 schema 草案。

➡️ 推荐：父子分块父=章节块、子=段落块；元数据带 `rules` 字段（COC/DND）但不参与过滤，仅用于问答时标注来源规则；`entity_mention` 复用 04 的识别结果做实体→chunk 反查。
