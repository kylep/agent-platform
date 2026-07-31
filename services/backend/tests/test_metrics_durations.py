"""The seconds-per-run series endpoint."""
from datetime import timedelta

from agentplatform.db import Run, RunState, utcnow


async def _mk_run(sf, agent, seconds, *, days_ago=0.0, state=RunState.SUCCEEDED):
    start = utcnow() - timedelta(days=days_ago, seconds=seconds)
    async with sf() as s:
        r = Run(agent=agent, trigger="manual", requested_by="t", prompt="x",
                state=state, started_at=start,
                finished_at=start + timedelta(seconds=seconds))
        s.add(r); await s.commit()
        return r.id


async def test_durations_series(admin_client, sf):
    a = await _mk_run(sf, "alpha", 12.0)
    await _mk_run(sf, "beta", 90.0, days_ago=1)
    await _mk_run(sf, "beta", 30.0, days_ago=40)          # outside 14d window
    await _mk_run(sf, "alpha", 5.0, state=RunState.FAILED)

    r = await admin_client.get("/api/metrics/durations")
    rows = r.json()
    assert {x["agent"] for x in rows} == {"alpha", "beta"}
    assert len(rows) == 3                                  # 40d-old point excluded
    # chronological, seconds computed from started/finished
    assert rows == sorted(rows, key=lambda x: x["finished_at"])
    by_id = {x["run_id"]: x for x in rows}
    assert by_id[a]["seconds"] == 12.0 and by_id[a]["state"] == "succeeded"

    # agent filter + widened window picks up the old run
    r = await admin_client.get("/api/metrics/durations?days=60&agent=beta")
    assert {x["agent"] for x in r.json()} == {"beta"} and len(r.json()) == 2

    # window clamp: days=0 → 1
    r = await admin_client.get("/api/metrics/durations?days=0")
    assert r.status_code == 200
