"""Agent-invokes-agent: a per-run operator token can request runs, which become
children in the caller run's chain; depth is a loop guard the caller can't forge."""
from agentplatform.apikeys import generate_token, hash_token, token_prefix
from agentplatform.db import ApiKey, Run, RunState


async def _mk_run(sf, *, depth=0, state=RunState.SUCCEEDED) -> str:
    async with sf() as s:
        run = Run(agent="hello-world", trigger="manual", requested_by="t",
                  prompt="x", depth=depth, state=state)
        s.add(run)
        await s.commit()
        return run.id


async def _mk_key(sf, *, role="operator", run_id=None) -> str:
    token = generate_token()
    async with sf() as s:
        s.add(ApiKey(name="invoke:hello-world", role=role, agent="hello-world",
                     run_id=run_id, key_hash=hash_token(token), prefix=token_prefix(token)))
        await s.commit()
    return token


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def test_per_run_token_invoke_sets_parent_and_depth(client, sf):
    parent = await _mk_run(sf, depth=0)
    token = await _mk_key(sf, run_id=parent)
    r = await client.post("/api/runs", json={"agent": "hello-world", "prompt": "hi"}, headers=_auth(token))
    assert r.status_code == 200
    child_id = r.json()["id"]
    detail = (await client.get(f"/api/runs/{child_id}", headers=_auth(token))).json()
    assert detail["trigger"] == "agent"
    assert detail["parent_run_id"] == parent
    assert detail["depth"] == 1


async def test_depth_limit_rejects(client, sf):
    # A parent already at the max depth means the child would exceed it → 429.
    from agentplatform.config import Settings
    limit = Settings().max_run_chain_depth
    parent = await _mk_run(sf, depth=limit)
    token = await _mk_key(sf, run_id=parent)
    r = await client.post("/api/runs", json={"agent": "hello-world", "prompt": "hi"}, headers=_auth(token))
    assert r.status_code == 429


async def test_operator_token_without_run_is_root(client, sf):
    # A human operator key (no run scope) starts a fresh chain: manual, depth 0.
    token = await _mk_key(sf, run_id=None)
    r = await client.post("/api/runs", json={"agent": "hello-world", "prompt": "hi"}, headers=_auth(token))
    assert r.status_code == 200
    detail = (await client.get(f"/api/runs/{r.json()['id']}", headers=_auth(token))).json()
    assert detail["trigger"] == "manual" and detail["depth"] == 0 and detail["parent_run_id"] is None


async def test_annotator_token_cannot_invoke(client, sf):
    # The narrow system-agent role must not be able to spawn runs.
    token = await _mk_key(sf, role="annotator", run_id=await _mk_run(sf))
    r = await client.post("/api/runs", json={"agent": "hello-world", "prompt": "hi"}, headers=_auth(token))
    assert r.status_code == 403
