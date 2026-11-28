"""U4: the village-run fence in the API (plan 2026-11-27-village-game §10.2,
§6.1). A village run reaches whoami (and, for a look-back, its memory
routes), holds only the village Tool, gets no harness tools or skills, and
never reaches agent_self; the how-I-play section has one section-only writer."""
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from agentplatform.api import agent_self as AS
from agentplatform.api.auth import VILLAGE_TOOL, _dream_fence
from agentplatform.db import AgentDef, AgentVersion, Run, RunState, VillageConfig, VillageRun, VillageSession
from sqlalchemy import select

from .test_relay_api import _agent_token, _key

MEM = "mcp__platform__memory"
SELF = "mcp__platform__agent_self"
PERSONA_TOOLS = [VILLAGE_TOOL, MEM, SELF, "mcp__platform__relay", "mcp__platform__tickets"]
BASE_PROMPT = "# pai\nYou are Pai. Keep your core purpose."


async def _persona(seed_agent, agent_store, name="pai", **fields):
    fields.setdefault("platform_tools", PERSONA_TOOLS)
    await seed_agent(name, agent_type="persona", prompt=BASE_PROMPT,
                     harness_tools=["WebSearch", "WebFetch"], skills=["git"], **fields)
    await seed_agent("village-planner", platform_tools=[VILLAGE_TOOL, MEM],
                     harness_tools=["WebSearch"])
    await agent_store.reload()


async def _village_run(sf, *, role="reflection", persona="pai", row_state="running",
                       session_state="live", run_state=RunState.RUNNING, row=True):
    agent = "village-planner" if role == "planner" else persona
    run_id = uuid.uuid4().hex
    async with sf() as s:
        session = VillageSession(day=uuid.uuid4().hex[:10], kind="test", state=session_state)
        s.add(session)
        await s.flush()
        snap = {"name": agent, "prompt": BASE_PROMPT, "harness_tools": ["WebSearch", "WebFetch"],
                "skills": ["git"], "platform_tools": PERSONA_TOOLS if agent == persona else
                [VILLAGE_TOOL, MEM]}
        # Carry the agent's current generation, as materialize_run does: the
        # seeded village-planner row has been through migrate_authority's bump.
        generation = (await s.get(AgentDef, agent)).authorization_generation or 0
        s.add(Run(id=run_id, agent=agent, trigger="village" if row else "conversation",
                  requested_by="village:t", prompt="x", state=run_state, definition_snapshot=snap,
                  authorization_generation=generation))
        if row:
            s.add(VillageRun(session_id=session.id, persona=persona, role=role, run_id=run_id,
                             state=row_state))
        await s.commit()
    return agent, run_id


async def _tokens(sf, agent, run_id, role="relay"):
    return await _agent_token(sf, agent, role=role, run_id=run_id)


DENIED = [("patch", "/api/agent-self", {"expected_version": 0, "prompt": "I am free"}),
          ("put", "/api/agent-self/avatar", {"artifact_id": None}),
          ("get", "/api/agent-self", None),
          ("get", "/api/relay/channels", None),
          ("get", "/api/tickets", None),
          ("post", "/api/tickets", {"title": "x"}),
          ("post", "/api/wiki/pages", {"slug": "x", "title": "x", "body": "x"}),
          ("put", "/api/wiki/pages/x", {"body": "x"}),
          ("post", "/api/runs", {"agent": "pai", "prompt": "x"}),
          ("get", "/api/runs", None)]


async def _call(client, method, path, headers, body):
    kw = {"headers": headers}
    if body is not None:
        kw["json"] = body
    return await getattr(client, method)(path, **kw)


@pytest.mark.parametrize("row_state,session_state", [("running", "live"), ("failed", "closed"),
                                                     ("done", "closed"), ("cancelled", "live")])
async def test_a_village_run_reaches_only_whoami_in_any_row_state(
        token_client, sf, seed_agent, agent_store, row_state, session_state):
    await _persona(seed_agent, agent_store)
    agent, run_id = await _village_run(sf, row_state=row_state, session_state=session_state)
    headers = await _tokens(sf, agent, run_id)
    me = await token_client.get("/api/whoami", headers=headers)
    assert me.status_code == 200 and me.json()["tools"] == [VILLAGE_TOOL, MEM]
    for method, path, body in DENIED:
        r = await _call(token_client, method, path, headers, body)
        assert r.status_code == 403, (method, path, r.status_code)


async def test_planner_gets_only_village_and_no_memory_routes(token_client, sf, seed_agent, agent_store):
    await _persona(seed_agent, agent_store)
    agent, run_id = await _village_run(sf, role="planner")
    headers = await _tokens(sf, agent, run_id, role="reader")
    assert (await token_client.get("/api/whoami", headers=headers)).json()["tools"] == [VILLAGE_TOOL]
    r = await token_client.post("/api/memory-hybrid", headers=headers, json={"query": "x"})
    assert r.status_code == 403


async def test_reflection_memory_follows_the_config_key(token_client, sf, seed_agent, agent_store):
    await _persona(seed_agent, agent_store)
    agent, run_id = await _village_run(sf)
    headers = await _tokens(sf, agent, run_id, role="reader")
    r = await token_client.post("/api/memory-hybrid", headers=headers, json={"query": "x"})
    assert r.status_code != 403
    async with sf() as s:
        s.add(VillageConfig(id=1, rules={"reflection_memory_writes": False}))
        await s.commit()
    assert (await token_client.get("/api/whoami", headers=headers)).json()["tools"] == [VILLAGE_TOOL]
    r = await token_client.post("/api/memory-hybrid", headers=headers, json={"query": "x"})
    assert r.status_code == 403


async def test_an_ordinary_run_of_the_same_persona_is_unfenced(token_client, sf, seed_agent, agent_store):
    await _persona(seed_agent, agent_store)
    agent, run_id = await _village_run(sf, row=False)
    headers = await _tokens(sf, agent, run_id)
    assert (await token_client.get("/api/relay/channels", headers=headers)).status_code == 200
    assert SELF in (await token_client.get("/api/whoami", headers=headers)).json()["tools"]


async def test_agent_self_refuses_a_village_run_even_past_the_fence(
        token_client, sf, seed_agent, agent_store, monkeypatch):
    """Belt and braces: the handler itself refuses a run with a village_runs row."""
    from agentplatform.api import auth
    await _persona(seed_agent, agent_store)
    agent, run_id = await _village_run(sf)
    headers = await _tokens(sf, agent, run_id)
    monkeypatch.setattr(auth, "village_allows", lambda path, memory: True)
    for method, path, body in DENIED[:3]:
        r = await _call(token_client, method, path, headers, body)
        assert r.status_code == 403 and "village" in r.text, (path, r.text)
    async with sf() as s:
        assert (await s.get(AgentDef, "pai")).prompt == BASE_PROMPT


async def test_agentdef_strips_harness_tools_and_skills(token_client, sf, seed_agent, agent_store):
    await _persona(seed_agent, agent_store)
    for role, tools in [("reflection", [VILLAGE_TOOL, MEM]), ("planner", [VILLAGE_TOOL])]:
        agent, run_id = await _village_run(sf, role=role)
        headers = await _key(sf, name=f"session:{agent}", role="session", agent=agent, run_id=run_id)
        d = (await token_client.get(f"/api/runs/{run_id}/agentdef", headers=headers)).json()
        assert d["harness_tools"] == [] and d["skills"] == [], d
        assert d["platform_tools"] == tools


async def _workload_request(sf, method, path, agent):
    app = SimpleNamespace(state=SimpleNamespace(session_factory=sf))
    r = Request({"type": "http", "method": method, "path": path, "scheme": "http",
                 "server": ("test", 80), "headers": [], "query_string": b"", "app": app})
    r.state.api_key_agent = agent
    r.state.api_key_run_id = None
    r.state.auth_kind = "workload"
    return r


async def test_workload_token_without_run_jwt_is_fenced_and_ambiguity_refused(sf, seed_agent, agent_store):
    await _persona(seed_agent, agent_store)
    await _village_run(sf)
    await _dream_fence(await _workload_request(sf, "GET", "/api/whoami", "pai"))
    with pytest.raises(HTTPException) as exc:
        await _dream_fence(await _workload_request(sf, "GET", "/api/relay/channels", "pai"))
    assert exc.value.status_code == 403
    await _village_run(sf)            # a second active village run of pai
    with pytest.raises(HTTPException) as exc:
        await _dream_fence(await _workload_request(sf, "GET", "/api/whoami", "pai"))
    assert "ambiguous" in exc.value.detail
    # an agent with no active village run is not scoped by them
    await _dream_fence(await _workload_request(sf, "GET", "/api/relay/channels", "kai"))


async def test_a_run_id_credential_naming_another_agent_is_refused(sf, seed_agent, agent_store):
    await _persona(seed_agent, agent_store)
    _, run_id = await _village_run(sf)
    r = await _workload_request(sf, "GET", "/api/whoami", "kai")
    r.state.api_key_run_id = run_id
    with pytest.raises(HTTPException) as exc:
        await _dream_fence(r)
    assert "mismatch" in exc.value.detail


# --- the how-I-play section (§6.1) -----------------------------------------

async def test_play_section_appends_then_replaces_only_between_markers(sf, seed_agent, agent_store):
    await _persona(seed_agent, agent_store)
    async with sf() as s:
        gen = (await s.get(AgentDef, "pai")).authorization_generation or 0
        out = await AS.apply_play_section(s, "pai", "I fetch stone first.", expected_version=None)
        await s.commit()
    assert out["changed"] and out["before"] == ""
    async with sf() as s:
        row = await s.get(AgentDef, "pai")
        assert row.prompt.startswith(BASE_PROMPT) and AS.PLAY_START in row.prompt
        assert AS.play_section(row.prompt) == "I fetch stone first."
        assert row.authorization_generation == gen + 1
        out = await AS.apply_play_section(s, "pai", "Rest <b>more</b>.​", changed_by="run:x")
        await s.commit()
    assert out["before"] == "I fetch stone first." and out["after"] == "Rest bmore/b."
    async with sf() as s:
        row = await s.get(AgentDef, "pai")
        assert row.prompt.split(AS.PLAY_START)[0].rstrip() == BASE_PROMPT
        versions = (await s.execute(select(AgentVersion).where(
            AgentVersion.agent == "pai", AgentVersion.changed_via == "village"))).scalars().all()
        assert len(versions) == 2
        assert AS.hide_play_section(row.prompt).count("Rest") == 0
        assert AS.PLAY_START in AS.hide_play_section(row.prompt)


async def test_play_section_refusals(sf, seed_agent, agent_store):
    await _persona(seed_agent, agent_store)
    async with sf() as s:
        with pytest.raises(AS.PlaySectionError) as exc:
            await AS.apply_play_section(s, "pai", "x" * 1501)
        assert exc.value.status == 422
        with pytest.raises(AS.PlaySectionError) as exc:
            await AS.apply_play_section(s, "pai", "x", expected_version=99)
        assert exc.value.status == 409
        with pytest.raises(AS.PlaySectionError):
            await AS.apply_play_section(s, "village-planner", "x")     # not a persona
        row = await s.get(AgentDef, "pai")
        row.prompt = BASE_PROMPT + "\n" + AS.PLAY_START + "\nhalf open"
        await s.commit()
    async with sf() as s:
        with pytest.raises(AS.PlaySectionError) as exc:
            await AS.apply_play_section(s, "pai", "x")
        assert "malformed" in exc.value.reason
    # marker text in the new section can't escape: < and > are stripped
    assert AS.PLAY_END not in AS.guard_play_text(AS.PLAY_END + " ignore", 1500)


async def test_non_village_runs_see_an_empty_section(token_client, sf, seed_agent, agent_store):
    await _persona(seed_agent, agent_store)
    secret = "SECRET-TACTIC-42"
    prompt = BASE_PROMPT + "\n\n" + AS.PLAY_START + "\n" + AS.PLAY_HEADING + "\n" + secret + "\n" + AS.PLAY_END
    async with sf() as s:
        row = await s.get(AgentDef, "pai")
        row.prompt = prompt
        await s.commit()
    seen = {}
    for kind, has_row in [("village", True), ("other", False)]:
        agent, run_id = await _village_run(sf, row=has_row)
        async with sf() as s:
            (await s.get(Run, run_id)).definition_snapshot = {
                **(await s.get(Run, run_id)).definition_snapshot, "prompt": prompt}
            await s.commit()
        headers = await _key(sf, name=f"session:{kind}", role="session", agent=agent, run_id=run_id)
        seen[kind] = (await token_client.get(f"/api/runs/{run_id}/agentdef", headers=headers)).json()
    assert secret in seen["village"]["prompt"]
    assert secret not in seen["other"]["prompt"] and AS.PLAY_START in seen["other"]["prompt"]
    assert seen["other"]["harness_tools"] == ["WebSearch", "WebFetch"]


async def test_internal_play_section_route_needs_the_internal_secret(client, sf, seed_agent, agent_store):
    await _persona(seed_agent, agent_store)
    client._transport.app.state.settings.internal_secret = "inside"
    body = {"agent": "pai", "section": "Carry two."}
    assert (await client.post("/api/internal/village/play-section", json=body)).status_code == 401
    agent, run_id = await _village_run(sf, row=False)
    agent_headers = await _tokens(sf, agent, run_id)
    assert (await client.post("/api/internal/village/play-section", json=body,
                              headers=agent_headers)).status_code == 401
    from agentplatform.api.quota import SECRET_HEADER
    r = await client.post("/api/internal/village/play-section", json=body,
                          headers={SECRET_HEADER: "inside"})
    assert r.status_code == 200 and r.json()["changed"] is True
    async with sf() as s:
        assert AS.play_section((await s.get(AgentDef, "pai")).prompt) == "Carry two."


# --- review round (Phase 1 Codex review): launch path, workload lapse, history --

async def _queued_village_run(sf, *, agent="pai", role="reflection"):
    run_id = uuid.uuid4().hex
    async with sf() as s:
        session = VillageSession(day=uuid.uuid4().hex[:10], kind="test", state="live")
        s.add(session)
        await s.flush()
        generation = (await s.get(AgentDef, agent)).authorization_generation or 0
        s.add(Run(id=run_id, agent=agent, trigger="village", requested_by="village:t", prompt="x",
                  state=RunState.QUEUED, authorization_generation=generation))
        s.add(VillageRun(session_id=session.id, persona="pai", role=role, run_id=run_id,
                         state="launching"))
        await s.commit()
    return run_id


def _dispatcher(sf, agent_store):
    from agentplatform.config import Settings
    from agentplatform.dispatcher import Dispatcher, FakeLauncher
    from agentplatform.events import FakeProducer
    return Dispatcher(Settings(global_concurrency=8), sf, FakeProducer(), agent_store, FakeLauncher())


async def test_village_run_freezes_without_backup_skills_or_harness(sf, seed_agent, agent_store):
    from agentplatform.config import Settings
    from agentplatform.joblauncher import K8sJobLauncher
    await _persona(seed_agent, agent_store, backup_runtime="codex", backup_model="gpt-6.1-sol")
    run_id = await _queued_village_run(sf)
    async with sf() as s:
        run = await s.get(Run, run_id)
    manifest, error = await _dispatcher(sf, agent_store)._freeze_definition(run)
    assert error is None
    assert manifest.backup_runtime is None and manifest.skills == []
    assert manifest.platform_tools == [VILLAGE_TOOL, MEM]
    async with sf() as s:
        snap = (await s.get(Run, run_id)).definition_snapshot
    assert snap["backup_runtime"] is None and snap["skills"] == [] and snap["harness_tools"] == []
    job = K8sJobLauncher(batch=None, settings=Settings(runner_image="r:1", k8s_namespace="ap")
                         ).build_job(run, manifest)
    env = {e.name for e in job.spec.template.spec.containers[0].env}
    assert "AP_SKILLS" not in env and "AP_CODEX_PROXY_URL" not in env
    assert job.spec.template.metadata.labels["agent-platform/codex-access"] == "false"


async def test_village_run_cannot_take_the_codex_fallback(token_client, sf, seed_agent, agent_store):
    await _persona(seed_agent, agent_store, backup_runtime="codex", backup_model="gpt-6.1-sol")
    run_id = await _queued_village_run(sf)
    disp = _dispatcher(sf, agent_store)
    async with sf() as s:
        await disp._freeze_definition(await s.get(Run, run_id))
    async with sf() as s:
        (await s.get(Run, run_id)).state = RunState.RUNNING
        await s.commit()
    headers = await _key(sf, name="session:fb", role="session", agent="pai", run_id=run_id)
    r = await token_client.post(f"/api/runs/{run_id}/model-fallback", headers=headers,
                                json={"reason": "authentication"})
    assert r.status_code == 409, r.text
    async with sf() as s:
        run = await s.get(Run, run_id)
        assert not run.fallback_used and run.runtime == "claude"


async def test_a_codex_primary_village_run_is_rejected(sf, seed_agent, agent_store):
    await _persona(seed_agent, agent_store, runtime="codex", model="gpt-6.1-sol",
                   backup_runtime="claude", backup_model="claude-sonnet-5-5")
    run_id = await _queued_village_run(sf)
    disp = _dispatcher(sf, agent_store)
    await disp.handle({"type": "run", "run_id": run_id})
    assert disp.launcher.launched == []
    async with sf() as s:
        run = await s.get(Run, run_id)
    assert run.state == RunState.REJECTED and "claude runtime" in (run.error or "")


async def test_planner_seed_has_no_codex_backup(sf):
    async with sf() as s:
        row = await s.get(AgentDef, "village-planner")
    assert row is not None and not row.backup_runtime and not row.backup_model


@pytest.mark.parametrize("state", [RunState.KILLED, RunState.SUCCEEDED, RunState.FAILED])
async def test_a_finished_village_runs_workload_token_stays_fenced(sf, seed_agent, agent_store, state):
    from agentplatform.db import utcnow
    await _persona(seed_agent, agent_store)
    _, run_id = await _village_run(sf, run_state=state)
    async with sf() as s:
        (await s.get(Run, run_id)).finished_at = utcnow()
        await s.commit()
    with pytest.raises(HTTPException) as exc:
        await _dream_fence(await _workload_request(sf, "GET", "/api/relay/channels", "pai"))
    assert exc.value.status_code == 403
    # killed with no finished_at yet (pod still terminating) is fenced too
    async with sf() as s:
        (await s.get(Run, run_id)).finished_at = None
        await s.commit()
    with pytest.raises(HTTPException):
        await _dream_fence(await _workload_request(sf, "GET", "/api/relay/channels", "pai"))


async def test_a_long_gone_village_run_no_longer_fences_workload(sf, seed_agent, agent_store):
    from datetime import timedelta
    from agentplatform.db import utcnow
    await _persona(seed_agent, agent_store)
    _, run_id = await _village_run(sf, run_state=RunState.SUCCEEDED)
    async with sf() as s:
        (await s.get(Run, run_id)).finished_at = utcnow() - timedelta(days=1)
        await s.commit()
    await _dream_fence(await _workload_request(sf, "GET", "/api/relay/channels", "pai"))


async def test_village_run_history_is_admin_only(token_client, admin_client, sf, seed_agent, agent_store):
    await _persona(seed_agent, agent_store)
    _, run_id = await _village_run(sf)
    _, other_id = await _village_run(sf, row=False)
    reader = await _key(sf, name="reader-human", role="reader")
    pai = await _tokens(sf, "pai", other_id, role="reader")
    for headers in (reader, pai):
        for path in (f"/api/runs/{run_id}", f"/api/runs/{run_id}/events"):
            r = await token_client.get(path, headers=headers)
            assert r.status_code in (403, 404), (path, r.status_code)
        listed = await token_client.get("/api/runs", headers=headers)
        assert listed.status_code == 200 and run_id not in {x["id"] for x in listed.json()}
    assert (await admin_client.get(f"/api/runs/{run_id}")).status_code == 200
