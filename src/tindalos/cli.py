"""Typer CLI（task t7-cli）：`tindalos` 八命令族。

入口 `app` 经 pyproject `[project.scripts] tindalos = "tindalos.cli:app"` 暴露。

八命令：
  ① generate  模组文本（.md/.json）→ campaign JSON + 同目录 notes.md 备团笔记
              （默认 DeterministicGenerator；`--llm` 且 settings.llm_enabled 时用 OllamaGenerator）
  ② notes     campaign JSON → 重生成备团笔记 markdown
  ③ eval      campaign JSON → 4 维分数表 + 归因 + 建议（JSON 到 stdout 或 --out）
  ④ evolve    campaign JSON → 自进化循环（rounds 轮 eval→修复→复评，打印 loop_log）
  ⑤ kg        campaign JSON → 实体关系查询 / 多跳路径（--entity [--path-to]）
  ⑥ serve     HTTP API 服务：POST /api/generate（SSE）· GET /api/campaigns/<id> · POST /api/regenerate
  ⑦ regenerate campaign JSON → 单节点重生成（scene/event/npc/clue），其余保持不动
              （--node 必填；校验失败回滚原样；--llm 且 settings.llm_enabled 时用 OllamaGenerator）
  ⑧ memories  campaign id（或 campaign JSON 路径）→ 列出跨会话记忆事实
              （NPC 印象 / 关键事件 / 世界状态摘要；读 settings.store_dir 落盘 store）

契约：成功退出码 0；输入文件缺失 / JSON 解析失败 / 实体未知 → 非 0 退出码，错误打到 stderr。
全程确定性（零网络零 LLM）：--llm/--judge 仅在 TINDALOS_LLM_ENABLED=1 时生效，否则回退。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import typer
from pydantic import ValidationError

from tindalos.config import get_settings
from tindalos.eval_.deterministic import run_deterministic
from tindalos.eval_.judge import LLMJudge
from tindalos.eval_.report import eval_report
from tindalos.generator import DeterministicGenerator, Generator, OllamaGenerator
from tindalos.kg import build_from_campaign
from tindalos.models import Campaign, construct_loose_campaign as _construct_loose
from tindalos.pipeline import compose_campaign, extract_premise, render_notes

app = typer.Typer(help="Tindalos 克苏鲁 TRPG 备团 CLI", no_args_is_help=True)

_DEFAULT_N_ACTS = 2
_DEFAULT_N_NPCS = 3


# ---------------------------------------------------------------- 通用 IO


def _write_json(doc: Any, path: Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_campaign_json(path: Path) -> dict:
    """读取 campaign JSON → dict；文件缺失 / 解析失败 → 抛 ValueError / FileNotFoundError。"""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"campaign 文件不存在: {p}")
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ValueError(f"campaign JSON 解析失败: {e}") from e
    if not isinstance(doc, dict):
        raise ValueError("campaign JSON 必须是对象")
    return doc


def _load_campaign_model(path: Path) -> Campaign:
    """campaign JSON → Campaign 模型；校验失败时宽松构造（与 t5/t6 同哲学），不中断下游命令。"""
    raw = _load_campaign_json(path)
    try:
        return Campaign.model_validate(raw)
    except ValidationError:
        return _construct_loose(raw)


def _resolve_generator(use_llm: bool) -> Generator:
    """按开关构造生成器：--llm 且 settings.llm_enabled → Ollama，否则确定性（默认路径）。"""
    settings = get_settings()
    if use_llm:
        if not settings.llm_enabled:
            typer.echo("警告：--llm 请求但 TINDALOS_LLM_ENABLED != '1'，回退确定性生成器", err=True)
            return DeterministicGenerator()
        return OllamaGenerator(settings)
    return DeterministicGenerator()


# ---------------------------------------------------------------- generate 内部


def _premise_from_json(doc: dict) -> str:
    for key in ("premise", "前提", "title", "name"):
        v = doc.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()[:200]
    return ""


def _read_module(module_file: Path) -> tuple[str, str]:
    """读取模组文件 → (module_text, premise)。支持 .md 文本 与 .json（含 premise/title 键）。"""
    p = Path(module_file)
    if not p.exists():
        raise FileNotFoundError(f"模组文件不存在: {p}")
    if p.suffix.lower() == ".json":
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise ValueError(f"模组 JSON 解析失败: {e}") from e
        if not isinstance(doc, dict):
            raise ValueError("模组 JSON 必须是对象（含 premise/前提/title 键）")
        premise = _premise_from_json(doc)
        if not premise:
            raise ValueError("模组 JSON 缺少 premise/前提/title 键")
        return premise, premise
    text = p.read_text(encoding="utf-8")
    return text, extract_premise(text)


def _generate_campaign(
    generator: Generator,
    module_text: str,
    premise: str,
    n_acts: int = _DEFAULT_N_ACTS,
    n_npcs: int = _DEFAULT_N_NPCS,
) -> Campaign:
    """生成器 → 完整 Campaign：幕（重编号场景/事件 id，保证全局唯一）+ NPC。

    线索/关系/Campaign 装配与备团笔记统一委托 pipeline.compose_campaign（t10 去重复）。
    """
    if hasattr(generator, "set_module_context"):  # LLM 生成基于模组全文（loop 迭代改进）
        generator.set_module_context(module_text, title=module_text.strip().splitlines()[0][:40] if module_text.strip() else "")
    act_drafts = generator.generate_acts(premise, n_acts)
    npcs = {npc["id"]: npc for npc in generator.generate_npcs(premise, n_npcs)}
    npc_ids = list(npcs.keys())
    acts: list[dict] = []
    for i, draft in enumerate(act_drafts):
        draft = dict(draft)
        draft["npc_ids"] = npc_ids[i::len(act_drafts)] if act_drafts else []
        scenes: list[dict] = []
        for idx, scene_title in enumerate(draft.get("scene_titles") or ["场景一"]):
            scene = dict(generator.generate_scene(f"{draft['title']}·{scene_title}", premise, draft["npc_ids"]))
            scene["id"] = f"{draft['id']}-scene-{idx + 1}"
            scene["title"] = scene_title
            events = scene.get("events") or []
            renumbered = []
            for j, ev in enumerate(events):
                ev = dict(ev)
                ev["id"] = f"{draft['id']}-scene-{idx + 1}-ev-{j + 1}"
                ev["next_event_ids"] = [
                    f"{draft['id']}-scene-{idx + 1}-ev-{k + 1}" for k in range(j + 1, len(events))
                ]
                renumbered.append(ev)
            scene["events"] = renumbered
            scenes.append(scene)
        draft["scenes"] = scenes
        acts.append(draft)
    return compose_campaign(module_text, premise, acts, npcs)["campaign"]


# ---------------------------------------------------------------- 展示


def _resolve_campaign_id(campaign: str) -> str:
    """memories 参数解析：campaign JSON 路径 → 其 id；否则按 campaign id 直用。"""
    p = Path(campaign)
    if p.exists() and p.is_file():
        doc = _load_campaign_json(p)
        cid = doc.get("id")
        if not cid:
            raise ValueError(f"campaign JSON 缺少 id: {p}")
        return str(cid)
    return campaign


def _print_eval_report(report: dict, to_stderr: bool = False) -> None:
    """4 维分数表 + 归因 + 建议（人类可读）。"""
    echo = lambda s: typer.echo(s, err=to_stderr)  # noqa: E731
    echo(f"评测报告：{report.get('campaign_title') or report.get('campaign_id')}（total {report['total']}，judge={report['judge']}）")
    for dim, entry in report["table"].items():
        echo(f"[{dim}] score={entry['score']}")
        for ev in entry.get("evidence") or []:
            echo(f"    · {ev}")
        if entry.get("suggestion"):
            echo(f"    建议：{entry['suggestion']}")
    echo("归因：")
    for info in report["attribution"].values():
        echo(f"  {info['label']}（{info['count']}）")
        for item in info["items"]:
            text = item.get("text") or f"{item.get('name', '')}: {item.get('evidence', '')}"
            echo(f"    · {text}")


def _print_loop_log(loop_log: list[dict]) -> None:
    typer.echo("自进化日志（loop_log）：")
    for entry in loop_log:
        typer.echo(
            f"  round {entry['round']}: {entry['score_before']} -> {entry['score_after']} "
            f"(delta {entry['delta']})"
        )
        for a in entry.get("applied") or []:
            typer.echo(f"    [应用] {a}")
        for f in entry.get("failed") or []:
            typer.echo(f"    [失败] {f}")
    if not loop_log:
        typer.echo("  （无轮次：rounds=0 仅基线评估）")


# ---------------------------------------------------------------- ① generate


@app.command(name="generate")
def generate(
    module_file: Path = typer.Argument(..., help="模组文件：.md 文本或 .json（含 premise/前提/title 键）"),
    out: Path = typer.Option("campaign.json", "--out", "-o", help="campaign JSON 输出路径"),
    llm: bool = typer.Option(False, "--llm", help="使用 LLM 生成（需 TINDALOS_LLM_ENABLED=1）"),
) -> None:
    """① 生成 campaign：模组文本 → 分幕剧本 + 同目录 notes.md 备团笔记。"""
    try:
        generator = _resolve_generator(llm)
        module_text, premise = _read_module(module_file)
        campaign = _generate_campaign(generator, module_text, premise)
        _write_json(campaign.model_dump(mode="json"), out)
        notes_path = Path(out).parent / "notes.md"
        notes_text = render_notes(campaign)
        # 跨会话记忆：写入持久化 store（settings.store_dir 可写时落盘 SqliteStore）；
        # 笔记记忆节由 render_notes 内置派生（memory.render_memory_section），此处不再重复追加
        try:
            from tindalos.memory import build_store, write_memory_facts

            write_memory_facts(build_store(), campaign)
        except Exception as mem_err:  # noqa: BLE001 - 记忆写入失败不阻塞生成主流程
            typer.echo(f"注意：记忆写入失败（{mem_err}）", err=True)
        notes_path.write_text(notes_text, encoding="utf-8")
        typer.echo(f"已生成 campaign：{out}")
        typer.echo(f"已生成备团笔记：{notes_path}")
    except (OSError, ValueError) as e:
        typer.echo(f"错误：{e}", err=True)
        raise typer.Exit(code=1) from e


# ---------------------------------------------------------------- ② notes


@app.command(name="notes")
def notes(
    campaign_file: Path = typer.Argument(..., help="campaign JSON 路径"),
    out: Path = typer.Option("notes.md", "--out", "-o", help="备团笔记 markdown 输出路径"),
) -> None:
    """② 重生成备团笔记：campaign JSON → notes.md。"""
    try:
        campaign = _load_campaign_model(campaign_file)
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(render_notes(campaign), encoding="utf-8")
        typer.echo(f"已生成备团笔记：{out}")
    except (OSError, ValueError) as e:
        typer.echo(f"错误：{e}", err=True)
        raise typer.Exit(code=1) from e


# ---------------------------------------------------------------- ③ eval


@app.command(name="eval")
def eval_command(
    campaign_file: Path = typer.Argument(..., help="campaign JSON 路径"),
    judge: bool = typer.Option(False, "--judge", help="启用 LLM 裁判（需 TINDALOS_LLM_ENABLED=1）"),
    out: Path = typer.Option(None, "--out", "-o", help="评测报告 JSON 输出路径（缺省打印到 stdout）"),
) -> None:
    """③ 评测：4 维分数表 + 归因 + 建议。JSON 到 stdout（无 --out）或 --out 文件。"""
    try:
        campaign = _load_campaign_model(campaign_file)
        world = build_from_campaign(campaign)
        det = run_deterministic(campaign, world)
        judge_obj = LLMJudge() if judge else None
        if judge_obj is not None and not judge_obj.enabled:
            # 对齐 generate --llm：LLM 未启用时向 stderr 打降级提示
            typer.echo("警告：--judge 请求但 TINDALOS_LLM_ENABLED != '1'，回退确定性评测", err=True)
        judge_res = judge_obj.evaluate(campaign, world, det) if judge_obj else None
        report = eval_report(campaign, world, det, judge_res)
        if out is not None:
            _print_eval_report(report)
            _write_json(report, out)
            typer.echo(f"评测报告已写入：{out}")
        else:
            # stdout 纯 JSON（机器可解析）；人类摘要到 stderr
            _print_eval_report(report, to_stderr=True)
            typer.echo(json.dumps(report, ensure_ascii=False, indent=2))
    except (OSError, ValueError) as e:
        typer.echo(f"错误：{e}", err=True)
        raise typer.Exit(code=1) from e


# ---------------------------------------------------------------- ④ evolve


@app.command(name="evolve")
def evolve_command(
    campaign_file: Path = typer.Argument(..., help="campaign JSON 路径"),
    rounds: int = typer.Option(2, "--rounds", "-r", help="自进化轮数上限（0=不进化，仅基线评估）"),
    out: Path = typer.Option("campaign.evolved.json", "--out", "-o", help="进化结果 JSON 输出路径"),
) -> None:
    """④ 自进化：eval → 确定性修复 → 重建 → 复评，循环 rounds 轮；打印 loop_log。"""
    try:
        from tindalos.evolve import evolve

        raw = _load_campaign_json(campaign_file)
        result = evolve(
            raw,
            None,
            DeterministicGenerator(seed="cli-evolve"),
            run_deterministic,
            rounds=max(0, rounds),
            out_path=str(out),
        )
        _print_loop_log(result["loop_log"])
        typer.echo(f"进化结果已写入：{out}")
    except (OSError, ValueError) as e:
        typer.echo(f"错误：{e}", err=True)
        raise typer.Exit(code=1) from e


# ---------------------------------------------------------------- ⑤ kg


@app.command(name="kg")
def kg_command(
    campaign_file: Path = typer.Argument(..., help="campaign JSON 路径"),
    entity: str = typer.Option(..., "--entity", "-e", help="要查询的实体 id"),
    path_to: Optional[str] = typer.Option(None, "--path-to", "-p", help="目标实体 id：输出多跳路径"),
) -> None:
    """⑤ 世界知识图谱查询：--entity 实体关系；加 --path-to 输出多跳路径。"""
    try:
        campaign = _load_campaign_model(campaign_file)
        world = build_from_campaign(campaign)
        if not world.has_entity(entity):
            raise ValueError(f"实体不存在于世界知识图谱: {entity}")
        if path_to is not None:
            paths = world.path(entity, path_to)
            if not paths:
                typer.echo(f"实体 {entity} → {path_to} 之间没有可达路径。")
            else:
                typer.echo(f"实体 {entity} → {path_to} 的路径（{len(paths)} 条）：")
                for p in paths:
                    typer.echo("  " + " -> ".join(p))
        else:
            rels = world.relations_of(entity)
            if not rels:
                typer.echo(f"实体 {entity} 暂无关系。")
            else:
                typer.echo(f"实体 {entity} 的关系（{len(rels)} 条）：")
                for r in rels:
                    typer.echo(f"- {r['source']} --[{r['type']}]--> {r['target']}（{r.get('label', '')}）")
    except (OSError, ValueError) as e:
        typer.echo(f"错误：{e}", err=True)
        raise typer.Exit(code=1) from e


# ---------------------------------------------------------------- ⑦ regenerate


@app.command(name="regenerate")
def regenerate_command(
    campaign_file: Path = typer.Argument(..., help="campaign JSON 路径"),
    node: str = typer.Option(..., "--node", "-n", help="要重生成的节点 id（scene-*/event-*/npc-*/clue-*）"),
    llm: bool = typer.Option(False, "--llm", help="使用 LLM 生成（需 TINDALOS_LLM_ENABLED=1）"),
    out: Path = typer.Option("campaign.regenerated.json", "--out", "-o", help="重生成结果 JSON 输出路径"),
) -> None:
    """⑦ 重生成节点：scene/event/npc/clue 单节点重产，其余保持不动；校验失败回滚原样。"""
    try:
        from tindalos.regenerate import regenerate_node

        raw = _load_campaign_json(campaign_file)
        generator = _resolve_generator(llm)
        campaign, applied = regenerate_node(raw, node, generator)
        if not applied:
            typer.echo("警告：重生成校验失败，已回滚为原样（输出即输入副本）", err=True)
        _write_json(campaign.model_dump(mode="json"), out)
        for a in applied:
            typer.echo(f"[应用] {a}")
        typer.echo(f"重生成结果已写入：{out}")
    except (OSError, ValueError) as e:
        typer.echo(f"错误：{e}", err=True)
        raise typer.Exit(code=1) from e


# ---------------------------------------------------------------- ⑧ memories


@app.command(name="memories")
def memories_command(
    campaign: str = typer.Argument(..., help="campaign id（或 campaign JSON 路径）"),
) -> None:
    """⑧ 跨会话记忆：列出该 campaign 的已存记忆事实（NPC 印象 / 关键事件 / 世界状态摘要）。

    读 settings.store_dir 落盘的 store（缺省 data/store/memory.sqlite）；
    无事实时输出「暂无」提示（退出码 0）。
    """
    try:
        from tindalos.memory import build_store, list_memories

        store = build_store(get_settings())
        cid = _resolve_campaign_id(campaign)
        typer.echo(list_memories(store, cid))
    except (OSError, ValueError) as e:
        typer.echo(f"错误：{e}", err=True)
        raise typer.Exit(code=1) from e


# ---------------------------------------------------------------- ⑥ serve


@app.command(name="serve")
def serve_command(
    host: str = typer.Option("127.0.0.1", "--host", help="HTTP 监听地址（默认 127.0.0.1）"),
    port: int = typer.Option(8347, "--port", "-p", help="HTTP 监听端口（默认 8347）"),
) -> None:
    """⑥ HTTP API 服务：SSE 流式生成 + campaign 内存缓存 + 节点重生成（前端依赖契约）。"""
    try:
        from tindalos.serve import serve as serve_api

        serve_api(host=host, port=port)
    except OSError as e:
        typer.echo(f"错误：{e}", err=True)
        raise typer.Exit(code=1) from e


__all__ = ["app", "render_notes"]


if __name__ == "__main__":
    app()
