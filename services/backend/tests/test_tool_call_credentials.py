"""Tool-call credentials (docs/design/39, "Tool-call credentials").

The broker exchanges a caller's bearer and run JWT for a credential bound to
one call; the executor presents it beside its own ServiceAccount token; the
broker revokes it at return. These tests hold every guard the design names:
no run, no credential; a copy is useless off the executor, after return, for
another call, or once tampered with; and a tool-call caller is the agent
acting through the tool, never wider than the credential's `app_scope`.
"""
import time
import uuid

import httpx
import jwt
import pytest

from agentplatform import runjwt
from agentplatform.apikeys import generate_token, hash_token, token_prefix
from agentplatform.appdata import credentials as tc
from agentplatform.appdata.models import AppDataApp, AppDataDefinition, AppDataToolCall
from agentplatform.config import Settings
from agentplatform.db import AgentDef, ApiKey, Run, RunState
from agentplatform.api.app import create_app

from .conftest import REPO_APPS, REPO_REPORTS, REPO_SECRETS, REPO_SKILLS

# JWT-shaped bearers; the fake TokenReview below decides who each one is.
BROKER = "broker.sa.token"
EXECUTOR = "executor.sa.token"
PAI_SA = "pai.sa.token"
BOB_SA = "bob.sa.token"
IDENTITIES = {
    BROKER: "system:serviceaccount:ap:ap-mcp-broker",
    EXECUTOR: "system:serviceaccount:ap:ap-tool-executor",
    PAI_SA: "system:serviceaccount:ap:agent-pai",
    BOB_SA: "system:serviceaccount:ap:agent-bob",
}

LEDGER_YAML = """\
name: ledger
description: Records results into an App for the credential tests.
params: {type: object, properties: {}}
timeout_seconds: 40
app_access:
  roles: [results]
  verbs: [read, create]
"""
PLAIN_YAML = """\
name: plain
description: A tool that declares no App access at all, for tests.
params: {type: object, properties: {}}
"""


def _tool(root, name, text):
    d = root / name
    d.mkdir(parents=True)
    (d / "tool.yaml").write_text(text)
    (d / "run.py").write_text("print('ok')\n")


def _collection(name, read=None):
    body = {"collection": name, "fields": {"title": {"type": "string", "max": 40}}}
    if read:
        body["access"] = {"read": read}
    return body


async def _app(sf, owner, collections):
    kind, owner_id = ("kyle", "kyle") if owner == "kyle" else ("agent", owner[6:])
    app_id = uuid.uuid4().hex
    async with sf() as s:
        s.add(AppDataApp(id=app_id, name=f"app_{app_id[:10]}", owner_kind=kind,
                         owner_id=owner_id))
        for body in collections:
            s.add(AppDataDefinition(app_id=app_id, kind="collection",
                                    name=body["collection"], version=1, body=body,
                                    state="published", author=owner))
        await s.commit()
    return app_id


@pytest.fixture
async def env(sf, producer, secret_store, agent_store, seed_agent, tmp_path):
    tools = tmp_path / "tools"
    _tool(tools, "ledger", LEDGER_YAML)
    _tool(tools, "plain", PLAIN_YAML)
    await seed_agent("pai", description="t",
                     platform_tools=["mcp__platform__ledger", "mcp__platform__plain"])
    await seed_agent("bob", description="t", platform_tools=["mcp__platform__plain"])
    await agent_store.reload()
    keys = runjwt.generate_keypair()
    await secret_store.set(runjwt.SECRET_NAME, keys)
    async with sf() as s:
        s.add(Run(id="run-1", agent="pai", trigger="manual", requested_by="t",
                  prompt="x", state=RunState.RUNNING))
        await s.commit()
    (tmp_path / "checkout").mkdir()
    app = create_app(Settings(checkout_root=str(tmp_path / "checkout"),
                              secrets_root=str(REPO_SECRETS), skills_root=str(REPO_SKILLS),
                              reports_root=str(REPO_REPORTS), apps_root=str(REPO_APPS),
                              tools_root=str(tools)),
                     sf, producer, secret_store=secret_store, agent_store=agent_store)

    async def validate(token):
        return IDENTITIES.get(token)
    app.state.sa_validator = validate

    run_jwt = runjwt.mint(keys["private_key"], run_id="run-1", agent="pai",
                          initiated_by="kyle", tools=["mcp__platform__ledger"],
                          sa_name="agent-pai", timeout_seconds=300)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://t") as c:
        c.app = app
        c.run_jwt = run_jwt
        yield c


def _broker():
    return {"Authorization": f"Bearer {BROKER}"}


def _caller(c):
    return {"authorization": f"Bearer {PAI_SA}", "run_token": c.run_jwt}


async def _mint(c, tool="ledger", caller=None, headers=None, action="record"):
    return await c.post("/api/tool-calls", headers=headers or _broker(), json={
        "caller": caller or _caller(c), "tool": tool, "action": action})


def _as_executor(minted, *, call_id=None, sa=EXECUTOR):
    return {"Authorization": f"Bearer {sa}", "X-AP-Tool-Call": minted["credential"],
            "X-AP-Tool-Call-Id": call_id or minted["call_id"]}


# --- the exchange ------------------------------------------------------------------

async def test_mint_happy_path_binds_the_call_the_run_and_the_executor(env, sf):
    owned = await _app(sf, "agent:pai", [_collection("results"), _collection("notes")])
    shared = await _app(sf, "kyle", [_collection("results", read=["kyle", "agent:pai"])])
    await _app(sf, "agent:bob", [_collection("results")])          # not pai's
    r = await _mint(env)
    assert r.status_code == 200, r.text
    minted = r.json()
    claims = jwt.decode(minted["credential"], options={"verify_signature": False})
    assert claims["kind"] == "tool_call"
    assert (claims["call_id"], claims["run_id"], claims["agent"], claims["tool"],
            claims["action"]) == (minted["call_id"], "run-1", "pai", "ledger", "record")
    assert claims["cnf"] == {"sa": "ap-tool-executor"}
    assert claims["aud"] == tc.AUDIENCE and claims["jti"] == minted["jti"]
    # exp is the tool's timeout (40 s) plus the staging slack, nothing more.
    assert claims["exp"] - claims["iat"] == 40 + tc.EXP_SLACK_SECONDS
    # The owner's App, cut down to the declared role and verbs; Kyle's App
    # only as far as pai may read it; bob's not at all.
    assert sorted(claims["app_scope"], key=lambda e: e["app_id"]) == sorted([
        {"app_id": owned, "collections": ["results"], "verbs": ["read", "create"]},
        {"app_id": shared, "collections": ["results"], "verbs": ["read"]},
    ], key=lambda e: e["app_id"])
    assert minted["app_scope"] == claims["app_scope"]
    async with sf() as s:
        row = await s.get(AppDataToolCall, minted["jti"])
        assert row.call_id == minted["call_id"] and row.revoked_at is None
        assert (row.run_id, row.agent, row.tool) == ("run-1", "pai", "ledger")


async def test_admin_api_key_caller_gets_no_credential(env, sf):
    token = generate_token()
    async with sf() as s:
        s.add(ApiKey(name="ops", role="admin", key_hash=hash_token(token),
                     prefix=token_prefix(token)))
        await s.commit()
    r = await _mint(env, caller={"authorization": f"Bearer {token}"})
    assert r.status_code == 403 and "no run" in r.text


async def test_agent_workload_without_a_run_gets_no_credential(env):
    r = await _mint(env, caller={"authorization": f"Bearer {PAI_SA}"})
    assert r.status_code == 403 and "no run" in r.text


async def test_caller_credentials_must_verify(env):
    assert (await _mint(env, caller={"authorization": f"Bearer {PAI_SA}",
                                     "run_token": "garbage"})).status_code == 401
    # pai's run JWT presented by bob's workload: the run JWT's own cnf refuses it.
    assert (await _mint(env, caller={"authorization": f"Bearer {BOB_SA}",
                                     "run_token": env.run_jwt})).status_code == 401


@pytest.mark.parametrize("headers,status", [
    ({}, 401),
    ({"Authorization": f"Bearer {PAI_SA}"}, 403),      # an agent can't mint for itself
    ({"Authorization": f"Bearer {EXECUTOR}"}, 403),    # nor can the executor
])
async def test_only_the_broker_mints_and_revokes(env, headers, status):
    assert (await env.post("/api/tool-calls", headers=headers, json={
        "caller": _caller(env), "tool": "ledger"})).status_code == status
    minted = (await _mint(env)).json()
    assert (await env.delete(f"/api/tool-calls/{minted['jti']}",
                             headers=headers)).status_code == status


async def test_tool_must_declare_app_access_and_be_held(env):
    assert (await _mint(env, tool="plain")).status_code == 403
    assert (await _mint(env, tool="nope")).status_code == 404
    keys = await env.app.state.secret_store.get(runjwt.SECRET_NAME)
    # A run whose frozen grants don't include the tool.
    frozen = runjwt.mint(keys["private_key"], run_id="run-1", agent="pai",
                         initiated_by="kyle", tools=["mcp__platform__plain"],
                         sa_name="agent-pai", timeout_seconds=300)
    r = await _mint(env, caller={"authorization": f"Bearer {PAI_SA}", "run_token": frozen})
    assert r.status_code == 403 and "does not hold" in r.text


# --- presenting it -------------------------------------------------------------------

async def test_executor_presents_it_as_the_agent_via_the_tool(env):
    minted = (await _mint(env)).json()
    r = await env.get("/api/whoami", headers=_as_executor(minted))
    assert r.status_code == 200, r.text
    d = r.json()
    assert (d["principal"], d["role"], d["agent"], d["run_id"]) == (
        "agent:pai", "tools", "pai", "run-1")
    assert d["tools"] == []          # a tool call holds no platform tool
    # Outside whoami and app_data it authenticates nothing at all.
    assert (await env.get("/api/runs", headers=_as_executor(minted))).status_code == 401


async def test_a_tool_call_cannot_read_or_set_app_data_quotas(env):
    """The executor's proxy forwards all of /api/app-data/**, and Kyle's quota
    routes live under it: they still answer only Kyle's session."""
    minted = (await _mint(env)).json()
    path = "/api/app-data/quotas/owner/agent:pai"
    assert (await env.get(path, headers=_as_executor(minted))).status_code == 403
    assert (await env.put(path, headers=_as_executor(minted),
                          json={"limits": {"max_records": 10 ** 9}})).status_code == 403


async def test_replay_after_return_is_rejected(env):
    minted = (await _mint(env)).json()
    h = _as_executor(minted)
    assert (await env.get("/api/whoami", headers=h)).status_code == 200
    assert (await env.delete(f"/api/tool-calls/{minted['jti']}",
                             headers=_broker())).status_code == 200
    assert (await env.get("/api/whoami", headers=h)).status_code == 401
    # Revoking twice is fine; an unknown jti is not.
    assert (await env.delete(f"/api/tool-calls/{minted['jti']}",
                             headers=_broker())).status_code == 200
    assert (await env.delete("/api/tool-calls/" + "0" * 32,
                             headers=_broker())).status_code == 404


async def test_a_copied_credential_without_the_executor_is_useless(env, sf):
    minted = (await _mint(env)).json()
    # Beside another workload's token (cnf names the executor).
    assert (await env.get("/api/whoami", headers=_as_executor(
        minted, sa=PAI_SA))).status_code == 401
    # As the bearer itself, with nothing beside it.
    assert (await env.get("/api/whoami", headers={
        "Authorization": f"Bearer {minted['credential']}"})).status_code == 401
    # Beside an API key, even a run-bound one.
    token = generate_token()
    async with sf() as s:
        s.add(ApiKey(name="tools:pai", role="tools", agent="pai", run_id="run-1",
                     key_hash=hash_token(token), prefix=token_prefix(token)))
        await s.commit()
    assert (await env.get("/api/whoami", headers={
        "Authorization": f"Bearer {token}", "X-AP-Tool-Call": minted["credential"],
        "X-AP-Tool-Call-Id": minted["call_id"]})).status_code == 401


async def test_only_the_executor_may_present_one(env, sf):
    """Even a credential whose cnf names the presenter is refused unless the
    presenter is the executor: no other workload is a tool-call holder."""
    keys = await tc.keypair(env.app.state)
    token, claims = tc.mint_tool_call(
        keys["private_key"], call_id="e" * 32, run_id="run-1", agent="pai",
        tool="ledger", action="", app_scope=[], cnf_sa="agent-pai", ttl_seconds=60)
    async with sf() as s:
        await tc.record(s, claims)
        await s.commit()
    assert (await env.get("/api/whoami", headers=_as_executor(
        {"credential": token, "call_id": "e" * 32}, sa=PAI_SA))).status_code == 401


async def test_wrong_call_is_rejected(env):
    first = (await _mint(env)).json()
    second = (await _mint(env)).json()
    assert (await env.get("/api/whoami", headers=_as_executor(
        first, call_id=second["call_id"]))).status_code == 401
    assert (await env.get("/api/whoami", headers={
        "Authorization": f"Bearer {EXECUTOR}",
        "X-AP-Tool-Call": first["credential"]})).status_code == 401


async def test_tampered_credential_is_rejected(env):
    minted = (await _mint(env)).json()
    head, payload, sig = minted["credential"].split(".")
    claims = jwt.decode(minted["credential"], options={"verify_signature": False})
    claims["app_scope"] = [{"app_id": "x", "collections": ["*"],
                            "verbs": ["read", "create", "update", "delete"]}]
    import base64
    import json
    forged = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    for bad in (f"{head}.{forged}.{sig}", f"{head}.{payload}.{sig[:-4]}AAAA"):
        assert (await env.get("/api/whoami", headers=_as_executor(
            {**minted, "credential": bad}))).status_code == 401


async def test_expired_credential_is_rejected(env, sf):
    keys = await tc.keypair(env.app.state)
    token, claims = tc.mint_tool_call(
        keys["private_key"], call_id="c" * 32, run_id="run-1", agent="pai",
        tool="ledger", action="", app_scope=[], cnf_sa="ap-tool-executor",
        ttl_seconds=-5)
    async with sf() as s:
        await tc.record(s, claims)
        await s.commit()
    assert (await env.get("/api/whoami", headers=_as_executor(
        {"credential": token, "call_id": "c" * 32}))).status_code == 401


async def test_unrecorded_credential_is_rejected(env):
    """A credential signed with the right key but never minted through the
    exchange (no jti row) has no live call to belong to."""
    keys = await tc.keypair(env.app.state)
    token, _ = tc.mint_tool_call(
        keys["private_key"], call_id="d" * 32, run_id="run-1", agent="pai",
        tool="ledger", action="", app_scope=[], cnf_sa="ap-tool-executor",
        ttl_seconds=60)
    assert (await env.get("/api/whoami", headers=_as_executor(
        {"credential": token, "call_id": "d" * 32}))).status_code == 401


async def test_credential_dies_with_its_runs_authority(env, sf):
    minted = (await _mint(env)).json()
    async with sf() as s:
        (await s.get(AgentDef, "pai")).authorization_generation = 7
        await s.commit()
    assert (await env.get("/api/whoami", headers=_as_executor(minted))).status_code == 401


async def test_a_run_jwt_is_not_a_tool_call_credential(env):
    """Distinct key, audience and kind: the run JWT the agent holds never
    passes the tool-call verifier, even presented by the executor."""
    assert (await env.get("/api/whoami", headers=_as_executor(
        {"credential": env.run_jwt, "call_id": "x"}))).status_code == 401


# --- what app_data routes see ------------------------------------------------------

async def test_scope_is_minted_at_call_time_and_not_wider_later(env, sf):
    """A collection pai gains after the mint is not in this call's scope."""
    minted = (await _mint(env)).json()
    owned = await _app(sf, "agent:pai", [_collection("results")])
    r = await env.post("/api/app-data/agent/records/create", headers=_as_executor(minted),
                       json={"app": owned, "request_id": "r1", "collection": "results",
                             "values": {"title": "x"}})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "AD-OUT-OF-SCOPE"


def test_tool_only_collections_count_the_tool():
    """A `writers` reservation for this tool is reachable through it; one for
    another tool is not, even for the owner."""
    from agentplatform.appdata.access import Access, Caller
    from agentplatform.appdata.definitions import CollectionDef
    c = CollectionDef.model_validate({**_collection("results"),
                                      "writers": {"create": ["tool:ledger"]}})
    via = Access(c, Caller("agent:pai", via_tool="tool:ledger"), "agent:pai")
    other = Access(c, Caller("agent:pai", via_tool="tool:other"), "agent:pai")
    assert tc._holds(via, "create") and not tc._holds(other, "create")


# --- the verifier itself -------------------------------------------------------------

def _keys():
    return runjwt.generate_keypair()


def test_verifier_requires_kind_shape_and_cnf():
    k = _keys()
    token, _ = tc.mint_tool_call(k["private_key"], call_id="c1", run_id="r1", agent="pai",
                                 tool="ledger", action="", app_scope=[],
                                 cnf_sa="ap-tool-executor", ttl_seconds=60)
    assert tc.verify(k["public_key"], token, expected_sa="ap-tool-executor")
    assert tc.verify(k["public_key"], token, expected_sa="agent-pai") is None
    assert tc.verify(_keys()["public_key"], token, expected_sa="ap-tool-executor") is None
    # Right key, right audience, but not a tool call's claims.
    now = int(time.time())
    for claims in ({"kind": "run"}, {"kind": "tool_call", "call_id": "c1"},
                   {"kind": "tool_call", "call_id": "c1", "run_id": "r1", "agent": "pai",
                    "tool": "ledger", "action": "", "app_scope": [],
                    "principal": "kyle"}):
        forged = jwt.encode({"iss": tc.ISSUER, "aud": tc.AUDIENCE, "iat": now,
                             "exp": now + 60, "jti": "j", "cnf": {"sa": "ap-tool-executor"},
                             **claims}, k["private_key"], algorithm=tc.ALGORITHM)
        assert tc.verify(k["public_key"], forged, expected_sa="ap-tool-executor") is None


def test_page_intent_verifier_branch():
    """Kyle's page actions (Release 2): principal kyle, one intent, action,
    targets and budget, and no run. Only the verifier exists today; a
    page-intent credential is refused wherever tool calls are expected."""
    k = _keys()
    token, _ = tc.mint_page_intent(k["private_key"], intent_id="i1", action="resolve",
                                   targets=[{"app_id": "a", "collection": "c", "id": "1"}],
                                   budget={"writes": 1}, cnf_sa="ap-tool-executor",
                                   ttl_seconds=60)
    claims = tc.verify(k["public_key"], token, expected_sa="ap-tool-executor",
                       kinds=(tc.KIND_PAGE_INTENT,))
    assert claims["principal"] == "kyle" and claims["intent_id"] == "i1"
    assert "run_id" not in claims
    # The default verifier (what auth uses today) refuses it.
    assert tc.verify(k["public_key"], token, expected_sa="ap-tool-executor") is None
    # And a tool call never passes as a page intent.
    tool_token, _ = tc.mint_tool_call(k["private_key"], call_id="c1", run_id="r1",
                                      agent="pai", tool="ledger", action="", app_scope=[],
                                      cnf_sa="ap-tool-executor", ttl_seconds=60)
    assert tc.verify(k["public_key"], tool_token, expected_sa="ap-tool-executor",
                     kinds=(tc.KIND_PAGE_INTENT,)) is None
    now = int(time.time())
    base = {"iss": tc.ISSUER, "aud": tc.AUDIENCE, "iat": now, "exp": now + 60, "jti": "j",
            "cnf": {"sa": "ap-tool-executor"}, "kind": "page_intent", "principal": "kyle",
            "intent_id": "i1", "action": "resolve", "targets": [], "budget": {}}
    for bad in ({"run_id": "r1"}, {"principal": "agent:pai"}, {"budget": None},
                {"intent_id": ""}):
        forged = jwt.encode({**base, **bad}, k["private_key"], algorithm=tc.ALGORITHM)
        assert tc.verify(k["public_key"], forged, expected_sa="ap-tool-executor",
                         kinds=(tc.KIND_PAGE_INTENT,)) is None


async def test_page_intent_credential_authenticates_nothing_yet(env, sf):
    keys = await tc.keypair(env.app.state)
    token, claims = tc.mint_page_intent(keys["private_key"], intent_id="i" * 32,
                                        action="resolve", targets=[], budget={},
                                        cnf_sa="ap-tool-executor", ttl_seconds=60)
    async with sf() as s:
        await tc.record(s, claims)
        await s.commit()
    assert (await env.get("/api/whoami", headers=_as_executor(
        {"credential": token, "call_id": "i" * 32}))).status_code == 401


# --- the real app_data routes ----------------------------------------------------------

RECORDS = "/api/app-data/agent/records"


async def test_a_tool_call_writes_and_reads_through_the_record_routes(env, sf):
    """End to end: the credential reaches the record routes with no
    `app_data` grant, its writes are the agent via the tool, and a
    collection reserved for the tool's writers is writable through it."""
    owned = await _app(sf, "agent:pai", [
        {**_collection("results"), "writers": {"create": ["tool:ledger"]}},
        _collection("notes")])
    shared = await _app(sf, "kyle", [_collection("results", read=["kyle", "agent:pai"])])
    minted = (await _mint(env)).json()
    h = _as_executor(minted)
    r = await env.post(f"{RECORDS}/create", headers=h, json={
        "app": owned, "request_id": "r1", "collection": "results",
        "values": {"title": "won"}})
    assert r.status_code == 200, r.text
    record_id = r.json()["id"]
    r = await env.post(f"{RECORDS}/get", headers=h, json={
        "app": owned, "collection": "results", "id": record_id})
    assert r.status_code == 200, r.text
    values = r.json()["values"]
    assert (values["title"], values["author"], values["via"]) == (
        "won", "agent:pai", "tool:ledger")
    r = await env.post(f"{RECORDS}/describe", headers=h, json={"app": owned})
    assert r.status_code == 200, r.text
    assert [c["collection"] for c in r.json()["collections"]] == ["results"]

    def out_of_scope(resp):
        return resp.status_code == 403 and resp.json()["detail"]["code"] == "AD-OUT-OF-SCOPE"
    # Another collection pai owns but the tool never declared.
    assert out_of_scope(await env.post(f"{RECORDS}/create", headers=h, json={
        "app": owned, "request_id": "r2", "collection": "notes", "values": {"title": "x"}}))
    assert out_of_scope(await env.post(f"{RECORDS}/get", headers=h, json={
        "app": owned, "collection": "notes", "id": record_id}))
    # A verb the tool never declared.
    assert out_of_scope(await env.post(f"{RECORDS}/update", headers=h, json={
        "app": owned, "request_id": "r3", "collection": "results", "id": record_id,
        "values": {"title": "y"}, "expected_version": 1}))
    assert out_of_scope(await env.post(f"{RECORDS}/delete", headers=h, json={
        "app": owned, "request_id": "r4", "collection": "results", "id": record_id}))
    # Another App: read-only there, so no create.
    assert out_of_scope(await env.post(f"{RECORDS}/create", headers=h, json={
        "app": shared, "request_id": "r5", "collection": "results",
        "values": {"title": "x"}}))
    # An App outside the scope altogether.
    bobs = await _app(sf, "agent:bob", [_collection("results")])
    assert out_of_scope(await env.post(f"{RECORDS}/describe", headers=h,
                                       json={"app": bobs}))


async def test_a_tool_call_reaches_no_builder_or_kyle_route(env, sf):
    owned = await _app(sf, "agent:pai", [_collection("results")])
    h = _as_executor((await _mint(env)).json())
    for path, body in (("/api/app-data/agent/apps/list", {}),
                       ("/api/app-data/agent/apps/get", {"app": owned}),
                       ("/api/app-data/agent/apps/create",
                        {"request_id": "c1", "name": "sneaky"})):
        assert (await env.post(path, headers=h, json=body)).status_code == 403, path
    assert (await env.get("/api/app-data/apps", headers=h)).status_code == 403
    assert (await env.get(f"/api/app-data/apps/{owned}", headers=h)).status_code == 403


async def test_a_tool_call_is_not_its_persona_run(env, seed_agent):
    """The credential carries the agent and its run, so a route that trusts
    those alone would take the tool call for the persona's own run."""
    await seed_agent("pai", agent_type="persona")
    await env.app.state.agent_store.reload()
    h = _as_executor((await _mint(env)).json())
    assert (await env.get("/api/external-chat/identities", headers=h)).status_code == 403


async def test_a_tool_call_cannot_check_a_chat_identity_transport(env):
    h = _as_executor((await _mint(env)).json())
    r = await env.get("/api/chat-identities/discord-default/transport", headers=h)
    assert r.status_code == 401
