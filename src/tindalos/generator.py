"""内容生成器层：Generator 协议 + 确定性离线实现 + Ollama OpenAI 兼容实现。

- Generator：generate_acts / generate_npcs / generate_scene 三方法协议；
- DeterministicGenerator：模板 + 由 premise 派生的固定种子伪随机，内容简单但结构完整，
  零网络零 LLM，可离线端到端测试（全程确定性：同一 premise → 同一输出）；
- OllamaGenerator：requests 调 settings.ollama_base_url 的 /chat/completions（OpenAI 兼容），
  仅 settings.llm_enabled 时经 build_generator() 构造；generate_scene 附带 function calling
  声明（tools），解析失败回退确定性实现。
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from typing import Any, Protocol, runtime_checkable

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
    """从模型回复提取 JSON：容忍 ```json 围栏与前后缀文本。"""
    if not content:
        raise ValueError("空回复")
    text = content.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        text = text[start : end + 1]
    return json.loads(text)


class OllamaGenerator:
    """OpenAI 兼容 /chat/completions 客户端（Ollama）。

    仅 settings.llm_enabled 时经 build_generator() 构造；网络失败或回复不可解析时
    回退 DeterministicGenerator，保证管线在任何情况下可收敛。
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._fallback = DeterministicGenerator()
        try:
            import requests  # 延迟导入：离线环境不强制依赖

            self._requests = requests
        except ImportError:  # pragma: no cover - requests 缺失时退化为确定性
            self._requests = None

    # -- 底层对话 ------------------------------------------------
    def _chat(self, prompt: str, *, tools: list[dict] | None = None) -> str:
        if self._requests is None:
            raise RuntimeError("requests 不可用")
        url = f"{self.settings.ollama_base_url.rstrip('/')}/chat/completions"
        payload: dict[str, Any] = {
            "model": self.settings.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7,
        }
        if tools:
            payload["tools"] = tools
        resp = self._requests.post(url, json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        message = data["choices"][0]["message"]
        if message.get("tool_calls"):  # function calling 分支
            return json.dumps({"tool_calls": message["tool_calls"]})
        return message.get("content", "")

    def _generate(self, prompt: str, tools: list[dict] | None = None) -> dict:
        try:
            return _parse_json(self._chat(prompt, tools=tools))
        except Exception:
            return {}

    # -- 协议实现（失败一律回退确定性） ----------------------------
    def generate_acts(self, premise: str, n_acts: int) -> list[dict[str, Any]]:
        prompt = f"你是 TRPG 主控（KP）。根据前提拟定 {n_acts} 幕结构草案，输出 JSON 数组：每项含 id/roman/title/summary/npc_ids/scene_titles。\n前提：{premise}"
        doc = self._generate(prompt)
        acts = doc if isinstance(doc, list) else doc.get("acts", []) if isinstance(doc, dict) else []
        if not isinstance(acts, list) or not acts:
            return self._fallback.generate_acts(premise, n_acts)
        return [a for a in acts if isinstance(a, dict)][:n_acts]

    def generate_npcs(self, premise: str, n: int) -> list[dict[str, Any]]:
        prompt = f"你是 NPC 生成器。根据前提生成 {n} 个 NPC，输出 JSON 数组：每项含 id/name/archetype/personality(数组)/description/acts_roles。\n前提：{premise}"
        doc = self._generate(prompt)
        npcs = doc if isinstance(doc, list) else doc.get("npcs", []) if isinstance(doc, dict) else []
        if not isinstance(npcs, list) or not npcs:
            return self._fallback.generate_npcs(premise, n)
        return [n for n in npcs if isinstance(n, dict)][:n]

    def generate_scene(self, act_title: str, premise: str, npc_ids: list[str]) -> dict[str, Any]:
        prompt = (
            f"为「{act_title}」生成一个场景：调用 generate_scene 工具，npc_ids={list(npc_ids)}，"
            f"事件序列须为 entry→trigger→outcome。\n前提：{premise}"
        )
        doc = self._generate(prompt, tools=[_SCENE_TOOL])
        if isinstance(doc, dict) and doc.get("id"):
            return doc
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
