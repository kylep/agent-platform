import base64
from sqlalchemy import select

from agentplatform.db import AgentDef, AgentVersion, AuthorizedRelaySession, Conversation, Run, RunState
from .test_relay_api import _agent_token
from .test_session_api import _session_key, _auth
from .test_artifacts_api import png_bytes

TOOL = "mcp__platform__agent_self"


async def own_run(sf, seed_agent, agent_store, name="companion", **fields):
    fields.setdefault("agent_type", "persona")
    await seed_agent(name, platform_tools=fields.pop("platform_tools", [TOOL]), runtime="codex", **fields)
    await agent_store.reload()
    async with sf() as session:
        row = await session.get(AgentDef, name)
        conv = Conversation(connector="web", agent=name, title="self test")
        session.add(conv)
        await session.flush()
        run = Run(agent=name, trigger="conversation", requested_by="test", prompt="test",
            runtime="codex", conversation_id=conv.id, state=RunState.RUNNING,
            authorization_generation=row.authorization_generation)
        session.add(run)
        await session.commit()
        run_id = run.id
    return run_id, await _agent_token(sf, name, run_id=run_id)


async def test_self_profile_changes_are_scoped_versioned_and_reset_resume(client, sf, seed_agent, agent_store):
    run_id, headers = await own_run(sf, seed_agent, agent_store)
    profile = (await client.get("/api/agent-self", headers=headers)).json()
    assert profile["name"] == "companion" and "secrets" not in profile
    version = profile["version"]
    bad = await client.patch("/api/agent-self", headers=headers, json={"expected_version": version, "role": "admin"})
    assert bad.status_code == 422
    changed = await client.patch("/api/agent-self", headers=headers, json={"expected_version": version,
        "prompt": "I am a curious companion.", "description": "Curious", "model": "gpt-5.6-luna"})
    assert changed.status_code == 200, changed.text
    assert changed.json()["version"] == version + 1
    assert (await client.get("/api/agent-self", headers=headers)).status_code == 200
    stale = await client.patch("/api/agent-self", headers=headers, json={"expected_version": version, "prompt": "lost update"})
    assert stale.status_code == 409
    session_headers = _auth(await _session_key(sf, run_id))
    saved = await client.put(f"/api/runs/{run_id}/codex-session", headers=session_headers, json={"thread_id": "old-thread"})
    assert saved.status_code == 200, saved.text
    saved = await client.put(f"/api/runs/{run_id}/session", headers=session_headers, json={"session_id": "old-claude", "blob_b64": base64.b64encode(b"old").decode()})
    assert saved.status_code == 200 and saved.json()["reset"]
    async with sf() as session:
        agent = await session.get(AgentDef, "companion")
        run = await session.get(Run, run_id)
        assert agent.prompt == "I am a curious companion."
        assert agent.platform_tools == [TOOL]
        assert agent.authorization_generation == run.authorization_generation
        assert not (await session.execute(select(AuthorizedRelaySession).where(AuthorizedRelaySession.agent == "companion"))).scalars().all()
        audit = (await session.execute(select(AgentVersion).where(AgentVersion.agent == "companion"))).scalars().all()
        assert audit[-1].changed_by == f"run:{run_id}"
        assert audit[-1].changed_via == "tool:agent_self"
        assert len(audit[-1].changed_via) <= AgentVersion.__table__.c.changed_via.type.length
        assert len(audit[-1].changed_by) <= AgentVersion.__table__.c.changed_by.type.length


async def test_self_no_target_override_no_run_no_grant(client, sf, seed_agent, agent_store, admin_client):
    run_id, headers = await own_run(sf, seed_agent, agent_store)
    assert (await admin_client.get("/api/agent-self")).status_code == 403
    assert (await client.patch("/api/agent-self", headers=headers, json={"expected_version": 0, "name": "other", "prompt": "hijack"})).status_code == 422
    async with sf() as session:
        row = await session.get(AgentDef, "companion")
        row.platform_tools = []
        await session.commit()
    await agent_store.reload()
    assert (await client.get("/api/agent-self", headers=headers)).status_code == 403


async def test_self_system_prompt_locked_but_model_allowed(client, sf, seed_agent, agent_store):
    _, headers = await own_run(sf, seed_agent, agent_store, "health-monitor")
    async with sf() as session:
        row = await session.get(AgentDef, "health-monitor")
        row.system_source = "platform.health"
        await session.commit()
    profile = (await client.get("/api/agent-self", headers=headers)).json()
    response = await client.patch("/api/agent-self", headers=headers, json={"expected_version": profile["version"], "prompt": "new mission"})
    assert response.status_code == 409
    response = await client.patch("/api/agent-self", headers=headers, json={"expected_version": profile["version"], "model": "gpt-5.6-luna"})
    assert response.status_code == 200, response.text


async def test_self_avatar_assign_and_clear(client, admin_client, sf, seed_agent, agent_store, producer):
    _, headers = await own_run(sf, seed_agent, agent_store)
    upload = await admin_client.post("/api/artifacts", json={"name": "avatar.png", "content_b64": base64.b64encode(png_bytes()).decode()})
    artifact = upload.json()
    client.cookies.clear()
    response = await client.put("/api/agent-self/avatar", headers=headers, json={"artifact_id": artifact["id"]})
    assert response.status_code == 200, response.text
    assert response.json()["image_artifact_id"] == artifact["id"]
    response = await client.put("/api/agent-self/avatar", headers=headers, json={"artifact_id": None})
    assert response.status_code == 200 and response.json()["image_artifact_id"] is None
    assert any(e["type"] == "artifacts.event" and e["data"].get("event") == "agent_image" for e in producer.envelopes)


async def test_profile_change_revokes_other_run_but_new_run_can_resume(client, sf, seed_agent, agent_store):
    run_id, headers = await own_run(sf, seed_agent, agent_store)
    async with sf() as session:
        first = await session.get(Run, run_id)
        other = Run(agent=first.agent, trigger="conversation", requested_by="test", prompt="old",
            runtime="codex", conversation_id=first.conversation_id, state=RunState.RUNNING,
            authorization_generation=first.authorization_generation)
        session.add(other)
        await session.commit()
        other_id = other.id
    profile = (await client.get("/api/agent-self", headers=headers)).json()
    change = await client.patch("/api/agent-self", headers=headers,
        json={"expected_version": profile["version"], "model": "gpt-5.6-luna"})
    assert change.status_code == 200
    other_headers = await _agent_token(sf, "companion", run_id=other_id)
    assert (await client.get("/api/agent-self", headers=other_headers)).status_code == 401
    async with sf() as session:
        first = await session.get(Run, run_id)
        agent = await session.get(AgentDef, first.agent)
        new = Run(agent=first.agent, trigger="conversation", requested_by="test", prompt="new",
            runtime="codex", conversation_id=first.conversation_id, state=RunState.RUNNING,
            authorization_generation=agent.authorization_generation)
        session.add(new)
        await session.commit()
        new_id = new.id
    new_session_headers = _auth(await _session_key(sf, new_id))
    put = await client.put(f"/api/runs/{new_id}/codex-session", headers=new_session_headers, json={"thread_id": "new-context"})
    assert put.status_code == 200, put.text
    get = await client.get(f"/api/runs/{new_id}/codex-session", headers=new_session_headers)
    assert get.json()["thread_id"] == "new-context"
    old_session_headers = _auth(await _session_key(sf, run_id))
    assert (await client.get(f"/api/runs/{run_id}/codex-session", headers=old_session_headers)).json()["thread_id"] == ""


async def test_self_grant_is_default_and_opt_out_survives_restart(admin_client, sf):
    from agentplatform.db import init_db
    r = await admin_client.post("/api/agents", json={"name": "self-enabled", "prompt": "hi", "agent_type": "persona"})
    assert r.status_code == 201 and TOOL in r.json()["platform_tools"]
    r = await admin_client.post("/api/agents", json={"name": "self-disabled", "prompt": "hi", "agent_type": "persona", "agent_self": False})
    assert r.status_code == 201 and TOOL not in r.json()["platform_tools"]
    await init_db(sf.kw["bind"])
    async with sf() as session:
        assert TOOL not in (await session.get(AgentDef, "self-disabled")).platform_tools


async def test_self_avatar_cannot_expose_private_conversation_artifact(client, admin_client, sf, seed_agent, agent_store):
    from agentplatform.db import Artifact
    _, headers = await own_run(sf, seed_agent, agent_store)
    upload = await admin_client.post("/api/artifacts", json={"name": "private.png", "content_b64": base64.b64encode(png_bytes()).decode()})
    artifact_id = upload.json()["id"]
    client.cookies.clear()
    async with sf() as session:
        room = Conversation(connector="relay", kind="group", title="Private", home="relay")
        session.add(room)
        await session.flush()
        source = Run(agent="hello-world", trigger="conversation", requested_by="test", prompt="private", conversation_id=room.id)
        session.add(source)
        await session.flush()
        artifact = await session.get(Artifact, artifact_id)
        artifact.run_id = source.id
        await session.commit()
    response = await client.put("/api/agent-self/avatar", headers=headers, json={"artifact_id": artifact_id})
    assert response.status_code == 403, response.text


async def test_self_primary_and_backup_are_readable_but_cannot_change_together(client, sf, seed_agent, agent_store):
    _, headers = await own_run(sf, seed_agent, agent_store, model='gpt-6-sol', backup_runtime='claude', backup_model='claude-sonnet-5-5')
    read = (await client.get('/api/agent-self', headers=headers)).json()
    assert read['model'] == 'gpt-6-sol' and read['backup_model'] == 'claude-sonnet-5-5'
    both = await client.patch('/api/agent-self', headers=headers, json={'expected_version': read['version'], 'model': 'gpt-6-astra', 'backup_runtime': 'claude', 'backup_model': 'claude-sonnet-5'})
    assert both.status_code == 422 and 'never both' in both.text
    backup = await client.patch('/api/agent-self', headers=headers, json={'expected_version': read['version'], 'backup_runtime': 'claude', 'backup_model': 'claude-sonnet-5'})
    assert backup.status_code == 200, backup.text
    assert backup.json()['model'] == 'gpt-6-sol'
    cleared = await client.patch('/api/agent-self', headers=headers, json={'expected_version': backup.json()['version'], 'backup_runtime': None, 'backup_model': ''})
    assert cleared.status_code == 200 and cleared.json()['backup_runtime'] is None


async def test_self_primary_and_backup_edit_restriction_also_applies_to_broad_editor(client, sf, seed_agent, agent_store):
    _, headers = await own_run(sf, seed_agent, agent_store, platform_tools=[TOOL, 'mcp__platform__agents_edit'], model='gpt-6-sol', backup_runtime='claude', backup_model='claude-sonnet-5-5')
    definition = (await client.get('/api/agents/companion', headers=headers)).json()
    response = await client.put('/api/agents/companion', headers=headers, json={**definition, 'model': 'gpt-6-astra', 'backup_model': 'claude-sonnet-5'})
    # Since design 39 Phase 0 a broad editor can't edit itself through the
    # definition route at all, so agent_self is its only self path, and the
    # restriction holds there.
    assert response.status_code == 403 and 'agent_self' in response.text
    version = (await client.get('/api/agent-self', headers=headers)).json()['version']
    response = await client.patch('/api/agent-self', headers=headers, json={'expected_version': version, 'model': 'gpt-6-astra', 'backup_model': 'claude-sonnet-5'})
    assert response.status_code == 422 and 'never both' in response.text


async def test_self_crons_replace_own_list_with_bounds_and_keep_sessions(client, sf, seed_agent, agent_store):
    run_id, headers = await own_run(sf, seed_agent, agent_store)
    version = (await client.get("/api/agent-self", headers=headers)).json()["version"]
    ok = await client.patch("/api/agent-self", headers=headers, json={"expected_version": version,
        "crons": [{"schedule": "0 8 * * *", "prompt": "daily"}], "timezone": "America/Toronto"})
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["version"] == version + 1 and body["timezone"] == "America/Toronto"
    assert [c["schedule"] for c in body["crons"]] == ["0 8 * * *"]
    async with sf() as session:
        agent = await session.get(AgentDef, "companion")
        run = await session.get(Run, run_id)
        assert agent.entrypoints["crons"][0]["prompt"] == "daily"
        assert agent.platform_tools == [TOOL]
        assert agent.authorization_generation == run.authorization_generation
        audit = (await session.execute(select(AgentVersion).where(AgentVersion.agent == "companion"))).scalars().all()
        assert audit[-1].changed_via == "tool:agent_self"
    for crons in ([{"schedule": "*/5 * * * *", "prompt": "x"}],
                  [{"schedule": "0,30 * * * *", "prompt": "x"}],
                  [{"schedule": "nope", "prompt": "x"}],
                  [{"schedule": "0 8 * * *", "prompt": "x"}] * 7):
        bad = await client.patch("/api/agent-self", headers=headers, json={"expected_version": version + 1, "crons": crons})
        assert bad.status_code == 422, crons
    cleared = await client.patch("/api/agent-self", headers=headers, json={"expected_version": version + 1, "crons": []})
    assert cleared.status_code == 200 and cleared.json()["crons"] == []


async def test_self_cron_prompt_and_model_are_bounded(client, sf, seed_agent, agent_store):
    _, headers = await own_run(sf, seed_agent, agent_store)
    version = (await client.get("/api/agent-self", headers=headers)).json()["version"]
    for entry in ({"schedule": "0 8 * * *", "prompt": "x" * 8001},
                  {"schedule": "0 8 * * *", "prompt": "x", "model": "gpt-9-imaginary"}):
        bad = await client.patch("/api/agent-self", headers=headers, json={"expected_version": version, "crons": [entry]})
        assert bad.status_code == 422, entry
    good = await client.patch("/api/agent-self", headers=headers, json={"expected_version": version,
        "crons": [{"schedule": "0 8 * * *", "prompt": "x", "model": "gpt-5.6-luna"}]})
    assert good.status_code == 200, good.text


async def test_self_refused_for_a_worker_even_with_the_grant(client, sf, seed_agent, agent_store):
    _, headers = await own_run(sf, seed_agent, agent_store, name="grunt", agent_type="worker")
    assert (await client.get("/api/agent-self", headers=headers)).status_code == 403
    assert (await client.patch("/api/agent-self", headers=headers, json={
        "expected_version": 0, "crons": []})).status_code == 403


async def test_persona_only_migration_strips_workers_once_and_keeps_personas(sf, seed_agent):
    from sqlalchemy import delete
    from agentplatform.db import SchemaMark, SELF_PERSONA_ONLY_MARK, init_db
    await seed_agent("grunt", agent_type="worker", platform_tools=[TOOL, "mcp__platform__relay"])
    await seed_agent("muse", agent_type="persona", platform_tools=[TOOL])
    async with sf() as session:
        await session.execute(delete(SchemaMark).where(SchemaMark.name == SELF_PERSONA_ONLY_MARK))
        await session.commit()
    await init_db(sf.kw["bind"])
    async with sf() as session:
        assert (await session.get(AgentDef, "grunt")).platform_tools == ["mcp__platform__relay"]
        assert (await session.get(AgentDef, "muse")).platform_tools == [TOOL]
        log = (await session.execute(select(AgentVersion).where(AgentVersion.agent == "grunt"))).scalars().all()
        assert log[-1].changed_via == "migration" and log[-1].changed_by == "platform:agent-self-persona-only"
