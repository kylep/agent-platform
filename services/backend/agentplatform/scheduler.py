"""Cron scheduler for agents.

Agents declare 5-field crons in their `entrypoints` (an `agent_defs` column —
docs/design/15). The scheduler tracks each agent in the `schedules` table (runtime enable/disable + last/next
fire) and, when a schedule comes due, creates a `trigger="schedule"` run.
Missed fires are skipped, never backfilled: on each fire the next fire is
computed from *now*, so a scheduler outage never floods a burst of catch-up
runs.

Crons are evaluated in UTC unless a `timezone` is given. Everything stored
stays UTC — the zone only decides which UTC instant a wall-clock expression
means, which matters for anything pinned to human hours: "9:35 on a weekday"
is two different UTC times either side of a daylight-saving switch, and a
market-open job that quietly slides an hour every November is a bug.
"""
import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter
from sqlalchemy import select

from agentplatform.db import Schedule, ScheduledJob, utcnow
from agentplatform.events import TOPIC_RUN_INBOUND

if TYPE_CHECKING:   # `agentdefs` imports this module (lazily) for its validators
    from agentplatform.agentdefs import CronEntry

log = logging.getLogger("scheduler")


def as_utc(dt: datetime | None) -> datetime | None:
    """Attach UTC to a naive datetime (SQLite drops tzinfo on round-trip)."""
    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt


def is_valid_cron(expr: str) -> bool:
    return bool(expr) and croniter.is_valid(expr)


def is_valid_timezone(name: str | None) -> bool:
    """Empty means UTC. Otherwise it must be an IANA zone this host knows."""
    if not name:
        return True
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def next_fire(expr: str, after: datetime, tz: str | None = None) -> datetime:
    """Next firing instant, in UTC, for `expr` read in `tz` (default UTC).

    croniter matches wall-clock fields, so the conversion has to happen on the
    way in and out: hand it a local-time datetime, take a local-time answer,
    then convert back. An unknown zone falls back to UTC rather than wedging
    the whole scheduler loop over one bad row — the API validates on write, so
    a bad value here means the row predates validation or the tzdata moved.
    """
    zone = timezone.utc
    if tz:
        try:
            zone = ZoneInfo(tz)
        except (ZoneInfoNotFoundError, ValueError):
            log.warning("unknown timezone %r; falling back to UTC", tz)
    local = as_utc(after).astimezone(zone)
    return croniter(expr, local).get_next(datetime).astimezone(timezone.utc)


def prev_fire(expr: str, at: datetime, tz: str | None = None) -> datetime:
    """The most recent firing instant AT OR BEFORE `at`, in UTC. The mirror of
    `next_fire`, and the way we tell which of an agent's crons just came due.

    croniter's `get_prev` is strictly-before, so `at` itself would not count —
    and a tick landing exactly on the second a cron fires is precisely the case
    that must resolve to that cron. Hence the microsecond nudge."""
    zone = timezone.utc
    if tz:
        try:
            zone = ZoneInfo(tz)
        except (ZoneInfoNotFoundError, ValueError):
            log.warning("unknown timezone %r; falling back to UTC", tz)
    local = as_utc(at).astimezone(zone) + timedelta(microseconds=1)
    return croniter(expr, local).get_prev(datetime).astimezone(timezone.utc)


# What a cron fire asks for when its entry declares nothing.
GENERIC_PROMPT = "Scheduled run."


def _due_prompt(crons: list["CronEntry"], now: datetime, tz: str = "") -> str:
    """The prompt for the fire that just came due.

    Each cron entry carries its own ask (docs/design/15), so an agent with two
    rhythms says two different things — a morning brief is not an evening wrap.
    The entry that fired is the one whose most recent occurrence is latest;
    ties (the same expression twice) go to the first declared."""
    due = max(crons, key=lambda c: prev_fire(c.schedule, now, tz))
    return due.prompt.strip() or GENERIC_PROMPT


class Scheduler:
    def __init__(self, session_factory, agent_store, producer):
        self.sf = session_factory
        self.agents = agent_store
        self.producer = producer

    async def tick(self, now: datetime) -> None:
        await self.agents.reload()
        # Declared schedules: the crons on the agent's row, e.g. the
        # health-monitor system agent.
        for info in self.agents.list():
            if info.error is None and info.entrypoints.crons:
                await self._tick_agent(info.name, info.entrypoints.crons, now,
                                       info.entrypoints.timezone)
        # First-class Scheduled Jobs (1:many — one agent, many cron+prompt jobs).
        async with self.sf() as s:
            jobs = (await s.execute(select(ScheduledJob))).scalars().all()
        for job in jobs:
            if is_valid_cron(job.cron):
                await self._tick_job(job.id, now)

    async def _tick_agent(self, name: str, crons: list["CronEntry"], now: datetime,
                          tz: str = "") -> None:
        """One agent may declare several cron triggers (`entrypoints.crons`);
        the Schedule row tracks the EARLIEST upcoming fire across all of them,
        and the run carries the prompt of whichever one came due."""
        run_id = prompt = None
        soonest = min(next_fire(c.schedule, now, tz) for c in crons)
        async with self.sf() as s:
            sched = await s.get(Schedule, name)
            if sched is None:
                sched = Schedule(agent=name)
                s.add(sched)
            if sched.next_fire is None:
                # Newly seen (or armed by the API): set the next fire, don't
                # fire this tick.
                sched.next_fire = soonest
                await s.commit()
                return
            if not sched.enabled or now < as_utc(sched.next_fire):
                return
            run_id = uuid.uuid4().hex
            prompt = _due_prompt(crons, now, tz)
            sched.last_fire = now
            sched.next_fire = soonest  # from now → skip any missed fires
            await s.commit()
        # Event-sourced: emit a run.requested event; the ingest consumer
        # materializes the run.
        try:
            await self.producer.publish(TOPIC_RUN_INBOUND, run_id, {
                "run_id": run_id, "agent": name, "prompt": prompt,
                "trigger": "schedule", "requested_by": "scheduler",
            }, type="run.requested")
        except Exception:
            log.warning("publish failed for scheduled run %s", run_id)

    async def _tick_job(self, job_id: str, now: datetime) -> None:
        """Fire one Scheduled Job when due, using its own agent + prompt."""
        run_id = agent = prompt = None
        async with self.sf() as s:
            job = await s.get(ScheduledJob, job_id)
            if job is None:
                return
            if job.next_fire is None:
                # Newly created (or armed): set the next fire, don't fire now.
                job.next_fire = next_fire(job.cron, now, job.timezone)
                await s.commit()
                return
            if not job.enabled or now < as_utc(job.next_fire):
                return
            run_id, agent, prompt = uuid.uuid4().hex, job.agent, job.prompt
            job.last_fire = now
            # from now → skip missed fires
            job.next_fire = next_fire(job.cron, now, job.timezone)
            await s.commit()
        try:
            await self.producer.publish(TOPIC_RUN_INBOUND, run_id, {
                "run_id": run_id, "agent": agent, "prompt": prompt,
                "trigger": "schedule", "requested_by": f"job:{job_id}",
            }, type="run.requested")
        except Exception:
            log.warning("publish failed for scheduled job run %s", run_id)

    async def run_forever(self, interval_seconds: int = 30) -> None:
        while True:
            try:
                await self.tick(utcnow())
            except Exception:
                log.exception("scheduler tick failed")
            await asyncio.sleep(interval_seconds)
