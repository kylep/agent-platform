"""Cron scheduler for agents.

Agents declare a 5-field cron `schedule` in their manifest. The scheduler
tracks each in the `schedules` table (runtime enable/disable + last/next
fire) and, when a schedule comes due, creates a `trigger="schedule"` run.
Missed fires are skipped, never backfilled: on each fire the next fire is
computed from *now*, so a scheduler outage never floods a burst of catch-up
runs.
"""
import asyncio
import logging
from datetime import datetime, timezone

from croniter import croniter

from agentplatform.db import Run, Schedule, utcnow
from agentplatform.events import TOPIC_RUN_REQUESTS

log = logging.getLogger("scheduler")


def as_utc(dt: datetime | None) -> datetime | None:
    """Attach UTC to a naive datetime (SQLite drops tzinfo on round-trip)."""
    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt


def is_valid_cron(expr: str) -> bool:
    return bool(expr) and croniter.is_valid(expr)


def next_fire(expr: str, after: datetime) -> datetime:
    return as_utc(croniter(expr, after).get_next(datetime))


class Scheduler:
    def __init__(self, session_factory, agent_store, producer):
        self.sf = session_factory
        self.agents = agent_store
        self.producer = producer

    async def tick(self, now: datetime) -> None:
        self.agents.reload()
        for info in self.agents.list():
            if info.error is None and info.manifest and is_valid_cron(info.manifest.schedule):
                await self._tick_agent(info.name, info.manifest.schedule, now)

    async def _tick_agent(self, name: str, cron: str, now: datetime) -> None:
        run_id = None
        async with self.sf() as s:
            sched = await s.get(Schedule, name)
            if sched is None:
                sched = Schedule(agent=name)
                s.add(sched)
            if sched.next_fire is None:
                # Newly seen (or armed by the API): set the next fire, don't
                # fire this tick.
                sched.next_fire = next_fire(cron, now)
                await s.commit()
                return
            if not sched.enabled or now < as_utc(sched.next_fire):
                return
            run = Run(agent=name, trigger="schedule", requested_by="scheduler",
                      prompt="Scheduled run.")
            s.add(run)
            sched.last_fire = now
            sched.next_fire = next_fire(cron, now)  # from now → skip any missed fires
            await s.commit()
            run_id = run.id
        try:
            await self.producer.publish(TOPIC_RUN_REQUESTS, run_id,
                                        {"type": "run", "run_id": run_id})
        except Exception:
            log.warning("publish failed for scheduled run %s; sweep will drain it", run_id)

    async def run_forever(self, interval_seconds: int = 30) -> None:
        while True:
            try:
                await self.tick(utcnow())
            except Exception:
                log.exception("scheduler tick failed")
            await asyncio.sleep(interval_seconds)
