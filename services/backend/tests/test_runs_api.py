from agentplatform.events import TOPIC_RUN_REQUESTS

async def test_create_run_writes_db_then_kafka(admin_client, producer):
    r = await admin_client.post("/api/runs", json={"agent": "hello-world", "prompt": "hi"})
    assert r.status_code == 200
    run_id = r.json()["id"]
    assert producer.published == [(TOPIC_RUN_REQUESTS, run_id, {"type": "run", "run_id": run_id})]
    r = await admin_client.get(f"/api/runs/{run_id}")
    assert r.json()["state"] == "queued" and r.json()["agent"] == "hello-world"

async def test_unknown_agent_404(admin_client):
    assert (await admin_client.post("/api/runs", json={"agent": "nope", "prompt": "x"})).status_code == 404

async def test_kill_publishes_cancel(admin_client, producer):
    run_id = (await admin_client.post("/api/runs", json={"agent": "hello-world", "prompt": "hi"})).json()["id"]
    assert (await admin_client.post(f"/api/runs/{run_id}/kill")).status_code == 200
    assert producer.published[-1] == (TOPIC_RUN_REQUESTS, run_id, {"type": "cancel", "run_id": run_id})


async def test_create_run_survives_publish_failure(admin_client, producer):
    async def boom(topic, key, value):
        raise RuntimeError("kafka down")
    producer.publish = boom
    r = await admin_client.post("/api/runs", json={"agent": "hello-world", "prompt": "hi"})
    assert r.status_code == 200
    run_id = r.json()["id"]
    r = await admin_client.get(f"/api/runs/{run_id}")
    assert r.json()["state"] == "queued"


async def test_run_views_carry_the_ticket_it_was_summoned_from(admin_client, sf):
    """A run started from a ticket (docs/design/20) has to say which one, or
    the run page has no way back to the ticket that caused it."""
    from agentplatform.db import Run
    run_id = (await admin_client.post("/api/runs", json={"agent": "hello-world", "prompt": "hi"})).json()["id"]
    async with sf() as s:
        (await s.get(Run, run_id)).ticket_id = "PAI-7"
        await s.commit()
    assert (await admin_client.get(f"/api/runs/{run_id}")).json()["ticket_id"] == "PAI-7"
    row = next(r for r in (await admin_client.get("/api/runs")).json() if r["id"] == run_id)
    assert row["ticket_id"] == "PAI-7"


async def test_a_run_with_no_ticket_says_so(admin_client):
    run_id = (await admin_client.post("/api/runs", json={"agent": "hello-world", "prompt": "hi"})).json()["id"]
    assert (await admin_client.get(f"/api/runs/{run_id}")).json()["ticket_id"] is None
