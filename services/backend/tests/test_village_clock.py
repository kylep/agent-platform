"""U2: the session clock and quota gates (plan §4, App. A)."""
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from agentplatform.db import QuotaSnapshot, VillageCommand, VillageConfig, VillageSession
from agentplatform.village.clock import (QuotaView, decide_start, local_day, mid_gate,
                                         snapshot_reader, start_for)
from agentplatform.village.contract import merged_rules
from tests.test_village_store import T0, Now, make_engine

RULES = merged_rules(None)
NOW = T0


def q(five=10, seven=10, age_min=1, now=NOW):
    return QuotaView(five, seven, now - timedelta(minutes=age_min))


# --- noon in Toronto -----------------------------------------------------------------

def test_noon_toronto_across_dst():
    utc = lambda d: start_for(d, RULES)
    assert utc(date(2026, 3, 7)) == datetime(2026, 3, 7, 17, 0, tzinfo=timezone.utc)     # EST
    assert utc(date(2026, 3, 9)) == datetime(2026, 3, 9, 16, 0, tzinfo=timezone.utc)     # EDT
    assert utc(date(2026, 10, 31)) == datetime(2026, 10, 31, 16, 0, tzinfo=timezone.utc)
    assert utc(date(2026, 11, 2)) == datetime(2026, 11, 2, 17, 0, tzinfo=timezone.utc)
    # 23:30 Toronto on Nov 26 is still Nov 26; 05:30 UTC Nov 27 is Nov 27 00:30 Toronto
    assert local_day(datetime(2026, 11, 27, 4, 30, tzinfo=timezone.utc), RULES) == date(2026, 11, 26)
    assert local_day(datetime(2026, 11, 27, 5, 30, tzinfo=timezone.utc), RULES) == date(2026, 11, 27)


# --- start gates -------------------------------------------------------------------------

@pytest.mark.parametrize("seven,state,length", [(70, "live", 60), (71, "live", 30), (85, "live", 30),
                                                (86, "rest", 0)])
def test_gates_70_and_85_at_versus_over(seven, state, length):
    gate = decide_start(RULES, q(seven=seven), None, NOW)
    assert (gate.state, gate.length_min) == (state, length)
    if state == "rest":
        assert "86%" in gate.reason and gate.reason.startswith("Rest day")


@pytest.mark.parametrize("five,state", [(84, "live"), (85, "skipped"), (99, "skipped")])
def test_five_hour_at_85_skips_the_session(five, state):
    gate = decide_start(RULES, q(five=five), None, NOW)
    assert gate.state == state
    if state == "skipped":
        assert gate.reason == "the 5-hour AI limit is nearly used"


def test_percent_is_rounded_before_comparing():
    from agentplatform.village.clock import pct
    assert pct(0.855) == 86 and pct(0.85) == 85 and pct(0.29) == 29 and pct(None) is None


def test_stale_or_missing_snapshot_starts_a_30_minute_session():
    assert decide_start(RULES, None, None, NOW).length_min == 30
    old = decide_start(RULES, q(age_min=16), None, NOW)
    assert (old.state, old.length_min) == ("live", 30) and "stale" in old.reason
    assert decide_start(RULES, q(age_min=14), None, NOW).length_min == 60
    # a stale reading can't rest a day: it is not believed
    assert decide_start(RULES, q(seven=99, five=99, age_min=60), None, NOW).state == "live"


def test_codex_gate_applies_only_when_codex_is_in_use():
    codex_hot = q(seven=90)
    assert decide_start(RULES, q(), codex_hot, NOW, use_codex=False).state == "live"
    assert decide_start(RULES, q(), codex_hot, NOW, use_codex=True).state == "rest"
    assert decide_start(RULES, q(), q(seven=75), NOW, use_codex=True).length_min == 30
    assert decide_start(RULES, q(), None, NOW, use_codex=True).length_min == 30     # missing codex snapshot


def test_override_skips_the_gates_for_a_test_session():
    gate = decide_start(RULES, q(seven=99, five=99), None, NOW, override=True, requested_length=15)
    assert (gate.state, gate.length_min) == ("live", 15)


def test_mid_session_gate():
    assert mid_gate(RULES, q(seven=85), None, NOW) is None
    assert mid_gate(RULES, q(seven=86), None, NOW) == "quota:7d"
    assert mid_gate(RULES, q(five=94), None, NOW) is None
    assert mid_gate(RULES, q(five=95), None, NOW) == "quota:5h"
    assert mid_gate(RULES, q(seven=99, age_min=30), None, NOW) is None              # stale: not believed
    assert mid_gate(RULES, q(), q(seven=90), NOW, use_codex=False) is None
    assert mid_gate(RULES, q(), q(seven=90), NOW, use_codex=True) == "quota:7d"


# --- the clock -------------------------------------------------------------------------------

async def set_rules(sf, **rules):
    async with sf() as s:
        s.add(VillageConfig(id=1, rules=rules))
        await s.commit()


def quota_reader(**kw):
    views = {"claude": q(**kw), "codex": None}

    async def read(provider):
        return views[provider]
    read.views = views
    return read


async def sessions(sf):
    async with sf() as s:
        return (await s.execute(select(VillageSession).order_by(VillageSession.created_at))).scalars().all()


async def system_actions(sf):
    async with sf() as s:
        return [c.action for c in (await s.execute(select(VillageCommand).where(
            VillageCommand.source == "system").order_by(VillageCommand.seq))).scalars()]


async def clock_engine(sf, now, **kw):
    eng = make_engine(sf, now=now, quota=kw.pop("quota", None) or quota_reader())
    await eng.acquire_lock_once()
    await eng.boot()
    return eng


async def test_nothing_auto_starts_unless_auto_schedule_is_on(sf):
    now = Now(T0 + timedelta(minutes=1))
    eng = await clock_engine(sf, now)
    await eng.clock.poll()
    assert await sessions(sf) == []
    await set_rules(sf, auto_schedule=True)
    await eng.clock.poll()
    (row,) = await sessions(sf)
    assert row.state == "live" and row.kind == "daily" and row.length_min == 60 and row.day == "2026-11-27"
    assert row.planned_start == T0.replace(tzinfo=None) or row.planned_start.replace(tzinfo=timezone.utc) == T0
    assert eng.live and eng.session_id == row.id


async def test_before_noon_nothing_starts(sf):
    await set_rules(sf, auto_schedule=True)
    eng = await clock_engine(sf, Now(T0 - timedelta(minutes=1)))
    await eng.clock.poll()
    assert await sessions(sf) == []


async def test_rest_and_skipped_days_have_a_row_with_the_reason(sf):
    await set_rules(sf, auto_schedule=True)
    eng = await clock_engine(sf, Now(T0), quota=quota_reader(seven=90))
    await eng.clock.poll()
    (row,) = await sessions(sf)
    assert row.state == "rest" and "90%" in row.quota_reason and not eng.live
    await eng.clock.poll()                                      # one daily row per day
    assert len(await sessions(sf)) == 1


async def test_engine_down_at_noon_within_ten_minutes_starts_now_with_the_planned_end(sf):
    await set_rules(sf, auto_schedule=True)
    now = Now(T0 + timedelta(minutes=9))
    eng = await clock_engine(sf, now)
    await eng.clock.poll()
    (row,) = await sessions(sf)
    assert row.state == "live"
    assert row.planned_end.replace(tzinfo=timezone.utc) == T0 + timedelta(minutes=60)


async def test_engine_down_at_noon_after_ten_minutes_is_skipped(sf):
    await set_rules(sf, auto_schedule=True)
    eng = await clock_engine(sf, Now(T0 + timedelta(minutes=11)))
    await eng.clock.poll()
    (row,) = await sessions(sf)
    assert row.state == "skipped" and row.end_reason == "engine was down at noon" and not eng.live


async def test_test_rows_do_not_collide_with_the_daily_row(sf):
    await set_rules(sf, auto_schedule=True)
    now = Now(T0)
    eng = await clock_engine(sf, now)
    await eng.clock.poll()                                      # daily goes live
    for _ in range(3):
        await eng.store.add_session(day="2026-11-27", kind="test", state="closed", length_min=5)
    assert len(await sessions(sf)) == 4


async def test_an_admin_test_session_is_started_by_the_clock_and_obeys_the_gates(sf):
    eng = await clock_engine(sf, Now(T0), quota=quota_reader(seven=90))
    row = await eng.store.add_session(day="2026-11-27", kind="test", state="scheduled", length_min=20)
    await eng.clock.poll()
    assert (await eng.store.get_session(row.id)).state == "rest"
    row2 = await eng.store.add_session(day="2026-11-27", kind="test", state="scheduled", length_min=20,
                                       quota_at_start={"override": True})
    await eng.clock.poll()
    got = await eng.store.get_session(row2.id)
    assert got.state == "live" and got.length_min == 20 and eng.live


async def test_one_session_at_a_time(sf):
    eng = await clock_engine(sf, Now(T0))
    a = await eng.store.add_session(day="2026-11-27", kind="test", state="scheduled", length_min=20)
    b = await eng.store.add_session(day="2026-11-27", kind="test", state="scheduled", length_min=20)
    await eng.clock.poll()
    await eng.clock.poll()
    assert (await eng.store.get_session(a.id)).state == "live"
    assert (await eng.store.get_session(b.id)).state == "scheduled"


async def test_closing_sequence_is_journaled_once_in_order(sf):
    now = Now(T0)
    eng = await clock_engine(sf, now)
    row = await eng.store.add_session(day="2026-11-27", kind="test", state="scheduled", length_min=60)
    await eng.clock.poll()
    end = T0 + timedelta(minutes=60)
    assert await system_actions(sf) == ["session_open"]
    now.t = end - timedelta(minutes=10, seconds=1)
    await eng.clock.poll()
    assert await system_actions(sf) == ["session_open"]
    now.t = end - timedelta(minutes=10)
    await eng.clock.poll()
    await eng.clock.poll()
    assert await system_actions(sf) == ["session_open", "no_big_jobs"]
    assert (await eng.store.get_session(row.id)).state == "closing"
    now.t = end - timedelta(minutes=3)
    await eng.clock.poll()
    assert await system_actions(sf) == ["session_open", "no_big_jobs", "walk_to_square"]
    now.t = end
    await eng.clock.poll()
    await eng.clock.poll()
    assert await system_actions(sf) == ["session_open", "no_big_jobs", "walk_to_square", "freeze"]


async def test_the_whole_close_runs_through_the_engine(sf):
    now = Now(T0)
    eng = await clock_engine(sf, now)
    row = await eng.store.add_session(day="2026-11-27", kind="test", state="scheduled", length_min=30)
    await eng.clock.poll()
    now.t = T0 + timedelta(minutes=30)
    await eng.clock.poll()
    for _ in range(3):
        eng.tick_once()
    assert eng.pending_close
    await eng.close_session()
    got = await eng.store.get_session(row.id)
    assert got.state == "closed" and got.end_reason == "time" and got.tick_end == eng.world.tick


async def test_mid_session_quota_closes_early_and_says_why(sf):
    now = Now(T0)
    reader = quota_reader()
    eng = await clock_engine(sf, now, quota=reader)
    row = await eng.store.add_session(day="2026-11-27", kind="test", state="scheduled", length_min=60)
    await eng.clock.poll()
    now.t = T0 + timedelta(minutes=20)
    reader.views["claude"] = q(seven=90, now=now.t)
    await eng.clock.poll()
    got = await eng.store.get_session(row.id)
    assert got.end_reason == "quota:7d"
    assert got.planned_end.replace(tzinfo=timezone.utc) == now.t + timedelta(minutes=3)
    assert "quota_close" in await system_actions(sf)
    assert "walk_to_square" in await system_actions(sf)       # the T-3 sequence starts now
    now.t += timedelta(minutes=3)
    await eng.clock.poll()
    assert (await system_actions(sf))[-1] == "freeze"


async def test_five_hour_mid_gate_closes_at_95(sf):
    now = Now(T0)
    reader = quota_reader()
    eng = await clock_engine(sf, now, quota=reader)
    row = await eng.store.add_session(day="2026-11-27", kind="test", state="scheduled", length_min=60)
    await eng.clock.poll()
    now.t = T0 + timedelta(minutes=5)
    reader.views["claude"] = q(five=94, now=now.t)
    await eng.clock.poll()
    assert (await eng.store.get_session(row.id)).end_reason is None
    now.t += timedelta(minutes=2)
    reader.views["claude"] = q(five=95, now=now.t)
    await eng.clock.poll()
    assert (await eng.store.get_session(row.id)).end_reason == "quota:5h"


async def test_the_clock_never_refreshes_quota(sf, monkeypatch):
    """§4: no refresh, no probe, no model call; only the stored snapshot is read."""
    import agentplatform.quota_service as quota_service

    async def boom(*a, **kw):
        raise AssertionError("quota refresh/probe must never be called by the village clock")
    for name in ("refresh", "refresh_claude", "refresh_codex", "launch_probe_run"):
        monkeypatch.setattr(quota_service, name, boom, raising=False)
    async with sf() as s:
        s.add(QuotaSnapshot(id=1, five_hour_utilization=0.1, seven_day_utilization=0.2,
                            observed_at=T0 - timedelta(minutes=1), raw={}))
        await s.commit()
    await set_rules(sf, auto_schedule=True)
    now = Now(T0)
    eng = make_engine(sf, now=now, quota=snapshot_reader(sf))
    await eng.boot()
    await eng.clock.poll()
    now.t += timedelta(minutes=2)
    await eng.clock.poll()
    (row,) = await sessions(sf)
    assert row.state == "live" and row.length_min == 60 and row.quota_at_start["claude"]["seven_day"] == 20
