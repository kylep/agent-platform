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
