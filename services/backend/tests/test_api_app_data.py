"""The `/api/app-data` routes (design 39): Kyle's read routes for the web,
shaped exactly as services/web/src/lib/appData.ts documents, and the agent
routes the `apps` and `app_data` broker tools call."""
import pytest
from sqlalchemy import select

from agentplatform.api import app_data as app_data_api
from agentplatform.appdata import lifecycle as L
from agentplatform.appdata.access import Caller
from agentplatform.appdata.lifecycle import Actor
from agentplatform.appdata.models import AppDataApp, AppDataDefinition
from agentplatform.appdata.records import create_record, load_app
from agentplatform.db import Principal

from .test_relay_api import _agent_token, _key
from .test_wiki_api import _run_id

APPS = "mcp__platform__apps"
APP_DATA = "mcp__platform__app_data"
PAI = Actor("agent:pai")
AGENT = "/api/app-data/agent"

HABITS = {"collection": "habits",
          "fields": {"habit": {"type": "string", "required": True},
                     "day": {"type": "date", "required": True},
                     "done": {"type": "bool"},
                     "note": {"type": "string", "access": {"read": ["owner"]}}},
          "access": {"read": ["owner", "kyle"], "create": ["owner"], "update": ["owner"],
                     "delete": ["owner"]}}
RECENT = {"view": "recent", "collection": "habits", "sort": [{"field": "day", "dir": "desc"}],
          "paging": True, "params": {"habit": {"type": "string"}},
          "filter": [{"field": "habit", "op": "eq", "value": {"param": "habit"}}]}
DONE = {"view": "done_count", "collection": "habits",
        "filter": [{"field": "done", "op": "eq", "value": True}],
        "aggregates": [{"fn": "count", "as": "n"}]}
OVERVIEW = {"page": "overview", "title": "Habits", "blocks": [
    {"kind": "metric", "label": "Days done", "view": "done_count"},
    {"kind": "table", "view": "recent", "title": "Recent",
     "columns": [{"field": "habit"}, {"field": "day", "format": "date"}]}]}
PRIVATE = {"collection": "diary", "fields": {"entry": {"type": "text"}},
           "access": {"read": ["owner"]}}
DIARY = {"view": "diary_all", "collection": "diary"}
DIARY_PAGE = {"page": "diary", "title": "Diary", "blocks": [
    {"kind": "table", "view": "diary_all", "columns": [{"field": "entry"}]}]}


async def build(sf, owner=PAI, name="habits", defs=(("collection", HABITS), ("view", RECENT),
                                                    ("view", DONE), ("page", OVERVIEW))):
    async with sf() as s:
        app_id = (await L.create(s, owner, request_id=f"c-{name}", name=name,
                                 description="Daily habits."))["app_id"]
    for i, (kind, body) in enumerate(defs):
        async with sf() as s:
            await L.draft(s, owner, app_id, request_id=f"d-{name}-{i}", kind=kind,
                          definition=body)
    async with sf() as s:
        await L.publish(s, owner, app_id, request_id=f"p-{name}",
                        expected_approved_version=None)
    return app_id


async def add(sf, app_id, values, collection="habits"):
    async with sf() as s:
        ctx = await load_app(s, app_id)
        return await create_record(s, ctx, Caller("agent:pai"), collection, values)


async def agent(sf, seed_agent, agent_store, name="pai", tools=(APPS, APP_DATA), run=True):
    await seed_agent(name, description=name, platform_tools=list(tools))
    await agent_store.reload()
    return await _agent_token(sf, name, run_id=await _run_id(sf, name) if run else None)


# --- Kyle's routes: the web contract ----------------------------------------------------

async def test_list_matches_the_contract(admin_client, sf):
    app_id = await build(sf)
    r = await admin_client.get("/api/app-data/apps")
    assert r.status_code == 200, r.text
    [app] = r.json()
    assert set(app) == {"id", "name", "owner", "status", "description", "approved_version",
                        "updated_at", "health"}
    assert (app["id"], app["owner"], app["status"], app["approved_version"]) == (
        app_id, "pai", "active", 1)
    assert set(app["health"]) == {"status", "issues", "checked_at"}


async def test_detail_matches_the_contract(admin_client, sf):
    app_id = await build(sf)
    r = await admin_client.get(f"/api/app-data/apps/{app_id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert {(a["kind"], a["name"]) for a in body["approved"]} == {
        ("collection", "habits"), ("view", "recent"), ("view", "done_count"),
        ("page", "overview")}
    assert body["drafts"] == [] and body["build_notes"] is None
    assert body["build_ops"][0]["action"] == "publish"
    assert set(body["health"]["quota"]) == {"records", "records_limit", "bytes",
                                            "bytes_limit"}
    assert (await admin_client.get("/api/app-data/apps/nope")).status_code == 404


async def test_page_is_the_published_definition_in_the_web_shape(admin_client, sf):
    app_id = await build(sf)
    r = await admin_client.get(f"/api/app-data/apps/{app_id}/pages/overview")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"app_id", "app_name", "page", "version", "definition"}
    assert (body["app_name"], body["page"], body["version"]) == ("habits", "overview", 1)
    assert body["definition"] == {"renderer": "typed/v2", "title": "Habits", "components": [
        {"kind": "metric", "label": "Days done", "view": "done_count"},
        {"kind": "table", "label": "Recent", "view": "recent",
         "columns": [{"field": "habit"}, {"field": "day", "format": "date"}]}]}
    assert (await admin_client.get(f"/api/app-data/apps/{app_id}/pages/nope")
            ).status_code == 404


async def test_a_page_kyle_cant_read_is_403(admin_client, sf):
    app_id = await build(sf, defs=(("collection", PRIVATE), ("view", DIARY),
                                   ("page", DIARY_PAGE)))
    r = await admin_client.get(f"/api/app-data/apps/{app_id}/pages/diary")
    assert r.status_code == 403
    r = await admin_client.get(f"/api/app-data/apps/{app_id}/views/diary_all")
    assert r.status_code == 403 and isinstance(r.json()["detail"], str)


async def test_a_page_that_no_longer_validates_is_503(admin_client, sf):
    app_id = await build(sf)
    async with sf() as s:
        s.add(AppDataDefinition(app_id=app_id, kind="page", name="broken", version=1,
                                body={"page": "broken", "title": "B", "blocks": [
                                    {"kind": "table", "view": "recent",
                                     "columns": [{"field": "mood"}]}]},
                                state="published", author="agent:pai"))
        await s.commit()
    r = await admin_client.get(f"/api/app-data/apps/{app_id}/pages/broken")
    assert r.status_code == 503 and isinstance(r.json()["detail"], str)
    assert (await admin_client.get(f"/api/app-data/apps/{app_id}/views/recent")
            ).status_code == 503


async def test_view_rows_paging_params_and_restricted_fields(admin_client, sf):
    app_id = await build(sf)
    for day in ("2026-09-28", "2026-09-29", "2026-09-30"):
        await add(sf, app_id, {"habit": "run", "day": day, "done": True, "note": "x"})
    await add(sf, app_id, {"habit": "read", "day": "2026-09-30"})
    base = f"/api/app-data/apps/{app_id}/views/recent"
    r = await admin_client.get(base, params={"habit": "run", "limit": 2})
    assert r.status_code == 200, r.text
    page = r.json()
    assert set(page) == {"rows", "next_cursor", "as_of", "stale"}
    assert [row["values"]["day"] for row in page["rows"]] == ["2026-09-30", "2026-09-29"]
    row = page["rows"][0]
    assert set(row) == {"id", "values", "restricted"}
    assert row["values"]["note"] is None and row["restricted"] == ["note"]
    r = await admin_client.get(base, params={"habit": "run", "limit": 2,
                                             "cursor": page["next_cursor"]})
    assert [row["values"]["day"] for row in r.json()["rows"]] == ["2026-09-28"]
    assert r.json()["next_cursor"] is None
    r = await admin_client.get(f"/api/app-data/apps/{app_id}/views/done_count")
    assert r.json()["count"] == 3 and set(r.json()) == {"count", "as_of", "stale"}


async def test_view_errors(admin_client, sf):
    app_id = await build(sf)
    base = f"/api/app-data/apps/{app_id}/views"
    assert (await admin_client.get(f"{base}/recent", params={"bogus": "1"})).status_code == 422
    assert (await admin_client.get(f"{base}/recent", params={"limit": 999})).status_code == 422
    assert (await admin_client.get(f"{base}/recent", params={"limit": "x"})).status_code == 422
    assert (await admin_client.get(f"{base}/nope")).status_code == 404


async def test_a_retired_apps_views_stop(admin_client, sf):
    app_id = await build(sf)
    async with sf() as s:
        await L.retire(s, PAI, app_id, request_id="r")
    r = await admin_client.get(f"/api/app-data/apps/{app_id}/views/recent")
    assert r.status_code == 409
    listed = (await admin_client.get("/api/app-data/apps")).json()
    assert listed[0]["status"] == "retired"


async def test_kyle_routes_refuse_everyone_but_kyles_session(client, token_client, sf,
                                                             seed_agent, agent_store):
    from argon2 import PasswordHasher
    app_id = await build(sf)
    paths = ["/api/app-data/apps", f"/api/app-data/apps/{app_id}",
             f"/api/app-data/apps/{app_id}/pages/overview",
             f"/api/app-data/apps/{app_id}/views/recent"]
    for path in paths:
        assert (await token_client.get(path)).status_code == 401
    admin_key = await _key(sf, name="ops", role="admin")
    agent_headers = await agent(sf, seed_agent, agent_store)
    for headers in (admin_key, agent_headers):
        for path in paths:
            assert (await token_client.get(path, headers=headers)).status_code == 403, path
    # A reader's browser session is a session, but not Kyle's.
    async with sf() as s:
        s.add(Principal(name="qa", role="reader", password_hash=PasswordHasher().hash("pw")))
        await s.commit()
    assert (await token_client.post("/api/login", json={"principal": "qa",
                                                         "password": "pw"})).status_code == 200
    for path in paths:
        assert (await token_client.get(path)).status_code == 403, path


# --- agent routes: auth -----------------------------------------------------------------

async def test_agent_routes_need_the_tool_and_a_run(token_client, admin_client, sf,
                                                    seed_agent, agent_store):
    no_tool = await agent(sf, seed_agent, agent_store, name="bob", tools=(APP_DATA,))
    r = await token_client.post(f"{AGENT}/apps/list", json={}, headers=no_tool)
    assert r.status_code == 403 and APPS in r.json()["detail"]
    records_only = await agent(sf, seed_agent, agent_store, name="cal", tools=(APPS,))
    r = await token_client.post(f"{AGENT}/records/describe", json={"app": "x"},
                                headers=records_only)
    assert r.status_code == 403
    no_run = await agent(sf, seed_agent, agent_store, name="dee", run=False)
    r = await token_client.post(f"{AGENT}/apps/list", json={}, headers=no_run)
    assert r.status_code == 403
    admin_key = await _key(sf, name="ops", role="admin")
    r = await token_client.post(f"{AGENT}/apps/list", json={}, headers=admin_key)
    assert r.status_code == 403
    # Kyle's own session isn't an agent run either.
    assert (await admin_client.post(f"{AGENT}/apps/list", json={})).status_code == 403
    assert (await token_client.post(f"{AGENT}/apps/list", json={})).status_code == 401


async def test_a_frozen_run_token_without_the_tool_is_refused(token_client, sf, seed_agent,
                                                              agent_store, monkeypatch):
    headers = await agent(sf, seed_agent, agent_store)
    original = app_data_api.authenticate

    async def frozen(request):
        ident = await original(request)
        if ident is not None:
            request.state.frozen_tools = [APP_DATA]
        return ident

    monkeypatch.setattr(app_data_api, "authenticate", frozen)
    r = await token_client.post(f"{AGENT}/apps/list", json={}, headers=headers)
    assert r.status_code == 403


async def test_a_builder_acts_only_on_its_own_apps(token_client, sf, seed_agent, agent_store):
    app_id = await build(sf)
    bob = await agent(sf, seed_agent, agent_store, name="bob")
    for action, body in (("get", {}), ("draft", {"request_id": "x", "kind": "view",
                                                "definition": DONE}),
                         ("publish", {"request_id": "y", "expected_approved_version": 1}),
                         ("retire", {"request_id": "z"}), ("validate", {}),
                         ("preview", {"kind": "view", "name": "recent"}),
                         ("authority", {}), ("health", {}), ("notes", {})):
        r = await token_client.post(f"{AGENT}/apps/{action}", json={"app": app_id, **body},
                                    headers=bob)
        assert r.status_code == 403, (action, r.text)
        assert r.json()["detail"]["code"] == "AL-NOT-OWNER"
    listed = (await token_client.post(f"{AGENT}/apps/list", json={}, headers=bob)).json()
    assert listed == []


# --- agent routes: the builder flow -----------------------------------------------------

async def test_a_builder_builds_an_app_end_to_end(token_client, sf, seed_agent, agent_store):
    h = await agent(sf, seed_agent, agent_store)

    async def call(action, **body):
        r = await token_client.post(f"{AGENT}/apps/{action}", json=body, headers=h)
        assert r.status_code == 200, (action, r.text)
        return r.json()

    schema = await call("schema")
    assert "collection" in schema["kinds"] and schema["capabilities"]["version"] >= 1
    app = await call("create", request_id="c1", name="habits", timezone="UTC",
                     description="Daily habits.")
    assert app["owner"] == "agent:pai"
    ref = "habits"  # agents may name an App instead of quoting its id
    for i, (kind, body) in enumerate((("collection", HABITS), ("view", RECENT),
                                      ("view", DONE), ("page", OVERVIEW))):
        await call("draft", app=ref, request_id=f"d{i}", kind=kind, definition=body)
    report = await call("validate", app=ref, expected_approved_version=None)
    assert report["publishable"] is True
    shown = await call("preview", app=ref, kind="view", name="done_count",
                       samples={"habits": [{"habit": "run", "day": "2026-09-30",
                                            "done": True}]})
    assert shown["count"] == 1
    shown = await call("preview", app=ref, kind="page", name="overview", **{"as": "kyle"})
    assert shown["definition"]["title"] == "Habits"
    out = await call("publish", app=ref, request_id="p1", expected_approved_version=None)
    assert out["approved_version"] == 1
    again = await call("publish", app=ref, request_id="p1", expected_approved_version=None)
    assert again["replayed"] is True
    notes = await call("notes", app=ref)
    assert notes["revision"] == 0
    notes = await call("notes", app=ref, request_id="n1", text="Log at 21:00.",
                       expected_revision=0)
    assert notes["revision"] == 1
    got = await call("get", app=ref)
    assert got["build_notes"]["text"] == "Log at 21:00." and got["approved_version"] == 1
    facts = await call("authority", app=ref)
    assert facts["approved_version"] == 1 and facts["facts"]
    health = await call("health", app=ref)
    assert health["status"] == "ok"
    await call("draft", app=ref, request_id="d9", kind="view",
               definition={**DONE, "view": "done_two"})
    await call("publish", app=ref, request_id="p2", expected_approved_version=1)
    rolled = await call("rollback", app=ref, request_id="rb", to_version=1,
                        expected_approved_version=2)
    assert rolled["approved_version"] == 3
    listed = await call("list")
    assert [a["name"] for a in listed] == ["habits"]
    retired = await call("retire", app=ref, request_id="rt", reason="done")
    assert retired["status"] == "retired"


async def test_publish_refuses_a_widening_with_the_list(token_client, sf, seed_agent,
                                                        agent_store):
    h = await agent(sf, seed_agent, agent_store)
    app_id = (await token_client.post(f"{AGENT}/apps/create", headers=h, json={
        "request_id": "c", "name": "shared"})).json()["app_id"]
    shared = {**HABITS, "access": {"read": ["owner", "kyle", "agent:bob"]}}
    r = await token_client.post(f"{AGENT}/apps/draft", headers=h, json={
        "app": app_id, "request_id": "d", "kind": "collection", "definition": shared})
    assert r.status_code == 200, r.text
    r = await token_client.post(f"{AGENT}/apps/publish", headers=h, json={
        "app": app_id, "request_id": "p", "expected_approved_version": None})
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["code"] == "AL-NEEDS-PROPOSAL"
    assert detail["detail"]["suggest"] == "propose" and detail["detail"]["widening"]


async def test_publish_needs_an_explicit_expected_version(token_client, sf, seed_agent,
                                                          agent_store):
    h = await agent(sf, seed_agent, agent_store)
    await token_client.post(f"{AGENT}/apps/create", headers=h,
                            json={"request_id": "c", "name": "habits"})
    r = await token_client.post(f"{AGENT}/apps/publish", headers=h,
                                json={"app": "habits", "request_id": "p"})
    assert r.status_code == 422


async def test_a_reused_request_id_is_refused_over_http(token_client, sf, seed_agent,
                                                        agent_store):
    h = await agent(sf, seed_agent, agent_store)
    r = await token_client.post(f"{AGENT}/apps/create", headers=h,
                                json={"request_id": "c", "name": "one"})
    assert r.status_code == 200
    r = await token_client.post(f"{AGENT}/apps/create", headers=h,
                                json={"request_id": "c", "name": "two"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "AL-REQUEST-REUSED"


# --- agent routes: records --------------------------------------------------------------

async def test_records_go_through_the_engine_as_the_caller(token_client, sf, seed_agent,
                                                           agent_store):
    app_id = await build(sf)
    h = await agent(sf, seed_agent, agent_store)

    async def call(action, status=200, **body):
        r = await token_client.post(f"{AGENT}/records/{action}", json={"app": app_id, **body},
                                    headers=h)
        assert r.status_code == status, (action, r.text)
        return r.json()

    described = await call("describe")
    [c] = described["collections"]
    assert c["collection"] == "habits" and c["fields"]["note"]["read"] is True
    assert {v["view"] for v in described["views"]} == {"recent", "done_count"}
    made = await call("create", request_id="w1", collection="habits",
                      values={"habit": "run", "day": "2026-09-30", "note": "easy"})
    assert set(made) == {"collection", "id", "version"}
    again = await call("create", request_id="w1", collection="habits",
                       values={"habit": "run", "day": "2026-09-30", "note": "easy"})
    assert again["id"] == made["id"]
    got = await call("get", collection="habits", id=made["id"])
    assert got["values"]["note"] == "easy"
    up = await call("update", request_id="w2", collection="habits", id=made["id"],
                    values={"done": True}, expected_version=1)
    assert up["version"] == 2
    stale = await call("update", 409, request_id="w3", collection="habits", id=made["id"],
                       values={"done": False}, expected_version=1)
    assert stale["detail"]["code"] == "AD-VERSION-CONFLICT"
    rows = await call("query", view="recent", params={"habit": "run"})
    assert [r["id"] for r in rows["rows"]] == [made["id"]]
    count = await call("query", view="done_count")
    assert count["count"] == 1
    plan = await call("delete_preview", collection="habits", ids=[made["id"]])
    assert plan["deletes"]["habits"]["count"] == 1
    gone = await call("delete", request_id="w4", collection="habits", id=made["id"],
                      expected_version=2)
    assert gone["deleted"] is True
    await call("get", 404, collection="habits", id=made["id"])


async def test_records_refuse_a_principal_the_app_doesnt_name(token_client, sf, seed_agent,
                                                              agent_store):
    app_id = await build(sf)
    bob = await agent(sf, seed_agent, agent_store, name="bob")
    r = await token_client.post(f"{AGENT}/records/create", headers=bob, json={
        "app": app_id, "request_id": "w", "collection": "habits",
        "values": {"habit": "run", "day": "2026-09-30"}})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "AD-FORBIDDEN"
    r = await token_client.post(f"{AGENT}/records/query", headers=bob,
                                json={"app": app_id, "view": "recent"})
    assert r.status_code == 403
    r = await token_client.post(f"{AGENT}/records/describe", headers=bob, json={"app": app_id})
    assert r.status_code == 403


async def test_a_retired_apps_views_stop_for_agents_too(token_client, sf, seed_agent,
                                                        agent_store):
    app_id = await build(sf)
    async with sf() as s:
        await L.retire(s, PAI, app_id, request_id="r")
    h = await agent(sf, seed_agent, agent_store)
    r = await token_client.post(f"{AGENT}/records/query", headers=h,
                                json={"app": app_id, "view": "recent"})
    assert r.status_code == 409


# --- ownership outlives the agent -------------------------------------------------------

async def test_deleting_an_agent_hands_its_apps_to_kyle(admin_client, sf, seed_agent,
                                                        agent_store):
    await seed_agent("pai", description="pai")
    await agent_store.reload()
    app_id = await build(sf)
    r = await admin_client.delete("/api/agents/pai")
    assert r.status_code == 200, r.text
    async with sf() as s:
        app = (await s.execute(select(AppDataApp).where(AppDataApp.id == app_id))).scalar_one()
    assert (app.owner_kind, app.owner_id) == ("kyle", "kyle")
    listed = (await admin_client.get("/api/app-data/apps")).json()
    assert listed[0]["owner"] == "kyle"


# --- Kyle's quota routes ----------------------------------------------------------------

QUOTA = "/api/app-data/quotas"


async def test_kyle_reads_and_sets_a_quota(admin_client, sf):
    app_id = await build(sf)
    await add(sf, app_id, {"habit": "run", "day": "2026-10-01"})
    got = (await admin_client.get(f"{QUOTA}/app/{app_id}")).json()
    assert got["scope"] == "app" and got["scope_id"] == app_id
    assert got["set_by"] == "default" and got["set"] == {}
    assert got["used"]["records"] == 1
    r = await admin_client.put(f"{QUOTA}/app/{app_id}",
                               json={"limits": {"max_records": 10, "max_bytes": 4096}})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["set"] == {"max_records": 10, "max_bytes": 4096}
    assert out["limits"]["max_records"] == 10 and out["set_by"] == "kyle"
    # null puts a limit back to the default.
    out = (await admin_client.put(f"{QUOTA}/app/{app_id}",
                                  json={"limits": {"max_records": None}})).json()
    assert out["set"] == {"max_bytes": 4096} and out["limits"]["max_records"] == 250_000
    out = (await admin_client.put(f"{QUOTA}/owner/agent:pai",
                                  json={"limits": {"max_apps": 3}})).json()
    assert out["scope_id"] == "agent:pai" and out["limits"]["max_apps"] == 3
    assert (await admin_client.get(f"{QUOTA}/owner/agent:pai")).json()["used"]["records"] == 1


@pytest.mark.parametrize("path,body", [
    ("app/a1", {"limits": {"max_apps": 3}}),          # an owner limit on an App
    ("owner/kyle", {"limits": {"max_open_drafts": 1}}),
    ("app/a1", {"limits": {"max_records": -1}}),
    ("app/a1", {"limits": {"max_records": True}}),
    ("app/a1", {"limits": {}}),
    ("tenant/a1", {"limits": {"max_records": 1}}),
])
async def test_the_quota_setter_refuses_what_isnt_a_limit(admin_client, path, body):
    r = await admin_client.put(f"{QUOTA}/{path}", json=body)
    assert r.status_code == 422, r.text
    assert isinstance(r.json()["detail"], str)


async def test_the_quota_routes_refuse_bad_bodies_and_scopes(admin_client):
    assert (await admin_client.put(f"{QUOTA}/app/a1", json={"max_records": 1})
            ).status_code == 422
    assert (await admin_client.get(f"{QUOTA}/tenant/a1")).status_code == 422
    assert (await admin_client.get(f"{QUOTA}/app/{'x' * 161}")).status_code == 422


async def test_quota_routes_answer_only_kyles_session(client, token_client, sf, seed_agent,
                                                      agent_store):
    from argon2 import PasswordHasher

    from agentplatform.appdata.models import AppDataQuota
    app_id = await build(sf)
    path = f"{QUOTA}/app/{app_id}"
    body = {"limits": {"max_records": 10 ** 9}}
    assert (await token_client.get(path)).status_code == 401
    assert (await token_client.put(path, json=body)).status_code == 401
    admin_key = await _key(sf, name="ops", role="admin")
    agent_headers = await agent(sf, seed_agent, agent_store)
    for headers in (admin_key, agent_headers):
        assert (await token_client.get(path, headers=headers)).status_code == 403
        assert (await token_client.put(path, json=body, headers=headers)).status_code == 403
    async with sf() as s:
        s.add(Principal(name="qa", role="reader", password_hash=PasswordHasher().hash("pw")))
        await s.commit()
    assert (await token_client.post("/api/login", json={"principal": "qa",
                                                         "password": "pw"})).status_code == 200
    assert (await token_client.put(path, json=body)).status_code == 403
    async with sf() as s:
        row = await s.get(AppDataQuota, ("app", app_id))
    assert row is None or row.max_records is None


# --- App tools (design 39, "The authority model"; R1b B3) ------------------------------

TRACKER = {"tool": "tracker", "roles": {"log": {"collection": "habits",
                                               "verbs": ["read", "create"]}}}


async def approve(sf, app_id, kind, body):
    """Stand in for Kyle's approval (proposals arrive with B2/B8): the
    definition goes live as the App's next approved version."""
    async with sf() as s:
        app = await s.get(AppDataApp, app_id)
        version = (app.approved_version or 0) + 1
        s.add(AppDataDefinition(app_id=app_id, kind=kind, name=body[kind], version=version,
                                body=body, state="published", author="kyle"))
        app.approved_version = version
        await s.commit()
    return version


async def publish(sf, app_id, *drafts, request_id="p-tools"):
    async with sf() as s:
        app = await s.get(AppDataApp, app_id)
        expected = app.approved_version
    for i, (kind, body) in enumerate(drafts):
        async with sf() as s:
            await L.draft(s, PAI, app_id, request_id=f"{request_id}-d{i}", kind=kind,
                          definition=body)
    async with sf() as s:
        try:
            return await L.publish(s, PAI, app_id, request_id=request_id,
                                   expected_approved_version=expected)
        except L.LifecycleError as exc:
            return exc


def _tracker(*verbs):
    return {"tool": "tracker", "roles": {"log": {"collection": "habits",
                                                 "verbs": list(verbs)}}}


async def test_adding_an_app_tool_is_a_proposal(sf):
    app_id = await build(sf)
    refused = await publish(sf, app_id, ("tool", TRACKER))
    assert isinstance(refused, L.LifecycleError) and refused.code == "AL-NEEDS-PROPOSAL"
    assert "new: tool tracker may create habits (role log)" in refused.detail["widening"]


async def test_widening_an_app_tool_is_a_proposal_and_narrowing_self_publishes(sf):
    app_id = await build(sf)
    await approve(sf, app_id, "tool", _tracker("read", "create"))
    refused = await publish(sf, app_id, ("tool", _tracker("read", "create", "update")),
                            request_id="p-wide")
    assert isinstance(refused, L.LifecycleError) and refused.code == "AL-NEEDS-PROPOSAL"
    assert refused.detail["widening"] == ["new: tool tracker may update habits (role log)"]
    async with sf() as s:
        await L.draft(s, PAI, app_id, request_id="discard", kind="tool", name="tracker",
                      discard=True, expected_revision=1)
    out = await publish(sf, app_id, ("tool", _tracker("read")), request_id="p-narrow")
    assert not isinstance(out, L.LifecycleError), out.detail
    async with sf() as s:
        ctx = await load_app(s, app_id)
    assert ctx.bundle.app_tools["tracker"].roles["log"].verbs == ["read"]
    async with sf() as s:
        detail = await L.get_app(s, PAI, app_id)
    assert {"kind": "tool", "name": "tracker"} in [
        {"kind": d["kind"], "name": d["name"]} for d in detail["approved"]]


async def test_tool_only_writers_self_publish_only_for_an_approved_app_tool(sf):
    app_id = await build(sf)
    await approve(sf, app_id, "tool", TRACKER)
    out = await publish(sf, app_id, ("collection", {**HABITS, "writers": {
        "create": ["tool:tracker"]}}), request_id="p-ours")
    assert not isinstance(out, L.LifecycleError), out.detail

    other = await build(sf, name="other")
    refused = await publish(sf, other, ("collection", {**HABITS, "writers": {
        "create": ["tool:tracker"]}}), request_id="p-theirs")
    assert isinstance(refused, L.LifecycleError) and refused.code == "AL-NEEDS-PROPOSAL"
    assert any("not an approved App tool" in line for line in refused.detail["widening"])


async def test_an_app_tool_naming_a_missing_collection_is_reported_against_it(sf):
    app_id = await build(sf)
    async with sf() as s:
        await L.draft(s, PAI, app_id, request_id="d-bad", kind="tool", definition={
            "tool": "tracker", "roles": {"log": {"collection": "nope", "verbs": ["read"]}}})
    async with sf() as s:
        report = await L.validate(s, PAI, app_id, expected_approved_version=1)
    assert report["publishable"] is False
    [error] = report["errors"]
    assert error["code"] == "JD-TOOL-COLLECTION"
    assert error["definition"] == {"kind": "tool", "name": "tracker"}
