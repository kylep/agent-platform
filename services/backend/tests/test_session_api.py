"""Run-scoped conversation session blob GET/PUT (docs/design/14). A per-run
`session` token may read/write only its own run's conversation blob."""
import base64

from agentplatform.apikeys import generate_token, hash_token, token_prefix
from agentplatform.db import ApiKey, Conversation, Run, RunState


async def _conv_run(sf, *, with_conv=True) -> tuple[str, str | None]:
    async with sf() as s:
        conv_id = None
        if with_conv:
            conv = Conversation(connector="web", agent="hello-world", title="t")
            s.add(conv); await s.flush(); conv_id = conv.id
        run = Run(agent="hello-world", trigger="conversation", requested_by="t",
                  prompt="x", state=RunState.RUNNING, conversation_id=conv_id)
        s.add(run); await s.commit()
        return run.id, conv_id


async def _session_key(sf, run_id: str) -> str:
    token = generate_token()
    async with sf() as s:
        s.add(ApiKey(name="session:hello-world", role="session", agent="hello-world",
                     run_id=run_id, key_hash=hash_token(token), prefix=token_prefix(token)))
        await s.commit()
    return token


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def test_put_then_get_roundtrips(client, sf):
    run_id, _ = await _conv_run(sf)
    tok = await _session_key(sf, run_id)
    blob = base64.b64encode(b"\x00some jsonl bytes").decode()
    put = await client.put(f"/api/runs/{run_id}/session",
                           json={"session_id": "sid-1", "blob_b64": blob}, headers=_auth(tok))
    assert put.status_code == 200 and put.json() == {"ok": True, "reset": False}
    got = (await client.get(f"/api/runs/{run_id}/session", headers=_auth(tok))).json()
    assert got["session_id"] == "sid-1" and got["blob_b64"] == blob


async def test_get_no_conversation_404(client, sf):
    run_id, _ = await _conv_run(sf, with_conv=False)
    tok = await _session_key(sf, run_id)
    r = await client.get(f"/api/runs/{run_id}/session", headers=_auth(tok))
    assert r.status_code == 404


async def test_get_no_blob_yet_returns_nulls(client, sf):
    run_id, _ = await _conv_run(sf)
    tok = await _session_key(sf, run_id)
    got = (await client.get(f"/api/runs/{run_id}/session", headers=_auth(tok))).json()
    assert got == {"session_id": None, "blob_b64": None}


async def test_wrong_run_token_forbidden(client, sf):
    run_a, _ = await _conv_run(sf)
    run_b, _ = await _conv_run(sf)
    tok_a = await _session_key(sf, run_a)
    r = await client.get(f"/api/runs/{run_b}/session", headers=_auth(tok_a))
    assert r.status_code == 403


async def test_oversized_put_resets(client, sf):
    client._transport.app.state.settings.session_blob_max_bytes = 8
    run_id, conv_id = await _conv_run(sf)
    tok = await _session_key(sf, run_id)
    # First store a valid small blob, then overflow it.
    await client.put(f"/api/runs/{run_id}/session",
                     json={"session_id": "sid-1", "blob_b64": base64.b64encode(b"tiny").decode()},
                     headers=_auth(tok))
    big = base64.b64encode(b"way too many bytes here").decode()
    put = await client.put(f"/api/runs/{run_id}/session",
                           json={"session_id": "sid-2", "blob_b64": big}, headers=_auth(tok))
    assert put.status_code == 200 and put.json() == {"ok": True, "reset": True}
    got = (await client.get(f"/api/runs/{run_id}/session", headers=_auth(tok))).json()
    assert got == {"session_id": None, "blob_b64": None}


async def test_old_generation_cannot_read_or_upload_session(client, sf):
    from agentplatform.db import AgentDef, AuthorizedRelaySession
    run_id, conv_id = await _conv_run(sf)
    tok = await _session_key(sf, run_id)
    async with sf() as s:
        s.add(AuthorizedRelaySession(channel_id=conv_id, agent="hello-world",
                                     authorization_generation=0,
                                     claude_session_id="old", session_blob=b"private"))
        agent = await s.get(AgentDef, "hello-world")
        agent.authorization_generation = 1
        s.add(AuthorizedRelaySession(channel_id=conv_id, agent="hello-world",
                                     authorization_generation=1,
                                     claude_session_id="fresh", session_blob=b"new context"))
        await s.commit()
    assert (await client.get(f"/api/runs/{run_id}/session", headers=_auth(tok))).status_code in (401, 403)
    result = await client.put(f"/api/runs/{run_id}/session", headers=_auth(tok),
                              json={"session_id": "late", "blob_b64": base64.b64encode(b"old context").decode()})
    assert result.status_code in (401, 403)
    async with sf() as s:
        fresh = await s.get(AuthorizedRelaySession, (conv_id, "hello-world", 1))
        assert fresh.claude_session_id == "fresh" and fresh.session_blob == b"new context"


async def test_legacy_blob_never_becomes_resume_fallback(client, sf):
    from agentplatform.db import RelaySession
    run_id, conv_id = await _conv_run(sf)
    tok = await _session_key(sf, run_id)
    async with sf() as s:
        s.add(RelaySession(channel_id=conv_id, agent="hello-world",
                           claude_session_id="historical", session_blob=b"old private context"))
        await s.commit()
    response = await client.get(f"/api/runs/{run_id}/session", headers=_auth(tok))
    assert response.status_code == 200
    assert response.json() == {"session_id": None, "blob_b64": None}


async def test_silent_account_lease_expiry_fences_internal_session(client, sf):
    from datetime import timedelta
    from agentplatform.db import AgentDef, ChatIdentity, utcnow
    run_id, _ = await _conv_run(sf)
    tok = await _session_key(sf, run_id)
    async with sf() as s:
        s.add(ChatIdentity(id="expired-account", connector="discord", display_name="Expired",
                           owner_agent="hello-world", access_expires_at=utcnow() - timedelta(seconds=1),
                           lease_invalidated=False))
        await s.commit()
    response = await client.get(f"/api/runs/{run_id}/session", headers=_auth(tok))
    assert response.status_code in (401, 403)
    async with sf() as s:
        assert (await s.get(AgentDef, "hello-world")).authorization_generation == 1
        assert (await s.get(ChatIdentity, "expired-account")).lease_invalidated is True


async def test_session_insert_retry_rechecks_authority(client, sf, monkeypatch):
    from sqlalchemy.exc import IntegrityError
    from agentplatform.api import runs as api
    from agentplatform.db import AgentDef
    run_id, _ = await _conv_run(sf)
    tok = await _session_key(sf, run_id)
    original = api._store_session
    calls = 0
    async def raced_store(s, key, session_id, blob):
        nonlocal calls
        calls += 1
        if calls == 1:
            agent = await s.get(AgentDef, "hello-world")
            agent.authorization_generation += 1
            await s.commit()
            raise IntegrityError("simulated unique insert race", {}, Exception("race"))
        await original(s, key, session_id, blob)
    monkeypatch.setattr(api, "_store_session", raced_store)
    response = await client.put(f"/api/runs/{run_id}/session", headers=_auth(tok),
                                json={"session_id": "late", "blob_b64": base64.b64encode(b"old").decode()})
    assert response.status_code == 403
    assert calls == 1
