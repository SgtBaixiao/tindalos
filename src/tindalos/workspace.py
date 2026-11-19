"""Campaign operations shared by transports: source, revisions and adoption."""
from __future__ import annotations

import copy
from pathlib import Path

from tindalos import history
from tindalos.models import Campaign


def prepare_generation(request: dict) -> dict:
    request = dict(request)
    cid = request.get("campaign_id")
    if cid:
        rec = history.get_campaign(cid)
        if not rec:
            raise LookupError("campaign not found")
        request["expected_revision"] = request.get("expected_revision") or rec["revision"]
        if request["expected_revision"] != rec["revision"]:
            raise history.RevisionConflict("campaign changed before continuation")
        request["module_id"] = rec["module_id"]
        request["rules"] = rec["rules"]
        request["base_snapshot"] = rec["snapshot"]
    if request.get("module_id"):
        row = history.get_module(request["module_id"])
        if not row:
            raise LookupError("module not found")
        import os
        path = Path(os.environ.get("TINDALOS_DATA_DIR", "data")) / "modules" / row["id"] / "text.txt"
        if not path.exists():
            raise ValueError("module text is missing")
        request["module_text"] = path.read_text(encoding="utf-8")
        request["rules"] = row["rules"]
        request["module_images"] = (row.get("meta_json") or {}).get("images", [])
    if cid and not request.get("module_text"):
        # Legacy text-only campaigns keep their own material and adopted edits.
        rec = history.get_campaign(cid)
        request["module_text"] = rec['snapshot'].get('source_text') or f"# {rec['title']}\n{rec['snapshot'].get('premise', '')}"
    if not str(request.get("module_text", "")).strip():
        raise ValueError("module_text or module_id is required")
    return request


def append_continuation(current: dict, generated: dict, revision: int) -> dict:
    """Append preparation without replacing adopted edits or older acts."""
    result = copy.deepcopy(current)
    draft = copy.deepcopy(generated)
    prefix = f"r{revision + 1}-"
    npc_map = {ident: ident if ident in result["npcs"] else prefix + ident for ident in draft["npcs"]}
    ids = {clue["id"]: prefix + clue["id"] for clue in draft["clues"]}
    for act in draft["acts"]:
        ids[act["id"]] = prefix + act["id"]
        for scene in act["scenes"]:
            ids[scene["id"]] = prefix + scene["id"]
            for event in scene["events"]:
                ids[event["id"]] = prefix + event["id"]
    ids.update(npc_map)
    for act in draft["acts"]:
        act["id"] = ids[act["id"]]
        act["npc_ids"] = [ids[n] for n in act["npc_ids"]]
        for scene in act["scenes"]:
            scene["id"] = ids[scene["id"]]
            scene["npc_ids"] = [ids[n] for n in scene["npc_ids"]]
            for event in scene["events"]:
                event["id"] = ids[event["id"]]
                event["next_event_ids"] = [ids[n] for n in event["next_event_ids"]]
    for old_id, npc in draft["npcs"].items():
        npc["id"] = ids[old_id]
        npc["acts_roles"] = {ids[a]: value for a, value in npc.get("acts_roles", {}).items()}
        if old_id not in result["npcs"]:
            result["npcs"][npc["id"]] = npc
    for clue in draft["clues"]:
        clue["id"] = ids[clue["id"]]
        for field in ("linked_npc_ids", "linked_event_ids"):
            clue[field] = [ids[n] for n in clue[field]]
        clue['source_refs'] = [remap_source_ref(ref, ids) for ref in clue.get('source_refs', [])]
    for relation in draft["relations"]:
        relation["source"] = ids[relation["source"]]
        relation["target"] = ids[relation["target"]]
    result["acts"].extend(draft["acts"])
    result["clues"].extend(draft["clues"])
    result["relations"].extend(draft["relations"])
    result["generation"] = draft.get("generation", {})
    return Campaign.model_validate(result).model_dump(mode="json")


def remap_source_ref(ref: str, ids: dict[str, str]) -> str:
    """Internal proposal references contain a domain ID; external source refs remain opaque."""
    for kind in ('act', 'scene', 'event', 'npc', 'clue'):
        prefix = f'proposal:{kind}:'
        if ref.startswith(prefix):
            ident = ref[len(prefix):]
            if ident not in ids:
                raise ValueError(f'proposal source reference does not resolve: {ref}')
            return prefix + ids[ident]
    return ref


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
