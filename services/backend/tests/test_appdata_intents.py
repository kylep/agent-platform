"""A page write is the one operation Kyle confirmed, even across retries."""
import copy
from datetime import timedelta

from agentplatform.appdata import lifecycle as L
from agentplatform.appdata import proposals as P
from agentplatform.appdata.access import Caller
from agentplatform.appdata.lifecycle import Actor
from agentplatform.appdata.models import AppDataPageIntent, AppDataPageReceipt
from agentplatform.appdata.records import load_app, update_record
from agentplatform.db import Principal, utcnow
from argon2 import PasswordHasher
from sqlalchemy import select

from .test_api_app_data import DONE, HABITS, OVERVIEW, PAI, RECENT, add, agent, build
from .test_relay_api import _key

PAGE = {**copy.deepcopy(OVERVIEW), "actions": [
    {"name": "log", "kind": "create", "collection": "habits",
     "editable_fields": ["habit", "day"], "presets": {"done": True}},
    {"name": "rename", "kind": "update", "collection": "habits",
     "editable_fields": ["habit"]},
    {"name": "remove", "kind": "delete", "collection": "habits"},
]}
PAGE["blocks"][1]["actions"] = ["log", "rename", "remove"]


async def setup_app(sf, name="intent"):
    app_id = await build(sf, name=name)
    async with sf() as s:
        await L.draft(s, PAI, app_id, request_id=f"actions-{name}", kind="page",
                      definition=PAGE)
    async with sf() as s:
        proposal = await P.propose(s, PAI, app_id, request_id=f"propose-{name}")
    async with sf() as s:
        await P.approve(s, Actor("kyle"), proposal["id"],
                        request_id=f"approve-{name}", digest=proposal["digest"])
    return app_id


def path(app_id):
    return f"/api/app-data/apps/{app_id}/pages/overview/intents"


async def confirm(client, app_id, template, *, record_id=None, values=None):
    r = await client.post(path(app_id), json={"template": template,
                          "record_id": record_id, "values": values or {}})
    assert r.status_code == 201, r.text
    return r.json()


async def dispatch(client, intent):
    return await client.post(f"/api/app-data/page-intents/{intent['intent_id']}/dispatch",
                             json={"digest": intent["digest"]})


async def test_create_confirmation_presets_and_single_receipt(admin_client, sf):
    app_id = await setup_app(sf)
    page = (await admin_client.get(f"/api/app-data/apps/{app_id}/pages/overview")).json()
    assert [a["name"] for a in page["definition"]["actions"]] == [
        "log", "rename", "remove"]
    assert page["definition"]["components"][1]["actions"] == [
        "log", "rename", "remove"]
    assert page["definition"]["actions"][0]["editable_fields"][0]["type"] == "string"
    bad = await admin_client.post(path(app_id), json={"template": "log", "values": {
        "habit": "run", "day": "2026-10-02", "done": False}})
    assert bad.status_code == 422
    intent = await confirm(admin_client, app_id, "log", values={
        "habit": "run", "day": "2026-10-02"})
    assert intent["confirmation"]["resulting_values"]["done"] is True
    first = await dispatch(admin_client, intent)
    assert first.status_code == 200, first.text
    again = await dispatch(admin_client, intent)
    assert again.status_code == 200 and again.json()["replayed"] is True
    assert again.json()["id"] == first.json()["id"]
    async with sf() as s:
        receipts = (await s.execute(select(AppDataPageReceipt))).scalars().all()
        assert len(receipts) == 1


async def test_stale_record_and_changed_page_require_new_confirmation(admin_client, sf):
    app_id = await setup_app(sf, "stale")
    row = await add(sf, app_id, {"habit": "run", "day": "2026-10-02"})
    intent = await confirm(admin_client, app_id, "rename", record_id=row["id"],
                           values={"habit": "walk"})
    async with sf() as s:
        ctx = await load_app(s, app_id)
        await update_record(s, ctx, Caller("agent:pai"), "habits", row["id"],
                            {"habit": "swim"}, expected_version=1)
    stale = await dispatch(admin_client, intent)
    assert stale.status_code == 409 and "confirm again" in stale.text
    fresh = await confirm(admin_client, app_id, "rename", record_id=row["id"],
                          values={"habit": "walk"})
    page = copy.deepcopy(PAGE)
    page["title"] = "Updated title"
    async with sf() as s:
        await L.draft(s, PAI, app_id, request_id="changed-page", kind="page", definition=page)
    async with sf() as s:
        await L.publish(s, PAI, app_id, request_id="publish-page",
                        expected_approved_version=2)
    changed = await dispatch(admin_client, fresh)
    assert changed.status_code == 409 and "confirm again" in changed.text


async def test_delete_and_expired_or_tampered_intents(admin_client, sf):
    app_id = await setup_app(sf, "delete")
    row = await add(sf, app_id, {"habit": "run", "day": "2026-10-02"})
    intent = await confirm(admin_client, app_id, "remove", record_id=row["id"])
    assert intent["confirmation"]["delete_plan"] is not None
    wrong = await admin_client.post(
        f"/api/app-data/page-intents/{intent['intent_id']}/dispatch",
        json={"digest": "0" * 64})
    assert wrong.status_code == 409
    async with sf() as s:
        frozen = await s.get(AppDataPageIntent, intent["intent_id"])
        frozen.expires_at = utcnow() - timedelta(seconds=1)
        await s.commit()
    assert (await dispatch(admin_client, intent)).status_code == 409


async def test_update_delete_and_changed_frozen_payload(admin_client, sf):
    app_id = await setup_app(sf, "complete")
    row = await add(sf, app_id, {"habit": "run", "day": "2026-10-02"})
    update = await confirm(admin_client, app_id, "rename", record_id=row["id"],
                           values={"habit": "walk"})
    async with sf() as s:
        frozen = await s.get(AppDataPageIntent, update["intent_id"])
        frozen.values = {"habit": "tampered"}
        await s.commit()
    assert (await dispatch(admin_client, update)).status_code == 409
    update = await confirm(admin_client, app_id, "rename", record_id=row["id"],
                           values={"habit": "walk"})
    changed = await dispatch(admin_client, update)
    assert changed.status_code == 200 and changed.json()["version"] == 2
    deletion = await confirm(admin_client, app_id, "remove", record_id=row["id"])
    gone = await dispatch(admin_client, deletion)
    assert gone.status_code == 200 and gone.json()["deleted"] is True
    replay = await dispatch(admin_client, deletion)
    assert replay.status_code == 200 and replay.json()["replayed"] is True


async def test_new_version_page_action_and_field_redacted_history(admin_client, token_client,
                                                                   sf):
    collection = copy.deepcopy(HABITS)
    collection["write_mode"] = "versioned"
    app_id = await build(sf, name="version-action", defs=(("collection", collection),
                         ("view", RECENT), ("view", DONE), ("page", OVERVIEW)))
    page = copy.deepcopy(PAGE)
    page["actions"].append({"name": "revise", "kind": "new_version",
                            "collection": "habits", "editable_fields": ["habit"]})
    page["blocks"][1]["actions"].append("revise")
    page["blocks"].append({"kind": "detail", "view": "recent", "fields": ["habit", "note"],
                           "history": True})
    async with sf() as s:
        await L.draft(s, PAI, app_id, request_id="draft-version-page", kind="page",
                      definition=page)
    async with sf() as s:
        proposal = await P.propose(s, PAI, app_id, request_id="propose-version-page")
    async with sf() as s:
        await P.approve(s, Actor("kyle"), proposal["id"],
                        request_id="approve-version-page", digest=proposal["digest"])
    row = await add(sf, app_id, {"habit": "run", "day": "2026-10-02", "note": "private"})
    published = (await admin_client.get(f"/api/app-data/apps/{app_id}/pages/overview")).json()
    assert published["definition"]["components"][-1]["history"] == {
        "collection": "habits"}
    intent = await confirm(admin_client, app_id, "revise", record_id=row["id"],
                           values={"habit": "walk"})
    assert intent["action"] == "new_version"
    assert (await dispatch(admin_client, intent)).json()["version"] == 2
    path = f"/api/app-data/apps/{app_id}/records/habits/{row['id']}/history"
    history = (await admin_client.get(path)).json()
    assert [v["version"] for v in history["versions"]] == [2, 1]
    assert [v["record"]["values"]["habit"] for v in history["versions"]] == ["walk", "run"]
    assert all(v["record"]["values"]["note"] is None for v in history["versions"])
    assert all("note" in v["record"]["restricted"] for v in history["versions"])
    assert (await token_client.get(path)).status_code in (401, 403)


async def test_page_write_refuses_api_keys_and_agents(token_client, admin_client, sf,
                                                       seed_agent, agent_store):
    app_id = await setup_app(sf, "auth")
    intent = await confirm(admin_client, app_id, "log", values={
        "habit": "run", "day": "2026-10-02"})
    paths = [(path(app_id), {"template": "log", "values": {"habit": "x"}}),
             (f"/api/app-data/page-intents/{intent['intent_id']}/dispatch",
              {"digest": intent["digest"]})]
    for headers in (await _key(sf, name="intent-admin", role="admin"),
                    await agent(sf, seed_agent, agent_store)):
        for route, body in paths:
            assert (await token_client.post(route, json=body,
                                            headers=headers)).status_code == 403


async def test_shared_reader_sees_no_page_actions(token_client, sf):
    app_id = await setup_app(sf, "shared-actions")
    shared = copy.deepcopy(HABITS)
    shared["access"]["read"].append("login:qa")
    async with sf() as s:
        await L.draft(s, PAI, app_id, request_id="share-actions", kind="collection",
                      definition=shared)
    async with sf() as s:
        proposal = await P.propose(s, PAI, app_id, request_id="propose-share-actions")
    async with sf() as s:
        await P.approve(s, Actor("kyle"), proposal["id"],
                        request_id="approve-share-actions", digest=proposal["digest"])
        s.add(Principal(name="qa", role="reader", password_hash=PasswordHasher().hash("pw")))
        await s.commit()
    assert (await token_client.post("/api/login", json={"principal": "qa",
                                                         "password": "pw"})).status_code == 200
    page = (await token_client.get(f"/api/app-data/apps/{app_id}/pages/overview")).json()
    assert "actions" not in page["definition"]
    assert all("actions" not in block for block in page["definition"]["components"])
    assert (await token_client.post(path(app_id), json={"template": "log"})).status_code == 403
