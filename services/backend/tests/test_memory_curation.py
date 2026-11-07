"""Memory usage and guards (ENG-12, ENG-13). The guard trigger itself is
Postgres-only: tests/test_memory_history_postgres.py."""
from agentplatform import dreams as D
from agentplatform import memory_curation as MC
from agentplatform.db import Memory, Run

from .test_dreams import _agent, _materialize, _review, _start
from .test_relay_api import _agent_token


async def test_retrievals_count_per_memory_but_never_for_a_dream(sf, producer, seed_agent):
    await _agent(seed_agent)
    async with sf() as s:
        a, b = Memory(agent="pai", content="tea"), Memory(agent="pai", content="coffee")
        s.add_all([a, b, Run(id="r" * 32, agent="pai", trigger="manual", requested_by="kyle", prompt="")])
        await s.commit()
    async with sf() as s:
        await MC.note_use(s, "r" * 32, "pai", [a.id, b.id, a.id])
        await s.commit()
    async with sf() as s:
        await MC.note_use(s, "r" * 32, "pai", [a.id])
        await MC.note_use(s, None, "pai", [b.id])
        await s.commit()
    d = await _start(sf, producer)
    async with sf() as s:
        await MC.note_use(s, d.run_id, "pai", [a.id, b.id])
        await s.commit()
        usage, _ = await MC.signals(s, "pai")
    assert usage[a.id].retrievals == 2 and usage[b.id].retrievals == 1
    assert usage[a.id].last_retrieved_at is not None


async def test_review_shows_use_and_guards_that_admins_set(admin_client, token_client, sf, producer, seed_agent):
    await _agent(seed_agent)
    async with sf() as s:
        used, kept, plain = (Memory(agent="pai", content=c) for c in ("used", "kept", "plain"))
        s.add_all([used, kept, plain])
        await s.flush()
        s.add(MC.MemoryUsage(memory_id=used.id, agent="pai", retrievals=4))
        await s.commit()
    r = await admin_client.put(f"/api/memories/{kept.id}/guard", json={"level": "pin", "note": "identity"})
    assert r.status_code == 200 and r.json()["level"] == "pin" and r.json()["set_by"].startswith("admin:")
    assert [g["memory_id"] for g in (await admin_client.get("/api/memory-guards?agent=pai")).json()] == [kept.id]
    d = await _start(sf, producer)
    await _materialize(sf, d)
    items = {i["memory_id"]: i for i in (await _review(token_client, sf, d, "memories")).json()["items"]}
    assert items[used.id]["use"]["retrievals"] == 4 and "guard" not in items[used.id]
    assert items[plain.id]["use"] == {"retrievals": 0, "last_retrieved_at": None}
    assert items[kept.id]["guard"] == "pin"
    assert "pin" in D.DREAM_PROMPT and "`use`" in D.DREAM_PROMPT
    # Only admins set guards; lifting one removes it.
    headers = await _agent_token(sf, "pai", role="operator")
    assert (await token_client.put(f"/api/memories/{kept.id}/guard", headers=headers,
                                   json={"level": None})).status_code in (401, 403)
    assert (await admin_client.put(f"/api/memories/{kept.id}/guard", json={"level": None})).json()["level"] is None
    assert (await admin_client.get("/api/memory-guards")).json() == []
    assert (await admin_client.put(f"/api/memories/{'0' * 32}/guard", json={"level": "lock"})).status_code == 404


def test_guard_refusals_are_recognised():
    assert MC.refusal(Exception("memory_locked: an admin locked memory x")) == "memory_locked"
    assert MC.refusal(Exception("memory_pinned: ...")) == "memory_pinned"
    assert MC.refusal(Exception("memory_dream_loss_limit")) is None


def test_only_a_real_admin_writes_as_rest_admin():
    from types import SimpleNamespace
    from agentplatform.api.memory import _writer

    def req(**state):
        return SimpleNamespace(state=SimpleNamespace(**state))
    assert _writer(req(resolved_role="admin")) == "rest:admin"
    assert _writer(req(resolved_role="operator")) == "rest:human"
    assert _writer(req(resolved_role="admin", api_key_agent="pai")) == "rest:pai"
    assert _writer(req(api_key_run_id="r1", api_key_agent="pai")) == "r1"
