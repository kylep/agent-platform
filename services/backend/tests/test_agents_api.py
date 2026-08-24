"""The agents API (docs/design/15): definitions are rows, edited directly.

Covers the whole write surface — CRUD, the change log, rollback, import — plus
the two authorization rules that replaced "protected by PR review": the
field-level grant guard (`agents_edit` cannot escalate) and attribution from
the verified principal.
"""
from sqlalchemy import select

from agentplatform.apikeys import generate_token, hash_token, token_prefix
from agentplatform.db import AgentDef, AgentVersion, ApiKey

AGENTS_EDIT = "mcp__platform__agents_edit"
AGENTS_GRANT = "mcp__platform__agents_grant"


def a_def(name: str, **over) -> dict:
    """A COMPLETE definition payload — what the UI PUTs. The endpoints replace
    the whole definition, so tests spell out every field and override what they
    are actually about. The prompt matches what `seed_agent` writes, so
    `a_def(x)` against a seeded agent changes only what the test overrides."""
    d = {"name": name, "prompt": f"# {name}\nYou are {name}.", "description": "",
         "model": "", "role": "operator", "system": False, "can_invoke": False,
         "concurrency": 1, "timeout_seconds": 1800, "result_topic": "",
         "transcript_retention_days": None, "harness_tools": [],
         "platform_tools": [], "skills": [], "secrets": [],
         "entrypoints": {"crons": [], "webhooks": [], "topics": [], "timezone": ""},
         "enabled": True}
    d.update(over)
    return d


async def bearer(sf, agent: str | None, *, role: str = "tools",
                 name: str | None = None) -> dict[str, str]:
    """An API key bound to `agent` — the shape an agent's own token has, so the
    grant lookup resolves through the same path the tool executor will use."""
    token = generate_token()
    async with sf() as s:
        s.add(ApiKey(name=name or f"{role}:{agent}", role=role, agent=agent,
                     key_hash=hash_token(token), prefix=token_prefix(token)))
        await s.commit()
    return {"Authorization": f"Bearer {token}"}


async def versions_of(sf, agent: str) -> list[AgentVersion]:
    async with sf() as s:
        return list((await s.execute(
            select(AgentVersion).where(AgentVersion.agent == agent)
            .order_by(AgentVersion.version))).scalars().all())


# --- the edge shapes mirror the definition -----------------------------------

def test_wire_models_cover_every_definition_field():
    """The API's input/output models spell the definition out by hand (they
    need strictness on the way in and tolerance on the way out), so nothing but
    a test stops a new column from being silently unwritable and unreadable."""
    from agentplatform.agentdefs import DEF_FIELDS
    from agentplatform.api.schemas import AgentDefIn, AgentDefOut, AgentSummary
    assert set(AgentDefIn.model_fields) == set(DEF_FIELDS)
    assert set(AgentDefOut.model_fields) == set(DEF_FIELDS)
    assert set(AgentSummary.model_fields) - set(DEF_FIELDS) == {
        "quarantined", "error", "blocked", "blocked_reason", "schedule"}


def test_the_grant_split_covers_the_definition():
    """Every mutable field belongs to exactly one authority — a field in
    neither half would be writable by anyone who can write at all."""
    from agentplatform.agentdefs import DEF_FIELDS
    from agentplatform.api.agents import EDIT_FIELDS, GRANT_FIELDS
    assert set(GRANT_FIELDS) | set(EDIT_FIELDS) == set(DEF_FIELDS) - {"name"}
    assert not set(GRANT_FIELDS) & set(EDIT_FIELDS)


# --- read --------------------------------------------------------------------

async def test_list_carries_the_full_definition_and_readiness(admin_client, seed_agent,
                                                              agent_store):
    await seed_agent("worker", description="does work", skills=["git"],
                     platform_tools=["mcp__platform__runs_read"],
                     entrypoints={"crons": [{"schedule": "0 9 * * *", "prompt": ""}],
                                  "webhooks": [], "topics": [], "timezone": ""})
    await agent_store.reload()
    rows = {a["name"]: a for a in (await admin_client.get("/api/agents")).json()}
    w = rows["worker"]
    assert w["description"] == "does work" and w["skills"] == ["git"]
    assert w["platform_tools"] == ["mcp__platform__runs_read"]
    assert w["prompt"].endswith("You are worker.") and w["enabled"] is True
    # Readiness is server-derived and rides alongside the definition — the UI
    # reads these off the listing, so they must survive the DB-first rewrite.
    assert w["quarantined"] is False and w["error"] is None
    assert set(w) >= {"blocked", "blocked_reason"}
    assert w["schedule"] == "0 9 * * *"


async def test_list_still_renders_a_quarantined_row(admin_client, sf, agent_store):
    """A row that no longer validates must stay READABLE — the listing is where
    you go to fix it. The definition models at the edge therefore carry no
    validators of their own."""
    async with sf() as s:
        s.add(AgentDef(name="broken", role="superuser"))
        await s.commit()
    await agent_store.reload()
    rows = {a["name"]: a for a in (await admin_client.get("/api/agents")).json()}
    assert rows["broken"]["quarantined"] is True and rows["broken"]["error"]
    assert rows["broken"]["role"] == "superuser"
    detail = await admin_client.get("/api/agents/broken")
    assert detail.status_code == 200 and detail.json()["role"] == "superuser"


async def test_get_returns_the_definition(admin_client):
    r = await admin_client.get("/api/agents/hello-world")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "hello-world" and body["description"] == "test"
    assert body["prompt"].endswith("You are hello-world.")
    assert body["platform_tools"] == [] and body["enabled"] is True
    assert (await admin_client.get("/api/agents/ghost")).status_code == 404


async def test_reader_may_read_but_not_write(client, sf):
    h = await bearer(sf, None, role="reader", name="ro")
    assert (await client.get("/api/agents", headers=h)).status_code == 200
    assert (await client.post("/api/agents", json=a_def("x"), headers=h)).status_code == 403
    assert (await client.delete("/api/agents/hello-world", headers=h)).status_code == 403


# --- create ------------------------------------------------------------------

async def test_create_writes_a_row_and_version_one(admin_client, sf):
    r = await admin_client.post("/api/agents", json=a_def(
        "newbie", description="a new one", skills=["git"], harness_tools=["WebFetch"],
        secrets=["github-token"]))
    assert r.status_code == 201, r.text
    assert r.json()["name"] == "newbie" and r.json()["skills"] == ["git"]
    async with sf() as s:
        row = await s.get(AgentDef, "newbie")
    assert row.description == "a new one" and row.harness_tools == ["WebFetch"]
    log = await versions_of(sf, "newbie")
    assert [v.version for v in log] == [1]
    assert log[0].changed_by == "admin" and log[0].changed_via == "admin"
    assert log[0].snapshot["skills"] == ["git"]


async def test_create_rejects_duplicate_bad_name_and_unknown_grants(admin_client):
    assert (await admin_client.post("/api/agents",
                                    json=a_def("hello-world"))).status_code == 409
    assert (await admin_client.post("/api/agents",
                                    json=a_def("Bad Name"))).status_code == 422
    for bad in ({"skills": ["ghost"]}, {"secrets": ["nope"]},
                {"platform_tools": ["mcp__platform__not_a_tool"]},
                {"harness_tools": ["Nope"]}):
        r = await admin_client.post("/api/agents", json=a_def("a-1", **bad))
        assert r.status_code == 422, f"{bad} → {r.status_code}"


async def test_create_rejects_an_unknown_field(admin_client):
    """A typo'd field must fail loudly, not silently do nothing."""
    r = await admin_client.post("/api/agents", json=a_def("typo", **{"promt": "oops"}))
    assert r.status_code == 422


async def test_create_accepts_a_registry_tool_and_rejects_a_bad_cron(admin_client):
    r = await admin_client.post("/api/agents", json=a_def(
        "quant", platform_tools=["mcp__platform__stocks"]))
    assert r.status_code == 201, r.text
    r = await admin_client.post("/api/agents", json=a_def(
        "cronky", entrypoints={"crons": [{"schedule": "not-a-cron", "prompt": ""}],
                               "webhooks": [], "topics": [], "timezone": ""}))
    assert r.status_code == 422


# --- update ------------------------------------------------------------------

async def test_put_replaces_the_definition_and_appends_a_version(admin_client, sf):
    r = await admin_client.put("/api/agents/hello-world", json=a_def(
        "hello-world", description="rewritten", prompt="You are new.",
        concurrency=4, harness_tools=["Grep"]))
    assert r.status_code == 200, r.text
    assert r.json()["description"] == "rewritten" and r.json()["concurrency"] == 4
    async with sf() as s:
        row = await s.get(AgentDef, "hello-world")
    assert row.prompt == "You are new." and row.harness_tools == ["Grep"]
    log = await versions_of(sf, "hello-world")
    assert [v.version for v in log] == [1]
    assert log[0].snapshot["description"] == "rewritten"


async def test_put_omitting_a_field_resets_it(admin_client, seed_agent, agent_store, sf):
    """PUT is a full replacement, not a patch: what the caller does not send is
    reset to the field's default, so the stored row always equals the payload."""
    await seed_agent("verbose", description="chatty", concurrency=7)
    await agent_store.reload()
    body = a_def("verbose")
    body.pop("concurrency")
    assert (await admin_client.put("/api/agents/verbose", json=body)).status_code == 200
    async with sf() as s:
        assert (await s.get(AgentDef, "verbose")).concurrency == 1


async def test_put_ignores_the_name_in_the_body(admin_client, sf):
    r = await admin_client.put("/api/agents/hello-world",
                               json=a_def("somebody-else", description="renamed?"))
    assert r.status_code == 200 and r.json()["name"] == "hello-world"
    async with sf() as s:
        assert await s.get(AgentDef, "somebody-else") is None
        assert (await s.get(AgentDef, "hello-world")).description == "renamed?"


async def test_put_that_changes_nothing_logs_nothing(admin_client, sf):
    current = (await admin_client.get("/api/agents/hello-world")).json()
    assert (await admin_client.put("/api/agents/hello-world", json=current)).status_code == 200
    assert await versions_of(sf, "hello-world") == []


async def test_put_unknown_agent_404(admin_client):
    assert (await admin_client.put("/api/agents/ghost",
                                   json=a_def("ghost"))).status_code == 404


async def test_put_rejects_unknown_grants(admin_client):
    r = await admin_client.put("/api/agents/hello-world",
                               json=a_def("hello-world", skills=["ghost"]))
    assert r.status_code == 422 and "ghost" in r.json()["detail"]


# --- delete ------------------------------------------------------------------

async def test_delete_removes_the_row_and_keeps_the_log(admin_client, sf, seed_agent,
                                                        agent_store):
    await seed_agent("temp")
    await agent_store.reload()
    await admin_client.put("/api/agents/temp", json=a_def("temp", description="v2"))
    assert (await admin_client.delete("/api/agents/temp")).status_code == 200
    async with sf() as s:
        assert await s.get(AgentDef, "temp") is None
    # The change log is append-only: it outlives the definition.
    assert [v.version for v in await versions_of(sf, "temp")] == [1]
    assert (await admin_client.delete("/api/agents/temp")).status_code == 404


async def test_delete_refuses_a_system_agent(admin_client, seed_agent, agent_store, sf):
    await seed_agent("run-summarizer", system=True)
    await agent_store.reload()
    r = await admin_client.delete("/api/agents/run-summarizer")
    assert r.status_code == 409
    async with sf() as s:
        assert await s.get(AgentDef, "run-summarizer") is not None


# --- change log + rollback ---------------------------------------------------

async def test_versions_list_omits_snapshots(admin_client):
    await admin_client.put("/api/agents/hello-world", json=a_def("hello-world", description="v2"))
    rows = (await admin_client.get("/api/agents/hello-world/versions")).json()
    assert len(rows) == 1
    v = rows[0]
    assert set(v) == {"version", "changed_by", "changed_via", "created_at"}
    assert v["changed_by"] == "admin" and v["changed_via"] == "admin"


async def test_version_detail_carries_the_snapshot(admin_client):
    await admin_client.put("/api/agents/hello-world", json=a_def("hello-world", description="v2"))
    r = await admin_client.get("/api/agents/hello-world/versions/1")
    assert r.status_code == 200
    body = r.json()
    assert body["version"] == 1 and body["changed_via"] == "admin"
    assert body["snapshot"]["description"] == "v2"
    assert (await admin_client.get("/api/agents/hello-world/versions/9")).status_code == 404


async def test_rollback_reapplies_a_snapshot_as_a_new_version(admin_client, sf):
    await admin_client.put("/api/agents/hello-world",
                           json=a_def("hello-world", description="v1", skills=["git"]))
    await admin_client.put("/api/agents/hello-world",
                           json=a_def("hello-world", description="v2"))
    r = await admin_client.post("/api/agents/hello-world/rollback/1")
    assert r.status_code == 200, r.text
    assert r.json()["description"] == "v1" and r.json()["skills"] == ["git"]
    async with sf() as s:
        assert (await s.get(AgentDef, "hello-world")).description == "v1"
    log = await versions_of(sf, "hello-world")
    assert [(v.version, v.changed_via) for v in log] == [
        (1, "admin"), (2, "admin"), (3, "rollback")]
    assert log[2].snapshot["description"] == "v1"
    assert (await admin_client.post("/api/agents/hello-world/rollback/9")).status_code == 404


async def test_rollback_is_admin_only(client, sf, seed_agent, agent_store):
    await seed_agent("editor", platform_tools=[AGENTS_EDIT, AGENTS_GRANT])
    await agent_store.reload()
    h = await bearer(sf, "editor")
    assert (await client.post("/api/agents/hello-world/rollback/1",
                              headers=h)).status_code == 403


# --- import ------------------------------------------------------------------

async def test_import_creates_updates_and_is_idempotent(admin_client, sf):
    payload = [a_def("hello-world", description="imported"), a_def("fresh")]
    r = await admin_client.post("/api/agents/import", json=payload)
    assert r.status_code == 200, r.text
    assert r.json() == [{"name": "hello-world", "status": "updated"},
                        {"name": "fresh", "status": "created"}]
    # Second run of the SAME payload changes nothing and logs nothing.
    r = await admin_client.post("/api/agents/import", json=payload)
    assert r.json() == [{"name": "hello-world", "status": "unchanged"},
                        {"name": "fresh", "status": "unchanged"}]
    assert [(v.version, v.changed_via)
            for v in await versions_of(sf, "hello-world")] == [(1, "import")]
    assert [(v.version, v.changed_via)
            for v in await versions_of(sf, "fresh")] == [(1, "import")]


async def test_import_rejects_a_bad_definition_without_writing_anything(admin_client, sf):
    r = await admin_client.post("/api/agents/import",
                                json=[a_def("good"), a_def("bad", skills=["ghost"])])
    assert r.status_code == 422
    async with sf() as s:
        assert await s.get(AgentDef, "good") is None      # all-or-nothing


async def test_import_is_admin_only(client, sf, seed_agent, agent_store):
    await seed_agent("editor", platform_tools=[AGENTS_EDIT, AGENTS_GRANT])
    await agent_store.reload()
    h = await bearer(sf, "editor")
    r = await client.post("/api/agents/import", json=[a_def("x")], headers=h)
    assert r.status_code == 403


# --- RBAC: agents_edit vs agents_grant ---------------------------------------

async def test_an_agent_with_neither_tool_cannot_write(client, sf, seed_agent, agent_store):
    await seed_agent("plain")
    await agent_store.reload()
    h = await bearer(sf, "plain")
    r = await client.put("/api/agents/hello-world",
                         json=a_def("hello-world", description="mine now"), headers=h)
    assert r.status_code == 403


async def test_agents_edit_may_change_prose_but_not_grants(client, sf, seed_agent,
                                                           agent_store):
    """The escalation boundary: `agents_edit` writes what an agent IS, never
    what it may DO."""
    await seed_agent("editor", platform_tools=[AGENTS_EDIT])
    await agent_store.reload()
    h = await bearer(sf, "editor")
    r = await client.put("/api/agents/hello-world",
                         json=a_def("hello-world", description="edited by a tool"),
                         headers=h)
    assert r.status_code == 200, r.text
    log = await versions_of(sf, "hello-world")
    assert log[-1].changed_via == "tool:agents_edit"
    assert log[-1].changed_by == "sa:editor" or log[-1].changed_by.endswith("editor")

    r = await client.put("/api/agents/hello-world",
                         json=a_def("hello-world", description="edited by a tool",
                                    skills=["git"]), headers=h)
    assert r.status_code == 403 and "skills" in r.json()["detail"]
    async with sf() as s:
        assert (await s.get(AgentDef, "hello-world")).skills == []
    # can_invoke is a grant too: it is what makes the launcher mint an
    # operator-scoped run token, i.e. permission to start other agents.
    r = await client.put("/api/agents/hello-world",
                         json=a_def("hello-world", description="edited by a tool",
                                    can_invoke=True), headers=h)
    assert r.status_code == 403 and "can_invoke" in r.json()["detail"]


async def test_agents_grant_may_change_grants_but_not_prose(client, sf, seed_agent,
                                                            agent_store):
    await seed_agent("granter", platform_tools=[AGENTS_GRANT])
    await agent_store.reload()
    h = await bearer(sf, "granter")
    r = await client.put("/api/agents/hello-world",
                         json=a_def("hello-world", description="test", skills=["git"]),
                         headers=h)
    assert r.status_code == 200, r.text
    log = await versions_of(sf, "hello-world")
    assert log[-1].changed_via == "tool:agents_grant"

    r = await client.put("/api/agents/hello-world",
                         json=a_def("hello-world", description="reworded",
                                    skills=["git"]), headers=h)
    assert r.status_code == 403 and "description" in r.json()["detail"]


async def test_a_mixed_change_needs_both_tools(client, sf, seed_agent, agent_store):
    await seed_agent("editor", platform_tools=[AGENTS_EDIT])
    await seed_agent("both", platform_tools=[AGENTS_EDIT, AGENTS_GRANT])
    await agent_store.reload()
    mixed = a_def("hello-world", description="new words", skills=["git"])
    assert (await client.put("/api/agents/hello-world", json=mixed,
                             headers=await bearer(sf, "editor"))).status_code == 403
    r = await client.put("/api/agents/hello-world", json=mixed,
                         headers=await bearer(sf, "both"))
    assert r.status_code == 200, r.text
    # A change touching grants is attributed to the escalation-capable tool.
    assert (await versions_of(sf, "hello-world"))[-1].changed_via == "tool:agents_grant"


async def test_an_agent_may_not_flip_the_system_flag(client, sf, seed_agent, agent_store):
    """`system` protects an agent from deletion and gets it platform
    credentials injected — it is not something a tool may hand itself."""
    await seed_agent("both", platform_tools=[AGENTS_EDIT, AGENTS_GRANT])
    await agent_store.reload()
    h = await bearer(sf, "both")
    r = await client.put("/api/agents/hello-world",
                         json=a_def("hello-world", description="test", system=True),
                         headers=h)
    assert r.status_code == 403 and "system" in r.json()["detail"]


async def test_an_admin_may_flip_the_system_flag(admin_client):
    r = await admin_client.put("/api/agents/hello-world",
                               json=a_def("hello-world", description="test", system=True))
    assert r.status_code == 200 and r.json()["system"] is True


async def test_creating_an_agent_with_grants_needs_agents_grant(client, sf, seed_agent,
                                                                agent_store):
    """Otherwise `agents_edit` escalates by the side door: mint a new agent
    already holding the keys."""
    await seed_agent("editor", platform_tools=[AGENTS_EDIT])
    await agent_store.reload()
    h = await bearer(sf, "editor")
    r = await client.post("/api/agents",
                          json=a_def("puppet", platform_tools=["mcp__platform__runs_read"]),
                          headers=h)
    assert r.status_code == 403 and "platform_tools" in r.json()["detail"]
    r = await client.post("/api/agents", json=a_def("plainpuppet"), headers=h)
    assert r.status_code == 201, r.text


async def test_a_frozen_run_token_cannot_be_widened_mid_run(client, sf, seed_agent,
                                                            agent_store, monkeypatch):
    """design/13 C: the grant set froze at launch. Adding `agents_grant` to the
    row mid-run must not widen the run that is already using its token."""
    from agentplatform.api import agents as agents_api
    await seed_agent("frozen", platform_tools=[AGENTS_EDIT, AGENTS_GRANT])
    await agent_store.reload()
    h = await bearer(sf, "frozen")
    # Stand in for a run JWT that froze only agents_edit at launch.
    original = agents_api.authenticate

    async def _frozen(request):
        ident = await original(request)
        if ident is not None:
            request.state.frozen_tools = [AGENTS_EDIT]
        return ident

    monkeypatch.setattr(agents_api, "authenticate", _frozen)
    r = await client.put("/api/agents/hello-world",
                         json=a_def("hello-world", description="test", skills=["git"]),
                         headers=h)
    assert r.status_code == 403


# --- the `enabled` switch is enforced ----------------------------------------

async def test_a_disabled_agent_refuses_runs_and_conversations(admin_client, sf,
                                                               seed_agent, agent_store):
    await seed_agent("napping", enabled=False)
    await agent_store.reload()
    r = await admin_client.post("/api/runs", json={"agent": "napping", "prompt": "hi"})
    assert r.status_code == 409 and "disabled" in r.json()["detail"]
    r = await admin_client.post("/api/conversations",
                                json={"connector": "web", "agent": "napping"})
    assert r.status_code == 409 and "disabled" in r.json()["detail"]


async def test_disabling_an_agent_stops_its_in_flight_conversation(admin_client, sf,
                                                                   agent_store):
    conv = (await admin_client.post("/api/conversations",
                                    json={"connector": "web", "agent": "hello-world"})).json()
    body = (await admin_client.get("/api/agents/hello-world")).json()
    body["enabled"] = False
    assert (await admin_client.put("/api/agents/hello-world", json=body)).status_code == 200
    await agent_store.reload()
    r = await admin_client.post(f"/api/conversations/{conv['id']}/messages",
                                json={"text": "still there?"})
    assert r.status_code == 409 and "disabled" in r.json()["detail"]
