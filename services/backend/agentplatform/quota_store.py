"""The usage snapshot: one row, and the change log that is not a table
(docs/design/22).

`observe` is the only writer. Every observation rewrites the singleton — the
platform's answer to "how much is left" must not depend on which pod saw the
last response — but an envelope reaches `quota.events` only when a number
actually moved. That split is what makes the topic a burn-rate log instead of a
record of every request the platform happened to make, and it is why a repeat
still bumps `observed_at`: the value is unchanged, the knowledge of it is not.

Serialisation lives here too, in one function, because the REST shape, the SSE
frame and the Kafka payload are the same object seen three ways; the moment
they are three functions they start to disagree about what `stale` means."""
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from agentplatform.db import QuotaSnapshot, utcnow
from agentplatform.events import TOPIC_QUOTA_EVENTS
from agentplatform.quota import FUTURE_SKEW, Observation, changed, is_stale
from agentplatform.relay_feed import TopicFeed

log = logging.getLogger("quota_store")

# The row is a singleton by construction: a fixed primary key, so a second
# writer racing the first insert collides with the constraint rather than
# quietly creating a second "current" answer.
SNAPSHOT_ID = 1
# The envelope key and the feed's only stream key: there is one snapshot for
# the whole platform, so everything watching it watches the same key.
QUOTA_KEY = STREAM = "quota"


def _aware(ts: datetime) -> datetime:
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts


async def latest(session) -> QuotaSnapshot | None:
    return await session.get(QuotaSnapshot, SNAPSHOT_ID)


def _for_update(session, stmt):
    """`FOR UPDATE` where it means something, as `ticket_store.py` does: two
    observers racing is a postgres problem, and sqlite's compiler drops the
    clause anyway."""
    return (stmt.with_for_update()
            if session.get_bind().dialect.name == "postgresql" else stmt)


def _apply(row: QuotaSnapshot, obs: Observation) -> None:
    row.five_hour_utilization = obs.five_hour_utilization
    row.five_hour_resets_at = obs.five_hour_resets_at
    row.seven_day_utilization = obs.seven_day_utilization
    row.seven_day_resets_at = obs.seven_day_resets_at
    row.status = obs.status
    row.raw = obs.raw
    row.observed_at = obs.observed_at
    row.source = obs.source
    row.updated_at = utcnow()


def _superseded(row: QuotaSnapshot, obs: Observation, now: datetime) -> bool:
    """Whether the row already knows something newer. Two proxy reports can
    cross on the wire, and the older one landing last would roll the numbers
    backwards and publish a drop in usage that never happened. Equal timestamps
    apply: the same instant is not evidence of being behind.

    A stored time from the FUTURE is the exception, and it is why `now` is a
    parameter. Nothing real can be later than it, so it would win this
    comparison against every observation the platform ever makes again — one
    bad timestamp, and the snapshot is frozen at whatever it held, for good.
    The route clamps on the way in (`quota.parse_observed_at`); this is the
    other half, so a row that got past an earlier version of that check, or a
    clock that jumped, heals on the next observation instead of needing a
    hand on the database."""
    if row.observed_at is None:
        return False
    stored = _aware(row.observed_at)
    if stored > _aware(now) + FUTURE_SKEW:
        return False
    return _aware(obs.observed_at) < stored


async def _record(session, obs: Observation) -> tuple[QuotaSnapshot, bool]:
    """Write the singleton, returning it and whether a number moved.

    The loop is the whole point: before the row exists there is nothing for
    `FOR UPDATE` to lock, so several first observations can reach the insert at
    once and exactly one can win. The loser's insert is scoped to a SAVEPOINT —
    the pattern `db.py` uses for the same race — so only that statement is
    rolled back, the caller's transaction survives intact, and the next trip
    round the loop finds the winner's row and updates it. Nothing here raises
    on a plain race, and nothing here rolls back work the caller put in the
    session before calling."""
    now = utcnow()
    while True:
        stmt = _for_update(session, select(QuotaSnapshot)
                           .where(QuotaSnapshot.id == SNAPSHOT_ID))
        row = (await session.execute(stmt)).scalar_one_or_none()
        if row is not None:
            if _superseded(row, obs, now):
                return row, False
            moved = changed(row, obs)
            _apply(row, obs)
            await session.commit()
            return row, moved
        fresh = QuotaSnapshot(id=SNAPSHOT_ID)
        _apply(fresh, obs)
        try:
            async with session.begin_nested():
                session.add(fresh)
        except IntegrityError:
            log.debug("lost the quota singleton insert; updating the winner's row")
            continue
        await session.commit()
        return fresh, True


async def observe(session, producer, obs: Observation) -> QuotaSnapshot:
    """Record an observation and, if it moved, announce it."""
    row, moved = await _record(session, obs)
    if moved and producer is not None:
        try:
            await producer.publish(TOPIC_QUOTA_EVENTS, QUOTA_KEY,
                                   serialize(row, utcnow()), type="quota.event")
        except Exception:
            # The row is the current answer and it is already committed; a
            # broker that is down costs the history, never the snapshot.
            log.warning("quota.events publish failed", exc_info=True)
    return row


def serialize(snapshot, now) -> dict:
    """The one shape: the REST body, the SSE frame and the Kafka payload.

    A missing snapshot is the same shape with nulls rather than a 404 — the
    sidebar draws empty bars before the first observation, and `stale` is what
    tells it so. Datetimes are re-attached to UTC on the way out because sqlite
    hands them back naive and an ISO string without an offset is a timestamp
    the browser reads in its own zone."""
    if snapshot is None:
        return {"five_hour": {"utilization": None, "resets_at": None},
                "seven_day": {"utilization": None, "resets_at": None},
                "status": None, "observed_at": None, "source": None,
                "stale": True, "age_seconds": None}
    observed = _aware(snapshot.observed_at) if snapshot.observed_at else None
    age = int(max((_aware(now) - observed).total_seconds(), 0)) if observed else None
    return {
        "five_hour": {"utilization": snapshot.five_hour_utilization,
                      "resets_at": _iso(snapshot.five_hour_resets_at)},
        "seven_day": {"utilization": snapshot.seven_day_utilization,
                      "resets_at": _iso(snapshot.seven_day_resets_at)},
        "status": snapshot.status,
        "observed_at": _iso(snapshot.observed_at),
        "source": snapshot.source,
        "stale": is_stale(snapshot, now),
        "age_seconds": age,
    }


def _iso(ts):
    return _aware(ts).isoformat() if ts else None


def quota_feed(session_factory=None) -> TopicFeed:
    """The API's live usage fan-out. One stream for the whole platform: there is
    one snapshot, and every sidebar watching wants the same frame."""
    return TopicFeed(TOPIC_QUOTA_EVENTS, event="quota",
                     frame_of=lambda data: ((STREAM, data) if data.get("five_hour") else None),
                     session_factory=session_factory)
