"""GEN-11 Phase 1 wiring, end to end in one process (plan §1, §3.1, §5, §6, §7).

A real Engine over the real World, the host glue and the commands.py port, with
the real materialize_run behind a spy that records every launch. The
dispatcher is played by hand (a Run goes `running` when the test says so)."""
import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select, update

from agentplatform import materialize
from agentplatform.db import Run, RunState, VillageNote, VillagePlannerCycle, VillageRun, VillageSession
from agentplatform.events import FakeProducer
from agentplatform.village import host, main as village_main
from agentplatform.village.commands import Refused
from agentplatform.village.main import Engine, NullLock
from tests.test_village_store import T0, Now

PAI_PROMPT = ("You are Pai.\n<!-- village:how-i-play -->\n## How I play the village game\n"
              "Build benches by the pond.\n<!-- /village:how-i-play -->\n")


@pytest.fixture
def launches(monkeypatch):
    """Records every materialize_run call (spec + kwargs), then runs the real one."""
    calls = []
    real = materialize.materialize_run

    async def spy(sf_, producer_, spec, **kw):
        calls.append((dict(spec), dict(kw)))
        return await real(sf_, producer_, spec, **kw)

    monkeypatch.setattr(materialize, "materialize_run", spy)
    return calls


async def _no_quota(provider):
    return None


async def _no_codex():
    return False


async def _set_run(sf, run_id, state=RunState.RUNNING):
    async with sf() as s:
        await s.execute(update(Run).where(Run.id == run_id).values(state=state))
        await s.commit()


async def _village_rows(sf):
    async with sf() as s:
        return list((await s.execute(select(VillageRun).order_by(VillageRun.launched_at))).scalars())


def _ticks(eng, n):
    for _ in range(n):
        eng.tick_once()


async def test_a_test_session_runs_end_to_end(sf, producer, seed_agent, launches):
    await seed_agent("pai", prompt=PAI_PROMPT, platform_tools=["mcp__platform__village"])
    now, mono = Now(T0), [0.0]
    eng = Engine(sf, producer, lock=NullLock(), now=now, quota=_no_quota, codex_in_use=_no_codex)
    await eng.acquire_lock_once()
    await eng.boot()
    assert eng.recovery.fresh and eng.world.actors["pai"]["kind"] == "character"
    village = host.attach(eng, global_concurrency=8, clock=lambda: mono[0])

    # --- open: the API inserts the row, the clock opens it -------------------------
    async with sf() as s:
        s.add(VillageSession(id="t1", day="2026-11-27", kind="test", state="scheduled",
                             length_min=30, quota_at_start={"override": True}))
        await s.commit()
    await eng.clock.poll()
    assert eng.live and eng.session_id == "t1"
    ((spec, kw),) = launches
    assert spec["agent"] == "village-planner" and spec["trigger"] == "village"
    assert kw["timeout_seconds"] == 30 * 60 + host.PLANNER_MARGIN_S
    assert "Build benches by the pond." in spec["prompt"] and "persona-brief" in spec["prompt"]
    planner = spec["run_id"]
    await _set_run(sf, planner)                       # the dispatcher picked it up

    # --- a tool command through the port, authenticated as the planner run --------
    first = (await village.port.call("village-planner", planner,
                                     {"action": "wait", "args": {"cursor": "0"}}))["data"]
    assert first["full"] and first["look"]["self"]["id"] == "pai"
    edit = {"action": "edit_queue", "args": {
        "request_id": "r1", "base_version": first["queue_version"],
        "ops": [{"op": "append", "steps": [{"op": "walk", "target": "square", "id": "w1"},
                                           {"op": "say", "say": "Morning, village.", "id": "hi"}]}]}}
    receipt = (await village.port.call("village-planner", planner, edit))["data"]
    assert receipt["state"] == "accepted" and receipt["seq"]
    again = (await village.port.call("village-planner", planner, edit))["data"]
    assert again["repeat"] and again["seq"] == receipt["seq"]
    with pytest.raises(Refused) as no:                 # same request_id, other args
        await village.port.call("village-planner", planner,
                                {**edit, "args": {**edit["args"], "base_version": 99}})
    assert no.value.status == 409
    with pytest.raises(Refused) as wrong:              # the planner run is not Pai
        await village.port.call("pai", planner, {"action": "look", "args": {}})
    assert wrong.value.status == 403

    applied = eng.wait_for(receipt["seq"])
    _ticks(eng, 4)
    assert applied.result().state == "applied"
    assert [s["id"] for s in eng.world.actors["pai"]["queue"]][:2] == ["w1", "hi"]
    async with sf() as s:
        cycles = list((await s.execute(select(VillagePlannerCycle))).scalars())
    assert len(cycles) == 1 and cycles[0].run_id == planner and cycles[0].think_ms is not None

    mono[0] += 11                                      # past planner_min_s
    second = (await village.port.call("village-planner", planner,
                                      {"action": "wait", "args": {"cursor": first["cursor"]}}))["data"]
    assert not second["full"] and second["changes"]["self"]["queue"][0]["id"] == "w1"
    assert second["queue_version"] == eng.world.actors["pai"]["queue_version"]

    # --- the first reflection slot (+5:00) ------------------------------------------
    now.t = T0 + timedelta(minutes=5, seconds=2)
    await village.tend()
    assert len(launches) == 2
    rspec, rkw = launches[1]
    assert rspec["agent"] == "pai" and rkw["timeout_seconds"] == 480
    assert rspec["memory_query"].startswith("village: pai") and "village-reflection" in rspec["prompt"]
    reflection = rspec["run_id"]
    (rrow,) = [r for r in await _village_rows(sf) if r.role == "reflection"]
    assert rrow.slot == "r1" and rrow.persona == "pai"
    await _set_run(sf, reflection)
    await village.tend()
    note = await village.port.call("pai", reflection, {"action": "note", "args": {
        "request_id": "n1", "text": "Reeds run out fast; fetch early."}})
    assert note["data"]["state"] == "accepted"
    goals = (await village.port.call("pai", reflection, {"action": "set_goals", "args": {
        "request_id": "g1", "goals": ["a bench by the pond"]}}))["data"]
    recalled = (await village.port.call("pai", reflection, {"action": "recall_notes",
                                                            "args": {"q": "reeds"}}))["data"]
    assert [n["text"] for n in recalled["notes"]] == ["Reeds run out fast; fetch early."]
    with pytest.raises(Refused):                       # reflection runs don't `wait`
        await village.port.call("pai", reflection, {"action": "wait", "args": {}})
    done = eng.wait_for(goals["seq"])
    _ticks(eng, 2)
    assert done.result().state == "applied" and eng.world.actors["pai"]["goals"] == ["a bench by the pond"]

    # --- close: Stop pulls the end in, the clock freezes the world -------------------
    async with sf() as s:
        await s.execute(update(VillageSession).where(VillageSession.id == "t1")
                        .values(planned_end=now.t))
        await s.commit()
    await eng.clock.poll()
    _ticks(eng, 1)
    assert eng.pending_close
    await eng.close_session()
    async with sf() as s:
        row = await s.get(VillageSession, "t1")
    assert row.state == "closed" and eng.world.mode == "frozen" and not eng.live
    frozen_tick, frozen_hash = eng.world.tick, eng.world.state_hash()
    _ticks(eng, 20)
    assert eng.world.tick == frozen_tick and eng.world.state_hash() == frozen_hash   # hands froze
    assert "tomorrow" in eng.world.actors["pai"]                                   # the T-0 fallback
    rows = await _village_rows(sf)
    assert {r.state for r in rows} == {"cancelled"} and len(rows) == 2
    cancels = [d["run_id"] for t, k, d in producer.published if d.get("type") == "cancel"]
    assert planner in cancels and reflection in cancels

    # --- nothing launches after close, and writes are refused -----------------------
    n = len(launches)
    for minutes in (8, 12, 20):
        now.t = T0 + timedelta(minutes=minutes)
        await eng.clock.poll()
        await village.tend()
    assert len(launches) == n
    with pytest.raises(Refused) as late:
        await village.port.call("village-planner", planner, {"action": "edit_queue", "args": {
            "request_id": "r9", "base_version": 0, "ops": [{"op": "say", "line": "hi"}]}})
    assert late.value.status == 403
    async with sf() as s:
        assert (await s.execute(select(VillageNote))).scalars().one().agent == "pai"


async def test_main_boots_on_sqlite_and_runs_a_session(tmp_path, monkeypatch, launches):
    """`python -m agentplatform.village.main` on sqlite: init_db, boot a fresh
    world, open a scheduled test session and tick."""
    from agentplatform.config import get_settings
    monkeypatch.setenv("AP_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 'v.db'}")
    get_settings.cache_clear()
    fake = FakeProducer()
    monkeypatch.setattr(village_main, "Producer", lambda *a, **k: fake)
    monkeypatch.setattr(village_main, "CLOCK_POLL_S", 0.05)
    seen = {}

    class Spy(Engine):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            seen["engine"] = self

    async def no_port(self):
        await self.engine.stop.wait()

    monkeypatch.setattr(village_main, "Engine", Spy)
    monkeypatch.setattr(host.Village, "serve", no_port)
    task = asyncio.create_task(village_main.main())
    try:
        for _ in range(200):
            if seen.get("engine") is not None and seen["engine"].world is not None:
                break
            await asyncio.sleep(0.02)
        eng = seen["engine"]
        assert eng.recovery.fresh and eng.world.actors["pai"]
        await eng.store.add_session(day="2026-11-27", kind="test", state="scheduled",
                                    length_min=8, quota_at_start={"override": True})
        for _ in range(200):
            if eng.live and eng.world.tick > 4:
                break
            await asyncio.sleep(0.02)
        assert eng.live and eng.world.tick > 4
        assert [s["agent"] for s, _ in launches] == ["village-planner"]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        get_settings.cache_clear()
