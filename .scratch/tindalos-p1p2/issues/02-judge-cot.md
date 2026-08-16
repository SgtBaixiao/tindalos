# 02 L3 LLM judge 增强：CoT + evidence_refs + judge_model

Type: task
Status: claimed

## 目标

P1 #4。落在 `src/tindalos/eval_/judge.py` + `src/tindalos/eval_/runner.py` + `src/tindalos/eval_store.py`（仅必要时）+ `tests/test_eval_judge.py`（新文件）。**不要触碰 memory_entries.py / web.py / frontend**（其他 worker 并行拥有）。

## 规格（摘自 spec.md）

- `LLMJudge`：
  - Prompt 增强：要求先逐步推理（CoT），再输出 JSON——每维 `{score, comment, suggestion, evidence_refs: [源条目/字段引用]}`。保持 4 维 rubric（structural/consistency/depth/playability）语义不变。
  - `temperature=0`（设计文档明确；现有 0.2 需改）。
  - 结果记录 `judge_model`（取 settings.model，或新 env `TINDALOS_JUDGE_MODEL` 覆盖）；与生成同模型时结果标注 `self_preference_risk: true`（按设计文档 §3.5 L3 注）。
  - 输出解析健壮：键缺失/类型错/JSON 损坏 → 确定性降级（judge='none'），不抛异常。
- `runner.run_eval`：
  - L3 调用后把 judge_model / self_preference_risk 写进 trace（annotation 或 run params，先读 eval_store 现有结构再定最小改动）。
  - `estimate_usd` 计入 CoT 额外 token（如 +50%），预算门（EVAL_MAX_USD）语义不变。
- 保持现有 `tests/test_eval_trace.py` 全绿（trace 回放/短路/预算门用例不可破）。

## 验收

- FakeLLM 返回带 CoT + evidence_refs 的合法输出 → 解析出 per-dim evidence_refs + judge_model 记录 + temp=0 生效。
- FakeLLM 返回坏 JSON / 缺键 → judge='none' 确定性降级，无崩溃。
- 预算估算含 CoT 开销；超限跳 L3 的现有路径仍绿。
- 运行 `python -m pytest tests/test_eval_judge.py tests/test_eval_trace.py tests/test_eval.py -q` 全绿；再全量确认无回归。
