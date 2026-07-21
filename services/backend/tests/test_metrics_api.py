from datetime import timedelta

from agentplatform.db import Run, RunModelUsage, RunState, utcnow


async def test_recorder_captures_per_model_usage(sf):
    from sqlalchemy import select
    from agentplatform.recorder import Recorder
    async with sf() as s:
        r = Run(agent="echo", trigger="manual", requested_by="t", prompt="x")
        s.add(r); await s.commit(); rid = r.id
    rec = Recorder(sf)
    await rec._handle_transcript(rid, {"seq": 1, "type": "result", "result": "hi",
        "modelUsage": {"claude-sonnet-5": {"inputTokens": 10, "outputTokens": 20},
                       "claude-haiku-4-5": {"inputTokens": 3, "outputTokens": 1}}})
    async with sf() as s:
        rows = {u.model: u for u in (await s.execute(select(RunModelUsage).where(RunModelUsage.run_id == rid))).scalars()}
    assert rows["claude-sonnet-5"].tokens_in == 10 and rows["claude-sonnet-5"].agent == "echo"
    assert rows["claude-haiku-4-5"].tokens_out == 1


async def test_metrics_by_model_and_agent_filter(admin_client, sf):
    async with sf() as s:
        s.add(RunModelUsage(run_id="r1", model="claude-sonnet-5", agent="echo", tokens_in=10, tokens_out=20))
        s.add(RunModelUsage(run_id="r1", model="claude-haiku-4-5", agent="echo", tokens_in=5, tokens_out=2))
        s.add(RunModelUsage(run_id="r2", model="claude-sonnet-5", agent="hello", tokens_in=100, tokens_out=200))
        await s.commit()
    rows = (await admin_client.get("/api/metrics/models")).json()
    by = {r["model"]: r for r in rows}
    assert by["claude-sonnet-5"]["tokens_in"] == 110 and by["claude-sonnet-5"]["runs"] == 2
    assert rows[0]["model"] == "claude-sonnet-5"   # sorted by total tokens desc
    # agent filter
    echo = {r["model"]: r for r in (await admin_client.get("/api/metrics/models?agent=echo")).json()}
    assert echo["claude-sonnet-5"]["tokens_in"] == 10 and "claude-haiku-4-5" in echo
    assert "claude-sonnet-5" in echo and len(echo) == 2


async def _mk(sf, agent, state, *, tokens=(0, 0), tool_calls=0, dur=None, created=None):
    async with sf() as s:
        r = Run(agent=agent, trigger="manual", requested_by="t", prompt="x", state=state,
                tokens_in=tokens[0], tokens_out=tokens[1], tool_calls=tool_calls,
                created_at=created or utcnow())
        if dur is not None:
            r.started_at = r.created_at
            r.finished_at = r.created_at + timedelta(seconds=dur)
        s.add(r)
        await s.commit()
        return r.id


async def test_overview_aggregates(admin_client, sf):
    await _mk(sf, "echo", RunState.SUCCEEDED, tokens=(10, 20), tool_calls=2, dur=4)
    await _mk(sf, "echo", RunState.FAILED, dur=10)
    await _mk(sf, "echo", RunState.RUNNING)
    o = (await admin_client.get("/api/metrics/overview")).json()
    assert o["total"] == 3
    assert o["active"] == 1
    assert o["succeeded"] == 1
    # success_rate over terminal runs only (1 succeeded of 2 terminal)
    assert o["success_rate"] == 0.5
    assert o["tokens_in"] == 10 and o["tokens_out"] == 20 and o["tool_calls"] == 2
    assert o["avg_duration_seconds"] == 7.0 and o["max_duration_seconds"] == 10.0


async def test_overview_time_windows(admin_client, sf):
    await _mk(sf, "echo", RunState.SUCCEEDED, created=utcnow())
    await _mk(sf, "echo", RunState.SUCCEEDED, created=utcnow() - timedelta(days=3))
    await _mk(sf, "echo", RunState.SUCCEEDED, created=utcnow() - timedelta(days=30))
    o = (await admin_client.get("/api/metrics/overview")).json()
    assert o["runs_24h"] == 1 and o["runs_7d"] == 2 and o["total"] == 3


async def test_per_agent_and_failure_streak(admin_client, sf):
    # echo: newest two are failures after a success → streak 2
    await _mk(sf, "echo", RunState.SUCCEEDED, created=utcnow() - timedelta(minutes=30))
    await _mk(sf, "echo", RunState.FAILED, created=utcnow() - timedelta(minutes=20))
    await _mk(sf, "echo", RunState.TIMED_OUT, created=utcnow() - timedelta(minutes=10))
    await _mk(sf, "hello", RunState.SUCCEEDED, created=utcnow())
    rows = (await admin_client.get("/api/metrics/agents")).json()
    by = {r["agent"]: r for r in rows}
    assert by["echo"]["total"] == 3 and by["echo"]["failure_streak"] == 2
    assert by["hello"]["failure_streak"] == 0
    # sorted by total desc → echo first
    assert rows[0]["agent"] == "echo"


async def test_active_run_does_not_break_streak(admin_client, sf):
    # A still-running newest run is skipped; streak counts terminal failures.
    await _mk(sf, "echo", RunState.SUCCEEDED, created=utcnow() - timedelta(minutes=30))
    await _mk(sf, "echo", RunState.FAILED, created=utcnow() - timedelta(minutes=20))
    await _mk(sf, "echo", RunState.RUNNING, created=utcnow())
    rows = (await admin_client.get("/api/metrics/agents")).json()
    assert {r["agent"]: r for r in rows}["echo"]["failure_streak"] == 1
