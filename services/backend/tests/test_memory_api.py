"""Agent memory: namespaced save/search/recall, with an agent key locked to
its own namespace."""
from agentplatform.apikeys import generate_token, hash_token, token_prefix
from agentplatform.db import ApiKey


async def _agent_key(sf, agent="notetaker", role="annotator") -> str:
    token = generate_token()
    async with sf() as s:
        s.add(ApiKey(name=f"memory:{agent}", role=role, agent=agent,
                     key_hash=hash_token(token), prefix=token_prefix(token)))
        await s.commit()
    return token


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def test_agent_saves_and_recalls_across_calls(client, sf):
    token = await _agent_key(sf)
    r = await client.post("/api/memories", json={"key": "fav", "content": "the sky is blue"},
                          headers=_auth(token))
    assert r.status_code == 201 and r.json()["agent"] == "notetaker"
    # A separate call (new "run") with the same namespace recalls it.
    hits = (await client.get("/api/memories?q=sky", headers=_auth(token))).json()
    assert len(hits) == 1 and hits[0]["content"] == "the sky is blue"


async def test_key_upsert_overwrites(client, sf):
    token = await _agent_key(sf)
    await client.post("/api/memories", json={"key": "fav", "content": "first"}, headers=_auth(token))
    await client.post("/api/memories", json={"key": "fav", "content": "second"}, headers=_auth(token))
    hits = (await client.get("/api/memories", headers=_auth(token))).json()
    assert len(hits) == 1 and hits[0]["content"] == "second"


async def test_namespace_isolation_on_search(client, sf):
    a = await _agent_key(sf, agent="alpha")
    b = await _agent_key(sf, agent="beta")
    await client.post("/api/memories", json={"content": "alpha secret"}, headers=_auth(a))
    # beta sees nothing of alpha's.
    assert (await client.get("/api/memories", headers=_auth(b))).json() == []


async def test_agent_cannot_target_other_namespace(client, sf):
    token = await _agent_key(sf, agent="alpha")
    r = await client.post("/api/memories", json={"content": "x", "agent": "beta"}, headers=_auth(token))
    assert r.status_code == 403


async def test_get_delete_other_namespace_is_404(client, sf):
    a = await _agent_key(sf, agent="alpha")
    b = await _agent_key(sf, agent="beta")
    mid = (await client.post("/api/memories", json={"content": "hi"}, headers=_auth(a))).json()["id"]
    assert (await client.get(f"/api/memories/{mid}", headers=_auth(b))).status_code == 404
    assert (await client.delete(f"/api/memories/{mid}", headers=_auth(b))).status_code == 404
    # owner can delete
    assert (await client.delete(f"/api/memories/{mid}", headers=_auth(a))).status_code == 200


async def test_admin_must_name_namespace(admin_client, sf):
    # Human/admin (no agent-scoped key) must pass ?agent=.
    assert (await admin_client.get("/api/memories")).status_code == 400
    assert (await admin_client.get("/api/memories?agent=notetaker")).status_code == 200


async def test_admin_can_target_any_namespace(admin_client, sf):
    r = await admin_client.post("/api/memories", json={"content": "seeded", "agent": "notetaker"})
    assert r.status_code == 201
    hits = (await admin_client.get("/api/memories?agent=notetaker")).json()
    assert any(m["content"] == "seeded" for m in hits)
