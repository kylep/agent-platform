"""Deterministic platform health checks, evaluated in the dispatcher.

This replaced the hourly LLM health-monitor pass, which found nothing almost
every time. The incident keys are the ones that worker used, so tickets it
opened keep deduplicating; `report_incident` still owns open/update/recover/
reopen and the Pai summons. A check that cannot learn its metric says nothing:
unknown is never healthy, so it neither opens nor resolves an incident.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select

from agentplatform.api.backups import backup_status
from agentplatform.api.health import kafka_status
from agentplatform.api.metrics import agent_rows, recent_runs, tool_rows
from agentplatform.db import AgentDef, RunState, Ticket, utcnow
from agentplatform.health_incidents import HealthIncident, report_incident
from agentplatform.ticket_store import TicketBudgetError, TicketRuleError
from agentplatform.tickets import CLOSED_STATES

log = logging.getLogger("health_checker")

FAILURE_STREAK = 3
KAFKA_LAG = 50
KAFKA_PROBE_TIMEOUT = 30
MAX_TRACKED_INCIDENTS = 500


@dataclass
class Observation:
    """What one check saw: the incidents that are live now, and the key
    prefixes whose metric it could actually read this pass (so only those may
    be resolved when absent from `active`)."""
    active: dict[str, tuple[str, str]] = field(default_factory=dict)
    known: tuple[str, ...] = ()


async def check_dlq_and_streaks(ctx) -> Observation:
    runs = await recent_runs(ctx.sf)
    async with ctx.sf() as s:
        enabled = dict((await s.execute(select(AgentDef.name, AgentDef.enabled))).all())
    obs = Observation(known=("dlq", "failure-streak:"))
    dlq = sum(1 for r in runs if r.state == RunState.DLQ)
    if dlq > 0:
        obs.active["dlq"] = ("Dead-letter queue has runs", (
            f"{dlq} run(s) are in the DLQ (state dlq) within the recent metrics window. "
            "Intervention: open /dlq, read each run's error, then retry or discard it."))
    for row in agent_rows(runs, enabled):
        if row["enabled"] and row["failure_streak"] >= FAILURE_STREAK:
            agent = row["agent"]
            obs.active[f"failure-streak:{agent}"] = (f"Agent {agent} is failing repeatedly", (
                f"{agent} has failed its last {row['failure_streak']} terminal runs "
                f"(threshold {FAILURE_STREAK}); newest failure at {row['last_failed_at']}. "
                f"Intervention: inspect /runs?agent={agent}, find the shared cause, fix or disable it."))
    return obs


async def check_kafka(ctx) -> Observation:
    try:
        status = await asyncio.wait_for(kafka_status(ctx.settings, ctx.sf), KAFKA_PROBE_TIMEOUT)
    except TimeoutError:
        status = {"reachable": False, "lag": None,
                  "error": f"no answer within {KAFKA_PROBE_TIMEOUT}s"}
    obs = Observation(known=("kafka-down",))
    if not status["reachable"]:
        obs.active["kafka-down"] = ("Kafka is unreachable", (
            f"The broker did not answer ({status['error']}). Intervention: check the Kafka "
            "pod and service; runs and events cannot flow until it is back."))
        return obs
    lag = status["lag"]
    if lag is not None:
        obs.known += ("kafka-lag",)
        if lag > KAFKA_LAG:
            obs.active["kafka-lag"] = ("Dispatcher is lagging on run.requests", (
                f"The dispatcher group is {lag} messages behind (threshold {KAFKA_LAG}). "
                "Intervention: check the dispatcher pod and its logs for a stuck consumer."))
    return obs


async def check_tool_denials(ctx) -> Observation:
    obs = Observation(known=("tool-denials:",))
    for row in await tool_rows(ctx.sf, 24):
        if row["denials"] <= 0:
            continue
        samples = "; ".join(
            f"{d['ts']} agent={d['agent']} run={d['run_id']} action={d['action']} "
            f"decision={d['decision']}" for d in row["recent_denials"])
        obs.active[f"tool-denials:{row['tool']}"] = (f"Tool {row['tool']} calls were denied", (
            f"{row['denials']} denied call(s) to {row['tool']} in the last 24 hours. "
            f"Newest denials: {samples}. Intervention: decide whether each is correctly "
            "rejected unauthenticated traffic or a declared agent's broken workflow; do not "
            "widen grants just to silence the metric."))
    return obs


async def check_backups(ctx) -> Observation:
    status = backup_status(Path(ctx.settings.backup_output_dir), utcnow().timestamp())
    if status is None:
        return Observation()
    obs = Observation(known=("backup-stale",))
    if status["stale"] or status["shrunk"]:
        obs.active["backup-stale"] = ("Backups are stale or shrank", (
            f"Newest bundle {status['newest']}, {status['newest_age_hours']} h old "
            f"(stale after {status['stale_after_hours']} h); size {status['newest_bytes']} bytes "
            f"against previous {status['previous_bytes']}; shrunk={status['shrunk']}; "
            f"failed leftovers: {status['failed_leftovers'] or 'none'}. Intervention: check the "
            "ap-cloud-backup and ap-pg-backup Jobs and run a backup now."))
    return obs


CHECKS = (check_dlq_and_streaks, check_kafka, check_tool_denials, check_backups)


class HealthChecker:
    def __init__(self, session_factory, producer, settings):
        self.sf, self.producer, self.settings = session_factory, producer, settings

    async def _report(self, key: str, title: str = "", body: str = "", resolved: bool = False):
        try:
            async with self.sf() as s:
                await report_incident(s, self.producer, incident_key=key, title=title,
                                      body=body, resolved=resolved)
        except TicketRuleError as exc:
            # A tool-denial recovery without proof is a rejected recovery: the
            # key stays active until Pai or a human closes its ticket.
            log.info("incident %s not updated: %s", key, exc)
        except TicketBudgetError as exc:
            log.warning("incident %s over budget: %s", key, exc)
        except Exception:
            log.exception("reporting incident %s failed", key)

    async def _tracked(self) -> dict[str, bool]:
        """Active incident key -> whether its ticket is already closed."""
        async with self.sf() as s:
            rows = (await s.execute(select(HealthIncident.incident_key, Ticket.state)
                .join(Ticket, Ticket.id == HealthIncident.ticket_id)
                .where(HealthIncident.status == "active")
                .limit(MAX_TRACKED_INCIDENTS))).all()
        return {key: state in CLOSED_STATES for key, state in rows}

    async def check_once(self) -> None:
        tracked = await self._tracked()
        for check in CHECKS:
            try:
                obs = await check(self)
                for key, (title, body) in obs.active.items():
                    # A closed tool-denial ticket is a disposition: accept it
                    # first, so only denials newer than it reopen the ticket.
                    if tracked.get(key) and key.startswith("tool-denials:"):
                        await self._report(key, resolved=True, body="Ticket disposition accepted.")
                    await self._report(key, title, body)
                for key in tracked:
                    if key not in obs.active and key.startswith(obs.known):
                        await self._report(key, resolved=True,
                                           body="The health check now reads healthy.")
            except Exception:
                log.exception("health check %s failed", check.__name__)

    async def run_forever(self) -> None:
        if not self.settings.health_checker_enabled:
            return
        while True:
            await self.check_once()
            await asyncio.sleep(self.settings.health_checker_interval_seconds)
