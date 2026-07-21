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
import uuid
from datetime import datetime, timezone

from croniter import croniter
from sqlalchemy import select

from agentplatform.db import Schedule, ScheduledJob, utcnow
from agentplatform.events import TOPIC_RUN_INBOUND

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
        # Manifest-declared schedules (1:1, e.g. the health-monitor system agent).
        for info in self.agents.list():
            if info.error is None and info.manifest and is_valid_cron(info.manifest.schedule):
                await self._tick_agent(info.name, info.manifest.schedule, now)
        # First-class Scheduled Jobs (1:many — one agent, many cron+prompt jobs).
        async with self.sf() as s:
            jobs = (await s.execute(select(ScheduledJob))).scalars().all()
        for job in jobs:
            if is_valid_cron(job.cron):
                await self._tick_job(job.id, now)

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
            run_id = uuid.uuid4().hex
            sched.last_fire = now
            sched.next_fire = next_fire(cron, now)  # from now → skip any missed fires
            await s.commit()
        # Event-sourced: emit a run.requested event; the ingest consumer
        # materializes the run.
        try:
            await self.producer.publish(TOPIC_RUN_INBOUND, run_id, {
                "run_id": run_id, "agent": name, "prompt": "Scheduled run.",
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
                job.next_fire = next_fire(job.cron, now)
                await s.commit()
                return
            if not job.enabled or now < as_utc(job.next_fire):
                return
            run_id, agent, prompt = uuid.uuid4().hex, job.agent, job.prompt
            job.last_fire = now
            job.next_fire = next_fire(job.cron, now)  # from now → skip missed fires
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
