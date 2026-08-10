"""t10-refactor 回归：pipeline 公共装配 API + cli 去重复 / OSError / judge 降级。

覆盖（对齐验收）：
1. pipeline 公共 API：compose_campaign / render_notes / extract_premise / title_from_text；
2. cli.py 无 compose/render/construct 私有副本（源码 grep 断言，验收 #2）；
3. cli 写文件/生成路径 OSError（IsADirectoryError 等）→ typer 错误 + exit 1（验收 #3）；
4. eval --judge 在 TINDALOS_LLM_ENABLED != '1' 时向 stderr 打降级提示（验收 #3）；
5. 行为不变：cli.generate 输出与 pipeline 装配结果逐字段一致（回归护栏）。
"""
import json
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.store.memory import InMemoryStore
from typer.testing import CliRunner

import tindalos.cli as cli_mod
import tindalos.config as config_mod
from tindalos.cli import app
from tindalos.generator import DeterministicGenerator
from tindalos.models import Campaign, construct_loose_campaign
from tindalos.pipeline import (
    build_pipeline,
    campaign_id_for,
    compose_campaign,
    extract_premise,
    render_notes,
    title_from_text,
)

runner = CliRunner()

MODULE_TEXT = """# 雾港之夜

前提：海边小镇的失踪案背后藏着深海的低语。

调查员受邀调查码头仓库的离奇失踪事件，雾气中传来低语。
"""

PREMISE = "海边小镇的失踪案背后藏着深海的低语。"


def _as_dict(obj):
    return obj.model_dump(mode="json") if hasattr(obj, "model_dump") else obj


def _run_pipeline(tmp_path):
    settings = config_mod.Settings(
        llm_enabled=False,
        checkpoint_dir=tmp_path / "checkpoints",
        store_dir=tmp_path / "store",
    )
    db = str(tmp_path / "cp.db").replace("\\", "/")
    with SqliteSaver.from_conn_string(db) as cp:
        app = build_pipeline(settings=settings, checkpointer=cp, store=InMemoryStore())
        return app.invoke(
            {"module_text": MODULE_TEXT},
            config={"configurable": {"thread_id": "t1"}},
        )


@pytest.fixture()
def module_md(tmp_path):
    p = tmp_path / "module.md"
    p.write_text(MODULE_TEXT, encoding="utf-8")
    return p


@pytest.fixture()
def campaign_json(tmp_path, module_md):
    out = tmp_path / "campaign.json"
    r = runner.invoke(app, ["generate", str(module_md), "--out", str(out)])
    assert r.exit_code == 0, r.stdout
    assert out.exists()
    return out


# ---------------------------------------------------------------- ① compose_campaign 公共 API


def test_compose_campaign_public_api_and_structure(tmp_path):
    """compose_campaign 是纯函数：acts/npcs 草案 → Campaign + clues/relations/world/notes。"""
    result = _run_pipeline(tmp_path)
    acts = [_as_dict(a) for a in result["acts"]]
    npcs = {k: _as_dict(v) for k, v in result["npcs"].items()}
    out = compose_campaign(MODULE_TEXT, result["premise"], acts, npcs)
    assert set(out) == {"campaign", "clues", "relations", "world", "notes_md"}
    campaign = out["campaign"]
    assert isinstance(campaign, Campaign)
    assert campaign.id == campaign_id_for(MODULE_TEXT)
    assert campaign.title == "模组《雾港之夜》"
    assert campaign.premise == PREMISE
    assert len(campaign.acts) == 2
    assert len(campaign.npcs) == 3
    assert campaign.clues, "每幕至少一条线索"
    assert campaign.relations, "含 指向/认识 关系边"
    assert out["notes_md"].startswith("# 备团笔记")
    assert isinstance(out["world"], dict) and out["world"]["nodes"]


def test_compose_campaign_deterministic(tmp_path):
    result = _run_pipeline(tmp_path)
    acts = [_as_dict(a) for a in result["acts"]]
    npcs = {k: _as_dict(v) for k, v in result["npcs"].items()}
    a = compose_campaign(MODULE_TEXT, result["premise"], acts, npcs)["campaign"].model_dump(mode="json")
    b = compose_campaign(MODULE_TEXT, result["premise"], acts, npcs)["campaign"].model_dump(mode="json")
    assert a == b


def test_compose_campaign_matches_pipeline_compose(tmp_path):
    """pipeline.compose 节点与 compose_campaign 输出一致（节点委托公共函数）。"""
    result = _run_pipeline(tmp_path)
    acts = [_as_dict(a) for a in result["acts"]]
    npcs = {k: _as_dict(v) for k, v in result["npcs"].items()}
    assembled = compose_campaign(MODULE_TEXT, result["premise"], acts, npcs)
    assert _as_dict(result["campaign"]) == assembled["campaign"].model_dump(mode="json")
    assert result["notes_md"] == assembled["notes_md"]


# ---------------------------------------------------------------- ② render_notes / 文本工具


def test_render_notes_lenient_on_loose_campaign():
    """宽松容错分支：setting/personality/acts_roles 缺失、非枚举 relation.type 仍可渲染。"""
    raw = {
        "id": "campaign-loose",
        "title": "宽松模组",
        "premise": "一段前提",
        "acts": [
            {
                "id": "act-1", "title": "第一幕", "roman": "I", "summary": "摘要",
                "scenes": [
                    {
                        "id": "act-1-scene-1", "title": "码头", "setting": None,
                        "events": [
                            {"id": "act-1-scene-1-ev-1", "title": "抵达", "kind": "entry",
                             "description": "雾中来客", "conditions": [], "next_event_ids": []}
                        ],
                        "npc_ids": ["npc-1"],
                    }
                ],
                "npc_ids": ["npc-1"],
            }
        ],
        "npcs": {
            "npc-1": {"id": "npc-1", "name": "老船长", "archetype": "向导",
                      "personality": None, "acts_roles": None, "description": ""},
        },
        "clues": [],
        "relations": [{"source": "npc-1", "target": "clue-x", "type": "LINKS",
                       "label": "关联", "valid_from": "1900-01-01"}],
    }
    campaign = construct_loose_campaign(raw)
    text = render_notes(campaign)
    assert text.startswith("# 备团笔记：宽松模组")
    assert "老船长" in text and "码头" in text and "雾中来客" in text
    assert "（无特质）" in text, "personality 缺失 → 无特质占位"
    assert "LINKS" in text, "非枚举 relation.type 原样输出（不崩）"


def test_render_notes_strict_campaign(tmp_path):
    result = _run_pipeline(tmp_path)
    text = render_notes(result["campaign"])
    assert text.startswith("# 备团笔记")
    assert "## 世界关系" in text
    assert any(act["title"] in text for act in _as_dict(result["campaign"])["acts"])


def test_extract_premise_public():
    assert extract_premise(MODULE_TEXT) == PREMISE
    assert extract_premise("标题行\n首段内容") == "标题行"
    assert extract_premise("") == "无名模组"


def test_title_from_text_public():
    assert title_from_text(MODULE_TEXT) == "雾港之夜"
    assert title_from_text("") == "未命名模组"


def test_campaign_id_for_stable():
    assert campaign_id_for(MODULE_TEXT).startswith("campaign-")
    assert campaign_id_for(MODULE_TEXT) == campaign_id_for(MODULE_TEXT)
    assert len(campaign_id_for(MODULE_TEXT)) == len("campaign-") + 8


# ---------------------------------------------------------------- ③ cli 无私有副本（验收 #2）


def test_cli_has_no_private_duplicates():
    src = Path(cli_mod.__file__).read_text(encoding="utf-8")
    forbidden = [
        "def _extract_premise",
        "def _title_from_text",
        "def _campaign_id_for",
        "def _build_clues_and_relations",
        "def _render_notes",
        "def render_notes",
        "def compose",
    ]
    for pat in forbidden:
        assert pat not in src, f"cli.py 不应含私有副本: {pat}"
    assert "from tindalos.pipeline import" in src, "公共装配 API 应来自 pipeline 导入"


def test_cli_generate_delegates_to_compose_campaign(monkeypatch):
    """行为不变护栏：cli._generate_campaign 的装配委托给 pipeline.compose_campaign（验收 #1）。

    注：cli 输出与 pipeline 完整结果在 NPC personality 上历来有意不同（pipeline 的
    npc_persona 节点会多注入一条人格特质，cli 不注入）——此处只锁定 cli 自己的装配路径。
    """
    captured: dict = {}
    sentinel = Campaign.model_construct(id="sentinel", title="x")

    def spy(module_text, premise, acts, npcs):
        captured["module_text"] = module_text
        captured["premise"] = premise
        captured["acts"] = acts
        captured["npcs"] = npcs
        return {"campaign": sentinel, "clues": [], "relations": [], "world": {}, "notes_md": ""}

    monkeypatch.setattr(cli_mod, "compose_campaign", spy)
    gen = DeterministicGenerator()
    out = cli_mod._generate_campaign(gen, MODULE_TEXT, PREMISE)
    assert out is sentinel, "_generate_campaign 应返回 compose_campaign 的 campaign"
    assert captured["module_text"] == MODULE_TEXT
    assert captured["premise"] == PREMISE
    # 装配输入：幕草案已含重编号场景 + 轮转 npc_ids + 全部 NPC
    assert [a["id"] for a in captured["acts"]] == ["act-1", "act-2"]
    assert all(a["scenes"] for a in captured["acts"])
    assert all(a["npc_ids"] for a in captured["acts"])
    assert set(captured["npcs"]) == {"npc-1", "npc-2", "npc-3"}
    # 场景/事件 id 全局唯一（重编号在 cli 内完成，装配委托公共函数）
    ids = [s["id"] for a in captured["acts"] for s in a["scenes"]]
    assert len(ids) == len(set(ids))


# ⑤ 行为不变：cli 与 pipeline 的差异仅限 npc_persona 注入（见上注释），
# 其余结构一致——cli 输出含与 pipeline 相同的 clues/relations 装配来源。
def test_cli_generate_structure_parallels_pipeline(tmp_path, module_md):
    out = tmp_path / "cli.json"
    r = runner.invoke(app, ["generate", str(module_md), "--out", str(out)])
    assert r.exit_code == 0, r.stdout
    doc = json.loads(out.read_text(encoding="utf-8"))
    # 与 pipeline 输出共享的装配结构：幕/场景/事件/NPC/线索/关系 数量一致
    pipe = _run_pipeline(tmp_path)
    pipe_doc = _as_dict(pipe["campaign"])
    assert [a["id"] for a in doc["acts"]] == [a["id"] for a in pipe_doc["acts"]]
    assert [c["id"] for c in doc["clues"]] == [c["id"] for c in pipe_doc["clues"]]
    assert doc["relations"] == pipe_doc["relations"]
    assert set(doc["npcs"]) == set(pipe_doc["npcs"])
    # 场景/事件重编号一致（全局唯一 id 体系）
    assert [s["id"] for a in doc["acts"] for s in a["scenes"]] == [
        s["id"] for a in pipe_doc["acts"] for s in a["scenes"]
    ]


# ---------------------------------------------------------------- ④ OSError 统一捕获（验收 #3）


def test_generate_out_is_directory_oserror(tmp_path, module_md):
    out_dir = tmp_path / "outdir"
    out_dir.mkdir()
    r = runner.invoke(app, ["generate", str(module_md), "--out", str(out_dir)])
    assert r.exit_code == 1
    assert "错误" in r.stderr


def test_generate_module_is_directory_oserror(tmp_path):
    d = tmp_path / "mod"
    d.mkdir()
    r = runner.invoke(app, ["generate", str(d), "--out", str(tmp_path / "c.json")])
    assert r.exit_code == 1
    assert "错误" in r.stderr


def test_notes_out_is_directory_oserror(campaign_json, tmp_path):
    out_dir = tmp_path / "outdir"
    out_dir.mkdir()
    r = runner.invoke(app, ["notes", str(campaign_json), "--out", str(out_dir)])
    assert r.exit_code == 1
    assert "错误" in r.stderr


def test_eval_out_unwritable_parent_oserror(campaign_json, tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    r = runner.invoke(app, ["eval", str(campaign_json), "--out", str(blocker / "eval.json")])
    assert r.exit_code == 1
    assert "错误" in r.stderr


def test_evolve_out_unwritable_parent_oserror(campaign_json, tmp_path):
    blocker = tmp_path / "blocker2"
    blocker.write_text("x", encoding="utf-8")
    r = runner.invoke(
        app, ["evolve", str(campaign_json), "--rounds", "1", "--out", str(blocker / "evolved.json")]
    )
    assert r.exit_code == 1
    assert "错误" in r.stderr


# ---------------------------------------------------------------- ⑤ eval --judge 降级提示（验收 #3）


def test_eval_judge_degradation_notice_stderr(campaign_json, monkeypatch):
    monkeypatch.delenv("TINDALOS_LLM_ENABLED", raising=False)
    config_mod._settings = None  # 强制 get_settings 重读环境
    try:
        r = runner.invoke(app, ["eval", str(campaign_json), "--judge"])
    finally:
        config_mod._settings = None
    assert r.exit_code == 0, r.stdout
    assert "警告：--judge 请求" in r.stderr, "LLM 未启用时应向 stderr 打降级提示"
    report = json.loads(r.stdout)  # stdout 仍为纯 JSON（机器可解析）
    assert report["judge"] == "none"
