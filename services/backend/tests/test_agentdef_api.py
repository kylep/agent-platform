"""Run-scoped agent-definition delivery (docs/design/15).

Definitions are rows, so the run pod can't read them off the git-synced mount
any more: it fetches the one definition it is running with the same per-run
`session` token that unlocks the conversation session blob (docs/design/14).
"""
from agentplatform.apikeys import generate_token, hash_token, token_prefix
from agentplatform.db import ApiKey, Run, RunState


async def _run(sf, agent="hello-world") -> str:
    async with sf() as s:
        run = Run(agent=agent, trigger="manual", requested_by="t", prompt="x",
                  state=RunState.RUNNING)
        s.add(run)
        await s.commit()
        return run.id


async def _session_key(sf, run_id: str, agent="hello-world") -> str:
    token = generate_token()
    async with sf() as s:
        s.add(ApiKey(name=f"session:{agent}", role="session", agent=agent,
                     run_id=run_id, key_hash=hash_token(token),
                     prefix=token_prefix(token)))
        await s.commit()
    return token


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def test_serves_the_definition_the_pod_must_materialize(client, sf, seed_agent):
    """The payload is everything `~/.claude/agents/<name>.md` and the run's
    permission flags are built from. The agent is seeded AFTER the store was
    primed and the test never reloads it — the endpoint has to, or an agent
    created moments before its first run would 404."""
    await seed_agent("newsy", prompt="You are newsy.\n", model="sonnet",
                     harness_tools=["WebSearch", "WebFetch"],
                     platform_tools=["mcp__platform__memory"],
                     skills=["git"], secrets=["github-token"])
    rid = await _run(sf, "newsy")
    tok = await _session_key(sf, rid, "newsy")
    r = await client.get(f"/api/runs/{rid}/agentdef", headers=_auth(tok))
    assert r.status_code == 200
    assert r.json() == {"name": "newsy", "prompt": "You are newsy.\n",
                        "harness_tools": ["WebSearch", "WebFetch"],
                        "platform_tools": ["mcp__platform__memory"],
                        "skills": ["git"], "model": "sonnet"}


async def test_ungranted_agent_carries_empty_lists(client, sf, seed_agent):
    """Empty grants mean NO tools (fail-closed, docs/design/15) — the payload
    says so explicitly rather than omitting the fields."""
    await seed_agent("plain", prompt="hi")
    rid = await _run(sf, "plain")
    tok = await _session_key(sf, rid, "plain")
    body = (await client.get(f"/api/runs/{rid}/agentdef", headers=_auth(tok))).json()
    assert body["harness_tools"] == [] and body["platform_tools"] == []
    assert body["model"] == "" and body["skills"] == []


async def test_another_runs_token_is_forbidden(client, sf):
    run_a = await _run(sf)
    run_b = await _run(sf)
    tok_a = await _session_key(sf, run_a)
    r = await client.get(f"/api/runs/{run_b}/agentdef", headers=_auth(tok_a))
    assert r.status_code == 403


async def test_unauthenticated_is_rejected(client, sf):
    rid = await _run(sf)
    assert (await client.get(f"/api/runs/{rid}/agentdef")).status_code == 401


async def test_unknown_run_404(client, sf):
    tok = await _session_key(sf, "f" * 32)
    r = await client.get(f"/api/runs/{'f' * 32}/agentdef", headers=_auth(tok))
    assert r.status_code == 404


async def test_deleted_agent_404s_rather_than_serving_a_husk(client, sf, seed_agent):
    from agentplatform.db import AgentDef
    await seed_agent("doomed", prompt="x")
    rid = await _run(sf, "doomed")
    tok = await _session_key(sf, rid, "doomed")
    async with sf() as s:
        await s.delete(await s.get(AgentDef, "doomed"))
        await s.commit()
    r = await client.get(f"/api/runs/{rid}/agentdef", headers=_auth(tok))
    assert r.status_code == 404


async def test_admin_may_read_any_runs_definition(admin_client, sf):
    """Same shape as /session: an admin session has no api_key_run_id, so it can
    debug any run's definition."""
    rid = await _run(sf)
    r = await admin_client.get(f"/api/runs/{rid}/agentdef")
    assert r.status_code == 200 and r.json()["name"] == "hello-world"
