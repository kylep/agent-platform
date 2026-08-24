"""The `agents_edit` / `agents_grant` platform tools (docs/design/15, Task 4).

The tools live in the MCP broker (`services/mcp-broker/agenttools.py`), because
they forward the caller's own bearer to this API — a tool-executor custom tool
never receives one, and would have to write definitions under a shared
credential, which is exactly the attribution the design forbids. They are
therefore tested HERE rather than as a `tools/*/test_run.py`: their whole
contract is the conversation with this API, and the backend suite is the one
place both halves can be driven as the single mechanism they are. The module is
loaded by path (the broker is not an installed package) and needs nothing but
the stdlib.

Covered: the registry/help surface, the widened definition reads, and the
security invariants — an `agents_edit` holder cannot escalate through the tool
OR through the raw API, an `agents_grant` holder cannot rewrite prose, neither
tool is reachable without the grant, and `changed_by` is never an argument.
"""
import importlib.util
import json

from sqlalchemy import select

from agentplatform.agentspec import (GRANTABLE_PLATFORM_TOOLS,
                                     PLATFORM_MCP_AGENT_TOOLS,
                                     PLATFORM_MCP_TOOLS)
from agentplatform.api.agents import (EDIT_FIELDS, GRANT_FIELDS,
                                      TOOL_AGENTS_EDIT, TOOL_AGENTS_GRANT)
from agentplatform.db import AgentDef, AgentVersion

from .conftest import REPO_ROOT
from .test_agents_api import a_def, bearer, versions_of


def _load_agenttools():
    path = REPO_ROOT / "services" / "mcp-broker" / "agenttools.py"
    spec = importlib.util.spec_from_file_location("agenttools", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


agenttools = _load_agenttools()


def api(client, headers):
    """The broker's `call` seam, wired to the real ASGI app with a real agent
    bearer — the same forwarding the broker does with `_request`."""
    async def call(method, path, params=None, json=None):
        return await client.request(method, path, params=params, json=json,
                                    headers=headers)
    return call


async def granted(sf, seed_agent, agent_store, name, tools, **fields):
    """An agent holding `tools`, plus the bearer its runs would present."""
    await seed_agent(name, platform_tools=list(tools), **fields)
    await agent_store.reload()
    return await bearer(sf, name)


# --- registry / docs surface --------------------------------------------------

def test_the_tools_are_grantable_but_not_on_the_annotator_rung():
    """The whole ruling in one assertion: they are real, grantable broker tools
    (so a definition may name them and the picker offers them), and they are
    NOT in the list that promotes a run token to `annotator` — a
    definition-writing grant must not widen the rest of the API."""
    for t in (TOOL_AGENTS_EDIT, TOOL_AGENTS_GRANT):
        assert t in PLATFORM_MCP_AGENT_TOOLS
        assert t in GRANTABLE_PLATFORM_TOOLS
        assert t not in PLATFORM_MCP_TOOLS


def test_a_custom_tool_may_not_shadow_them():
    from agentplatform.toolregistry import CORE_TOOL_SUFFIXES
    assert {"agents_edit", "agents_grant"} <= CORE_TOOL_SUFFIXES


async def test_help_documents_both_with_their_cautions(admin_client):
    rows = {t["name"]: t for t in (await admin_client.get("/api/help/tools")).json()}
    edit, grant = rows[TOOL_AGENTS_EDIT], rows[TOOL_AGENTS_GRANT]
    assert edit["kind"] == grant["kind"] == "platform"
    # agents_grant is flagged as grants-editing, per the brief.
    assert "grants-editing" in grant["description"].lower()
    assert "care" in grant["description"].lower()
    # agents_edit's accepted risk: the guard is on the kind of change, not the
    # target, so it reaches MORE privileged agents.
    assert "privileged" in edit["description"].lower()
    assert "agents_grant" in edit["description"]


async def test_a_definition_may_grant_them(admin_client):
    """validate_def sources its tool vocabulary from the grantable list, not
    the ladder list — otherwise the grant these tools exist for is unsavable."""
    r = await admin_client.post("/api/agents", json=a_def(
        "steward", platform_tools=[TOOL_AGENTS_EDIT, TOOL_AGENTS_GRANT]))
    assert r.status_code == 201, r.text
    assert r.json()["platform_tools"] == [TOOL_AGENTS_EDIT, TOOL_AGENTS_GRANT]


def test_the_broker_copy_of_the_field_split_matches_the_api():
    """Two services, so the split is spelled out twice. Drift would either
    block legal edits or send payloads the API is bound to refuse."""
    assert set(agenttools.GRANT_FIELDS) == set(GRANT_FIELDS)
    assert agenttools.API_EDIT_FIELDS == EDIT_FIELDS
    assert set(agenttools.GRANT_LIST_FIELDS) < set(GRANT_FIELDS)
    # What the tool offers is the API's editorial half minus the fields the API
    # reserves for an admin — advertised surface == real surface.
    assert set(agenttools.ADMIN_ONLY_FIELDS) < set(EDIT_FIELDS)
    assert agenttools.EDITABLE_FIELDS == tuple(
        f for f in EDIT_FIELDS if f not in agenttools.ADMIN_ONLY_FIELDS)


# --- seed state ---------------------------------------------------------------

async def test_a_fresh_environment_has_no_holder(client, sf):
    """design/15's seed state: the tools exist, and nobody has them. Granting
    them is a deliberate act, never something the platform ships switched on.

    Its file-globbing twin (over `agents/*/agent.md`) went out with the tree —
    an empty glob would have kept passing for the wrong reason. Definitions are
    rows, so the rows are where the seed state has to be checked."""
    async with sf() as s:
        rows = (await s.execute(select(AgentDef))).scalars().all()
    assert rows, "the fixture seeds at least one agent"
    for row in rows:
        assert TOOL_AGENTS_EDIT not in (row.platform_tools or [])
        assert TOOL_AGENTS_GRANT not in (row.platform_tools or [])


# --- deliverable 2: if you can edit an agent, you can read it -----------------

READS = ("/api/agents", "/api/agents/hello-world",
         "/api/agents/hello-world/versions", "/api/agents/hello-world/versions/1")


async def _seed_a_version(sf):
    """One change-log row, so `/versions/1` is a real resource to authorize."""
    async with sf() as s:
        s.add(AgentVersion(agent="hello-world", version=1, snapshot={},
                           changed_by="admin", changed_via="admin"))
        await s.commit()


async def test_an_editor_agent_may_read_definitions(client, sf, seed_agent,
                                                    agent_store):
    await _seed_a_version(sf)
    h = await granted(sf, seed_agent, agent_store, "editor", [TOOL_AGENTS_EDIT])
    for path in READS:
        r = await client.get(path, headers=h)
        assert r.status_code == 200, f"{path} → {r.status_code} {r.text}"


async def test_a_granter_agent_may_read_definitions(client, sf, seed_agent,
                                                    agent_store):
    await _seed_a_version(sf)
    h = await granted(sf, seed_agent, agent_store, "granter", [TOOL_AGENTS_GRANT])
    for path in READS:
        assert (await client.get(path, headers=h)).status_code == 200, path


async def test_a_plain_tools_agent_still_may_not_read(client, sf, seed_agent,
                                                      agent_store):
    """The widening follows the WRITE grant, not the `tools` rung: an agent
    with an unrelated custom tool sees exactly as much as before (nothing)."""
    await _seed_a_version(sf)
    h = await granted(sf, seed_agent, agent_store, "plain",
                      ["mcp__platform__memory"])
    for path in READS:
        assert (await client.get(path, headers=h)).status_code == 403, path


async def test_reads_still_need_a_caller(client):
    for path in READS:
        assert (await client.get(path)).status_code == 401, path


async def test_the_widening_is_scoped_to_the_definition_routes(client, sf,
                                                               seed_agent, agent_store):
    """`agents_edit` is not a general read grant. Everything else the `tools`
    rung could not reach, it still cannot reach — including /api/agent-models,
    which is a UI picker rather than part of a definition."""
    h = await granted(sf, seed_agent, agent_store, "editor", [TOOL_AGENTS_EDIT])
    for path in ("/api/agent-models", "/api/runs", "/api/metrics/overview",
                 "/api/memories", "/api/tools"):
        assert (await client.get(path, headers=h)).status_code == 403, path


# --- the broker-side gate -----------------------------------------------------

def test_the_broker_refuses_and_audits_an_ungranted_call():
    for tool in ("agents_edit", "agents_grant"):
        refused, decision = agenttools.guard({"agent": "plain", "tools": []}, tool)
        assert refused and tool in refused and decision == "deny:undeclared"
        refused, decision = agenttools.guard(None, tool)
        assert refused and decision == "deny:unauthenticated"
        ok, decision = agenttools.guard(
            {"agent": "editor", "tools": [f"mcp__platform__{tool}"]}, tool)
        assert ok is None and decision == "allow"


def test_holding_one_tool_does_not_unlock_the_other():
    refused, _ = agenttools.guard(
        {"agent": "editor", "tools": [TOOL_AGENTS_EDIT]}, "agents_grant")
    assert refused is not None


async def test_whoami_is_where_that_grant_set_comes_from(client, sf, seed_agent,
                                                         agent_store):
    """The gate reads /api/whoami's `tools`, so the deny above is only as good
    as this: an ungranted agent's answer must be empty, not everything."""
    h = await granted(sf, seed_agent, agent_store, "plain", [])
    body = (await client.get("/api/whoami", headers=h)).json()
    assert body["agent"] == "plain" and body["tools"] == []
    assert agenttools.guard(body, "agents_edit")[0] is not None


# --- agents_edit: the tool, against the real API ------------------------------

async def test_agents_edit_reads_and_writes_definitions(client, sf, seed_agent,
                                                        agent_store):
    h = await granted(sf, seed_agent, agent_store, "editor", [TOOL_AGENTS_EDIT])
    call = api(client, h)

    rows = json.loads(await agenttools.agents_edit(call, {"action": "list"}))
    assert {r["name"] for r in rows} >= {"hello-world", "editor"}
    # The listing is a compact projection — prompts are what `get` is for.
    assert all("prompt" not in r for r in rows)

    got = json.loads(await agenttools.agents_edit(
        call, {"action": "get", "name": "hello-world"}))
    assert got["name"] == "hello-world" and "prompt" in got

    out = json.loads(await agenttools.agents_edit(call, {
        "action": "create", "name": "helper",
        "definition": {"description": "made by a tool", "prompt": "# helper"}}))
    assert out["name"] == "helper" and out["description"] == "made by a tool"

    out = json.loads(await agenttools.agents_edit(call, {
        "action": "update", "name": "helper",
        "definition": {"description": "reworded"}}))
    assert out["description"] == "reworded" and out["prompt"] == "# helper"

    out = json.loads(await agenttools.agents_edit(
        call, {"action": "delete", "name": "helper"}))
    assert out["deleted"] == "helper"
    log = await versions_of(sf, "helper")
    assert [v.changed_via for v in log] == ["tool:agents_edit", "tool:agents_edit",
                                            "delete:tool:agents_edit"]
    assert all(v.changed_by.endswith("editor") for v in log)


async def test_an_update_leaves_the_agents_grants_exactly_as_they_were(
        client, sf, seed_agent, agent_store):
    """The reason the tool is read-modify-write. A full-replacement PUT built
    from the caller's fields alone would blank every grant — which is a grant
    change, which `agents_edit` may not make, so the edit would 403 on agents
    that hold anything at all."""
    await seed_agent("target", skills=["git"], can_invoke=True, role="coder",
                     platform_tools=["mcp__platform__runs_read"])
    h = await granted(sf, seed_agent, agent_store, "editor", [TOOL_AGENTS_EDIT])
    out = json.loads(await agenttools.agents_edit(api(client, h), {
        "action": "update", "name": "target",
        "definition": {"description": "new words"}}))
    assert out["description"] == "new words"
    assert out["skills"] == ["git"] and out["can_invoke"] is True
    assert out["role"] == "coder"
    assert out["platform_tools"] == ["mcp__platform__runs_read"]
    assert (await versions_of(sf, "target"))[-1].changed_via == "tool:agents_edit"


async def test_agents_edit_refuses_grant_fields_by_name(client, sf, seed_agent,
                                                        agent_store):
    """Refused, not silently dropped: a model told "done" when nothing was
    granted will build on a grant that does not exist."""
    h = await granted(sf, seed_agent, agent_store, "editor", [TOOL_AGENTS_EDIT])
    call = api(client, h)
    for field, value in (("skills", ["git"]), ("secrets", ["discord"]),
                         ("harness_tools", ["Bash"]), ("platform_tools", []),
                         ("can_invoke", True), ("role", "coder")):
        for action in ("update", "create"):
            out = await agenttools.agents_edit(call, {
                "action": action, "name": "hello-world" if action == "update" else "n1",
                "definition": {"description": "x", field: value}})
            assert out.startswith("error:") and field in out
            assert "agents_grant" in out
    async with sf() as s:
        row = await s.get(AgentDef, "hello-world")
        assert row.skills == [] and row.role == "operator" and not row.can_invoke
        assert await s.get(AgentDef, "n1") is None


async def test_the_system_flag_is_refused_with_its_own_reason(client, sf, seed_agent,
                                                              agent_store):
    """`system` is admin-only server-side, so a tool call can never land it.
    Saying so here beats forwarding it into a 403 the model did not see
    coming — the same reasoning as the grant fields, and a different reason,
    so the message is different too."""
    h = await granted(sf, seed_agent, agent_store, "editor", [TOOL_AGENTS_EDIT])
    out = await agenttools.agents_edit(api(client, h), {
        "action": "update", "name": "hello-world",
        "definition": {"description": "x", "system": True}})
    assert out.startswith("error:") and "system" in out and "admin-only" in out
    assert "agents_grant" not in out          # not a grant — a reserved field
    async with sf() as s:
        assert (await s.get(AgentDef, "hello-world")).system is False


async def test_a_tool_side_bypass_still_meets_the_api(client, sf, seed_agent,
                                                      agent_store):
    """The tool-side refusal is ergonomics; `agent_write_scope` is the control.
    Driving the write path directly with a grant in the overlay — what a
    tampered-with broker would do — is refused by the API, and the refusal
    reaches the model intact, naming the field."""
    h = await granted(sf, seed_agent, agent_store, "editor", [TOOL_AGENTS_EDIT])
    try:
        await agenttools._put_def(api(client, h), "hello-world",
                                  agenttools._constant({"skills": ["git"]}),
                                  what="bypass")
        raise AssertionError("the API allowed a grant change from agents_edit")
    except agenttools.ToolError as e:
        assert "403" in str(e) and "skills" in str(e)
        assert "agents_grant" in str(e)
    async with sf() as s:
        assert (await s.get(AgentDef, "hello-world")).skills == []


async def test_attribution_is_never_an_argument(client, sf, seed_agent,
                                                agent_store):
    """`changed_by` comes from the verified token. There is no argument for it,
    and smuggling one into the definition is rejected as a non-field rather
    than forwarded to the API."""
    h = await granted(sf, seed_agent, agent_store, "editor", [TOOL_AGENTS_EDIT])
    call = api(client, h)
    for smuggled in ({"changed_by": "admin"}, {"changed_via": "admin"},
                     {"principal": "admin"}, {"name": "somebody-else"}):
        out = await agenttools.agents_edit(call, {
            "action": "update", "name": "hello-world",
            "definition": {"description": "x", **smuggled}})
        assert out.startswith("error:") or "somebody-else" not in out
    await agenttools.agents_edit(call, {
        "action": "update", "name": "hello-world",
        "definition": {"description": "written by the editor"}})
    v = (await versions_of(sf, "hello-world"))[-1]
    assert v.changed_by.endswith("editor") and v.changed_via == "tool:agents_edit"


class Resp:
    """A stand-in response, for the concurrency paths a single test coroutine
    over one sqlite connection cannot interleave for real."""

    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, json.dumps(body)

    def json(self):
        return self._body


CONFLICT = {"detail": "conflicting concurrent write, retry"}


async def test_a_lost_race_is_retried_once():
    """The API turns a version-number collision into a 409 (Task 3, rider 2).
    One retry from a FRESH read is right — the definition being merged into may
    be the thing that moved — and a second is not, because a 409 that survives
    a retry is contention, not a blip."""
    calls = []
    puts = iter([Resp(409, CONFLICT),
                 Resp(200, {"name": "a", "description": "second try"})])

    async def call(method, path, params=None, json=None):
        calls.append(method)
        if method == "GET":
            return Resp(200, {"name": "a", "description": "old", "skills": []})
        return next(puts)

    out = await agenttools.agents_edit(call, {
        "action": "update", "name": "a", "definition": {"description": "new"}})
    assert json.loads(out)["description"] == "second try"
    assert calls == ["GET", "PUT", "GET", "PUT"]

    always = Resp(409, CONFLICT)

    async def conflicting(method, path, params=None, json=None):
        return Resp(200, {"name": "a", "skills": []}) if method == "GET" else always

    out = await agenttools.agents_edit(conflicting, {
        "action": "update", "name": "a", "definition": {"description": "new"}})
    assert out.startswith("error:") and "409" in out


async def test_a_derived_grant_is_recomputed_on_the_retry():
    """The lost-update trap in a read-modify-write that DERIVES its payload.

    `add_grant` builds the new list from the list it read. If that list were
    computed once and replayed on the retry, the retry would PUT a list
    assembled before the winning writer's grant existed — deleting it, and
    logging the deletion as a deliberate grant change. So the union is
    recomputed from every read, including the retry's.
    """
    reads = iter([{"name": "a", "skills": []},          # nothing yet
                  {"name": "a", "skills": ["git"]}])    # someone else won
    puts = []

    async def call(method, path, params=None, json=None):
        if method == "GET":
            return Resp(200, next(reads))
        puts.append(json)
        return Resp(409, CONFLICT) if len(puts) == 1 else Resp(200, json)

    out = json.loads(await agenttools.agents_grant(call, {
        "action": "add_grant", "name": "a", "field": "skills",
        "values": ["reports"]}))
    assert [p["skills"] for p in puts] == [["reports"], ["git", "reports"]]
    assert out["skills"] == ["git", "reports"] and out["changed"] is True


async def test_a_retry_that_finds_the_work_already_done_writes_nothing():
    """The same recomputation, the other way round: if the concurrent writer
    added the very grant this call wanted, the retry's overlay is empty and
    there is nothing left to write — no second PUT, no duplicate version."""
    reads = iter([{"name": "a", "skills": []},
                  {"name": "a", "skills": ["reports"]}])
    puts = []

    async def call(method, path, params=None, json=None):
        if method == "GET":
            return Resp(200, next(reads))
        puts.append(json)
        return Resp(409, CONFLICT)

    out = json.loads(await agenttools.agents_grant(call, {
        "action": "add_grant", "name": "a", "field": "skills",
        "values": ["reports"]}))
    assert len(puts) == 1
    assert out["skills"] == ["reports"] and out["changed"] is False


async def test_bad_calls_get_usable_errors(client, sf, seed_agent, agent_store):
    h = await granted(sf, seed_agent, agent_store, "editor", [TOOL_AGENTS_EDIT])
    call = api(client, h)
    assert "action must be one of" in await agenttools.agents_edit(
        call, {"action": "frobnicate"})
    assert "requires the agent's name" in await agenttools.agents_edit(
        call, {"action": "get"})
    assert "requires a definition" in await agenttools.agents_edit(
        call, {"action": "update", "name": "hello-world"})
    assert "not found" in await agenttools.agents_edit(
        call, {"action": "get", "name": "ghost"})


# --- agents_grant: the tool, against the real API -----------------------------

async def test_agents_grant_moves_grants_and_nothing_else(client, sf, seed_agent,
                                                          agent_store):
    await seed_agent("target", description="untouched prose", role="coder")
    h = await granted(sf, seed_agent, agent_store, "granter", [TOOL_AGENTS_GRANT])
    call = api(client, h)

    before = json.loads(await agenttools.agents_grant(
        call, {"action": "get", "name": "target"}))
    assert before == {"name": "target", "harness_tools": [], "platform_tools": [],
                      "skills": [], "secrets": [], "can_invoke": False}

    out = json.loads(await agenttools.agents_grant(call, {
        "action": "set_grants", "name": "target", "skills": ["git"],
        "can_invoke": True}))
    assert out["skills"] == ["git"] and out["can_invoke"] is True

    async with sf() as s:
        row = await s.get(AgentDef, "target")
        # The prose and the role the grant tool may not touch came back
        # unchanged, because the write was built on a fresh read.
        assert row.description == "untouched prose" and row.role == "coder"
    assert (await versions_of(sf, "target"))[-1].changed_via == "tool:agents_grant"


async def test_add_and_remove_are_set_operations(client, sf, seed_agent,
                                                 agent_store):
    await seed_agent("target", skills=["git"])
    h = await granted(sf, seed_agent, agent_store, "granter", [TOOL_AGENTS_GRANT])
    call = api(client, h)

    out = json.loads(await agenttools.agents_grant(call, {
        "action": "add_grant", "name": "target", "field": "skills",
        "values": ["reports", "git"]}))
    assert out["skills"] == ["git", "reports"] and out["changed"] is True

    # Adding what is already there writes nothing — the change log records
    # changes, and a no-op version is noise in an audit trail.
    out = json.loads(await agenttools.agents_grant(call, {
        "action": "add_grant", "name": "target", "field": "skills",
        "values": ["git"]}))
    assert out["changed"] is False
    assert len(await versions_of(sf, "target")) == 1

    out = json.loads(await agenttools.agents_grant(call, {
        "action": "remove_grant", "name": "target", "field": "skills",
        "values": ["git"]}))
    assert out["skills"] == ["reports"] and out["changed"] is True

    assert "field must be one of" in await agenttools.agents_grant(call, {
        "action": "add_grant", "name": "target", "field": "role",
        "values": ["coder"]})
    assert "values must be" in await agenttools.agents_grant(call, {
        "action": "add_grant", "name": "target", "field": "skills", "values": []})


async def test_agents_grant_may_grant_itself_onward(client, sf, seed_agent,
                                                    agent_store):
    """The documented escalation (design/15): a grant-holder can hand the grant
    tool to another agent. The control is that it is impossible to do quietly —
    the change log names the granter, the tool and the agent."""
    await seed_agent("target")
    h = await granted(sf, seed_agent, agent_store, "granter", [TOOL_AGENTS_GRANT])
    out = json.loads(await agenttools.agents_grant(api(client, h), {
        "action": "add_grant", "name": "target", "field": "platform_tools",
        "values": [TOOL_AGENTS_GRANT]}))
    assert out["platform_tools"] == [TOOL_AGENTS_GRANT]
    v = (await versions_of(sf, "target"))[-1]
    assert v.changed_via == "tool:agents_grant" and v.changed_by.endswith("granter")
    assert v.snapshot["platform_tools"] == [TOOL_AGENTS_GRANT]


async def test_agents_grant_cannot_rewrite_prose(client, sf, seed_agent,
                                                 agent_store):
    """Both directions of the split. The tool offers no editorial argument at
    all, and the raw API refuses the same caller when it tries anyway."""
    h = await granted(sf, seed_agent, agent_store, "granter", [TOOL_AGENTS_GRANT])
    assert not (set(agenttools.EDITABLE_FIELDS) &
                set(agenttools.GRANT_LIST_FIELDS + ("can_invoke",)))
    r = await client.put("/api/agents/hello-world",
                         json=a_def("hello-world", description="reworded"),
                         headers=h)
    assert r.status_code == 403 and "description" in r.json()["detail"]


async def test_the_grant_tool_needs_the_grant_at_the_api_too(client, sf,
                                                             seed_agent, agent_store):
    """Belt and braces: even reaching the grant tool's code path with only
    `agents_edit` gets nowhere, because the API re-derives the authority from
    the token rather than trusting the tool that was called."""
    await seed_agent("target")
    h = await granted(sf, seed_agent, agent_store, "editor", [TOOL_AGENTS_EDIT])
    out = await agenttools.agents_grant(api(client, h), {
        "action": "add_grant", "name": "target", "field": "skills",
        "values": ["git"]})
    assert out.startswith("error:") and "403" in out and "skills" in out
    async with sf() as s:
        assert (await s.get(AgentDef, "target")).skills == []


async def test_neither_tool_works_without_a_grant_at_all(client, sf, seed_agent,
                                                         agent_store):
    h = await granted(sf, seed_agent, agent_store, "plain", [])
    call = api(client, h)
    # Reads are refused (the widening follows the write grant) …
    assert "403" in await agenttools.agents_edit(
        call, {"action": "get", "name": "hello-world"})
    # … and so are writes.
    assert "403" in await agenttools.agents_edit(call, {
        "action": "update", "name": "hello-world",
        "definition": {"description": "x"}})
    assert "403" in await agenttools.agents_grant(call, {
        "action": "add_grant", "name": "hello-world", "field": "skills",
        "values": ["git"]})
    async with sf() as s:
        assert (await s.execute(select(AgentVersion))).scalars().all() == []
