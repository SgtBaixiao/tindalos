"""LLM 裁判（可选）：Ollama 兼容端点，rubric 提示词要求 4 维 JSON 输出。

启用条件：settings.llm_enabled（TINDALOS_LLM_ENABLED == '1'）。未启用、调用失败或
解析失败（键缺失/类型错）一律降级 judge='none'，评分退回确定性路径。

零外部依赖：默认 client 经 urllib 调用 OpenAI 兼容 /chat/completions；
LLMJudge(client=...) 可注入可调用对象（callable(messages)->str），便于测试与替换后端。
"""

from __future__ import annotations

import json
import re
import urllib.request
from typing import Any, Callable, Optional

from tindalos.config import Settings, get_settings
from tindalos.eval_.rubric import DIMENSIONS, RUBRIC
from tindalos.models import Campaign

_REQUIRED_KEYS = ("score", "comment", "suggestion")

PROMPT_TEMPLATE = """你是资深 TRPG 剧本评审（KP 视角）。对给定克苏鲁剧本按 4 维 rubric 各打 1-5 分。

rubric 锚点：
{anchors}

评分规则：
- structural 结构完整性 / consistency 一致性 / depth 深度 / playability 可玩性
- 每维必须且只能输出 score（1-5 整数）、comment（一句评语）、suggestion（一句改进建议）三个键
- 分数与确定性检查冲突时，以你对剧本的全局判断为准，但需在 comment 中说明理由

只输出一个 JSON 对象，不要任何额外文字：
{{"structural": {{"score": 4, "comment": "...", "suggestion": "..."}},
  "consistency": {{"score": 4, "comment": "...", "suggestion": "..."}},
  "depth": {{"score": 4, "comment": "...", "suggestion": "..."}},
  "playability": {{"score": 4, "comment": "...", "suggestion": "..."}}}}
"""


def build_judge_prompt() -> str:
    anchors = "\n".join(
        f"- {dim}: 1={RUBRIC[dim][1]}；5={RUBRIC[dim][5]}" for dim in DIMENSIONS
    )
    return PROMPT_TEMPLATE.format(anchors=anchors)


def parse_judge_json(text: Any) -> Optional[dict]:
    """解析裁判输出；任一维键缺失/类型错/分数越界 → 返回 None（调用方降级 judge='none'）。"""
    if not isinstance(text, str) or not text.strip():
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```$", "", cleaned)
    obj: Any = None
    try:
        obj = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        m = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
        except (json.JSONDecodeError, ValueError):
            return None
    if not isinstance(obj, dict):
        return None

    dims: dict[str, dict] = {}
    for dim in DIMENSIONS:
        d = obj.get(dim)
        if not isinstance(d, dict):
            return None
        if not all(k in d for k in _REQUIRED_KEYS):
            return None
        score = d["score"]
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            return None
        if not (1 <= float(score) <= 5):
            return None
        comment, suggestion = d["comment"], d["suggestion"]
        if not isinstance(comment, str) or not isinstance(suggestion, str):
            return None
        dims[dim] = {"score": int(score), "comment": comment, "suggestion": suggestion}
    return dims


def _default_client(settings: Settings) -> Callable[[list[dict]], str]:
    """OpenAI 兼容 /chat/completions 客户端（urllib，零依赖）。"""

    def call(messages: list[dict]) -> str:
        url = settings.ollama_base_url.rstrip("/") + "/chat/completions"
        body = json.dumps({
            "model": settings.model,
            "messages": messages,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if settings.api_key:  # 云端 API（DeepSeek/Kimi/GLM/Qwen 等）需 Bearer 头（2026-08-11 接入）
            headers["Authorization"] = f"Bearer {settings.api_key}"
        req = urllib.request.Request(url, data=body, headers=headers)
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 —— 用户配置的本地端点
            data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"]

    return call


def _build_user_payload(campaign: Any, world: Any, deterministic_result: dict) -> str:
    try:
        cdata = campaign.model_dump() if isinstance(campaign, Campaign) else campaign
    except Exception:  # noqa: BLE001 —— 兜底：无法序列化则转字符串
        cdata = str(campaign)
    wdata = world.to_json() if world is not None else {"nodes": [], "edges": []}
    return json.dumps(
        {"campaign": cdata, "world": wdata, "deterministic": deterministic_result},
        ensure_ascii=False,
    )


class LLMJudge:
    """可选 LLM 裁判。settings.llm_enabled=False 或解析失败时返回 judge='none'。

    evaluate(campaign, world, deterministic_result) -> {
        judge: "llm" | "none",
        reason?: str,          # none 时：llm_disabled / llm_error / parse_failed
        dims?: {dim: {score, comment, suggestion}},
        raw?: str,             # 原始模型输出（llm 或 parse_failed 时）
    }
    """

    def __init__(
        self,
        settings: Optional[Settings] = None,
        client: Optional[Callable[[list[dict]], str]] = None,
    ) -> None:
        self.settings = settings if settings is not None else get_settings()
        self._client = client

    @property
    def enabled(self) -> bool:
        return self.settings.llm_enabled

    def evaluate(self, campaign: Any, world: Any, deterministic_result: dict) -> dict:
        if not self.enabled:
            return {"judge": "none", "reason": "llm_disabled"}
        client = self._client or _default_client(self.settings)
        messages = [
            {"role": "system", "content": build_judge_prompt()},
            {
                "role": "user",
                "content": _build_user_payload(campaign, world, deterministic_result),
            },
        ]
        try:
            text = client(messages)
        except Exception as e:  # noqa: BLE001 —— 网络/后端异常统一降级
            return {"judge": "none", "reason": f"llm_error: {e}"}
        dims = parse_judge_json(text)
        if dims is None:
            return {"judge": "none", "reason": "parse_failed", "raw": text}
        return {"judge": "llm", "dims": dims, "raw": text}


JUDGE_PROMPT = build_judge_prompt()
