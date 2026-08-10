"""内容生成器层：Generator 协议 + 确定性离线实现 + Ollama OpenAI 兼容实现。

- Generator：generate_acts / generate_npcs / generate_scene 三方法协议；
- DeterministicGenerator：模板 + 由 premise 派生的固定种子伪随机，内容简单但结构完整，
  零网络零 LLM，可离线端到端测试（全程确定性：同一 premise → 同一输出）；
- OllamaGenerator：requests 调 settings.ollama_base_url 的 /chat/completions（OpenAI 兼容），
  仅 settings.llm_enabled 时经 build_generator() 构造；generate_scene 附带 function calling
  声明（tools）；超时/网络/5xx/429 自动重试（TINDALOS_LLM_MAX_RETRIES），回复不可解析或
  字段规整失败时回退 DeterministicGenerator 并发出 UserWarning（降级不阻塞管线）。
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import time
import warnings
from typing import Any, Protocol, runtime_checkable

import requests.exceptions as _rexc

from tindalos.config import Settings, get_settings

# ---------------------------------------------------------------- 内容池（确定性模板素材）

_ACT_TITLES = ["初现端倪", "深入漩涡", "终局揭示"]
_ROMANS = ["I", "II", "III", "IV", "V", "VI"]
_ARCHETYPES = ["调查员", "神秘学者", "警探", "占卜师", "药剂师", "记者"]
_NAMES = ["林晚", "沈一舟", "陈默", "白鹭", "顾长歌", "苏晚晴", "赵远山", "江望舒"]
_TRAITS = ["谨慎多疑", "求知欲强", "沉默寡言", "古道热肠", "唯利是图", "神经质", "冷静理性", "冲动鲁莽"]
_PLACES = ["旧图书馆", "废弃码头", "古董店", "档案馆", "城郊古宅", "雾气弥漫的小巷"]
_TIMES = ["傍晚", "深夜", "清晨", "午夜"]
_EVENT_KINDS = ("entry", "trigger", "outcome")


# ---------------------------------------------------------------- 协议


@runtime_checkable
class Generator(Protocol):
    """生成器协议：KP 管线只依赖这三个方法。"""

    def generate_acts(self, premise: str, n_acts: int) -> list[dict[str, Any]]:
        """由前提拟定 n_acts 幕结构草案（每项含 id/roman/title/summary/npc_ids/scene_titles）。"""
        ...

    def generate_npcs(self, premise: str, n: int) -> list[dict[str, Any]]:
        """由前提生成 n 个 NPC 草案（每项含 id/name/archetype/personality/description/acts_roles）。"""
        ...

    def generate_scene(self, act_title: str, premise: str, npc_ids: list[str]) -> dict[str, Any]:
        """为一幕生成一个场景（含 setting/events 事件序列/npc_ids）。"""
        ...


def _seed_for(salt: str, explicit: str | None = None) -> str:
    if explicit is not None:
        return explicit
    return hashlib.sha256(salt.encode("utf-8")).hexdigest()


def _clip(text: str, limit: int = 16) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= limit else text[:limit] + "…"


# ---------------------------------------------------------------- 确定性实现


class DeterministicGenerator:
    """模板 + 固定种子伪随机：结构完整、内容简单、同一 premise 输出可复现。

    seed 参数缺省时由 premise（+ 方法盐）派生，保证「同一输入 → 同一输出」。
    """

    def __init__(self, seed: str | None = None) -> None:
        self._seed = seed

    def _rng(self, salt: str) -> random.Random:
        return random.Random(_seed_for(salt, self._seed))

    # -- 幕结构草案 -------------------------------------------------
    def generate_acts(self, premise: str, n_acts: int) -> list[dict[str, Any]]:
        rng = self._rng(premise + "::acts")
        n_acts = max(1, int(n_acts))
        acts: list[dict[str, Any]] = []
        for i in range(n_acts):
            roman = _ROMANS[i % len(_ROMANS)]
            title = _ACT_TITLES[i % len(_ACT_TITLES)]
            acts.append(
                {
                    "id": f"act-{i + 1}",
                    "roman": roman,
                    "title": f"第{roman}幕·{title}",
                    "summary": f"围绕「{_clip(premise)}」的第{roman}幕：{title}。",
                    "npc_ids": [],
                    "scene_titles": [f"场景·{title}其一", f"场景·{title}其二"],
                }
            )
        return acts

    # -- NPC 草案 ---------------------------------------------------
    def generate_npcs(self, premise: str, n: int) -> list[dict[str, Any]]:
        rng = self._rng(premise + "::npcs")
        n = max(1, int(n))
        names = rng.sample(_NAMES, k=min(n, len(_NAMES)))
        while len(names) < n:  # n 超出素材池时循环补齐
            names.extend(rng.sample(_NAMES, k=min(n - len(names), len(_NAMES))))
        npcs: list[dict[str, Any]] = []
        for i in range(n):
            name = names[i]
            archetype = rng.choice(_ARCHETYPES)
            npcs.append(
                {
                    "id": f"npc-{i + 1}",
                    "name": name,
                    "archetype": archetype,
                    "personality": rng.sample(_TRAITS, k=2),
                    "description": f"{name}是{archetype}，与「{_clip(premise)}」的传闻有所牵连。",
                    "acts_roles": {},
                }
            )
        return npcs

    # -- 场景草案 ---------------------------------------------------
    def generate_scene(self, act_title: str, premise: str, npc_ids: list[str]) -> dict[str, Any]:
        rng = self._rng(act_title + "::" + premise)
        setting = {"time": rng.choice(_TIMES), "place": rng.choice(_PLACES)}
        scene_id = "scene-" + hashlib.sha1((act_title + premise).encode("utf-8")).hexdigest()[:6]
        ev_ids = {k: f"{scene_id}-{k}" for k in _EVENT_KINDS}
        events = [
            {
                "id": ev_ids["entry"],
                "title": "抵达现场",
                "kind": "entry",
                "description": f"众人于{setting['time']}抵达{setting['place']}，气氛异样。",
                "conditions": [],
                "next_event_ids": [ev_ids["trigger"]],
            },
            {
                "id": ev_ids["trigger"],
                "title": "发现线索",
                "kind": "trigger",
                "description": "场景中发现与传闻相符的痕迹，指向更深的秘密。",
                "conditions": [],
                "next_event_ids": [ev_ids["outcome"]],
            },
            {
                "id": ev_ids["outcome"],
                "title": "事态升级",
                "kind": "outcome",
                "description": "真相浮出水面，局面失控，幕在此收束。",
                "conditions": [],
                "next_event_ids": [],
            },
        ]
        return {
            "id": scene_id,
            "title": f"{_clip(act_title, 12)}·场景",
            "setting": setting,
            "events": events,
            "npc_ids": list(npc_ids),
        }


# ---------------------------------------------------------------- Ollama 实现

_SCENE_TOOL = {
    "type": "function",
    "function": {
        "name": "generate_scene",
        "description": "生成一幕剧本中的一个场景（含 setting 与 entry/trigger/outcome 事件序列）。",
        "parameters": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "场景 id"},
                "title": {"type": "string", "description": "场景标题"},
                "setting": {
                    "type": "object",
                    "properties": {"time": {"type": "string"}, "place": {"type": "string"}},
                },
                "events": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "title": {"type": "string"},
                            "kind": {"type": "string", "enum": list(_EVENT_KINDS)},
                            "description": {"type": "string"},
                            "conditions": {"type": "array", "items": {"type": "string"}},
                            "next_event_ids": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                },
                "npc_ids": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["id", "title", "setting", "events", "npc_ids"],
        },
    },
}


def _parse_json(content: str) -> Any:
    """从模型回复提取 JSON：容忍围栏、未闭合围栏、前后缀文字与顶层数组。

    解析顺序：
    1. 剥离 ```json / ```（含无语言围栏、未闭合围栏）；
    2. 完整解析（数组/对象均可）；
    3. 失败则截取首个 {…} 或 […] 跨度兜底（容忍说明文字包裹）。
    """
    if not content:
        raise ValueError("空回复")
    text = content.strip()
    fence = re.search(r"```(?:[a-zA-Z]*)?\s*(.*?)(?:```|\Z)", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start, end = text.find(open_ch), text.rfind(close_ch)
        if start == -1 or end <= start:
            continue
        try:
            return json.loads(text[start : end + 1])
        except (json.JSONDecodeError, ValueError):
            continue
    raise ValueError(f"无法从模型回复解析 JSON: {content[:120]!r}")


def _is_retryable(exc: BaseException) -> bool:
    """请求异常是否值得重试：超时/连接错误、HTTP 5xx 与 429；4xx 属配置/模型问题不重试。"""
    if isinstance(exc, (_rexc.Timeout, _rexc.ConnectionError)):
        return True
    if isinstance(exc, _rexc.HTTPError):
        status = exc.response.status_code if exc.response is not None else None
        return status is None or status >= 500 or status == 429
    return False


class OllamaGenerator:
    """OpenAI 兼容 /chat/completions 客户端（Ollama）。

    仅 settings.llm_enabled 时经 build_generator() 构造；网络失败或回复不可解析时
    回退 DeterministicGenerator 并发出 UserWarning，保证管线在任何情况下可收敛。

    timeout / max_retries / retry_delay 可经构造参数覆盖，缺省取 settings.llm_timeout
    / llm_max_retries（环境变量 TINDALOS_LLM_TIMEOUT / TINDALOS_LLM_MAX_RETRIES）。
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        timeout: float | None = None,
        max_retries: int | None = None,
        retry_delay: float = 1.0,
    ) -> None:
        self.settings = settings or get_settings()
        self.timeout = float(timeout if timeout is not None else self.settings.llm_timeout)
        self.max_retries = int(max_retries if max_retries is not None else self.settings.llm_max_retries)
        self.retry_delay = float(retry_delay)
        self._fallback = DeterministicGenerator()
        self._module_context = ""
        self._module_title = ""
        try:
            import requests  # 延迟导入：离线环境不强制依赖

            self._requests = requests
        except ImportError:  # pragma: no cover - requests 缺失时退化为确定性
            self._requests = None

    # -- 模组上下文注入（loop 迭代改进，2026-08-11） ---------------
    def set_module_context(self, text: str, title: str = "") -> None:
        """注入模组全文背景（截断至 settings.llm_context_chars）。剧本生成将基于模组真实内容。"""
        self._module_title = title or ""
        limit = getattr(self.settings, "llm_context_chars", 12000) or 0
        self._module_context = text[:limit] if limit and text else (text if not limit else "")

    def _ctx(self) -> str:
        """prompt 追加的模组背景块（未注入则空串）；在 _chat 层统一应用，覆盖所有生成 prompt。"""
        if not self._module_context:
            return ""
        head = f"模组《{self._module_title}》背景资料（生成内容须忠实于以下背景）：\n" if self._module_title else "模组背景资料（生成内容须忠实于以下背景）：\n"
        return "\n\n" + head + self._module_context

    # -- 底层对话 ------------------------------------------------
    def _chat(self, prompt: str, *, tools: list[dict] | None = None) -> str:
        if self._requests is None:
            raise RuntimeError("requests 不可用")
        url = f"{self.settings.ollama_base_url.rstrip('/')}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.settings.api_key:  # 云端 API（DeepSeek/Kimi/GLM/Qwen 等）需 Bearer 头
            headers["Authorization"] = f"Bearer {self.settings.api_key}"
        payload: dict[str, Any] = {
            "model": self.settings.model,
            "messages": [{"role": "user", "content": prompt + self._ctx()}],
            "temperature": 0.7,
        }
        if tools:
            payload["tools"] = tools
        last_exc: BaseException | None = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = self._requests.post(url, json=payload, headers=headers, timeout=self.timeout)
                resp.raise_for_status()
                data = resp.json()
                message = data["choices"][0]["message"]
                tool_calls = message.get("tool_calls")
                if tool_calls:  # function calling 分支：提取首个工具调用的 arguments
                    fn = tool_calls[0].get("function", {})
                    args = fn.get("arguments")
                    if isinstance(args, str) and args.strip():
                        return args
                    if isinstance(args, dict):
                        return json.dumps(args, ensure_ascii=False)
                    raise ValueError("tool_calls 缺少 function.arguments（无法解析）")
                return message.get("content", "")
            except Exception as e:  # noqa: BLE001 - 网络/解析异常统一按可重试性处理
                last_exc = e
                if attempt >= self.max_retries or not _is_retryable(e):
                    raise
                time.sleep(self.retry_delay)
        raise last_exc  # pragma: no cover - 循环必然 raise 或 return

    def _generate(self, prompt: str, tools: list[dict] | None = None) -> tuple[Any, BaseException | None]:
        """返回 (解析结果, 异常)。异常透传根因（不吞栈）：调用方在告警中带类型与消息。"""
        try:
            return _parse_json(self._chat(prompt, tools=tools)), None
        except Exception as e:  # noqa: BLE001 - 网络/解析异常统一透传，由告警表达
            return {}, e

    @staticmethod
    def _exc_detail(exc: BaseException) -> str:
        """根因消息 + HTTP 状态码（如 HTTPStatusError (HTTP 410)）。"""
        code = ""
        resp = getattr(exc, "response", None)
        if resp is not None and getattr(resp, "status_code", None):
            code = f" (HTTP {resp.status_code})"
        return f"{type(exc).__name__}{code}: {exc}"

    # -- 失败告警 + 草案规整 --------------------------------------
    def _warn_fallback(self, what: str) -> None:
        warnings.warn(
            f"OllamaGenerator 生成失败（{what}），回退 DeterministicGenerator",
            UserWarning,
            stacklevel=3,
        )

    @staticmethod
    def _norm_acts(items: Any) -> list[dict]:
        """规整 LLM 幕草案：要求 title；补 id/roman/summary/scene_titles/npc_ids 缺省；
        所有标量统一转 str（真实模型会返回 int id 等，避免下游 re.search 崩溃）。"""
        out: list[dict] = []
        for i, it in enumerate(items or []):
            if not isinstance(it, dict):
                continue
            it = dict(it)
            title = str(it.get("title") or "").strip()
            if not title:
                continue
            if not str(it.get("id") or "").strip():
                it["id"] = "act-" + hashlib.sha1(title.encode("utf-8")).hexdigest()[:6]
            elif str(it["id"]).strip().isdigit():
                # 裸数字 id（如 1）会与 npc/scene id 跨实体冲突 → 加类型前缀
                it["id"] = f"act-{str(it['id']).strip()}"
            else:
                # 规范 id 格式：LLM 可能返回 act_1 / Act1 / actOne 等任意格式，
                # 统一为 act-{i+1} 保证 id 契约（前端/测试/引用依赖 act-N-scene-N-ev-N）
                it["id"] = f"act-{i + 1}"
            it["title"] = title
            it["roman"] = str(it.get("roman") or _ROMANS[len(out) % len(_ROMANS)])
            it["summary"] = str(it.get("summary") or "")
            it["npc_ids"] = [str(x) for x in (it.get("npc_ids") or []) if isinstance(x, str)]
            titles = it.get("scene_titles")
            it["scene_titles"] = (
                [str(x) for x in titles if isinstance(x, str)]
                if isinstance(titles, list)
                else ([str(titles)] if titles else [f"场景·{title}其一", f"场景·{title}其二"])
            )
            out.append(it)
        return out

    @staticmethod
    def _norm_npcs(items: Any) -> list[dict]:
        """规整 LLM NPC 草案：要求 name；补 id/archetype/personality/description/acts_roles；
        id/name 统一转 str；acts_roles 非 dict（如列表）与 personality 非 list[str] 时规整，
        避免 pydantic 校验失败中断整条管线。"""
        out: list[dict] = []
        for i, it in enumerate(items or []):
            if not isinstance(it, dict):
                continue
            it = dict(it)
            name = str(it.get("name") or "").strip()
            if not name:
                continue
            if not str(it.get("id") or "").strip():
                it["id"] = f"npc-{i + 1}"
            elif str(it["id"]).strip().isdigit():
                # 裸数字 id（如 1）会与 act/scene id 跨实体冲突 → 加类型前缀
                it["id"] = f"npc-{str(it['id']).strip()}"
            else:
                it["id"] = str(it["id"]).strip()
            it["name"] = name
            # 标量规整：archetype/description 可能是列表（如 ['Ghost']）或非 str → 统一转 str
            # （回归：真实模组 LLM 实验发现列表 archetype 会击穿 pydantic 校验中断整条管线）
            arch = it.get("archetype")
            it["archetype"] = (
                "、".join(str(x).strip() for x in arch if str(x).strip())
                if isinstance(arch, (list, tuple))
                else (str(arch).strip() if arch is not None else "调查员")
            ) or "调查员"
            desc = it.get("description")
            it["description"] = (
                "、".join(str(x).strip() for x in desc if str(x).strip())
                if isinstance(desc, (list, tuple))
                else (str(desc).strip() if desc is not None else "")
            )
            pers = it.get("personality")
            it["personality"] = (
                [str(p) for p in pers if isinstance(p, str)]
                if isinstance(pers, list)
                else ([str(pers)] if pers else [])
            )
            roles = it.get("acts_roles")
            it["acts_roles"] = (
                {str(k): str(v) for k, v in roles.items() if isinstance(v, (str, int))}
                if isinstance(roles, dict)
                else {}
            )
            out.append(it)
        return out

    @staticmethod
    def _norm_scene(doc: Any) -> dict | None:
        """规整 LLM 场景草案：要求 title 且至少 1 个事件（下游需 events[-1] 取 outcome）；
        事件 kind 非法时按位置兜底 entry/trigger/outcome。"""
        if not isinstance(doc, dict):
            return None
        if not str(doc.get("title") or "").strip():
            return None
        out = dict(doc)
        events: list[dict] = []
        for i, ev in enumerate(doc.get("events") or []):
            if not isinstance(ev, dict):
                continue
            ev = dict(ev)
            kind = str(ev.get("kind") or "").strip().lower()
            if kind not in _EVENT_KINDS:
                kind = _EVENT_KINDS[min(i, len(_EVENT_KINDS) - 1)]
            ev.setdefault("title", f"事件{i + 1}")
            ev["kind"] = kind
            events.append(ev)
        if not events:
            return None
        out["events"] = events
        out["npc_ids"] = [str(x) for x in (doc.get("npc_ids") or []) if isinstance(x, str)]
        return out

    # -- 协议实现（失败一律告警并回退确定性） ----------------------
    def generate_acts(self, premise: str, n_acts: int) -> list[dict[str, Any]]:
        prompt = f"你是 TRPG 主控（KP）。根据前提拟定 {n_acts} 幕结构草案，输出 JSON 数组：每项含 id/roman/title/summary/npc_ids/scene_titles。\n前提：{premise}"
        doc, exc = self._generate(prompt)
        items = doc if isinstance(doc, list) else (doc.get("acts", []) if isinstance(doc, dict) else [])
        acts = self._norm_acts(items)
        if not acts:
            detail = "无有效 JSON" if exc is None else self._exc_detail(exc)
            self._warn_fallback(f"generate_acts：{detail}")
            return self._fallback.generate_acts(premise, n_acts)
        return acts[:n_acts]

    def generate_npcs(self, premise: str, n: int) -> list[dict[str, Any]]:
        prompt = f"你是 NPC 生成器。根据前提生成 {n} 个 NPC，输出 JSON 数组：每项含 id/name/archetype/personality(数组)/description/acts_roles。\n前提：{premise}"
        doc, exc = self._generate(prompt)
        items = doc if isinstance(doc, list) else (doc.get("npcs", []) if isinstance(doc, dict) else [])
        npcs = self._norm_npcs(items)
        if not npcs:
            detail = "无有效 JSON" if exc is None else self._exc_detail(exc)
            self._warn_fallback(f"generate_npcs：{detail}")
            return self._fallback.generate_npcs(premise, n)
        return npcs[:n]

    def generate_scene(self, act_title: str, premise: str, npc_ids: list[str]) -> dict[str, Any]:
        prompt = (
            f"为「{act_title}」生成一个场景：调用 generate_scene 工具，npc_ids={list(npc_ids)}，"
            f"事件序列须为 entry→trigger→outcome。\n前提：{premise}"
        )
        doc, exc = self._generate(prompt, tools=[_SCENE_TOOL])
        norm = self._norm_scene(doc)
        if norm is not None:
            return norm
        detail = "无有效场景 JSON" if exc is None else self._exc_detail(exc)
        self._warn_fallback(f"generate_scene：{detail}")
        return self._fallback.generate_scene(act_title, premise, npc_ids)


# ---------------------------------------------------------------- 工厂


def build_generator(settings: Settings | None = None) -> Generator:
    """按开关构造生成器：llm_enabled 时 Ollama，否则确定性（默认路径）。"""
    settings = settings or get_settings()
    if settings.llm_enabled:
        return OllamaGenerator(settings)
    return DeterministicGenerator()


__all__ = [
    "Generator",
    "DeterministicGenerator",
    "OllamaGenerator",
    "build_generator",
]
