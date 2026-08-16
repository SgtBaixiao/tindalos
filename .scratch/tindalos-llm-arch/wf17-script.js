export const meta = {
  name: 'tindalos-17-multimodal',
  description: '#17 多模态植入收尾：3 个不相交文件域并行实现（vision / pdfio / generator+web）',
  phases: [{ title: 'Implement', detail: '3 parallel agents, disjoint file ownership' }],
}

const COMMON_PREAMBLE = `
工作目录：D:\\Agent Workspace\\Tindalos（Windows 11，Python 3.14，非 git 仓库）。
先按顺序阅读以下文件理解既有约定，再动手：
1. src/tindalos/llm.py —— 统一客户端 LLMClient：chat/chat_json/embed/classify_image；错误分类 LLMError.kind；
   _sleep_backoff(attempt) 可 monkeypatch；transport 注入为 (method,url,*,json,headers,timeout)->ResponseLike(.status_code/.text/.json())。
2. src/tindalos/config.py —— Settings 字段 + get_settings() 单例（首次调用后缓存；测试改环境变量必须先
   monkeypatch.setattr("tindalos.config._settings", None) 再读取，改完恢复）。
3. tests/test_llm.py —— FakeTransport/FakeResp 模式（respond()/raises() 动作队列 + .calls 记录）——新测试的样板。
4. tests/test_style_guide.py —— autouse _no_sleep fixture（monkeypatch tindalos.llm._sleep_backoff 为 no-op）+ transport 注入样板。

硬性纪律（全仓约定，违反即返工）：
- 零 LLM、零网络：所有新测试用 FakeTransport 注入，绝不发真实 HTTP，绝不要求真实 API key，绝不影响现有测试。
- 测试改动环境变量影响 settings 时：reset 单例 + 测后恢复（monkeypatch 自动恢复）。
- 自动 fixture：把 tindalos.llm._sleep_backoff monkeypatch 成 lambda attempt: None，避免任何真实退避 sleep。
- 注释/docstring 用中文（仓库约定），命名与既有风格一致。
- 只跑你自己的测试子集，绝不要跑全量套件（其他 agent 正在并发改共享目录；本仓非 git，无法隔离）。
  运行方式：python -m pytest <你的测试文件> -q
- 遵循全局技能策略：若 tdd / diagnosing-bugs / code-review 等技能可用，开工前先调用匹配技能。
- 你的最终文本就是返回值——返回结构化报告，不要面向人类口吻。`

const REPORT_SCHEMA = {
  type: 'object',
  required: ['passed', 'changed_files', 'tests_run', 'tests_passed', 'tests_failed', 'notes'],
  properties: {
    passed: { type: 'boolean' },
    changed_files: { type: 'array', items: { type: 'string' } },
    tests_run: { type: 'string' },
    tests_passed: { type: 'integer' },
    tests_failed: { type: 'integer' },
    notes: { type: 'string' },
  },
}

// ---------------- Agent A：vision.py + test_vision.py ----------------

const AGENT_A = COMMON_PREAMBLE + `
你现在拥有并只能改动以下文件（其余文件一律只读）：
- src/tindalos/vision.py
- tests/test_vision.py（新建）

任务（源自 spec §三.7 与审计发现）：
1. MIME 嗅探 + 大小限制（classify_image_online 当前硬编码 ("image/png", data)）：
   - 用魔数嗅探真实 MIME：PNG \\x89PNG\\r\\n\\x1a\\n → image/png；JPEG \\xff\\xd8\\xff → image/jpeg；GIF87a/GIF89a → image/gif；
     RIFF....WEBP → image/webp；BMP "BM" → image/bmp；其余 → image/png（防御性默认）。仍走 LLMClient.classify_image((mime, bytes)) 接口。
   - 大小限制：文件 > 4MB 时跳过在线识别，抛 ValueError（信息为中文，如"图像超过 4MB，跳过在线识别"），由 classify_image 捕获转离线降级。
   - 常量定义在模块顶部并注释。
2. 模型驱动置信度（当前 0.9/0.0 硬编码形同虚设）：
   - 扩展 _SYSTEM_PROMPT：要求模型同时输出 confidence（0~1 小数，表示分类把握，必须给出）。
   - 解析 confidence：clamp 到 [0,1]；缺失/非数字 → 0.0。
   - 删除 "0.9 if kind != 'unknown' else 0.0"。
   - needs_confirmation = (kind == "unknown") or (confidence < 0.7)；0.7 为确认门槛常量（VisionResult docstring 已引用 0.7）。
3. name 规整：strip；去掉首尾 markdown（** / * / 反引号）；内部连续空白折叠；截断 ≤80 字符。
4. degraded_reason 不持久化上游错误正文（classify_image 的 except 分支，当前 str(e)[:120]）：
   - 改为简短泛化：有 .kind 属性（LLMError）→ f"在线识别失败（{e.kind}）"；否则 f"在线识别失败（{type(e).__name__}）"。
   - 绝不内嵌上游错误正文（防泄露密钥/内部信息）。从 tindalos.llm 导入 LLMError。
5. 公开 API 契约不变：VisionResult / classify_image / classify_images / classify_image_online / KIND_VALUES / to_dict() 键集保持不变。

tests/test_vision.py（新建，FakeTransport 零网络零 LLM，参考 tests/test_llm.py 的 FakeTransport 与 test_style_guide.py 的 autouse _no_sleep）：
- 离线路径（无 vl_key）：kind=unknown、needs_confirmation=True、meta.hint 存在（用 PIL 在 tmp_path 生成一张真实 1x1 PNG 作为输入文件）。
- 在线成功：模型返回 {kind,name,caption,confidence} → VisionResult 各字段正确；transport.calls[0] 收到 data URI。
- 模型置信度驱动确认：confidence 0.9 → needs_confirmation False；0.3 → True；缺失 → 0.0 → True。
- kind 越界：模型返回 kind="foo" → 归一为 unknown、needs_confirmation True。
- name 规整：模型返回 "  **老船长**  " → name == "老船长"。
- MIME 嗅探：写 PNG 魔数文件 → 断言 data URI 前缀 data:image/png;base64；写 JPEG 魔数（\\xff\\xd8\\xff...）→ data:image/jpeg;base64。
- 大小限制：>4MB 文件 → 不发起任何请求（transport.calls == []）、返回离线 fallback、degraded_reason 以"在线识别失败"开头。
- 在线异常降级：transport 抛 ConnectionError（消息含敏感串如 "sk-super-secret"）→ fallback kind=unknown；断言 degraded_reason 不含该敏感串、以"在线识别失败"开头。
- 5xx 重试后失败 → fallback；4xx 不重试（1 次调用即 fallback）。
- classify_images 批量逐张返回 list。
- VisionResult.to_dict() 键集契约。
- 测试中设置 key 的方式：设置环境变量（先确认 config.py 中 vl 相关 env 名）+ monkeypatch.setattr("tindalos.config._settings", None) 再首次 get_settings()；测后自动恢复。

完成后只跑：python -m pytest tests/test_vision.py -q（全部绿为止）。`

// ---------------- Agent B：pdfio.py CMYK 修复 + test_pdfio.py ----------------

const AGENT_B = COMMON_PREAMBLE + `
你现在拥有并只能改动以下文件（其余文件一律只读）：
- src/tindalos/pdfio.py
- tests/test_pdfio.py（新建）

任务（源自审计发现：CMYK 图像导致整个上传 422 + 孤儿文件）：
1. 修复 _extract_page_images 中 pil.save(dest, format="PNG") 的 CMYK 崩溃：
   - PyMuPDF bitmap.to_pil() 对 CMYK 编码图像返回 CMYK 模式 PIL 图像，Pillow 无法存 PNG → OSError("cannot write mode CMYK as PNG")，
     当前一处坏图杀死整个 PDF 上传（422 + 留下孤儿文件）。
   - 修复：优先转换保图 —— 保存前若 pil.mode 不是 "RGB"，先 pil.convert("RGB")；to_pil() 与 save 各自 try/except，任何单个图像失败仅跳过该图
     （中文日志警告，注明 page_no 与索引），绝不抛出使 analyze_pdf 整体失败。
   - 确认失败图不留下孤儿 .png 残骸（建议先 save 到临时再改名，或失败时删掉已建的 dest）。
2. 公开 API 不变：analyze_pdf(path, out_dir) / PdfImageInfo（含 saved_path、page_no、width、height）契约不变。

tests/test_pdfio.py（新建，零网络）：
- 用 PyMuPDF(fitz) 在 tmp_path 构造含图 PDF：一张 RGB PNG（PIL 生成）+ 一张 CMYK JPEG（PIL Image.new("RGB",(32,32)).convert("CMYK") 存 JPEG）。
  analyze_pdf → 不抛异常；两个图像都被提取（CMYK 图已转 RGB 保存），或至少 RGB 图一定提取且 CMYK 图被跳过（若转 RGB 失败）。
  打开保存的 .png 验证 mode == "RGB"（CMYK 不会以 CMYK 形态落盘）。
- 无图 PDF → 返回空 list，不崩溃。
- saved_path 指向 out_dir 下、文件真实存在、page_no 正确。
- 边界：损坏/极小 PDF → 不抛异常（容错）。

完成后只跑：python -m pytest tests/test_pdfio.py -q（全部绿为止）。`

// ---------------- Agent C：generator + cli + serve + web ----------------

const AGENT_C = COMMON_PREAMBLE + `
你现在拥有并只能改动以下文件（其余文件一律只读；web.py 只有你碰，避免并行冲突）：
- src/tindalos/generator.py
- src/tindalos/cli.py
- src/tindalos/serve.py
- src/tindalos/web.py
- 测试：tests/test_generator_llm.py、tests/test_web.py、tests/test_cli.py、tests/test_serve.py（按存在情况；全量读取后只新增不改坏既有用例）

任务：

A. 生成上下文注入多模态参考（spec §四.3）——generator.py：
   - 新增状态 self._module_images: list[dict] = []，方法 set_module_images(images: list[dict])（拷贝存入；条目按 vision.VisionResult.to_dict() 形状：kind/name/caption；无 kind 的丢弃）。
   - _ctx()（约 267-286 行，风格规范块 + 模组背景块之后）追加第三块"模组图像参考"：仅当 self._module_images 非空时输出。
     每图一行：图N（{kind}）：{name or '无名字'}——{caption}；kind=="unknown" 的跳过（无可用信息）；
     整块长度设上限（选一个与 llm_context_chars 一致的合理预算，如 2000 字符，超了截断，注释说明）。
   - 空 images → 不出现任何新块 → 现有测试不受影响。set_module_context 行为不动。

B. 接线 serve.py + web.py + cli.py：
   - serve.py default_generate(module_text, llm, emit) 增加 keyword-only 参数 module_images: list | None = None；
     set_module_context 之后（约 78 行）在 hasattr 保护下调用 generator.set_module_images(module_images or [])；更新 docstring。
   - web.py /api/generate（api_generate，约 272-331 行）请求体模型新增可选字段 module_images: list[dict] | None = None；
     透传给 default_generate(..., module_images=...)；SSE 帧格式与既有契约（前端依赖）不变。
   - cli.py（约 135-136 行 set_module_context 附近）：若 CLI 当前数据流已能拿到模组图像（PDF 提取产物或 meta 文件）则顺手接上 set_module_images；
     拿不到就保持现状（generator 空 images 不产生块）。不要为接图像重构 CLI 的输入处理。
   - 前端（frontend/）：可选。若让模组生成请求携带所选模组的 vision 结果 images 是 ≤2 文件、无需改 vitest 的一行级改动，就接上；
     否则跳过并在报告 notes 里说明（后端能力已就绪）。

C. web.py /files 安全修复（关键，约 756-758 行）：
   - 现状 app.mount("/files", StaticFiles(directory=str(_data_dir()))) 暴露整个 data 目录：/files/store/*.sqlite 与 /files/modules/<id>/text.txt（模组全文）可下载。
   - 修复：用 StaticFiles 子类挂载，仅放行 modules/<module_id>/images/<文件> 且扩展名 ∈ .png/.jpg/.jpeg/.webp/.gif，其余一律 404。
   - 覆盖 get_response 前先读已安装 starlette 的 StaticFiles 源码确认签名（venv 里），适配签名。
   - _image_url（约 142-148 行）产出的 URL 形状（/files/modules/<id>/images/...）保持不变。
   - 先读 tests/test_web.py 了解现有 app/client 构造方式，新增用例：
     (1) GET /files/store/<任意> → 404；GET /files/modules/<id>/text.txt → 404；
     (2) GET /files/modules/<id>/images/a.png → 200（需 monkeypatch TINDALOS_DATA_DIR 或 _data_dir 到 tmp_path 并真实创建该文件）。

D. web.py:451 事件循环阻塞（关键）：
   - 上传端点里 vision_results = vision.classify_images(info.images) 是同步阻塞调用在 async 端点中 → 改为 await asyncio.to_thread(vision.classify_images, info.images)。
   - 确保 import asyncio。上传端点相关测试：monkeypatch tindalos.web.vision.classify_images 返回罐头结果（避免依赖并发中被 Agent A 改动的 vision.py 运行时行为），验证 meta 中 images 照常写入。

E. 测试：
   - test_generator_llm.py：set_module_images → _ctx 含"模组图像参考"块且含 kind/name/caption；unknown 图跳过；空 list 无块；超长块截断。
   - test_web.py：上述 /files 404/200 用例；/api/generate 带 module_images → FakeTransport 断言 prompt 含"模组图像参考"。
   - test_serve.py（若存在）：default_generate 透传 module_images 到 generator。

完成后只跑（按存在情况组合）：python -m pytest tests/test_generator_llm.py tests/test_web.py tests/test_cli.py tests/test_serve.py -q（全部绿为止）。`

// ---------------- run ----------------

phase('Implement')
const results = await parallel([
  () => agent(AGENT_A, { label: 'vision', phase: 'Implement', schema: REPORT_SCHEMA }),
  () => agent(AGENT_B, { label: 'pdfio', phase: 'Implement', schema: REPORT_SCHEMA }),
  () => agent(AGENT_C, { label: 'generator+web', phase: 'Implement', schema: REPORT_SCHEMA }),
])

log('三个 agent 报告聚合，待主循环串行复核全量测试')
return { reports: results.filter(Boolean) }
