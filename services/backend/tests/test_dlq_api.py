from agentplatform.db import Run, RunState
from agentplatform.events import TOPIC_RUN_REQUESTS


async def _mk_dlq(sf, error="boom") -> str:
    async with sf() as s:
        run = Run(agent="hello-world", trigger="manual", requested_by="t",
                  prompt="x", state=RunState.DLQ, error=error)
        s.add(run)
        await s.commit()
        return run.id


async def test_list_dlq(admin_client, sf):
    rid = await _mk_dlq(sf)
    rows = (await admin_client.get("/api/dlq")).json()
    assert [r["id"] for r in rows] == [rid]
    assert rows[0]["error"] == "boom"


async def test_retry_requeues_and_republishes(admin_client, sf, producer):
    rid = await _mk_dlq(sf)
    r = await admin_client.post(f"/api/dlq/{rid}/retry")
    assert r.status_code == 200
    detail = (await admin_client.get(f"/api/runs/{rid}")).json()
    assert detail["state"] == RunState.QUEUED and detail["error"] is None
    assert (TOPIC_RUN_REQUESTS, rid, {"type": "run", "run_id": rid}) in producer.published
    # It has left the DLQ view.
    assert (await admin_client.get("/api/dlq")).json() == []


async def test_discard_marks_failed(admin_client, sf):
    rid = await _mk_dlq(sf)
    r = await admin_client.post(f"/api/dlq/{rid}/discard")
    assert r.status_code == 200
    detail = (await admin_client.get(f"/api/runs/{rid}")).json()
    assert detail["state"] == RunState.FAILED and "discarded" in detail["error"]
    assert (await admin_client.get("/api/dlq")).json() == []


async def test_retry_non_dlq_conflicts(admin_client, sf):
    async with sf() as s:
        run = Run(agent="hello-world", trigger="manual", requested_by="t",
                  prompt="x", state=RunState.SUCCEEDED)
        s.add(run)
        await s.commit()
        rid = run.id
    assert (await admin_client.post(f"/api/dlq/{rid}/retry")).status_code == 409
