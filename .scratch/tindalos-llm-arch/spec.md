# Tindalos LLM 层统一架构 + 多模态植入 实施 spec

> 来源：ultracode 请求"验证 LLM 正确运行 + 多模态模型植入 + 架构优化"。
> 用户已确认：需要真实 API 时 HITL 让用户配置 key，然后用真实 API 验证。

## 〇、审计结果（Workflow 1，5 agent，54 findings）

> 完整 journal：`C:\Users\Administrator\.claude\projects\D--Agent-Workspace\baaae957-8117-48b1-be83-f627995c08f3\subagents\workflows\wf_6232e936-d8f\journal.jsonl`

**critical/high（必须修）**：
1. **[correctness] generator._norm_scene** 不规整 setting/事件 description/conditions → LLM 坏字段（`description` 为 list 等）击穿 pydantic `ValidationError`，整条备团崩溃而非降级。
2. **[security] web.py:758 `/files` 挂载整个 data/**：`GET /files/store/memory_entries.sqlite` 可下载含全部游玩会话/记忆的整库，`/files/modules/<id>/text.txt` 下载模组全文。应只暴露 images 子目录。
3. **[correctness] cli.py:284 `eval --trace` 无条件构造 LLMJudge**：`--judge` 标志被忽略；`TINDALOS_LLM_ENABLED=1` 时 `eval --trace` 不带 `--judge` 也调用云端并计费。应 `LLMJudge() if judge else None`。
4. **[correctness] judge.py:152 `TINDALOS_JUDGE_MODEL` 只改标签不改请求模型**：请求体写死 `settings.model`，self_preference_risk 失真。
5. **[correctness] rag.py:565 混合维度向量**：单次 ingest 内子块在线成功(1024 维)后父块在线失败 → 降级 256 维 → 入库崩溃或检索静默全空。
6. **[duplication] rag.py:803 `_llm_answer` 第三份端点解析**：只读 `TINDALOS_API_BASE` 硬编码 DeepSeek，完全忽略 `OLLAMA_BASE_URL`/settings；只配本地 Ollama 时 RAG 悄悄打云端。
7. **[design] config.py Settings 缺 vl/embed 字段**：vision/rag 直读 `os.environ` 绕过 Settings 单例，且不受 `TINDALOS_LLM_ENABLED` 门控（配置了 DashScope key 即使 llm_enabled=0 也走在线）。
8. **[robustness] pdfio.py:122 CMYK 图保存 PNG 抛 OSError 未捕获**：一张坏图毁掉整份模组上传（422 + 孤儿文件）。
9. **[robustness] web.py:451 `classify_images` 同步串行阻塞 async 事件循环**：30 图最坏 30×60s 停摆所有端点。

**medium（本次一并修）**：
- generator：`llm_context_chars=0` 语义反（注入全文）；重试缺 408/Retry-After/退避（最坏 9 分钟阻塞）；tool_calls 不校验 name/并行静默丢弃。
- judge：payload 构造在 try 之外（build_user_payload 异常会逃出降级）；`_extract_json_object` 取首个对象可能误取 CoT 示例。
- rag：`_EMBED_STATE` 熔断永久无恢复；`search()` 静默吞在线失败返回 `[]` 不诚实降级；`_online_embed` 不校验返回条数/维度。
- vision：data URI 硬编码 image/png + 无体积上限；`_parse_vl_json` 弱化重复；confidence 硬编码 0.9/0.0 门槛形同虚设；name 未规整；degraded_reason 持久化上游错误正文。
- config：数值字段解析无防护（垃圾值 ValueError 击穿 CLI）；默认 model=deepseek-chat 与本地 Ollama 路径冲突；`TINDALOS_LLM_ENABLED` 只认字面 "1"。
- scripts/organize_module.py：第四份 chat 客户端 + 强制要 key + `--title` 最后参数 IndexError。
- serve/web：`_resolve_serve_generator` 重复 + 静默回退无警告。
- test-gap：**无 test_vision.py / test_pdfio.py**，多模态核心路径零测试。

**low（记录，依性价比取舍）**：judge 预算门顺序、estimate_usd 低估、Bearer 明文 http、模组全文反复外发（需文档）、personality tuple 分支、`_cosine` 维度守卫、get_embedder 竞态、evolve 不用 LLM、web eval/run 自动裁判等。

## 一、审计发现（现状病灶）

**5 个 HTTP 调用面、4 套重复实现**：

| 调用面 | 文件 | 传输 | 重试 | 配置来源 | JSON 提取 |
|---|---|---|---|---|---|
| 生成 | generator.py `_chat` | requests | ✅ 全 | settings | `_parse_json`（剥 fence+容错） |
| 裁判 | judge.py `_default_client` | urllib | ❌ | settings | 平衡括号扫描 |
| 问答 | rag.py `_llm_answer` | requests | ❌ | **直读 os.environ** | 无 |
| 向量 | rag.py `_online_embed` | requests | ❌ | 直读 os.environ | 无 |
| 视觉 | vision.py `classify_image_online` | requests | ❌ | 直读 os.environ | `_parse_vl_json`（重复 generator） |

**病灶清单**：
1. **配置分裂**：主 LLM（`TINDALOS_API_BASE/OLLAMA_BASE_URL/TINDALOS_API_KEY/DEEPSEEK_API_KEY/TINDALOS_MODEL`）、VL（`TINDALOS_VL_BASE/TINDALOS_VL_MODEL/TINDALOS_DASHSCOPE_KEY`）、embedding（`TINDALOS_EMBED_BASE/TINDALOS_EMBED_MODEL`）三套 env 各自读；config.py 只收主 LLM，vl/embed 字段缺失 → vision/rag 绕过 Settings 直读 env。
2. **重试不一致**：仅 generator 有 `_is_retryable`（Timeout/ConnectionError/5xx/429 重试，4xx 不重试）；judge/rag/vision 零重试 → 云端抖动即失败。
3. **JSON 容错重复**：`_parse_json` / 平衡括号扫描 / `_parse_vl_json` 三份实现。
4. **`_llm_answer` 绕过 settings**：`TINDALOS_API_BASE` 有值时代替 `OLLAMA_BASE_URL`，主 key 混读，与 generator/judge 行为不一致。
5. **vision 零测试**：tests/ 下无 test_vision.py；多模态无重试、无 4xx/5xx 分类、kind 越界未防御、批量逐张串行。

## 二、统一客户端设计：`src/tindalos/llm.py`

单一传输 + 统一重试 + 统一 JSON 容错 + 多模态与 embedding 一并收敛。

```python
class LLMError(Exception):
    """LLM 调用失败（网络/超时/HTTP 4xx5xx/解析）。携带 status_code、kind、partial_text。"""

class LLMClient:
    """OpenAI 兼容端点的统一客户端。所有在线 LLM 路径共用。

    - 传输：requests（可选依赖；未装时在线方法抛 LLMError("requests 未安装")）
    - 可注入 transport：test 用 fake（与 test_generator_llm._FakeRequests 同模式），零网络。
    """

    def __init__(self, settings, *, transport=None):
        self.settings = settings
        self._transport = transport or _requests_transport  # 可注入

    # --- chat（生成/裁判/问答共用） ---
    def chat(
        self, messages, *, temperature=0.7, response_format=None,
        tools=None, tool_choice=None, timeout=None, max_retries=None,
        base_url=None, model=None, api_key=None,
    ) -> str:
        """POST {base}/chat/completions → 返回 message.content。

        重试策略（与 generator._is_retryable 对齐）：connect timeout / ConnectionError /
        5xx / 429 重试 max_retries 次（指数退避）；4xx 不重试直接 LLMError。
        tools 存在时若返回 tool_calls 首个参数 → 返回 JSON 字符串。
        """

    def chat_json(self, messages, *, expect=None, **kw) -> Any:
        """chat + 统一容错 JSON 提取（剥 fence + 平衡括号扫描 + prose 包裹容忍）。
        expect 为顶层期望结构（如 dict）；解析失败抛 LLMError(kind="parse_failed", raw=...)。"""

    # --- embedding ---
    def embed(self, texts: list[str]) -> list[list[float]]:
        """POST {base}/embeddings → 归一化向量（模型返回 index 排序）。"""

    # --- 多模态（VL） ---
    def classify_image(self, image: "ImageInput", prompt: str) -> str:
        """POST {base}/chat/completions，消息含 image_url(data:image/png;base64,...)。
        image 为 data-URI 字符串或 (mime, bytes) 元组。"""

# 辅助
def _extract_json(text: str) -> Any:
    """合并 generator._parse_json（剥 fence）+ judge 平衡括号扫描的健壮提取。"""

def _is_retryable(exc, status_code=None) -> bool: ...
def _sleep_backoff(attempt: int) -> None: ...   # 可注入 sleep 便于测试
```

**关键决策**：
- **transport 注入**：`LLMClient(settings, transport=...)`，transport 是 `(method, url, *, json, headers, timeout) -> ResponseLike` 的可调用对象。生产用 requests.post 包装；测试注入 `_FakeRequests` 式 fake → 保持"零 LLM 零网络可测"铁律。
- **配置收敛**：config.py 补齐 `vl_base/vl_model/vl_key/embed_base/embed_model/embed_key` 六字段（含 env 解析 + 默认值），vision/rag 不再直读 os.environ，全部经 `get_settings()`。
- **requests 保持可选**：模块级 try/except 守卫，未装时在线方法抛 LLMError 明确报错；离线降级路径不受影响。
- **错误分类**：`LLMError.kind ∈ {no_requests, timeout, connection, http_4xx, http_5xx, http_429, parse_failed}`，各调用点按既有降级哲学处理（generator→回退 Deterministic、judge→judge='none'、rag→离线、vision→启发式降级）。

## 三、迁移计划（task #16-#17）

1. **config.py**：+6 字段（vl/embed），保留既有 env 兼容。
2. **llm.py**（新）：LLMClient + _extract_json + 重试 + transport。
3. **generator.py**：`_chat` 改调 `LLMClient.chat(..., tools=...)`，删 `_parse_json`/`_is_retryable` 内部逻辑（保留 `_norm_*` 规整与降级）。保留 `_FakeRequests` 兼容或改注入 transport。
4. **judge.py**：`_default_client` 替换为 `LLMClient.chat(temperature=0, response_format=json_object)`；`parse_judge_json` 保留（结构校验），复用 `_extract_json`。
5. **rag.py**：`_llm_answer` → `LLMClient.chat`（修掉绕过 settings 的 bug）；`_online_embed` → `LLMClient.embed`；`_EMBED_STATE` 降级状态机保留。
6. **vision.py**：`classify_image_online` → `LLMClient.classify_image` + 重试 + kind 越界防御；`_parse_vl_json` → `_extract_json`。
7. **test_vision.py**（新，task #17）：fence/容错/重试/4xx 不重试/kind 越界/base64 编码/降级/批量，FakeTransport 注入，零网络。
8. **test_llm.py**（新）：统一客户端单元测试（重试矩阵、JSON 提取、embed、多模态 payload 组装）。

## 四、可观测性与验证（task #18-#19）

1. **`tindalos doctor`**：连通性自检命令，四路探测并打印诊断：
   - 主 LLM chat（最小 ping：1 条 system "ping"）
   - VL classify（含 1px 测试图 data-URI）
   - embedding
   - 无 key / key 无效 / 端点不通 / requests 未装 分级提示 + 退出码。
   只读，不写库。
2. **`scripts/verify_llm.py`**：
   - 默认 mock 模式：起本地 loopback OpenAI 兼容假服务（stdlib http.server），对 generator/judge/rag-qa/embed/vision 五个调用面逐一发真请求验证 JSON/状态码/重试。
   - `--real` 模式：读真实 env key 直连真实 API（HITL 配置后跑）。
3. **多模态植入完善**（task #17）：vision 分类结果进生成上下文——`set_module_context` 时把图片 kind/name/caption 追加为"模组图像参考"注入 prompt；web 上传返回 meta 已含 images，前端可展示。

## 五、验收门（task #20）

- 后端 `pytest tests/ -q -k "not web_dockerfile_build"` 全绿（407 基线 + 新增）。
- 前端 vitest + build 全绿。
- `scripts/verify_llm.py` mock 模式五个调用面全通（零网络、零 key）。
- `--real` 模式：用户 HITL 配置 key 后真实 API 四路全通（生成/裁判/问答/embedding/VL）。
- README 更新配置说明（vl/embed 字段 + doctor + verify_llm）。
- 安全扫描：无 key 入库。
