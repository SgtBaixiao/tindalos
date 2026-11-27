"""Campaign node saving and compatible generation entry points."""
from __future__ import annotations

import copy

from tindalos import history
from tindalos.models import Campaign
from tindalos.generation_jobs import prepare_generation, append_continuation, remap_source_ref


def save_node(campaign_id: str, node_id: str, patch: dict, expected_revision: int) -> dict:
    rec = history.get_campaign(campaign_id)
    if not rec:
        raise LookupError("campaign not found")
    doc = copy.deepcopy(rec["snapshot"])
    from tindalos.regenerate import _strip_kind_prefix
    ident = _strip_kind_prefix(node_id)
    target = None
    kind = None
    for act in doc.get("acts", []):
        if act["id"] == ident:
            target, kind = act, "act"
        for scene in act.get("scenes", []):
            if scene["id"] == ident:
                target, kind = scene, "scene"
            for event in scene.get("events", []):
                if event["id"] == ident:
                    target, kind = event, "event"
    if ident in doc.get("npcs", {}):
        target, kind = doc["npcs"][ident], "npc"
    for clue in doc.get("clues", []):
        if clue["id"] == ident:
            target, kind = clue, "clue"
    if target is None:
        raise LookupError("node not found")
    allowed = {
        "act":{"title","summary"}, "scene":{"title","setting"},
        "event":{"title","description","conditions"},
        "npc":{"name","description","personality","goals","knowledge","resources"},
        "clue":{"name","description","conclusions","acquisition_conditions","alternatives","source_refs","origin"},
    }[kind]
    if patch.keys() - allowed:
        raise ValueError("unsupported node fields: " + ", ".join(sorted(patch.keys()-allowed)))
    target.update(patch)
    model = Campaign.model_validate(doc)
    return history.register_campaign(campaign_id, model.title, model.rules, model.model_dump(mode="json"),
        expected_revision=expected_revision, reason=f"edit:{kind}:{ident}")
