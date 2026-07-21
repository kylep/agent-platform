"""Kafka health endpoint. No broker in tests, so it must degrade gracefully:
report unreachable + the DB-derived backlog rather than erroring."""
from agentplatform.db import Run, RunState


async def test_kafka_health_degrades_without_broker(admin_client, sf):
    async with sf() as s:
        s.add(Run(agent="hello-world", trigger="manual", requested_by="t", prompt="x",
                  state=RunState.QUEUED))
        s.add(Run(agent="hello-world", trigger="manual", requested_by="t", prompt="x",
                  state=RunState.DLQ))
        await s.commit()
    r = await admin_client.get("/api/health/kafka")
    assert r.status_code == 200
    body = r.json()
    assert body["reachable"] is False
    assert body["error"] is not None
    assert body["backlog"] == {"queued": 1, "active": 0, "dlq": 1}


async def test_kafka_health_requires_auth(client):
    assert (await client.get("/api/health/kafka")).status_code == 401
