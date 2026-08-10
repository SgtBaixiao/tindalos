"""tindalos.cli Typer CLI 冒烟测试（task t7-cli）。

覆盖（对齐验收）：
1. generate：临时模组文件（.md/.json）→ campaign.json 存在且可 load，同目录 notes.md 备团笔记；
   --llm 未启用时回退确定性生成器（零网络）；同输入两次生成结果一致（确定性）；
2. eval：退出 0 + stdout 含 structural（4 维表）；--out 落盘 report JSON；
3. evolve：--rounds 2 --out → evolved json 存在且含 campaign/loop_log/report；rounds=0 空 loop_log；
4. kg：--entity 查询输出非空；--path-to 多跳路径输出非空；未知实体退出码非 0；
5. notes：重生成备团笔记（含 备团笔记 / NPC 一览）；
6. 错误路径：缺失输入文件 → 非 0 退出码；无参数打印帮助。
"""
import json
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import pytest
from typer.testing import CliRunner

from tindalos.cli import app
from tindalos.models import Campaign

runner = CliRunner()

MODULE_MD = """# 雾港之夜

前提：海边小镇的失踪案背后藏着深海的低语。

调查员受邀调查码头仓库的离奇失踪事件，雾气中传来低语。
"""

MODULE_JSON = {"title": "雾港之夜", "premise": "海边小镇的失踪案背后藏着深海的低语。"}


@pytest.fixture()
def module_md(tmp_path):
    p = tmp_path / "module.md"
    p.write_text(MODULE_MD, encoding="utf-8")
    return p


@pytest.fixture()
def campaign_json(tmp_path, module_md):
    """generate 冒烟产物：campaign.json（复用为后续命令输入）。"""
    out = tmp_path / "campaign.json"
    r = runner.invoke(app, ["generate", str(module_md), "--out", str(out)])
    assert r.exit_code == 0, r.stdout
    assert out.exists()
    return out


# ---------------------------------------------------------------- generate

def test_generate_writes_campaign_and_notes(tmp_path, module_md):
    out = tmp_path / "campaign.json"
    r = runner.invoke(app, ["generate", str(module_md), "--out", str(out)])
    assert r.exit_code == 0, r.stdout
    assert out.exists() and out.stat().st_size > 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["premise"]
    assert data["acts"], "campaign 含幕"
    assert data["npcs"], "campaign 含 NPC"
    assert data["relations"], "campaign 含世界关系"
    # campaign JSON 可 load 回 Campaign 模型（结构合法）
    Campaign.model_validate(data)
    # 同目录 notes.md 备团笔记
    notes = out.parent / "notes.md"
    assert notes.exists()
    assert "备团笔记" in notes.read_text(encoding="utf-8")


def test_generate_from_json_module(tmp_path):
    p = tmp_path / "module.json"
    p.write_text(json.dumps(MODULE_JSON, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "c.json"
    r = runner.invoke(app, ["generate", str(p), "--out", str(out)])
    assert r.exit_code == 0, r.stdout
    assert json.loads(out.read_text(encoding="utf-8"))["premise"]


def test_generate_llm_flag_falls_back_when_disabled(tmp_path, module_md):
    # TINDALOS_LLM_ENABLED 未设置 → --llm 回退确定性生成器，零网络不失败
    out = tmp_path / "c.json"
    r = runner.invoke(app, ["generate", str(module_md), "--out", str(out), "--llm"])
    assert r.exit_code == 0, r.stdout
    assert out.exists()


def test_generate_deterministic_same_input_same_output(tmp_path, module_md):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    assert runner.invoke(app, ["generate", str(module_md), "--out", str(a)]).exit_code == 0
    assert runner.invoke(app, ["generate", str(module_md), "--out", str(b)]).exit_code == 0
    assert json.loads(a.read_text(encoding="utf-8")) == json.loads(b.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- eval

def test_eval_smoke(campaign_json):
    r = runner.invoke(app, ["eval", str(campaign_json)])
    assert r.exit_code == 0, r.stdout
    assert "structural" in r.stdout, "4 维表含 structural"
    assert "consistency" in r.stdout
    # stdout 为可解析 JSON（未指定 --out 时输出到 stdout）
    report = json.loads(r.stdout)
    assert set(report["table"]) == {"structural", "consistency", "depth", "playability"}
    assert "attribution" in report


def test_eval_out_writes_report(campaign_json, tmp_path):
    out = tmp_path / "eval.json"
    r = runner.invoke(app, ["eval", str(campaign_json), "--out", str(out)])
    assert r.exit_code == 0, r.stdout
    assert "structural" in r.stdout
    report = json.loads(out.read_text(encoding="utf-8"))
    assert set(report["table"]) == {"structural", "consistency", "depth", "playability"}
    assert report["total"] > 0


# ---------------------------------------------------------------- evolve

def test_evolve_smoke(campaign_json, tmp_path):
    out = tmp_path / "evolved.json"
    r = runner.invoke(app, ["evolve", str(campaign_json), "--rounds", "2", "--out", str(out)])
    assert r.exit_code == 0, r.stdout
    assert out.exists() and out.stat().st_size > 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["campaign"]["id"]
    assert "loop_log" in payload and "report" in payload and "pending" in payload
    assert any("round" in l for l in payload["loop_log"]) or payload["loop_log"] == []
    assert "自进化" in r.stdout or "round" in r.stdout


def test_evolve_rounds_zero_no_evolution(campaign_json, tmp_path):
    out = tmp_path / "e.json"
    r = runner.invoke(app, ["evolve", str(campaign_json), "--rounds", "0", "--out", str(out)])
    assert r.exit_code == 0, r.stdout
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["loop_log"] == []
    assert payload["report"]["total"] > 0


# ---------------------------------------------------------------- kg

def test_kg_entity_query_non_empty(campaign_json):
    r = runner.invoke(app, ["kg", str(campaign_json), "--entity", "npc-1"])
    assert r.exit_code == 0, r.stdout
    assert r.stdout.strip(), "kg --entity 查询输出非空"
    assert "npc-1" in r.stdout


def test_kg_path_query_non_empty(campaign_json):
    # 生成剧本含 npc-1 --[指向]--> clue-act-1 边 → 单跳路径非空
    r = runner.invoke(app, ["kg", str(campaign_json), "--entity", "npc-1", "--path-to", "clue-act-1"])
    assert r.exit_code == 0, r.stdout
    assert r.stdout.strip()


def test_kg_unknown_entity_fails(campaign_json):
    r = runner.invoke(app, ["kg", str(campaign_json), "--entity", "ghost-entity"])
    assert r.exit_code != 0


# ---------------------------------------------------------------- notes

def test_notes_command(campaign_json, tmp_path):
    out = tmp_path / "notes.md"
    r = runner.invoke(app, ["notes", str(campaign_json), "--out", str(out)])
    assert r.exit_code == 0, r.stdout
    text = out.read_text(encoding="utf-8")
    assert "备团笔记" in text
    assert "NPC 一览" in text


# ---------------------------------------------------------------- 错误路径

def test_missing_input_file_nonzero(tmp_path):
    r = runner.invoke(app, ["generate", str(tmp_path / "nope.md"), "--out", str(tmp_path / "c.json")])
    assert r.exit_code != 0
    r2 = runner.invoke(app, ["eval", str(tmp_path / "nope.json")])
    assert r2.exit_code != 0
    r3 = runner.invoke(app, ["kg", str(tmp_path / "nope.json"), "--entity", "npc-1"])
    assert r3.exit_code != 0


def test_help_shows_commands():
    r = runner.invoke(app, ["--help"])
    assert r.exit_code == 0
    assert "generate" in r.stdout and "evolve" in r.stdout
    # 无参数：click 8.3 no_args_is_help 打印帮助并以非 0 退出（非命令调用，符合「成功 0 / 失败非 0」契约）
    r2 = runner.invoke(app, [])
    assert r2.exit_code != 0
    assert "generate" in (r2.stdout or "") and "evolve" in (r2.stdout or "")
