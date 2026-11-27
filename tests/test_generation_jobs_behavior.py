"""Generation assembly contracts observed through HTTP and the real job runtime."""
from __future__ import annotations

import copy
import json
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tindalos import history, web
from tindalos.models import Campaign


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("TINDALOS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TINDALOS_SITE_DB", str(tmp_path / "site.db"))
    monkeypatch.setenv("TINDALOS_FRONTEND_DIST", str(tmp_path / "absent"))
    monkeypatch.setenv("TINDALOS_JOB_WORKERS", "1")
    monkeypatch.setenv("TINDALOS_JOB_CAPACITY", "2")
    history.init_db()
    return tmp_path


def campaign(cid="legacy"):
    return Campaign.model_validate({
        "id": cid, "title": "Adopted story", "rules": "DND5e",
        "source_text": "Original legacy material", "premise": "The door is sealed.",
        "acts": [{"id": "act", "title": "Adopted act", "roman": "I",
                  "summary": "KP kept this plan", "npc_ids": ["npc"], "scenes": []}],
        "npcs": {"npc": {"id": "npc", "name": "Hope", "archetype": "witness",
                         "description": "KP adopted description", "goals": ["Find the key"],
                         "knowledge": ["The cellar is locked"], "resources": ["A lantern"]}},
        "clues": [], "relations": [],
    }).model_dump(mode="json")


def save(doc, **kwargs):
    return history.register_campaign(doc["id"], doc["title"], doc["rules"], doc, **kwargs)


def module(data):
    path = data / "modules" / "source" / "text.txt"
    path.parent.mkdir(parents=True)
    path.write_text("Full module material", encoding="utf-8")
    history.register_module("source", "source.pdf", "source-hash", 1, 20,
                            rules="COC7", meta={"images": [{"kind": "map", "caption": "Cellar"}]})
    return path


def events(client, jid):
    response = client.get(f"/api/jobs/{jid}/events")
    assert response.status_code == 200
    return [json.loads(frame.split("data:", 1)[1])
            for frame in response.text.split("\n\n") if "data:" in frame]


def submit(client, body, key=None):
    response = client.post("/api/jobs", json=body,
                           headers={"Idempotency-Key": key} if key else {})
    assert response.status_code == 202, response.text
    return response.json()["job"]["id"]


@pytest.mark.parametrize("endpoint", ["/api/jobs", "/api/generate"])
def test_same_key_survives_missing_source_and_changed_revision(data, monkeypatch, endpoint):
    path = module(data)
    save(campaign(), module_id="source")
    monkeypatch.setattr(web, "default_generate", lambda text, llm, emit: None)
    body = {"campaign_id": "legacy", "expected_revision": 1}
    with TestClient(web.create_app()) as client:
        first = client.post(endpoint, json=body, headers={"Idempotency-Key": "same"})
        jid = first.json()["job"]["id"] if endpoint == "/api/jobs" else first.headers["X-Job-ID"]
        events(client, jid)
        assert client.patch("/api/campaigns/legacy/nodes/act", json={
            "expected_revision": 1, "patch": {"summary": "Later adopted edit"},
        }).status_code == 200
        path.unlink()
        retry = client.post(endpoint, json=body, headers={"Idempotency-Key": "same"})
        assert retry.status_code == (202 if endpoint == "/api/jobs" else 200)
        assert (retry.json()["job"]["id"] if endpoint == "/api/jobs"
                else retry.headers["X-Job-ID"]) == jid
        # A changed raw identity must conflict even though preparation now fails.
        changed = client.post(endpoint, json={**body, "llm": True},
                              headers={"Idempotency-Key": "same"})
        assert changed.status_code == 409
        assert changed.json()["detail"] == "idempotency key reused with different input"


def test_module_precedence_and_raw_identity_survive_material_changes(data, monkeypatch):
    path = module(data)
    received = []

    def generate(text, llm, emit, *, module_images, campaign_id, rules, continuation,
                 persist_memory, adopted_npcs):
        received.append((text, llm, module_images, rules, continuation, persist_memory, adopted_npcs))
        return {**campaign(campaign_id), "rules": rules}

    with TestClient(web.create_app()) as client:
        # The production callable is replaced after lifespan startup.
        monkeypatch.setattr(web, "default_generate", generate)
        body = {"module_id": "source", "module_text": "Ignored input", "rules": "DND5e",
                "module_images": [{"caption": "Ignored image"}]}
        jid = submit(client, body, "raw-identity")
        assert events(client, jid)[-1]["status"] == "completed"
        assert received == [("Full module material", False, [{"kind": "map", "caption": "Cellar"}],
                             "COC7", "", False, {})]
        path.write_text("Changed source after admission", encoding="utf-8")
        assert submit(client, body, "raw-identity") == jid
        assert len(received) == 1


def test_continuation_executes_with_module_rules_but_keeps_adopted_campaign_rules(data, monkeypatch):
    module(data)  # COC7 material attached to a DND5e adopted campaign.
    save(campaign(), module_id="source")
    received = []

    def generate(text, llm, emit, *, campaign_id, rules, adopted_npcs):
        received.append((text, rules, copy.deepcopy(adopted_npcs)))
        doc = campaign(campaign_id)
        doc["rules"] = rules
        doc["acts"][0]["summary"] = "New module-rules preparation"
        doc["npcs"]["npc"]["description"] = "Unadopted replacement"
        return doc

    with TestClient(web.create_app()) as client:
        monkeypatch.setattr(web, "default_generate", generate)
        before = client.get("/api/campaigns/legacy").json()["campaign"]
        jid = submit(client, {"campaign_id": "legacy", "expected_revision": 1})
        terminal = events(client, jid)[-1]
        assert received == [("Full module material", "COC7", before["npcs"])]
        assert terminal["status"] == "completed", terminal
        assert terminal["revision"] == 2
        after = terminal["campaign"]
        assert after["rules"] == "DND5e"
        assert after["acts"][0] == before["acts"][0]
        assert after["acts"][0]["summary"] == "KP kept this plan"
        assert after["npcs"] == before["npcs"]
        assert after["npcs"]["npc"]["description"] == "KP adopted description"
        assert after["source_text"] == "Original legacy material"
        assert [act["id"] for act in after["acts"]] == ["act", "r2-act"]
        assert after["acts"][1]["summary"] == "New module-rules preparation"
        saved = client.get("/api/campaigns/legacy").json()
        assert saved["campaign"] == after
        assert saved["meta"]["rules"] == "DND5e"
        assert saved["meta"]["module_id"] == "source"
        assert saved["meta"]["revision"] == 2
        # Restore through HTTP to prove the first revision was not mutated.
        restored = client.post("/api/campaigns/legacy/restore/1", json={"expected_revision": 2})
        assert restored.status_code == 200
        assert restored.json()["revision"] == 3
        assert restored.json()["campaign"] == before
        assert restored.json()["campaign"]["rules"] == "DND5e"


@pytest.mark.parametrize("source_text, expected", [
    ("Legacy source retained", "Legacy source retained"),
    ("", "# Adopted story\nThe door is sealed."),
])
def test_legacy_material_rules_and_adopted_npcs_reach_generator(data, monkeypatch, source_text, expected):
    doc = campaign()
    doc["source_text"] = source_text
    save(doc)
    received = []

    def generate(text, llm, emit, *, rules, adopted_npcs, persist_memory):
        received.append((text, rules, adopted_npcs, persist_memory))
        return None

    monkeypatch.setattr(web, "default_generate", generate)
    with TestClient(web.create_app()) as client:
        adopted = client.get("/api/campaigns/legacy").json()["campaign"]
        jid = submit(client, {"campaign_id": "legacy", "rules": "COC7"})
        terminal = events(client, jid)[-1]
        assert received == [(expected, "DND5e", adopted["npcs"], False)]
        assert terminal["status"] == "failed"
        assert terminal["campaign"] is None
        assert client.get("/api/campaigns/legacy").json()["meta"]["revision"] == 1


def test_queued_continuation_freezes_adoption_but_reads_actual_records_at_execution(data, monkeypatch):
    save(campaign())
    started, release = threading.Event(), threading.Event()
    received = []

    def generate(text, llm, emit, *, campaign_id, rules, continuation, adopted_npcs):
        if text == "Block worker":
            started.set()
            assert release.wait(10)
            return None
        received.append((text, rules, continuation, copy.deepcopy(adopted_npcs)))
        return campaign(campaign_id)

    monkeypatch.setattr(web, "default_generate", generate)
    with TestClient(web.create_app()) as client:
        try:
            submit(client, {"module_text": "Block worker"})
            assert started.wait(5)
            adopted = client.get("/api/campaigns/legacy").json()["campaign"]
            jid = submit(client, {"campaign_id": "legacy"})
            assert client.post("/api/sessions/legacy", json={
                "summary": "Played after admission", "facts": [
                    {"subject_key": "Hope.life", "content": "alive"}],
            }).status_code == 200
            assert client.patch("/api/campaigns/legacy/nodes/npc", json={
                "expected_revision": 1, "patch": {"description": "Changed while queued"},
            }).status_code == 200
        finally:
            release.set()
        terminal = events(client, jid)[-1]
        text, rules, context, npcs = received[0]
        assert (text, rules, npcs) == ("Original legacy material", "DND5e", adopted["npcs"])
        assert "Played after admission" in context
        assert "Hope.life〔知情范围 kp〕：alive" in context
        assert context.endswith('\n【当前采用剧本提纲】\n{"title": "Adopted story", "acts": '
                                '[{"title": "Adopted act", "summary": "KP kept this plan"}]}')
        assert terminal["status"] == "failed"  # Runtime CAS rejects the stale adoption.
        assert client.get("/api/campaigns/legacy").json()["meta"]["revision"] == 2
        assert len(client.get("/api/campaigns/legacy/revisions").json()["revisions"]) == 2


def test_actual_and_outline_budgets_remain_separate_with_six_recent_sessions(data, monkeypatch):
    doc = campaign()
    doc["acts"][0]["summary"] = "O" * 30000
    save(doc)
    received = []

    def generate(text, llm, emit, *, continuation):
        received.append(continuation)
        return None

    monkeypatch.setattr(web, "default_generate", generate)
    with TestClient(web.create_app()) as client:
        for index in range(1, 9):
            assert client.post("/api/sessions/legacy", json={
                "summary": f"Record {index}", "facts": [],
            }).status_code == 200
        events(client, submit(client, {"campaign_id": "legacy"}))
        actual, outline = received.pop().split("\n【当前采用剧本提纲】\n")
        assert "Record 1" not in actual and "Record 2" not in actual
        assert all(f"Record {index}" in actual for index in range(3, 9))
        assert len(outline) == 24000
        assert outline.startswith('{"title": "Adopted story", "acts": [{"title": "Adopted act", "summary": "OOOO')
        assert outline.endswith("O" * 100)
        assert client.post("/api/sessions/legacy", json={
            "summary": "S" * 30000, "facts": [{"subject_key": "Hope.life", "content": "alive"}],
        }).status_code == 200
        events(client, submit(client, {"campaign_id": "legacy"}))
        actual, outline = received.pop().split("\n【当前采用剧本提纲】\n")
        assert "Hope.life〔知情范围 kp〕：alive" in actual
        assert actual.endswith("\n〔上下文已达预算，完整记录在场次档案〕")
        assert len(actual.removesuffix("\n〔上下文已达预算，完整记录在场次档案〕")) == 24000
        assert len(outline) == 24000


@pytest.mark.parametrize("body, status, detail", [
    ({}, 400, "module_text or module_id is required"),
    ({"module_id": "missing"}, 404, "module not found"),
    ({"campaign_id": "missing"}, 404, "campaign not found"),
    ({"module_id": "source"}, 400, "module text is missing"),
    ({"campaign_id": "legacy", "expected_revision": 2}, 409, "campaign changed before continuation"),
    ({"module_text": "Valid", "expected_revision": 0}, 422, None),
])
def test_submission_error_mapping_before_execution(data, monkeypatch, body, status, detail):
    path = module(data)
    path.unlink()
    save(campaign())
    monkeypatch.setattr(web, "default_generate", lambda text, llm, emit: pytest.fail("invalid job executed"))
    with TestClient(web.create_app()) as client:
        for endpoint in ("/api/jobs", "/api/generate"):
            response = client.post(endpoint, json=body)
            assert response.status_code == status
            if detail is not None:
                assert response.json()["detail"] == detail
            else:
                assert response.json()["detail"][0]["loc"] == ["body", "expected_revision"]


def test_capacity_error_preserves_retry_after_header(data, monkeypatch):
    started, release = threading.Event(), threading.Event()

    def generate(text, llm, emit):
        started.set()
        assert release.wait(10)
        return None

    monkeypatch.setattr(web, "default_generate", generate)
    with TestClient(web.create_app()) as client:
        try:
            submit(client, {"module_text": "First"})
            assert started.wait(5)
            submit(client, {"module_text": "Second"})
            for endpoint in ("/api/jobs", "/api/generate"):
                response = client.post(endpoint, json={"module_text": "Third"})
                assert response.status_code == 429
                assert response.headers["Retry-After"] == "1"
                assert response.json()["detail"] == "generation queue is full"
        finally:
            release.set()


def test_source_read_failure_is_server_error(data, monkeypatch):
    path = module(data)
    original = Path.read_text

    def read_text(self, *args, **kwargs):
        if self == path:
            raise OSError("injected storage failure")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    with TestClient(web.create_app(), raise_server_exceptions=False) as client:
        for endpoint in ("/api/jobs", "/api/generate"):
            response = client.post(endpoint, json={"module_id": "source"})
            assert response.status_code == 500
            assert response.text == "Internal Server Error"


def test_execution_error_is_a_failed_job_with_one_terminal_event(data, monkeypatch):
    def generate(text, llm, emit):
        raise ValueError("generation adapter failed")

    with TestClient(web.create_app()) as client:
        monkeypatch.setattr(web, "default_generate", generate)
        jid = submit(client, {"module_text": "Valid", "llm": True})
        stream = events(client, jid)
        assert stream[-2]["error"] == "generation adapter failed"
        assert stream[-1]["status"] == "failed"
        assert sum(bool(event.get("done")) for event in stream) == 1
        assert client.get(f"/api/jobs/{jid}").json()["job"]["error"] == "generation adapter failed"
        assert client.get("/api/campaigns").json()["campaigns"] == []


def test_short_generator_continuation_retains_adopted_content_and_remaps_references(data, monkeypatch):
    save(campaign())

    def generate(text, llm, emit, *, module_images=None):
        doc = campaign()
        doc["npcs"]["npc"]["description"] = "Unadopted replacement"
        doc["acts"][0]["scenes"] = [{
            "id": "scene", "title": "Cellar", "npc_ids": ["npc"],
            "events": [{"id": "event", "title": "Find key", "kind": "entry", "next_event_ids": []}],
        }]
        doc["clues"] = [{"id": "clue", "name": "Key", "linked_npc_ids": ["npc"],
                         "linked_event_ids": ["event"], "source_refs": [
                             "proposal:act:act", "proposal:scene:scene", "proposal:event:event",
                             "proposal:npc:npc", "proposal:clue:clue", "module:page:2"]}]
        return doc

    monkeypatch.setattr(web, "default_generate", generate)
    with TestClient(web.create_app()) as client:
        before = client.get("/api/campaigns/legacy").json()["campaign"]
        terminal = events(client, submit(client, {"campaign_id": "legacy"}))[-1]
        assert terminal["status"] == "completed", terminal
        assert terminal["revision"] == 2
        after = terminal["campaign"]
        assert after["acts"][0] == before["acts"][0]
        assert after["npcs"] == before["npcs"]
        assert after["acts"][0]["summary"] == "KP kept this plan"
        assert after["npcs"]["npc"]["description"] == "KP adopted description"
        assert after["npcs"]["npc"]["goals"] == ["Find the key"]
        assert [act["id"] for act in after["acts"]] == ["act", "r2-act"]
        assert after["acts"][1]["scenes"][0]["events"][0]["id"] == "r2-event"
        assert after["clues"][0]["source_refs"] == [
            "proposal:act:r2-act", "proposal:scene:r2-scene", "proposal:event:r2-event",
            "proposal:npc:npc", "proposal:clue:r2-clue", "module:page:2",
        ]
        assert history.get_revision("legacy", 1) == before
