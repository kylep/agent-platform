"""The DB-backed AgentStore (docs/design/15): definitions are `agent_defs`
rows, the store is a cached read model over them."""
import asyncio

from agentplatform.agents import AgentStore
from agentplatform.db import AgentDef


async def test_list_and_defaults(sf, seed_agent):
    await seed_agent("hello-world", description="test")
    store = AgentStore(sf)
    await store.reload()
    byname = {a.name: a for a in store.list()}
    info = byname["hello-world"]
    assert info.error is None
    assert info.manifest.concurrency == 1 and info.manifest.role == "operator"
    assert info.manifest.description == "test"
    # agent_md is the prompt, verbatim — no synthesized frontmatter.
    assert info.agent_md == "# hello-world\nYou are hello-world."
    assert not info.agent_md.startswith("---")


async def test_row_fields_project_onto_the_manifest(sf, seed_agent):
    await seed_agent("busy", description="d", role="coder", concurrency=3,
                     timeout_seconds=60, model="sonnet", system=True,
                     can_invoke=True, result_topic="app.news.digest",
                     transcript_retention_days=7, skills=["git"],
                     secrets=["github-token"])
    store = AgentStore(sf)
    await store.reload()
    m = store.get("busy").manifest
    assert (m.role, m.concurrency, m.timeout_seconds) == ("coder", 3, 60)
    assert (m.model, m.system, m.can_invoke) == ("sonnet", True, True)
    assert m.result_topic == "app.news.digest" and m.transcript_retention_days == 7
    assert m.skills == ["git"] and m.secrets == ["github-token"]


async def test_invalid_row_quarantines(sf):
    """A row that stopped validating (here: a role no longer in the ladder) is
    quarantined like a broken manifest.yaml was — not silently dispatched."""
    async with sf() as s:
        s.add(AgentDef(name="broken", role="superuser"))
        await s.commit()
    store = AgentStore(sf)
    await store.reload()
    info = store.get("broken")
    assert info.manifest is None and info.error is not None
    assert "role" in info.error


async def test_grants_come_from_the_row_not_frontmatter(sf, seed_agent):
    await seed_agent("granted", platform_tools=["mcp__platform__runs_read"],
                     harness_tools=["WebFetch"])
    store = AgentStore(sf)
    await store.reload()
    info = store.get("granted")
    assert info.platform_tools == ["mcp__platform__runs_read"]
    assert info.harness_tools == ["WebFetch"]
    # Nothing may re-derive grants by parsing the prompt: it has no frontmatter.
    from agentplatform.agentspec import parse_agent_tools
    assert parse_agent_tools(info.agent_md) is None


async def test_unknown_agent_is_none(sf):
    store = AgentStore(sf)
    await store.reload()
    assert store.get("nobody") is None


async def test_reload_picks_up_a_new_and_a_deleted_agent(sf, seed_agent):
    store = AgentStore(sf)
    await store.reload()
    assert store.get("late") is None
    await seed_agent("late")
    await store.reload()
    assert store.get("late") is not None
    async with sf() as s:
        await s.delete(await s.get(AgentDef, "late"))
        await s.commit()
    await store.reload()
    assert store.get("late") is None


async def test_stale_read_schedules_a_refresh(sf, seed_agent):
    """The TTL is what lets a long-lived reader (recorder, dispatcher) see a UI
    edit without a restart: a read of a stale cache kicks a background reload
    and the NEXT read is fresh."""
    store = AgentStore(sf, ttl_seconds=0.01)
    await store.reload()
    await seed_agent("newcomer")
    await asyncio.sleep(0.02)                  # let the cache go stale
    assert store.get("newcomer") is None       # stale read: schedules, serves cache
    await store._refresh                       # the refresh that read scheduled
    assert store.get("newcomer") is not None


async def test_fresh_read_does_not_hit_the_db(sf, seed_agent):
    store = AgentStore(sf, ttl_seconds=60)
    await store.reload()
    await seed_agent("invisible")
    for _ in range(5):
        assert store.get("invisible") is None
    assert store._refresh is None


async def test_store_without_a_session_factory_is_inert(sf):
    """api_main builds the store before the lifespan has a session factory."""
    store = AgentStore(None)
    await store.reload()
    assert store.list() == [] and store.get("x") is None


async def test_agents_api(admin_client):
    r = await admin_client.get("/api/agents")
    assert r.status_code == 200
    assert [a["name"] for a in r.json()] == ["hello-world"]


async def test_get_agent_returns_the_row(admin_client):
    r = await admin_client.get("/api/agents/hello-world")
    assert r.status_code == 200
    body = r.json()
    assert body["agent_md"].endswith("You are hello-world.")
    assert body["manifest"]["description"] == "test"
    assert body["platform_tools"] == [] and body["enabled"] is True


async def test_non_admin_key_can_list_agents(client, sf):
    # A reader+ key (here: operator) may list/inspect agents — needed for the
    # SDK / platform skill — but the router's edit routes stay admin-only.
    from agentplatform.apikeys import generate_token, hash_token, token_prefix
    from agentplatform.db import ApiKey
    token = generate_token()
    async with sf() as s:
        s.add(ApiKey(name="op", role="operator", key_hash=hash_token(token),
                     prefix=token_prefix(token)))
        await s.commit()
    h = {"Authorization": f"Bearer {token}"}
    assert (await client.get("/api/agents", headers=h)).status_code == 200
    # edits remain admin-only
    assert (await client.post("/api/agents/x/quick-edit",
                              json={"field": "prompt", "value": "y"}, headers=h)).status_code == 403
