"""评估模块：4 维 rubric + 确定性检查 + 可选 LLM 裁判 + 四类归因报告 + trace 编排。

对外导出：rubric（维度与检查清单）/ deterministic（run_deterministic）/
judge（LLMJudge）/ report（eval_report）/ runner（run_eval，六层编排 +
预算门 + trace 持久化）与 eval_store（append-only trace 存储）。
"""

from tindalos.eval_ import deterministic, judge, report, rubric, runner
from tindalos.eval_.deterministic import run_deterministic
from tindalos.eval_.judge import JUDGE_PROMPT, LLMJudge, parse_judge_json
from tindalos.eval_.report import eval_report
from tindalos.eval_.rubric import DETERMINISTIC_CHECKS, DIMENSIONS, RUBRIC
from tindalos.eval_.runner import estimate_usd, run_eval

__all__ = [
    "rubric",
    "deterministic",
    "judge",
    "report",
    "runner",
    "RUBRIC",
    "DIMENSIONS",
    "DETERMINISTIC_CHECKS",
    "run_deterministic",
    "LLMJudge",
    "parse_judge_json",
    "JUDGE_PROMPT",
    "eval_report",
    "run_eval",
    "estimate_usd",
]
