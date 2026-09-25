"""The singleton snapshot and its change log (docs/design/22): one row however
many observations arrive, and an envelope only when a number actually moved."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from agentplatform.db import (
    QuotaSnapshot,
    init_db,
    make_engine,
    make_session_factory,
    utcnow,
)
from agentplatform.events import TOPIC_QUOTA_EVENTS
from agentplatform.quota import parse_observation
from agentplatform.quota_store import (
    SNAPSHOT_ID,
    latest,
    observe,
    quota_feed,
    serialize,
)
from sqlalchemy import func, select

NOW = datetime(2026, 9, 14, 15, 2, 11, tzinfo=timezone.utc)
RESET_5H = datetime(2026, 9, 14, 19, 0, tzinfo=timezone.utc)


@pytest.fixture
async def concurrent_sf(tmp_path):
    # The global in-memory SQLite fixture gives every AsyncSession the SAME
    # connection. Its savepoints interfere with each other, which is not a
    # concurrency test of the store. Use independent file-backed connections.
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'quota-race.db'}")
    await init_db(engine)
    yield make_session_factory(engine)
    await engine.dispose()


def observation(util="0.22", *, at=NOW, source="proxy"):
    return parse_observation({
        "anthropic-ratelimit-unified-5h-utilization": util,
        "anthropic-ratelimit-unified-5h-reset": "2026-09-14T19:00:00Z",
        "anthropic-ratelimit-unified-status": "allowed",
    }, at, source)


async def row_count(sf) -> int:
    async with sf() as s:
        return (await s.execute(select(func.count()).select_from(QuotaSnapshot))).scalar_one()


async def test_latest_on_an_empty_table_is_none(sf):
    async with sf() as s:
        assert await latest(s) is None


async def test_first_observe_creates_the_singleton_and_publishes(sf, producer):
    async with sf() as s:
        row = await observe(s, producer, observation())
    assert row.id == SNAPSHOT_ID
    assert row.five_hour_utilization == 0.22
    assert row.status == "allowed"
    assert row.raw["anthropic-ratelimit-unified-status"] == "allowed"
    assert len(producer.published) == 1


async def test_a_repeat_bumps_observed_at_but_publishes_nothing(sf, producer):
    async with sf() as s:
        await observe(s, producer, observation())
    later = NOW + timedelta(minutes=5)
    async with sf() as s:
        row = await observe(s, producer, observation(at=later, source="refresh"))
    assert serialize(row, later)["observed_at"] == later.isoformat()
    assert row.source == "refresh"
    assert len(producer.published) == 1     # still only the first observation
    assert await row_count(sf) == 1


async def test_a_changed_value_publishes_one_envelope(sf, producer):
    async with sf() as s:
        await observe(s, producer, observation("0.22"))
    async with sf() as s:
        await observe(s, producer, observation("0.31"))
    assert len(producer.published) == 2
    topic, key, data = producer.published[-1]
    assert (topic, key) == (TOPIC_QUOTA_EVENTS, "quota")
    assert producer.envelopes[-1]["type"] == "quota.event"
    assert data["five_hour"]["utilization"] == 0.31
    assert data["five_hour"]["resets_at"] == RESET_5H.isoformat()
    assert data["status"] == "allowed"
    assert data["source"] == "proxy"


async def test_a_changed_reset_alone_publishes(sf, producer):
    async with sf() as s:
        await observe(s, producer, observation())
    moved = parse_observation({
        "anthropic-ratelimit-unified-5h-utilization": "0.22",
        "anthropic-ratelimit-unified-5h-reset": "2026-09-15T00:00:00Z",
    }, NOW, "proxy")
    async with sf() as s:
        await observe(s, producer, moved)
    assert len(producer.published) == 2


async def test_concurrent_observes_leave_one_row(concurrent_sf, producer):
    sf = concurrent_sf
    async def one(util):
        async with sf() as s:
            await observe(s, producer, observation(util))

    await asyncio.gather(*(one(u) for u in ("0.22", "0.23", "0.24", "0.25")))
    assert await row_count(sf) == 1


async def test_a_publish_failure_does_not_lose_the_write(sf, producer):
    class Broken:
        async def publish(self, *a, **kw):
            raise RuntimeError("kafka is down")

    async with sf() as s:
        await observe(s, Broken(), observation())
    async with sf() as s:
        assert (await latest(s)).five_hour_utilization == 0.22


async def test_sqlite_round_trip_keeps_the_timezone(sf, producer):
    async with sf() as s:
        await observe(s, producer, observation())
    async with sf() as s:
        row = await latest(s)
    # sqlite hands datetimes back naive; what the platform serves must not be.
    out = serialize(row, NOW)
    assert out["five_hour"]["resets_at"] == RESET_5H.isoformat()
    assert out["observed_at"] == NOW.isoformat()


async def test_serialize_of_no_snapshot_is_the_same_shape_with_nulls():
    out = serialize(None, NOW)
    assert out == {"five_hour": {"utilization": None, "resets_at": None},
                   "seven_day": {"utilization": None, "resets_at": None},
                   "status": None, "observed_at": None, "source": None,
                   "stale": True, "age_seconds": None}


async def test_serialize_reports_staleness_and_age(sf, producer):
    async with sf() as s:
        row = await observe(s, producer, observation())
    fresh = serialize(row, NOW + timedelta(seconds=30))
    assert (fresh["stale"], fresh["age_seconds"]) == (False, 30)
    assert serialize(row, RESET_5H + timedelta(seconds=1))["stale"] is True


def test_quota_feed_is_one_stream_over_the_quota_topic():
    feed = quota_feed()
    assert feed.topic == TOPIC_QUOTA_EVENTS
    assert feed.event == "quota"
    key, data = feed.frame_of(serialize(None, utcnow()))
    assert key == "quota"
    assert data["stale"] is True
    assert feed.frame_of({"nothing": "useful"}) is None


async def test_a_burst_of_first_observations_leaves_one_row(concurrent_sf, producer):
    """Every caller returns and exactly one row exists after the insert race.

    File-backed SQLite supplies independent connections but not PostgreSQL's
    FOR UPDATE row lock. Ordering is covered by the sequential newer/older
    tests below; the final writer of this SQLite burst is not deterministic.
    """
    sf = concurrent_sf
    stamps = [NOW + timedelta(seconds=i) for i in range(10)]

    async def one(i):
        async with sf() as s:
            return await observe(s, producer, observation(f"0.{20 + i}", at=stamps[i]))

    rows = await asyncio.gather(*(one(i) for i in range(len(stamps))))
    assert len(rows) == len(stamps)
    assert await row_count(sf) == 1
    async with sf() as s:
        row = await latest(s)
    assert row.five_hour_utilization in {float(f"0.{20 + i}") for i in range(10)}
    assert serialize(row, stamps[-1])["observed_at"] == row.observed_at.replace(
        tzinfo=timezone.utc).isoformat()


async def test_an_older_observation_never_overwrites_a_newer_one(sf, producer):
    # Anchored to the real clock rather than the module's fixed NOW: ordering
    # is now judged against `utcnow()` so a future-dated row cannot wedge the
    # snapshot, which means a fixture date sitting in the future would trip
    # THAT guard instead of exercising this one.
    newer = utcnow() - timedelta(minutes=1)
    older = newer - timedelta(minutes=5)
    async with sf() as s:
        await observe(s, producer, observation("0.31", at=newer))
    async with sf() as s:
        row = await observe(s, producer, observation("0.22", at=older))
    assert row.five_hour_utilization == 0.31
    assert serialize(row, newer)["observed_at"] == newer.isoformat()
    assert len(producer.published) == 1     # no false "usage decreased" event


async def test_an_observation_at_the_same_instant_still_applies(sf, producer):
    async with sf() as s:
        await observe(s, producer, observation("0.22"))
    async with sf() as s:
        row = await observe(s, producer, observation("0.31"))
    assert row.five_hour_utilization == 0.31
    assert len(producer.published) == 2


async def test_observe_leaves_the_caller_s_other_work_alone(sf, producer):
    """A losing insert must roll back its own statement and nothing else: the
    caller's pending work is in the same session."""
    from agentplatform.db import Memory

    async with sf() as s:
        s.add(Memory(agent="pai", key="k", content="unrelated"))
        await observe(s, producer, observation())
    async with sf() as s:
        assert (await s.execute(select(func.count()).select_from(Memory))).scalar_one() == 1


async def test_a_future_timestamp_cannot_freeze_the_snapshot(sf, producer):
    """The self-healing half of the future-timestamp guard.

    The route clamps on the way in, but a row written before that check
    existed — or a clock that jumped — would otherwise outrank every
    observation the platform ever made again: nothing real can be later than
    a year from now, so `_superseded` would refuse every write for good."""
    far = utcnow() + timedelta(days=365)
    async with sf() as s:
        await observe(s, producer, observation("0.10", at=far))
    async with sf() as s:
        row = await observe(s, producer, observation("0.50", at=utcnow()))
    assert row.five_hour_utilization == 0.50


async def test_a_slightly_future_timestamp_still_wins(sf, producer):
    """Inside the skew tolerance the ordering is still honoured — this guard
    heals a wedged row, it does not switch the ordering off."""
    async with sf() as s:
        await observe(s, producer, observation("0.10", at=utcnow() + timedelta(seconds=5)))
    async with sf() as s:
        row = await observe(s, producer, observation("0.50", at=utcnow() - timedelta(minutes=5)))
    assert row.five_hour_utilization == 0.10
