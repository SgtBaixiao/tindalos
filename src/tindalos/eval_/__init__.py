"""评估模块：4 维 rubric + 确定性检查 + 可选 LLM 裁判 + 四类归因报告。

对外导出：rubric（维度与检查清单）/ deterministic（run_deterministic）/
judge（LLMJudge）/ report（eval_report）四个子模块及主要符号。
"""

from tindalos.eval_ import deterministic, judge, report, rubric
from tindalos.eval_.deterministic import run_deterministic
from tindalos.eval_.judge import JUDGE_PROMPT, LLMJudge, parse_judge_json
from tindalos.eval_.report import eval_report
from tindalos.eval_.rubric import DETERMINISTIC_CHECKS, DIMENSIONS, RUBRIC

__all__ = [
    "rubric",
    "deterministic",
    "judge",
    "report",
    "RUBRIC",
    "DIMENSIONS",
    "DETERMINISTIC_CHECKS",
    "run_deterministic",
    "LLMJudge",
    "parse_judge_json",
    "JUDGE_PROMPT",
    "eval_report",
]
