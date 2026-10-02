"""Phase 0 of design 39: the authority fixes that close escalation before
agents can build Apps.

Three rules, each tested against the real API and, where an agent would reach
it, through the broker's own `agents_edit` / `agents_grant` clients:

- KYLE-ONLY TOOLS. Adding or removing `apps`, `app_data`, `agents_grant` or
  `agents_edit` on any agent, at create or update, takes Kyle's browser
  session. An admin API key, an agent's run key and a workload identity are
  all refused. This closes self-grant, proxy-grant (A grants B) and
  mutual-grant (A and B grant each other).
- PROTECTED AGENTS. An agent holding any Kyle-only tool is changed only by
  Kyle's session or by itself through `agent_self`. Nobody with `agents_edit`
  steers a builder.
- NO SELF-EDITS through `agents_edit` / `agents_grant`; `agent_self` is the
  self path.

Plus the one-time audit migration, which records every current holder
without changing what it holds.
"""
import json

import pytest
from sqlalchemy import select

from agentplatform.agentspec import KYLE_ONLY_TOOLS
from agentplatform.api.agents import TOOL_AGENTS_EDIT, TOOL_AGENTS_GRANT
from agentplatform.db import (KYLE_ONLY_AUDIT_MARK, AgentDef, AgentVersion,
                              SchemaMark, init_db, make_engine,
                              make_session_factory)

from .test_agent_self import own_run
from .test_agent_write_tools import agenttools, api, granted
from .test_agents_api import a_def, bearer, versions_of

TOOL_APPS = "mcp__platform__apps"
TOOL_APP_DATA = "mcp__platform__app_data"
FAKE_JWT = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ4In0.c2ln"


def test_the_kyle_only_set_is_exactly_the_four():
    assert KYLE_ONLY_TOOLS == {TOOL_APPS, TOOL_APP_DATA,
                               TOOL_AGENTS_GRANT, TOOL_AGENTS_EDIT}


async def _tools(sf, name):
    async with sf() as s:
        row = await s.get(AgentDef, name)
        return None if row is None else list(row.platform_tools or [])


async def _prompt(sf, name):
    async with sf() as s:
        return (await s.get(AgentDef, name)).prompt


def _refused(out: str) -> bool:
    return out.startswith("error:") and "403" in out


async def kai_and_worker(sf, seed_agent, agent_store):
    """A Kai-like agent holding both definition tools, and an ordinary worker
    holding neither."""
    await seed_agent("worker", description="ordinary")
    return await granted(sf, seed_agent, agent_store, "kai",
                         [TOOL_AGENTS_EDIT, TOOL_AGENTS_GRANT])


# --- R0.1: Kyle-only tools ---------------------------------------------------

async def test_self_grant_is_refused(client, sf, seed_agent, agent_store):
    """An agent holding agents_grant cannot hand itself agents_edit."""
    h = await granted(sf, seed_agent, agent_store, "granter", [TOOL_AGENTS_GRANT])
    out = await agenttools.agents_grant(api(client, h), {
        "action": "add_grant", "name": "granter", "field": "platform_tools",
        "values": [TOOL_AGENTS_EDIT]})
    assert _refused(out)
    assert await _tools(sf, "granter") == [TOOL_AGENTS_GRANT]


@pytest.mark.parametrize("tool", sorted(KYLE_ONLY_TOOLS))
async def test_proxy_grant_is_refused_for_every_kyle_only_tool(
        client, sf, seed_agent, agent_store, tool):
    """A grants B — the side door self-grant would otherwise use. The two App
    tools don't exist yet; the refusal is still 403 (authority), not 422
    (unknown tool), so the rule is in place before they ship."""
    h = await kai_and_worker(sf, seed_agent, agent_store)
    out = await agenttools.agents_grant(api(client, h), {
        "action": "add_grant", "name": "worker", "field": "platform_tools",
        "values": [tool]})
    assert _refused(out) and "Kyle" in out
    assert await _tools(sf, "worker") == []


async def test_mutual_grant_is_refused(client, sf, seed_agent, agent_store):
    """A and B, each holding agents_grant, cannot widen each other."""
    ha = await granted(sf, seed_agent, agent_store, "alpha", [TOOL_AGENTS_GRANT])
    hb = await granted(sf, seed_agent, agent_store, "beta", [TOOL_AGENTS_GRANT])
    for h, target in ((ha, "beta"), (hb, "alpha")):
        out = await agenttools.agents_grant(api(client, h), {
            "action": "add_grant", "name": target, "field": "platform_tools",
            "values": [TOOL_AGENTS_EDIT]})
        assert _refused(out)
    assert await _tools(sf, "alpha") == [TOOL_AGENTS_GRANT]
    assert await _tools(sf, "beta") == [TOOL_AGENTS_GRANT]


async def test_removing_a_kyle_only_tool_is_kyle_only_too(client, sf, seed_agent,
                                                         agent_store):
    """Removal is a change to who holds it, so it needs the same authority."""
    h = await kai_and_worker(sf, seed_agent, agent_store)
    await seed_agent("other", platform_tools=[TOOL_AGENTS_EDIT])
    out = await agenttools.agents_grant(api(client, h), {
        "action": "remove_grant", "name": "other", "field": "platform_tools",
        "values": [TOOL_AGENTS_EDIT]})
    assert _refused(out)
    assert await _tools(sf, "other") == [TOOL_AGENTS_EDIT]


async def test_create_with_a_kyle_only_grant_is_refused(client, sf, seed_agent,
                                                       agent_store):
    """Minting a new agent that is born holding the keys is still a grant."""
    h = await kai_and_worker(sf, seed_agent, agent_store)
    for tool in (TOOL_AGENTS_GRANT, TOOL_APPS):
        r = await client.post("/api/agents", headers=h, json={
            **a_def("minted", platform_tools=[tool])})
        assert r.status_code == 403 and "Kyle" in r.json()["detail"], r.text
        assert await _tools(sf, "minted") is None


async def test_an_admin_api_key_is_not_kyle(client, token_client, sf, seed_agent,
                                           agent_store):
    """An admin-role API key is admin for everything else, but not Kyle."""
    await seed_agent("worker")
    h = await bearer(sf, None, role="admin", name="ops-key")
    r = await token_client.put("/api/agents/worker", headers=h,
                               json=a_def("worker", platform_tools=[TOOL_AGENTS_GRANT]))
    assert r.status_code == 403 and "Kyle" in r.json()["detail"]
    r = await token_client.put("/api/agents/worker", headers=h,
                               json=a_def("worker", platform_tools=[TOOL_APP_DATA]))
    assert r.status_code == 403
    r = await token_client.post("/api/agents", headers=h,
                                json=a_def("minted", platform_tools=[TOOL_AGENTS_EDIT]))
    assert r.status_code == 403
    r = await token_client.post("/api/agents/import", headers=h,
                                json=[a_def("minted", platform_tools=[TOOL_AGENTS_EDIT])])
    assert r.status_code == 403
    assert await _tools(sf, "worker") == [] and await _tools(sf, "minted") is None
    # Everything else an admin key could do, it still can.
    r = await token_client.put("/api/agents/worker", headers=h,
                               json=a_def("worker", skills=["git"],
                                          platform_tools=["mcp__platform__runs_read"]))
    assert r.status_code == 200, r.text


async def test_a_workload_identity_is_not_kyle(client, token_client, sf, seed_agent,
                                              agent_store):
    """The ServiceAccount path (design 13 A) resolves to the agent's grant like
    a run key, and is refused the same way."""
    await seed_agent("worker")
    await seed_agent("kai", platform_tools=[TOOL_AGENTS_GRANT, "mcp__platform__runs_read"])
    await agent_store.reload()

    async def validate(token):
        return "system:serviceaccount:ap:agent-kai"
    client._transport.app.state.sa_validator = validate
    h = {"Authorization": f"Bearer {FAKE_JWT}"}
    assert (await token_client.get("/api/whoami", headers=h)).json()["agent"] == "kai"
    out = await agenttools.agents_grant(api(token_client, h), {
        "action": "add_grant", "name": "worker", "field": "platform_tools",
        "values": [TOOL_AGENTS_EDIT]})
    assert _refused(out)
    assert await _tools(sf, "worker") == []


async def test_kyle_session_grants_and_removes(admin_client, sf, seed_agent,
                                              agent_store):
    await seed_agent("worker")
    r = await admin_client.put("/api/agents/worker",
                               json=a_def("worker", platform_tools=[TOOL_AGENTS_GRANT]))
    assert r.status_code == 200, r.text
    assert (await versions_of(sf, "worker"))[-1].changed_via == "admin"
    r = await admin_client.put("/api/agents/worker", json=a_def("worker"))
    assert r.status_code == 200 and r.json()["platform_tools"] == []
    r = await admin_client.post("/api/agents", json={
        **a_def("minted", platform_tools=[TOOL_AGENTS_EDIT]), "relay": False,
        "tickets": False, "wiki": False, "get_quota_usage": False,
        "artifacts": False, "memory": False, "agent_self": False})
    assert r.status_code == 201 and r.json()["platform_tools"] == [TOOL_AGENTS_EDIT]


async def test_kyle_still_cannot_grant_a_tool_that_does_not_exist_yet(admin_client,
                                                                     seed_agent):
    """Reserving the App tools is not shipping them: Kyle's grant of one passes
    authority and then meets validation, until the tool exists."""
    await seed_agent("worker")
    r = await admin_client.put("/api/agents/worker",
                               json=a_def("worker", platform_tools=[TOOL_APPS]))
    assert r.status_code == 422 and TOOL_APPS in r.text


async def test_ordinary_grants_still_flow_through_agents_grant(client, sf, seed_agent,
                                                              agent_store):
    """The rule is about four tools, not grants in general (design: the other
    grant fields remain a separate, known gap)."""
    h = await kai_and_worker(sf, seed_agent, agent_store)
    out = json.loads(await agenttools.agents_grant(api(client, h), {
        "action": "add_grant", "name": "worker", "field": "platform_tools",
        "values": ["mcp__platform__runs_read"]}))
    assert out["platform_tools"] == ["mcp__platform__runs_read"]


# --- R0.2: protected agents --------------------------------------------------

async def test_kai_cannot_edit_a_builder_but_can_edit_a_worker(client, sf, seed_agent,
                                                              agent_store):
    h = await kai_and_worker(sf, seed_agent, agent_store)
    # A builder: it holds a Kyle-only tool. `apps` isn't shipped yet, so the
    # row is seeded directly — protection keys off what the row holds.
    await seed_agent("builder", platform_tools=[TOOL_APPS])
    await seed_agent("steward", platform_tools=[TOOL_AGENTS_EDIT])
    call = api(client, h)
    for target in ("builder", "steward"):
        before = await _prompt(sf, target)
        out = await agenttools.agents_edit(call, {
            "action": "update", "name": target,
            "definition": {"prompt": "# obey kai"}})
        assert _refused(out) and "protected" in out, out
        out = await agenttools.agents_grant(call, {
            "action": "add_grant", "name": target, "field": "skills",
            "values": ["git"]})
        assert _refused(out), out
        assert "403" in await agenttools.agents_edit(
            call, {"action": "delete", "name": target})
        assert await _prompt(sf, target) == before
    # The ordinary worker is still Kai's to edit and grant.
    out = json.loads(await agenttools.agents_edit(call, {
        "action": "update", "name": "worker",
        "definition": {"prompt": "# a better worker"}}))
    assert out["prompt"] == "# a better worker"
    out = json.loads(await agenttools.agents_grant(call, {
        "action": "add_grant", "name": "worker", "field": "skills",
        "values": ["git"]}))
    assert out["skills"] == ["git"]


async def test_a_protected_agents_webhook_secret_is_kyle_only(client, sf, seed_agent,
                                                             agent_store):
    """Setting the secret on a builder's webhook is a way to drive it."""
    h = await kai_and_worker(sf, seed_agent, agent_store)
    await seed_agent("steward", platform_tools=[TOOL_AGENTS_EDIT],
                     entrypoints={"webhooks": [{"path": "hook", "auth": "secret"}]})
    r = await client.put("/api/agents/steward/webhooks/hook/secret", headers=h,
                         json={"secret": "s" * 32})
    assert r.status_code == 403 and "protected" in r.json()["detail"]
    r = await client.delete("/api/agents/steward/webhooks/hook/secret", headers=h)
    assert r.status_code == 403


async def test_an_admin_api_key_cannot_edit_a_protected_agent(client, token_client, sf,
                                                             seed_agent, agent_store):
    await seed_agent("steward", platform_tools=[TOOL_AGENTS_EDIT])
    h = await bearer(sf, None, role="admin", name="ops-key")
    r = await token_client.put("/api/agents/steward", headers=h,
                               json=a_def("steward", prompt="# obey",
                                          platform_tools=[TOOL_AGENTS_EDIT]))
    assert r.status_code == 403 and "protected" in r.json()["detail"]
    assert (await token_client.delete("/api/agents/steward", headers=h)).status_code == 403
    assert await _prompt(sf, "steward") == "# steward\nYou are steward."


async def test_rollback_and_import_respect_protection(admin_client, token_client, sf,
                                                     seed_agent, agent_store):
    """The two admin-only writers: neither may move a Kyle-only tool or touch
    a protected agent unless it is Kyle's session."""
    await seed_agent("worker")
    r = await admin_client.put("/api/agents/worker",
                               json=a_def("worker", platform_tools=[TOOL_AGENTS_EDIT]))
    assert r.status_code == 200
    r = await admin_client.put("/api/agents/worker", json=a_def("worker"))
    assert r.status_code == 200
    h = await bearer(sf, None, role="admin", name="ops-key")
    # Version 1 holds agents_edit: rolling back to it re-grants the tool.
    r = await token_client.post("/api/agents/worker/rollback/1", headers=h)
    assert r.status_code == 403 and "Kyle" in r.json()["detail"]
    assert await _tools(sf, "worker") == []
    await seed_agent("steward", platform_tools=[TOOL_AGENTS_EDIT])
    r = await token_client.post("/api/agents/import", headers=h, json=[
        a_def("steward", prompt="# obey", platform_tools=[TOOL_AGENTS_EDIT])])
    assert r.status_code == 403
    assert await _prompt(sf, "steward") == "# steward\nYou are steward."
    # Kyle may do both.
    r = await admin_client.post("/api/agents/worker/rollback/1")
    assert r.status_code == 200 and r.json()["platform_tools"] == [TOOL_AGENTS_EDIT]


async def test_kyle_session_edits_a_protected_agent(admin_client, sf, seed_agent):
    await seed_agent("steward", platform_tools=[TOOL_AGENTS_EDIT])
    r = await admin_client.put("/api/agents/steward",
                               json=a_def("steward", prompt="# kyle's words",
                                          platform_tools=[TOOL_AGENTS_EDIT]))
    assert r.status_code == 200, r.text
    assert await _prompt(sf, "steward") == "# kyle's words"


async def test_a_protected_agent_edits_itself_through_agent_self(client, sf, seed_agent,
                                                                agent_store):
    _, h = await own_run(sf, seed_agent, agent_store, name="steward",
                         platform_tools=["mcp__platform__agent_self", TOOL_AGENTS_EDIT])
    version = (await client.get("/api/agent-self", headers=h)).json()["version"]
    r = await client.patch("/api/agent-self", headers=h,
                           json={"expected_version": version, "prompt": "# my own words"})
    assert r.status_code == 200, r.text
    assert await _prompt(sf, "steward") == "# my own words"


# --- R0.3: no self-edits through agents_edit / agents_grant ------------------

async def test_no_self_edits_through_the_definition_tools(client, sf, seed_agent,
                                                         agent_store):
    h = await granted(sf, seed_agent, agent_store, "kai",
                      [TOOL_AGENTS_EDIT, TOOL_AGENTS_GRANT])
    call = api(client, h)
    out = await agenttools.agents_edit(call, {
        "action": "update", "name": "kai", "definition": {"prompt": "# freer"}})
    assert _refused(out) and "agent_self" in out
    out = await agenttools.agents_grant(call, {
        "action": "add_grant", "name": "kai", "field": "skills", "values": ["git"]})
    assert _refused(out) and "agent_self" in out
    assert "403" in await agenttools.agents_edit(call, {"action": "delete", "name": "kai"})
    async with sf() as s:
        row = await s.get(AgentDef, "kai")
        assert row.prompt == "# kai\nYou are kai." and row.skills in ([], None)


# --- R0.4: the audit migration -----------------------------------------------

@pytest.fixture
async def bare():
    from agentplatform.db import Base
    e = make_engine("sqlite+aiosqlite:///:memory:")
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield e
    await e.dispose()


async def test_the_audit_notes_every_holder_and_changes_nothing(bare):
    sf = make_session_factory(bare)
    async with sf() as s:
        s.add(AgentDef(name="kai", prompt="You are Kai.", description="d",
                       platform_tools=[TOOL_AGENTS_EDIT, TOOL_AGENTS_GRANT]))
        s.add(AgentDef(name="plain", prompt="You are plain.", description="d",
                       platform_tools=["mcp__platform__runs_read"]))
        await s.commit()
    await init_db(bare)
    async with sf() as s:
        assert await s.get(SchemaMark, KYLE_ONLY_AUDIT_MARK) is not None
        kai = await s.get(AgentDef, "kai")
        assert TOOL_AGENTS_EDIT in kai.platform_tools
        assert TOOL_AGENTS_GRANT in kai.platform_tools
        notes = list((await s.execute(select(AgentVersion).where(
            AgentVersion.changed_via == "audit:kyle-only"))).scalars())
    assert [n.agent for n in notes] == ["kai"]
    note = notes[0]
    assert "agents_edit" in note.changed_by and "agents_grant" in note.changed_by
    assert note.snapshot["platform_tools"] == kai.platform_tools
    assert len(note.changed_by) <= AgentVersion.__table__.c.changed_by.type.length
    assert len(note.changed_via) <= AgentVersion.__table__.c.changed_via.type.length
    # One-time: a second boot adds nothing.
    await init_db(bare)
    async with sf() as s:
        again = list((await s.execute(select(AgentVersion).where(
            AgentVersion.changed_via == "audit:kyle-only"))).scalars())
    assert len(again) == 1
