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


async def test_malformed_input_is_422_not_500(admin_client, sf):
    # Regression for the adversarial finding: NUL bytes / over-length namespaces
    # must be rejected at the edge (422), never reach the DB as a 500. (The NUL-
    # in-query-string case the probe hit via curl can't be sent through httpx,
    # which rejects NUL in URLs; the _reject_nul guard covers it in prod. Here we
    # cover over-length query + NUL/over-length in the JSON body.)
    assert (await admin_client.get(f"/api/memories?agent={'x' * 200}")).status_code == 422
    r = await admin_client.post("/api/memories", json={"content": "hi\x00there", "agent": "alpha"})
    assert r.status_code == 422
    r = await admin_client.post("/api/memories", json={"content": "x", "agent": "y" * 200})
    assert r.status_code == 422


async def test_admin_can_target_any_namespace(admin_client, sf):
    r = await admin_client.post("/api/memories", json={"content": "seeded", "agent": "notetaker"})
    assert r.status_code == 201
    hits = (await admin_client.get("/api/memories?agent=notetaker")).json()
    assert any(m["content"] == "seeded" for m in hits)


async def test_edit_memory_content_in_place(admin_client, sf):
    r = await admin_client.post("/api/memories", json={"content": "draft note", "agent": "alpha"})
    mid = r.json()["id"]
    r = await admin_client.patch(f"/api/memories/{mid}", json={"content": "final note"})
    assert r.status_code == 200 and r.json()["content"] == "final note"
    got = (await admin_client.get(f"/api/memories/{mid}")).json()
    assert got["content"] == "final note"


async def test_edit_memory_empty_patch_is_422(admin_client, sf):
    r = await admin_client.post("/api/memories", json={"content": "x", "agent": "alpha"})
    mid = r.json()["id"]
    assert (await admin_client.patch(f"/api/memories/{mid}", json={})).status_code == 422


async def test_agent_cannot_edit_other_namespace(client, sf):
    a = await _agent_key(sf, agent="alpha")
    b = await _agent_key(sf, agent="beta")
    r = await client.post("/api/memories", json={"content": "mine"}, headers=_auth(a))
    mid = r.json()["id"]
    # Other namespace reads as 404 (existence not leaked), owner can edit.
    assert (await client.patch(f"/api/memories/{mid}", json={"content": "z"},
                               headers=_auth(b))).status_code == 404
    assert (await client.patch(f"/api/memories/{mid}", json={"content": "z"},
                               headers=_auth(a))).status_code == 200


async def test_duplicate_keys_deduped_and_constrained(sf):
    """The (agent, key) unique index: init_db dedupes pre-existing duplicates
    (keeping the newest) and further duplicate inserts are refused by the DB."""
    from datetime import datetime, timezone, timedelta
    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError
    from agentplatform.db import Memory
    import pytest

    async with sf() as s:
        m = Memory(agent="dup", key="k", content="new")
        s.add(m)
        await s.commit()
        # A straight duplicate insert must now violate the unique index.
        s.add(Memory(agent="dup", key="k", content="dupe"))
        with pytest.raises(IntegrityError):
            await s.commit()
        await s.rollback()
    async with sf() as s:
        rows = (await s.execute(select(Memory).where(Memory.agent == "dup"))).scalars().all()
    assert len(rows) == 1 and rows[0].content == "new"
