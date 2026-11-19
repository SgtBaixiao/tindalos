"""Tindalos 个人网站统一服务（SgtXLonelyHeartsClub）：FastAPI 一站式后端。

在 serve.py 的 SSE 契约之上扩展完整站点能力（wayfinder 一次性全套工程）：

  GET    /api/health                        → {ok:true, version}
  POST   /api/generate       body={module_text, llm?, rules?} → SSE 流（契约与 serve 相同：
                                                              data:{stage,message} … data:{done,campaign}）
  GET    /api/campaigns      → {campaigns:[...]}（元信息列表，不含快照）
  GET    /api/campaigns/<id> → {campaign, meta}（serve 契约向前兼容：工作台 json.campaign ?? json 取 campaign）
  POST   /api/regenerate     body={campaign_id, node_id, llm?} → {ok, campaign, applied}
  POST   /api/modules/upload | /api/modules   (multipart: file, rules?) → 201 {module}（同 sha256 → 200 已有）
  GET    /api/modules        → {modules:[...]}（列表，不含文本预览）
  GET    /api/modules/history → {modules:[...]}（site 层历史页数据源，与 /api/history/modules 同源）
  GET    /api/modules/<id>   → {module}（详情：images 视觉结果 + text_preview）
  POST   /api/modules/<id>/ingest → {indexed, chunks}（等价 /api/rag/ingest，id 取自路径）
  POST   /api/modules/<id>/images | /api/modules/<id>/confirm-image
                             body={image_path, kind, name?, caption?} → 人工确认写回 {ok, images}
  POST   /api/rag/ingest     body={module_id} → {indexed, chunks}
  POST   /api/rag/search     body={query, module_id?, top_k?} → {results:[...]}
  POST   /api/rag/qa | /api/qa   body={question, module_id?, rules?} → {answer, sources, mode}
  GET    /api/history/modules    → {modules:[...]}
  GET    /api/history/campaigns  → {campaigns:[...]}
  GET    /api/history/campaigns/<id> → {campaign, meta}
  DELETE /api/history/campaigns/<id> → 204
  POST   /api/sessions/<campaign_id>  body={summary, play_status?, conflicts?} → 会话结果（P2 回叙采集）
  GET    /api/sessions/<campaign_id> → {campaign_id, current_play_status, sessions:[...]}
  /files/**   静态挂载 data/（模组图像等）
  /           frontend/dist 存在时静态托管站点（hash 路由，无需 SPA 回退）

设计哲学与全仓一致：pdfio/vision/history/rag 全部**延迟导入**（模块缺失或异常时端点
诚实降级——离线确定性优先，云端是增强层不是硬依赖）；图像以 /files/<相对 data> 供前端
<img> 直接访问；上传的 PDF 全文落盘 `data/modules/<id>/text.txt` 供 RAG 入库。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import queue
import threading
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Request, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from tindalos.serve import default_generate, sse_frame, sse_stage

VERSION = "0.1.0"
RULES_VALUES = ("COC7", "DND5e")
MAX_UPLOAD_BYTES = 128 * 1024 * 1024  # 128MB（守秘人规则书 16MB 余量充足）

# ---------------------------------------------------------------- 请求模型


class GenerateRequest(BaseModel):
    module_text: str = Field(default="", max_length=2_000_000)
    module_id: str | None = None
    campaign_id: str | None = None
    expected_revision: int | None = Field(default=None, ge=1)
    llm: bool = False
    rules: str = "COC7"
    # 模组图像视觉识别结果（spec §四.3）：kind/name/caption 透传给 default_generate 注入生成上下文。
    module_images: list[dict] | None = None


class RegenerateRequest(BaseModel):
    expected_revision: int | None = Field(default=None, ge=1)
    campaign_id: str
    node_id: str
    llm: bool = False


class IngestRequest(BaseModel):
    module_id: str


class SearchRequest(BaseModel):
    query: str
    module_id: str | None = None
    top_k: int = Field(default=6, ge=1, le=50)


class QaRequest(BaseModel):
    question: str
    module_id: str | None = None
    rules: str | None = None


class EvalRunRequest(BaseModel):
    """POST /api/eval/run：对某历史剧本跑完整六层 eval（零 LLM 也可全绿）。

    campaign_id 缺省时取最新一条历史剧本；module_id 传给 L4 限定检索语料；
    max_usd 覆盖预算门上限（默认 Settings.eval_max_usd，环境变量 EVAL_MAX_USD）。
    """
    campaign_id: str | None = None
    module_id: str | None = None
    max_usd: float | None = None


class HumanReviewRequest(BaseModel):
    revision: int = Field(ge=1)
    usable: bool
    notes: str = Field(min_length=1, max_length=20_000)


class ConfirmImageRequest(BaseModel):
    image_path: str
    kind: str
    name: str | None = None
    caption: str | None = None


class RecordSessionRequest(BaseModel):
    """POST /api/sessions/{campaign_id}：KP 回叙采集一场游玩会话（P2）。

    summary 必填（非空）；play_status / conflicts 可选。conflicts 为分歧/规则裁定
    记录列表，交由 record_session json.dumps 落库（读回时解析回对象）。
    """
    summary: str = Field(max_length=100_000)
    play_status: str | None = None
    conflicts: list[dict[str, Any]] | None = None
    request_key: str | None = None
    facts: list[dict[str, str]] = Field(default_factory=list, max_length=200)


class EditNodeRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    patch: dict[str, Any]


# ---------------------------------------------------------------- 路径与状态


class _State:
    """跨请求共享状态：campaign 内存缓存（serve 契约兜底，历史库为持久真相）。"""

    def __init__(self) -> None:
        self.campaigns: dict[str, dict] = {}
        self.lock = threading.Lock()


def _data_dir() -> Path:
    return Path(os.environ.get("TINDALOS_DATA_DIR", "data"))


def _modules_dir() -> Path:
    return _data_dir() / "modules"


def _validate_rules(rules: str) -> str:
    return rules if rules in RULES_VALUES else "COC7"


def _image_url(image_path: str) -> str:
    """本地绝对图像路径 → /files/ 静态 URL（供 <img> 直接访问）。"""
    try:
        rel = Path(image_path).resolve().relative_to(_data_dir().resolve())
        return "/files/" + rel.as_posix()
    except ValueError:
        return ""


class _ModuleImagesFiles(StaticFiles):
    """/files 静态挂载的安全收窄：仅放行 modules/<module_id>/images/<图片>（扩展名白名单），
    其余一律 404——防止 data/ 整目录泄露（store/*.sqlite、modules/<id>/text.txt 均不可下载）。
    _image_url 输出形状（/files/modules/<id>/images/...）保持不变。
    """

    _IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif")

    async def get_response(self, path: str, scope):  # noqa: ARG001 - 兼容父类签名
        if not self._is_allowed(path):
            raise HTTPException(status_code=404, detail="not found")
        return await super().get_response(path, scope)

    @classmethod
    def _is_allowed(cls, path: str) -> bool:
        # 目标形状：modules/<module_id>/images/<file>（4 段）。
        # Starlette get_path 经 os.path.normpath（Windows 下产出反斜杠）→ 先归一为 '/'
        parts = [p for p in path.replace("\\", "/").split("/") if p]
        if len(parts) != 4:
            return False
        module_dir, _module_id, images_dir, fname = parts
        if module_dir != "modules" or images_dir != "images":
            return False
        return fname.lower().endswith(cls._IMAGE_EXT)


def _module_dir(module_id: str) -> Path:
    return _modules_dir() / module_id


# ---------------------------------------------------------------- 模块载荷


def _module_payload(row: dict, *, detail: bool = False) -> dict:
    """history 行 → API 载荷（images 补 image_url；detail 加 text_preview）。

    history 模块返回的 meta_json 已 json.loads 回对象；此处只兜底解析字符串
    （防御旧数据/外部直接写库），两者都兼容。
    """
    meta = row.get("meta_json") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except json.JSONDecodeError:
            meta = {}
    images = list(meta.get("images") or [])
    for img in images:
        if isinstance(img, dict):
            img["image_url"] = _image_url(str(img.get("image_path", "")))
    payload: dict[str, Any] = {
        "id": row["id"],
        "filename": row["filename"],
        "sha256": row["sha256"],
        "pages": row["pages"],
        "chars": row["chars"],
        "rules": row["rules"],
        "status": row["status"],
        "created_at": row["created_at"],
        "images": images,
    }
    if detail:
        text_path = _module_dir(row["id"]) / "text.txt"
        try:
            payload["text_preview"] = text_path.read_text(encoding="utf-8")[:4000]
        except OSError:
            payload["text_preview"] = ""
    return payload


def _session_payload(row: dict) -> dict:
    """play_sessions 行 → API 载荷（conflicts JSON 字符串解析回对象，与 /api/memories 同款）。"""
    conflicts = row.get("conflicts")
    if isinstance(conflicts, str):
        try:
            conflicts = json.loads(conflicts)
        except json.JSONDecodeError:
            conflicts = None
    return {
        "session_id": row["id"],
        "session_index": row["session_index"],
        "summary": row["summary"],
        "play_status": row["play_status"],
        "conflicts": conflicts,
        "created_at": row["created_at"],
    }


def _find_module_by_sha256(sha256: str) -> dict | None:
    from tindalos import history as history_mod

    return history_mod.find_module_by_hash(sha256)


def _load_campaign_for_regenerate(campaign_id: str) -> dict:
    """Load the authoritative saved snapshot. Storage failures remain server errors."""
    from tindalos import history as history_mod
    rec = history_mod.get_campaign(campaign_id)
    if rec is None or not rec.get("snapshot"):
        raise HTTPException(status_code=404, detail="campaign not found")
    return rec["snapshot"]


# ---------------------------------------------------------------- 应用工厂


def create_app() -> FastAPI:
    """构造站点后端 app（测试可隔离实例；数据目录随环境变量 TINDALOS_DATA_DIR）。"""

    from tindalos.jobs import JobRuntime, CapacityError, TERMINAL
    from tindalos import workspace

    def execute_generation(body, emit):
        import inspect
        from tindalos import memory_entries as me
        cid = body.get("campaign_id") or "campaign-" + uuid.uuid4().hex
        continuation = me.continuation_context(cid, _data_dir() / "store" / "memory_entries.sqlite")
        if body.get("campaign_id"):
            adopted = body['base_snapshot']
            outline = {'title':adopted.get('title'), 'acts':[
                {'title':a.get('title'),'summary':a.get('summary')} for a in adopted.get('acts',[])]}
            continuation += "\n【当前采用剧本提纲】\n" + json.dumps(outline, ensure_ascii=False)[:24000]
        options = {"module_images":body.get("module_images"),"campaign_id":cid,
                   "rules":body.get("rules","COC7"),"continuation":continuation,"persist_memory":False}
        options["adopted_npcs"] = body.get("base_snapshot", {}).get("npcs", {})
        signature = inspect.signature(default_generate)
        options = {k:v for k,v in options.items() if k in signature.parameters}
        generated = default_generate(body["module_text"], body.get("llm",False), emit, **options)
        if generated and body.get("campaign_id"):
            return workspace.append_continuation(body["base_snapshot"], generated, body["expected_revision"])
        return generated

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        _modules_dir().mkdir(parents=True, exist_ok=True)
        from tindalos import history as history_mod
        app.state.runtime = JobRuntime(execute_generation,
            workers=max(1,int(os.environ.get("TINDALOS_JOB_WORKERS","2"))),
            capacity=max(2,int(os.environ.get("TINDALOS_JOB_CAPACITY","16"))),
            max_duration=max(1,float(os.environ.get('TINDALOS_JOB_TIMEOUT','600'))))
        try:
            yield
        finally:
            await asyncio.to_thread(app.state.runtime.close)

    from tindalos import history as history_mod
    app = FastAPI(title="SgtXLonelyHeartsClub API", version=VERSION, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ---------------------------------------------------------- 健康检查

    @app.get("/api/health")
    async def health() -> dict:
        return {"ok": True, "version": VERSION, "runtime":"single_process", "storage":"sqlite_wal", "job_workers":app.state.runtime.workers, "capacity":app.state.runtime.capacity}

    # ---------------------------------------------------------- 生成（SSE）

    def submit_job(body, key=None):
        try:
            identity = body.model_dump(exclude_none=True)
            if key:
                prior = app.state.runtime.lookup(key, identity)
                if prior:
                    return prior
            prepared = workspace.prepare_generation(identity)
            return app.state.runtime.submit(prepared, key=key, identity=identity)
        except CapacityError as exc:
            raise HTTPException(429, str(exc), headers={"Retry-After":"1"})
        except history_mod.RevisionConflict as exc:
            raise HTTPException(409, str(exc))
        except LookupError as exc:
            raise HTTPException(404, str(exc))
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    async def job_stream(job_id, after=0, with_ids=True):
        cursor = after
        last_heartbeat = asyncio.get_running_loop().time()
        while True:
            events = await asyncio.to_thread(app.state.runtime.events, job_id, cursor)
            for event in events:
                cursor = event["sequence"]
                yield (f"id:{cursor}\n".encode() if with_ids else b"") + sse_frame(event["payload"])
            info = await asyncio.to_thread(app.state.runtime.get, job_id)
            if not events and info["status"] in TERMINAL:
                break
            now = asyncio.get_running_loop().time()
            if now - last_heartbeat > 10:
                yield b":heartbeat\n\n"
                last_heartbeat = now
            await asyncio.sleep(0.05)

    def stream_response(job, after=0, with_ids=True):
        return StreamingResponse(job_stream(job["id"],after,with_ids), media_type="text/event-stream",
            headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no","X-Job-ID":job["id"]})

    @app.post("/api/jobs", status_code=202)
    def api_submit_job(body: GenerateRequest, idempotency_key: str | None = Header(default=None)):
        return {"job":submit_job(body,idempotency_key)}

    @app.get("/api/jobs/{job_id}")
    def api_get_job(job_id: str):
        job = app.state.runtime.get(job_id)
        if not job:
            raise HTTPException(404,"job not found")
        return {"job":job}

    @app.delete("/api/jobs/{job_id}")
    def api_cancel_job(job_id: str):
        job = app.state.runtime.cancel(job_id)
        if not job:
            raise HTTPException(404,"job not found")
        return {"job":job}

    @app.get("/api/jobs/{job_id}/events")
    def api_job_events(job_id: str, after: int = 0, last_event_id: str | None = Header(default=None)):
        job = app.state.runtime.get(job_id)
        if not job:
            raise HTTPException(404,"job not found")
        try:
            cursor = max(0,int(last_event_id)) if last_event_id else max(0,after)
        except ValueError:
            raise HTTPException(400,"invalid event cursor")
        return stream_response(job,cursor)

    @app.post("/api/generate")
    def api_generate(body: GenerateRequest, idempotency_key: str | None = Header(default=None)):
        return stream_response(submit_job(body,idempotency_key),with_ids=False)

    # ---------------------------------------------------------- campaign 读取 / 重生成

    @app.get("/api/campaigns")
    def api_campaigns():
        from tindalos import history as history_mod

        return {"campaigns": history_mod.list_campaigns()}

    @app.get("/api/campaigns/{campaign_id}")
    def api_campaign(campaign_id: str) -> JSONResponse:
        # {campaign, meta}：serve 契约向前兼容——工作台 fetchCampaignJson 以
        # json.campaign ?? json 取值，两种形状都可用；site 层直接读 campaign/meta。
        rec = history_mod.get_campaign(campaign_id)
        if rec is None:
            raise HTTPException(404, 'campaign not found')
        return JSONResponse(content={"campaign": rec['snapshot'], "meta": {k:v for k,v in rec.items() if k!='snapshot'}})

    @app.post("/api/regenerate")
    def api_regenerate(body: RegenerateRequest):
        from tindalos import history as history_mod
        from tindalos.models import Campaign, construct_loose_campaign

        raw = _load_campaign_for_regenerate(body.campaign_id)
        base = history_mod.get_campaign(body.campaign_id)
        expected = body.expected_revision or base["revision"]
        if expected != base["revision"]:
            raise HTTPException(409,"campaign revision changed")
        try:
            campaign = Campaign.model_validate(raw)
        except Exception:  # noqa: BLE001 - 容错输入（与 cli 同哲学）
            campaign = construct_loose_campaign(raw)
        from tindalos.regenerate import regenerate_node
        from tindalos.serve import _resolve_serve_generator

        try:
            generator = _resolve_serve_generator(body.llm)
            updated, applied = regenerate_node(
                campaign,
                body.node_id,
                generator,
                db_path=None,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        doc_out = updated.model_dump(mode="json") if hasattr(updated, "model_dump") else updated
        if not applied:
            raise HTTPException(502, 'regeneration failed validation; the saved campaign is unchanged')
        try:
            history_mod.register_campaign(
                body.campaign_id,
                str(doc_out.get("title", "")),
                _validate_rules(str(doc_out.get("rules", "COC7"))),
                doc_out,
                expected_revision=expected, reason=f"regenerate:{body.node_id}",
            )
        except history_mod.RevisionConflict as exc:
            raise HTTPException(409,str(exc))
        rec = history_mod.get_campaign(body.campaign_id)
        from tindalos.regenerate import _apply_memory_hook, _classify_node, _strip_kind_prefix
        nid = _strip_kind_prefix(body.node_id)
        _apply_memory_hook(updated, nid, _classify_node(updated, nid), _data_dir() / 'store' / 'memory_entries.sqlite')
        return {"ok": True, "campaign": doc_out, "revision":rec["revision"], "applied": list(applied or [])}

    @app.patch("/api/campaigns/{campaign_id}/nodes/{node_id}")
    def api_edit_node(campaign_id: str, node_id: str, body: EditNodeRequest):
        try:
            rec = workspace.save_node(campaign_id,node_id,body.patch,body.expected_revision)
        except history_mod.RevisionConflict as exc:
            raise HTTPException(409,str(exc))
        except LookupError as exc:
            raise HTTPException(404,str(exc))
        except ValueError as exc:
            raise HTTPException(422,str(exc))
        return {"ok":True,"campaign":rec["snapshot"],"revision":rec["revision"]}

    @app.get("/api/campaigns/{campaign_id}/revisions")
    def api_revisions(campaign_id: str):
        return {"revisions":history_mod.list_revisions(campaign_id)}

    @app.post("/api/campaigns/{campaign_id}/restore/{revision}")
    def api_restore(campaign_id: str, revision: int, body: dict):
        snapshot = history_mod.get_revision(campaign_id,revision)
        if not snapshot:
            raise HTTPException(404,"revision not found")
        if not isinstance(body.get("expected_revision"),int):
            raise HTTPException(422,"expected_revision required")
        try:
            rec = history_mod.register_campaign(campaign_id,snapshot["title"],snapshot.get("rules","COC7"),snapshot,
                expected_revision=body["expected_revision"],reason=f"restore:{revision}")
        except history_mod.RevisionConflict as exc:
            raise HTTPException(409,str(exc))
        return {"campaign":rec["snapshot"],"revision":rec["revision"]}

    @app.get("/api/campaigns/{campaign_id}/state")
    def api_current_state(campaign_id: str, audience: str = "kp"):
        from tindalos import memory_entries as me
        return {"facts":me.current_facts(campaign_id,_data_dir()/"store"/"memory_entries.sqlite",audience=audience)}

    # ---------------------------------------------------------- 模组上传 + 解析

    @app.post("/api/modules/upload")
    @app.post("/api/modules")
    async def api_upload(file: UploadFile = File(...), rules: str = Form("COC7")):
        if not file.filename or not file.filename.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail="仅支持 PDF 上传")
        data = bytearray()
        while chunk := await file.read(1024 * 1024):
            data.extend(chunk)
            if len(data) > MAX_UPLOAD_BYTES:
                raise HTTPException(413, "文件过大（上限 128MB）")
        data = bytes(data)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="文件过大（上限 128MB）")
        if not data:
            raise HTTPException(status_code=400, detail="空文件")

        from tindalos import history as history_mod
        from tindalos import pdfio
        from tindalos import vision

        digest = hashlib.sha256(data).hexdigest()
        existing = _find_module_by_sha256(digest)
        if existing is not None:
            return JSONResponse(status_code=200, content={"module": _module_payload(existing, detail=True)})

        module_id = f"mod-{uuid.uuid4().hex[:10]}"
        midir = _module_dir(module_id)
        midir.mkdir(parents=True, exist_ok=True)
        images_dir = midir / "images"
        safe_name = Path(file.filename).name  # 去路径分隔（防目录穿越）
        pdf_path = midir / safe_name
        pdf_path.write_bytes(data)

        try:
            info = await asyncio.to_thread(pdfio.analyze_pdf, pdf_path, out_dir=images_dir)
        except Exception as e:  # noqa: BLE001 - 解析失败清理并报 422
            try:
                pdf_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise HTTPException(status_code=422, detail=f"PDF 解析失败：{e}")

        # 同步阻塞的视觉识别（含云端 LLM 调用）不能卡死事件循环：移到线程池执行。
        vision_results = await asyncio.to_thread(vision.classify_images, info.images)
        (midir / "text.txt").write_text(info.full_text(), encoding="utf-8")
        meta = {
            "source_filename": safe_name,
            "images": [r.to_dict() for r in vision_results],
        }
        row = history_mod.register_module(
            module_id,
            safe_name,
            digest,
            info.pages,
            info.chars,
            rules=_validate_rules(rules),
            status="uploaded",
            meta=meta,
        )
        return JSONResponse(status_code=201, content={"module": _module_payload(row, detail=True)})

    @app.get("/api/modules")
    def api_modules():
        from tindalos import history as history_mod

        return {"modules": [_module_payload(r) for r in history_mod.list_modules()]}

    @app.get("/api/modules/history")
    def api_modules_history():
        """site 层历史页数据源（与 /api/history/modules 同源）。

        必须注册在 /api/modules/{module_id} 之前——否则 "history" 会被参数路由吞掉，
        FastAPI 按注册顺序匹配。
        """
        from tindalos import history as history_mod

        return {"modules": [_module_payload(r) for r in history_mod.list_modules()]}

    @app.get("/api/modules/{module_id}")
    def api_module(module_id: str):
        from tindalos import history as history_mod

        row = history_mod.get_module(module_id)
        if row is None:
            raise HTTPException(status_code=404, detail="module not found")
        return {"module": _module_payload(row, detail=True)}

    @app.post("/api/modules/{module_id}/images")
    @app.post("/api/modules/{module_id}/confirm-image")
    def api_confirm_image(module_id: str, body: ConfirmImageRequest):
        from tindalos import history as history_mod

        row = history_mod.get_module(module_id)
        if row is None:
            raise HTTPException(status_code=404, detail="module not found")
        meta = row.get("meta_json") or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except json.JSONDecodeError:
                meta = {}
        images = list(meta.get("images") or [])
        target = body.kind
        if target not in ("portrait", "map", "scene", "cover", "unknown"):
            raise HTTPException(status_code=400, detail="kind 非法")
        found = False
        for img in images:
            if isinstance(img, dict) and img.get("image_path") == body.image_path:
                img["kind"] = target
                if body.name is not None:
                    img["name"] = body.name
                if body.caption is not None:
                    img["caption"] = body.caption
                img["confidence"] = 1.0
                img["needs_confirmation"] = False
                found = True
                break
        if not found:
            raise HTTPException(status_code=404, detail="image not found")
        history_mod.update_module(module_id, meta_json=meta)  # history 内部 dumps 落库
        updated = history_mod.get_module(module_id)
        return {"ok": True, "images": _module_payload(updated)["images"]}

    # ---------------------------------------------------------- RAG（模组检索 + 问答）

    def _ingest_module(module_id: str) -> dict:
        """共享入库逻辑：/api/rag/ingest（body.module_id）与
        /api/modules/<id>/ingest（路径 id）等价。"""
        from tindalos import history as history_mod
        from tindalos import rag

        row = history_mod.get_module(module_id)
        if row is None:
            raise HTTPException(status_code=404, detail="module not found")
        text_path = _module_dir(module_id) / "text.txt"
        try:
            full_text = text_path.read_text(encoding="utf-8")
        except OSError:
            raise HTTPException(status_code=409, detail="模组全文缺失（上传解析未完成）")
        chunks = rag.ingest_module(module_id, row["filename"], full_text)
        history_mod.update_module(module_id, status="indexed")
        return {"indexed": True, "chunks": chunks}

    @app.post("/api/rag/ingest")
    def api_rag_ingest(body: IngestRequest):
        return _ingest_module(body.module_id)

    @app.post("/api/modules/{module_id}/ingest")
    def api_module_ingest(module_id: str):
        return _ingest_module(module_id)

    @app.post("/api/rag/search")
    def api_rag_search(body: SearchRequest):
        from tindalos import rag

        query = body.query.strip()
        if not query:
            raise HTTPException(status_code=400, detail="query 必填")
        results = rag.search(query, module_id=body.module_id, top_k=body.top_k)
        return {"results": results}

    @app.post("/api/rag/qa")
    @app.post("/api/qa")
    def api_rag_qa(body: QaRequest):
        from tindalos import rag

        question = body.question.strip()
        if not question:
            raise HTTPException(status_code=400, detail="question 必填")
        return rag.qa(question, module_id=body.module_id, rules=_validate_rules(body.rules) if body.rules else None)

    # ---------------------------------------------------------- 历史记录

    @app.get("/api/history/modules")
    def api_history_modules():
        from tindalos import history as history_mod

        return {"modules": [_module_payload(r) for r in history_mod.list_modules()]}

    @app.get("/api/history/campaigns")
    def api_history_campaigns():
        from tindalos import history as history_mod

        return {"campaigns": history_mod.list_campaigns()}

    @app.get("/api/history/campaigns/{campaign_id}")
    def api_history_campaign(campaign_id: str):
        from tindalos import history as history_mod

        rec = history_mod.get_campaign(campaign_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="campaign not found")
        return {"campaign": rec["snapshot"], "meta": {k: v for k, v in rec.items() if k != "snapshot"}}

    @app.delete("/api/history/campaigns/{campaign_id}")
    def api_history_delete(campaign_id: str):
        from fastapi import Response

        from tindalos import history as history_mod

        history_mod.delete_campaign(campaign_id)
        return Response(status_code=204)

    # ---------------------------------------------------------- eval trace

    def _eval_db_path() -> Path:
        # 与 eval_store.eval_db_path 默认一致（Settings.store_dir = data/store）
        return _data_dir() / "store" / "eval.sqlite"

    @app.get("/api/eval/runs")
    def api_eval_runs(limit: int = 20, campaign_id: str | None = None):
        from tindalos import eval_store

        return {"runs": eval_store.list_runs(limit=limit, campaign_id=campaign_id, db_path=_eval_db_path())}

    @app.get("/api/eval/runs/{run_id}")
    def api_eval_run_detail(run_id: str):
        from tindalos import eval_store

        run = eval_store.get_run(run_id, db_path=_eval_db_path())
        if run is None:
            raise HTTPException(status_code=404, detail="eval run not found")
        return {"run": run, "annotations": eval_store.list_annotations(run_id, db_path=_eval_db_path())}

    @app.post("/api/eval/run")
    def api_eval_run(body: EvalRunRequest):
        from tindalos import eval_store, history as history_mod
        from tindalos.eval_.runner import run_eval

        campaign_id = body.campaign_id
        if not campaign_id:
            recent = history_mod.list_campaigns()
            if not recent:
                raise HTTPException(status_code=404, detail="无历史剧本，请先生成一个再评测")
            campaign_id = recent[0]["id"]
        rec = history_mod.get_campaign(campaign_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="campaign not found")
        # history 快照是 dict——runner 内 _ensure_campaign_model 会归一化供属性访问
        trace = run_eval(
            rec["snapshot"],
            module_id=body.module_id or rec["module_id"],
            max_usd=body.max_usd,
            db_path=_eval_db_path(),
            params={"revision":rec["revision"]},
        )
        trace["params"]["revision"] = rec["revision"]
        return {"trace": trace}

    @app.post("/api/eval/runs/{run_id}/reviews", status_code=201)
    def api_human_review(run_id: str, body: HumanReviewRequest):
        from tindalos import eval_store
        run = eval_store.get_run(run_id, db_path=_eval_db_path())
        if not run:
            raise HTTPException(404, "eval run not found")
        if run.get("params", {}).get("revision") != body.revision:
            raise HTTPException(409, "review must refer to the evaluated revision")
        # A later human observation is append-only. It never rewrites the
        # completed automatic verdict or claims an independent blind review.
        eval_store.append_annotations(run_id, [{"layer":"L5",
            "subject_ref":f"campaign:{run['campaign_id']}:revision:{body.revision}",
            "score":1.0 if body.usable else 0.0, "explanation":body.notes,
            "evidence_refs":[{"revision":body.revision,"origin":"local_kp_review"}]}],
            db_path=_eval_db_path())
        return {"ok":True,"review_saved":True,"automatic_verdict":run["verdict"]}

    # ---------------------------------------------------------- 记忆（P1 记忆核心）

    @app.get("/api/memories/{campaign_id}")
    def api_memories(campaign_id: str):
        """四类记忆 + 最近游玩状态（设计文档 §4.5 / P1 ticket 01）。

        campaign 无记忆时返回空四类与 play_status=None，不 404（记忆是增强层，
        与生成/历史解耦，本地文件不存在也应诚实返回空而不是报错）。
        """
        from tindalos import memory_entries as me

        db = _data_dir() / "store" / "memory_entries.sqlite"
        memories: dict[str, list[dict[str, Any]]] = {}
        for mt in me.MEMORY_TYPES:
            items: list[dict[str, Any]] = []
            for r in me.list_entries(campaign_id, mt, db):
                refs = r.get("ref_ids")
                if isinstance(refs, str):
                    try:
                        refs = json.loads(refs)
                    except json.JSONDecodeError:
                        refs = None
                items.append(
                    {
                        "id": r["id"],
                        "memory_type": r["memory_type"],
                        "content": r["content"],
                        "importance": r["importance"],
                        "subject_key": r["subject_key"],
                        "source_episode": r["source_episode"],
                        "ref_ids": refs,
                        "status": r["status"],
                        "origin":r.get("origin","planned"),
                        "created_at": r["created_at"],
                    }
                )
            memories[mt] = items
        return {
            "campaign_id": campaign_id,
            "status": "ok",
            "play_status": me.current_play_status(campaign_id, db),
            "briefing": me.briefing(campaign_id, db),
            "memories": memories,
        }

    # ---------------------------------------------------------- 游玩会话（P2 回叙采集）

    @app.post("/api/sessions/{campaign_id}")
    def api_record_session(campaign_id: str, body: RecordSessionRequest):
        """KP 回叙 → 记一场游玩会话 + 轻量整合（设计文档 §3.3 P2 / ticket 01）。

        summary 必填非空（空 → 400）；db_path 与 /api/memories 同款
        memory_entries.sqlite；record_session 内部走确定性 consolidate（零 LLM）。
        返回该会话结果（session_index / play_status / conflicts / created_at + consolidate）。
        """
        from tindalos import memory_entries as me

        summary = body.summary.strip()
        if not summary:
            raise HTTPException(status_code=400, detail="summary 必填非空字符串")
        for fact in body.facts:
            if not fact.get("subject_key") or not fact.get("content"):
                raise HTTPException(422,"事实需要 subject_key 和 content")
        db = _data_dir() / "store" / "memory_entries.sqlite"
        try:
            result = me.record_session(campaign_id, summary, db_path=db,
                play_status=body.play_status, conflicts=body.conflicts,
                request_key=body.request_key, facts=body.facts)
        except me.SessionConflict as exc:
            raise HTTPException(409, str(exc))
        sessions = me.list_play_sessions(campaign_id, db)
        row = next((s for s in sessions if s["id"] == result["session_id"]), None)
        if row is not None:
            payload = _session_payload(row)
        else:  # 防御：极端情况下读不回刚写的行 → 用请求参数兜底
            payload = {
                "session_id": result["session_id"],
                "session_index": result["session_index"],
                "summary": summary,
                "play_status": result["play_status"],
                "conflicts": body.conflicts,
                "created_at": None,
            }
        payload["consolidate"] = result["consolidate"]
        return payload

    @app.get("/api/sessions/{campaign_id}")
    def api_sessions(campaign_id: str):
        """某 campaign 的全部游玩会话 + 最近 play_status（设计文档 §3.3 P2 / ticket 01）。

        无会话时返回空列表与 current_play_status=None，不 404（与 /api/memories 同哲学）。
        """
        from tindalos import memory_entries as me

        db = _data_dir() / "store" / "memory_entries.sqlite"
        sessions = [_session_payload(s) for s in me.list_play_sessions(campaign_id, db)]
        return {
            "campaign_id": campaign_id,
            "current_play_status": me.current_play_status(campaign_id, db),
            "sessions": sessions,
        }

    # ---------------------------------------------------------- 静态托管

    _files = _data_dir()
    _files.mkdir(parents=True, exist_ok=True)
    app.mount("/files", _ModuleImagesFiles(directory=str(_files)), name="files")

    dist = Path(os.environ.get("TINDALOS_FRONTEND_DIST", "frontend/dist"))
    if dist.exists() and (dist / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=str(dist / "assets")), name="assets")

        @app.get("/", include_in_schema=False)
        async def index() -> FileResponse:
            return FileResponse(dist / "index.html")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_fallback(full_path: str) -> FileResponse:
            # hash 路由无需路径回退，但兜底 favicon/深层直链与 /api 404 之后的任意 GET
            if (
                full_path.startswith("api/")
                or full_path.startswith("files/")
                or full_path.startswith("assets/")
            ):
                raise HTTPException(status_code=404, detail="not found")
            target = (dist / full_path).resolve()
            if not target.is_relative_to(dist.resolve()):
                raise HTTPException(404, "not found")
            if target.is_file():
                return FileResponse(target)
            return FileResponse(dist / "index.html")

    return app


# 模块级共享状态（create_app 内的路由闭包引用它）
_state = _State()


def run(host: str = "127.0.0.1", port: int = 8347) -> None:
    """启动 uvicorn 站点服务（`tindalos web` 入口）。"""
    import uvicorn

    uvicorn.run(create_app(), host=host, port=port, log_level="info")


__all__ = ["create_app", "run", "VERSION"]
