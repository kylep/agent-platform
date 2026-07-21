from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from agentplatform.agents import AgentInfo, Manifest
from agentplatform.db import Run, Schedule
from agentplatform.events import FakeProducer, TOPIC_RUN_INBOUND
from agentplatform.scheduler import Scheduler, as_utc, is_valid_cron, next_fire


def test_cron_helpers():
    assert is_valid_cron("*/5 * * * *") and not is_valid_cron("nonsense") and not is_valid_cron("")
    base = datetime(2026, 7, 20, 10, 2, tzinfo=timezone.utc)
    assert next_fire("*/5 * * * *", base) == datetime(2026, 7, 20, 10, 5, tzinfo=timezone.utc)


class FakeStore:
    def __init__(self, infos): self._infos = infos
    def reload(self): pass
    def list(self): return self._infos


def _agent(name, cron, error=None):
    return AgentInfo(name=name, manifest=Manifest(schedule=cron), agent_md="", error=error)


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


async def test_invalid_cron_agent_not_scheduled(sf, now):
    sch = Scheduler(sf, FakeStore([_agent("bad", "not-a-cron"), _agent("q", "* * * * *", error="boom")]), FakeProducer())
    await sch.tick(now)
    async with sf() as s:
        assert (await s.execute(select(Schedule))).scalars().all() == []
