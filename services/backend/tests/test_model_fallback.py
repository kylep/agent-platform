from agentplatform.agentdefs import snapshot_of
from agentplatform.db import AgentDef, Run, AuthorizedRelaySession
from .test_agent_self import own_run
from .test_session_api import _auth, _session_key


async def test_fallback_is_own_run_frozen_single_use_and_does_not_change_agent(client, sf, seed_agent, agent_store):
    rid, _ = await own_run(sf, seed_agent, agent_store, model="gpt-6-sol", backup_runtime="claude", backup_model="claude-sonnet-5-5")
    async with sf() as s:
        row = await s.get(AgentDef, "companion")
        run = await s.get(Run, rid)
        run.definition_snapshot = snapshot_of(row)
        run.model = row.model
        s.add(AuthorizedRelaySession(channel_id=run.conversation_id, agent=run.agent, authorization_generation=run.authorization_generation or 0, claude_session_id="stale-primary", session_blob=b"old", codex_thread_id="stale-thread"))
        await s.commit()
    auth = _auth(await _session_key(sf, rid))
    # Test app's proxies are configured here; no network traffic is performed.
    client._transport.app.state.settings.claude_proxy_url = "http://claude-proxy"
    path = f"/api/runs/{rid}/model-fallback"
    response = await client.post(path, headers=auth, json={"reason": "capacity"})
    assert response.status_code == 200, response.text
    assert response.json()["model"] == "claude-sonnet-5-5"
    assert "same agent" in response.json()["notice"]
    assert (await client.post(path, headers=auth, json={"reason": "capacity"})).status_code == 409
    definition = await client.get(f"/api/runs/{rid}/agentdef", headers=auth)
    assert definition.status_code == 200, definition.text
    assert definition.json()["runtime"] == "claude"
    assert definition.json()["fallback_used"]
    saved = await client.put(f"/api/runs/{rid}/session", headers=auth, json={"session_id": "backup-session", "blob_b64": "e30="})
    assert saved.status_code == 200 and saved.json()["reset"]
    async with sf() as s:
        row = await s.get(AgentDef, "companion")
        run = await s.get(Run, rid)
        assert row.runtime == "codex" and row.model == "gpt-6-sol"
        cached = await s.get(AuthorizedRelaySession, {"channel_id": run.conversation_id, "agent": run.agent, "authorization_generation": run.authorization_generation or 0})
        assert cached.claude_session_id == "" and cached.codex_thread_id == "" and cached.session_blob is None
        assert run.definition_snapshot["runtime"] == "codex"
        assert run.runtime == "claude" and run.fallback_reason == "capacity"


async def test_fallback_refuses_started_work_and_invalid_reason(client, sf, seed_agent, agent_store):
    rid, _ = await own_run(sf, seed_agent, agent_store, model="gpt-6-sol", backup_runtime="claude", backup_model="claude-sonnet-5-5")
    async with sf() as s:
        run = await s.get(Run, rid)
        run.definition_snapshot = snapshot_of(await s.get(AgentDef, "companion"))
        run.tool_calls = 1
        await s.commit()
    auth = _auth(await _session_key(sf, rid))
    assert (await client.post(f"/api/runs/{rid}/model-fallback", headers=auth, json={"reason": "capacity"})).status_code == 409
    assert (await client.post('/api/runs/somebody-else/model-fallback', headers=auth, json={"reason": "capacity"})).status_code == 403


async def test_quota_gate_follows_effective_backup_runtime(sf, seed_agent, agent_store):
    from types import SimpleNamespace
    from agentplatform.api.quota import _thresholds
    rid, _ = await own_run(sf, seed_agent, agent_store, model='gpt-6-sol', backup_runtime='claude', backup_model='claude-sonnet-5-5')
    async with sf() as s:
        run = await s.get(Run, rid)
        run.runtime = 'claude'
        run.fallback_used = True
        await s.commit()
    request = SimpleNamespace(state=SimpleNamespace(api_key_agent='companion', api_key_run_id=rid), app=SimpleNamespace(state=SimpleNamespace(session_factory=sf)))
    assert (await _thresholds(request))[2] == 'claude'
