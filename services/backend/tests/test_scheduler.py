from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from agentplatform.agents import AgentInfo, Manifest
from agentplatform.db import Run, Schedule
from agentplatform.events import FakeProducer, TOPIC_RUN_INBOUND
from agentplatform.scheduler import (Scheduler, as_utc, is_valid_cron,
                                     is_valid_timezone, next_fire)


def test_cron_helpers():
    assert is_valid_cron("*/5 * * * *") and not is_valid_cron("nonsense") and not is_valid_cron("")
    base = datetime(2026, 7, 20, 10, 2, tzinfo=timezone.utc)
    assert next_fire("*/5 * * * *", base) == datetime(2026, 7, 20, 10, 5, tzinfo=timezone.utc)


def test_timezone_validation():
    assert is_valid_timezone("") and is_valid_timezone(None)
    assert is_valid_timezone("America/Toronto") and is_valid_timezone("UTC")
    assert not is_valid_timezone("Mars/Olympus") and not is_valid_timezone("EST5")


def test_next_fire_holds_wall_clock_across_daylight_saving():
    """9:35 on a weekday in Toronto is a different UTC instant in summer and
    winter. Pinning the zone is the whole point: without it a market-open job
    silently slides an hour every November."""
    summer = datetime(2026, 7, 20, 0, 0, tzinfo=timezone.utc)
    winter = datetime(2026, 12, 14, 0, 0, tzinfo=timezone.utc)
    tz = "America/Toronto"
    assert next_fire("35 9 * * 1-5", summer, tz) == \
        datetime(2026, 7, 20, 13, 35, tzinfo=timezone.utc)      # EDT, UTC-4
    assert next_fire("35 9 * * 1-5", winter, tz) == \
        datetime(2026, 12, 14, 14, 35, tzinfo=timezone.utc)     # EST, UTC-5


def test_next_fire_defaults_to_utc():
    base = datetime(2026, 7, 20, 0, 0, tzinfo=timezone.utc)
    expected = datetime(2026, 7, 20, 9, 35, tzinfo=timezone.utc)
    assert next_fire("35 9 * * 1-5", base) == expected
    assert next_fire("35 9 * * 1-5", base, "") == expected
    # An unknown zone must not wedge the loop over one bad row.
    assert next_fire("35 9 * * 1-5", base, "Mars/Olympus") == expected


def test_next_fire_returns_utc_for_a_naive_input():
    naive = datetime(2026, 7, 20, 0, 0)
    got = next_fire("35 9 * * 1-5", naive, "America/Toronto")
    assert got.tzinfo == timezone.utc
    assert got == datetime(2026, 7, 20, 13, 35, tzinfo=timezone.utc)


class FakeStore:
    def __init__(self, infos): self._infos = infos
    async def reload(self): pass
    def list(self): return self._infos


def _agent(name, cron, error=None, prompt=""):
    return AgentInfo(name=name, manifest=Manifest(), agent_md="", error=error,
                     entrypoints={"crons": [{"schedule": cron, "prompt": prompt}]})


@pytest.fixture
def now():
    return datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


async def test_first_sight_arms_without_firing(sf, now):
    sch = Scheduler(sf, FakeStore([_agent("cronbot", "*/10 * * * *")]), FakeProducer())
    await sch.tick(now)
    async with sf() as s:
        assert (await s.execute(select(Run))).scalars().all() == []   # no run yet
        row = await s.get(Schedule, "cronbot")
    assert row is not None and as_utc(row.next_fire) == next_fire("*/10 * * * *", now)


async def test_fires_when_due_and_advances(sf, now):
    producer = FakeProducer()
    sch = Scheduler(sf, FakeStore([_agent("cronbot", "*/10 * * * *")]), producer)
    await sch.tick(now)                                    # arm
    later = next_fire("*/10 * * * *", now) + timedelta(seconds=1)
    await sch.tick(later)                                  # due
    async with sf() as s:
        row = await s.get(Schedule, "cronbot")
    assert as_utc(row.last_fire) == later and as_utc(row.next_fire) == next_fire("*/10 * * * *", later)
    # Event-sourced: emits one run.inbound (the ingest consumer materializes it).
    inbound = [v for t, _, v in producer.published if t == TOPIC_RUN_INBOUND]
    assert len(inbound) == 1 and inbound[0]["agent"] == "cronbot" and inbound[0]["trigger"] == "schedule"


async def test_disabled_does_not_fire(sf, now):
    sch = Scheduler(sf, FakeStore([_agent("cronbot", "*/10 * * * *")]), FakeProducer())
    await sch.tick(now)
    async with sf() as s:
        (await s.get(Schedule, "cronbot")).enabled = False
        await s.commit()
    await sch.tick(now + timedelta(hours=1))
    async with sf() as s:
        assert (await s.execute(select(Run))).scalars().all() == []


async def test_missed_fires_are_skipped_not_backfilled(sf, now):
    producer = FakeProducer()
    sch = Scheduler(sf, FakeStore([_agent("cronbot", "*/10 * * * *")]), producer)
    await sch.tick(now)                                    # arm
    way_later = now + timedelta(hours=3)                   # scheduler "was down" 3h
    await sch.tick(way_later)
    async with sf() as s:
        row = await s.get(Schedule, "cronbot")
    inbound = [v for t, _, v in producer.published if t == TOPIC_RUN_INBOUND]
    assert len(inbound) == 1                               # exactly one, not ~18
    assert as_utc(row.next_fire) == next_fire("*/10 * * * *", way_later)   # advanced past the gap


async def test_cron_prompt_is_used_when_declared(sf, now):
    """A cron entry carries its own ask (docs/design/15); the fired run uses it
    instead of the generic prompt."""
    producer = FakeProducer()
    sch = Scheduler(sf, FakeStore([_agent("cronbot", "*/10 * * * *",
                                          prompt="  Summarize yesterday's runs.  ")]),
                    producer)
    await sch.tick(now)                                    # arm
    await sch.tick(next_fire("*/10 * * * *", now) + timedelta(seconds=1))
    inbound = [v for t, _, v in producer.published if t == TOPIC_RUN_INBOUND]
    assert inbound[0]["prompt"] == "Summarize yesterday's runs."


async def test_cron_without_a_prompt_falls_back_to_the_generic_one(sf, now):
    producer = FakeProducer()
    sch = Scheduler(sf, FakeStore([_agent("cronbot", "*/10 * * * *")]), producer)
    await sch.tick(now)
    await sch.tick(next_fire("*/10 * * * *", now) + timedelta(seconds=1))
    inbound = [v for t, _, v in producer.published if t == TOPIC_RUN_INBOUND]
    assert inbound[0]["prompt"] == "Scheduled run."


async def test_the_prompt_belongs_to_the_cron_that_actually_fired(sf):
    """Two rhythms with different asks is the whole reason a cron entry carries
    a prompt: each fire must say what ITS trigger asked for, not whichever was
    declared first."""
    producer = FakeProducer()
    two = AgentInfo(name="two", manifest=Manifest(), agent_md="", entrypoints={"crons": [
        {"schedule": "0 9 * * *", "prompt": "Morning brief."},
        {"schedule": "0 17 * * *", "prompt": "Evening wrap."}]})
    sch = Scheduler(sf, FakeStore([two]), producer)
    day = datetime(2026, 7, 20, tzinfo=timezone.utc)
    await sch.tick(day + timedelta(hours=8))                       # arm → 09:00
    await sch.tick(day + timedelta(hours=9, seconds=1))            # the morning cron
    await sch.tick(day + timedelta(hours=17, seconds=1))           # the evening cron
    prompts = [v["prompt"] for t, _, v in producer.published if t == TOPIC_RUN_INBOUND]
    assert prompts == ["Morning brief.", "Evening wrap."]


async def test_cronless_and_quarantined_agents_not_scheduled(sf, now):
    """Two non-starters: an agent that declares no cron, and one whose
    definition is quarantined. (An invalid cron expression can no longer reach
    the scheduler at all — the store rejects it into quarantine, which is the
    second case here.)"""
    cronless = AgentInfo(name="bad", manifest=Manifest(), agent_md="")
    sch = Scheduler(sf, FakeStore([cronless, _agent("q", "* * * * *", error="boom")]), FakeProducer())
    await sch.tick(now)
    async with sf() as s:
        assert (await s.execute(select(Schedule))).scalars().all() == []
