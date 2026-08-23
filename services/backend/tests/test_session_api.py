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
