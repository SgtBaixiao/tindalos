Type: research
Status: resolved
Blocked by:

## Question

**PDF 文本与图像提取的合规方案**：PyMuPDF 是 AGPL-3.0。把它用在「随时可访问的自托管网络服务」里做文本提取（`run-module.sh` 步骤 1 在用）与图像提取（头像/地图，供 04 多模态管线）——合规风险是什么，推荐哪条路？

**已知事实（前序 research，待深入）**：
- PyMuPDF（fitz）图像提取：`page.get_images(full=True)` / `doc.extract_image(xref)` / `page.get_image_rects(xref)` + 整页栅格化 `page.get_pixmap(matrix=...)`——功能齐全但 **AGPL-3.0**。
- 候选替代：pypdf（BSD，纯文本，无图像提取）；pdfium（Apache-2.0，可渲染/栅格化）；Poppler（GPL，有 copyleft 同源问题）；MuPDF AGPL 同上。

**需查明**：
1. **AGPL 对自托管网络服务的义务**：作为「对外提供的网络服务」使用 AGPL 库，是否要求开放整个服务的源码？个人站点（无商业分发）的执法风险现实吗？是否区分「本地命令行工具」vs「网络服务」两种使用场景（本地跑 run-module.sh 可能安全，网络服务有风险）。
2. **替代覆盖度**：pypdf（文本）+ pdfium（渲染/图像提取）能否覆盖全部需求（含图像位置坐标、页面栅格化）？有可用的 Python 绑定？维护状态？
3. **PyMuPDF 商业授权**：价格/条款（Artifex 商业授权）——买授权 vs 换库改造成本对比。
4. **给出推荐**：规避（换 pypdf+pdfium）/ 合规（公开服务源码）/ 购买授权，并给出对 04（多模态管线图像来源）的影响。

**产出**：一份决策备忘（合规结论 + 方案对比 + 推荐 + 对 tickets 的影响）。resolve 后阻塞 04。

## Answer

**结论（一句话）**：规避 AGPL——本地 CLI（`run-module.sh`）可继续留在 PyMuPDF（fitz，无分发/无网络交互 → 实际风险趋近零）；但**自托管 Web 服务的 PDF 管线改用 pypdf（BSD-3，文本）+ pypdfium2（Apache-2.0/BSD，图像提取/位置坐标/整页栅格化）**，两者均宽松许可，不触发任何 copyleft 网络服务义务。

**法律面**：AGPL-3.0 §13「远程网络交互」条款——服务端 import PyMuPDF ⇒ 合并程序 ⇒ 须向**该运行实例的用户**开放整个服务源码。即便一行 fitz 没改也适用（FSF 衍生作品立场）。唯一免责是「分离进程」聚合论证，但属灰色地带，不建议依赖。个人站点执法概率低（AGPL 执法主要针对商业主体），但 Tindalos 是 MIT，Web 服务用 fitz 会制造 MIT↔AGPL 许可冲突，被依赖审计工具标注是现实风险。

**方案对比**：PyMuPDF=AGPL/商业授权（能力最强，服务端风险高）；pypdf=BSD（文本✅、嵌入图像✅，但**无图像位置坐标**——公开 issue py-pdf#2763 未实现）；pypdfium2=Apache/BSD（文本基础级、图像直取✅、`get_matrix()`+`PdfMatrix.on_rect()` 可算 bbox✅、`page.render(scale=…).to_pil()` 栅格化✅，Chromium 同源 PDFium 引擎，wheel 全平台无强制运行时依赖）；Poppler=GPL（无 §13，SaaS 不分发一般不触发，但更脏，无理由选）。

**PyMuPDF 商业授权**：双许可（AGPL 或 Artifex 商业）；定价未公开、按案例报价（360Quadrants 证实），对个人非商业项目性价比最低，不建议购买。

**对 04 的影响**：图像来源由 pypdfium2 统一提供——嵌入图像直取（`page.get_objects(filter=IMAGE)` → `PdfImage` → `get_bitmap().to_pil()`/`extract()`，配 `get_matrix()` 定位）或页面区域裁剪（整页渲染 + bbox 裁剪喂多模态）；pypdf 只负责文本与元数据；文本-图像对齐靠坐标交集。整条 Web 管线（文本+图像+栅格化）落在 BSD/Apache 宽松许可内，04 无 AGPL 包袱。

**迁移动作清单（供后续 ticket）**：Web 服务路径 `fitz.open`→`pypdf.PdfReader`；`page.get_pixmap`→`pypdfium2 render().to_pil()`；`get_images/extract_image/get_image_rects`→`pypdfium2 get_objects`+`PdfImage`+`get_matrix`。`run-module.sh` 保持本地 fitz 合法；若当前 Web 管线步骤 1 是 shell 出 run-module.sh 提取文本，此路径必须改为 pypdf/pypdfium2（否则「分离进程」论证是灰的）。

（决策备忘全文含来源链接，见 research subagent aa6ccf2c5ed9d8071 输出。）
