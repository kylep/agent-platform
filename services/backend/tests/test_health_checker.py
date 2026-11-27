"""The deterministic checker opens, resolves and dedupes the keys the LLM worker used."""
import os
import time
from datetime import timedelta

from sqlalchemy import delete, select

from agentplatform import health_checker
from agentplatform.config import Settings
from agentplatform.db import AgentDef, Base, Run, RunState, Ticket, ToolAudit, utcnow
from agentplatform.health_checker import HealthChecker
from agentplatform.health_incidents import HealthIncident


async def _checker(sf, producer, tmp_path, **settings):
    async with sf() as s:
        await (await s.connection()).run_sync(Base.metadata.create_all)
        for name in ("pai", "worker", "idle"):
            if await s.get(AgentDef, name) is None:
                s.add(AgentDef(name=name, enabled=name != "idle"))
        await s.commit()
    settings.setdefault("backup_output_dir", str(tmp_path / "no-backups"))
    return HealthChecker(sf, producer, Settings(**settings))


async def _runs(sf, agent, *states):
    async with sf() as s:
        now = utcnow()
        for i, state in enumerate(states):
            s.add(Run(agent=agent, state=state, trigger="manual", requested_by="test",
                      prompt="x", depth=0, execution_location="cluster",
                      created_at=now - timedelta(minutes=len(states) - i)))
        await s.commit()


async def _status(sf, key):
    async with sf() as s:
        record = await s.get(HealthIncident, key)
        return record and record.status


async def _ticket(sf, key):
    async with sf() as s:
        record = await s.get(HealthIncident, key)
        return await s.get(Ticket, record.ticket_id)


async def test_dlq_opens_and_resolves(sf, producer, tmp_path):
    checker = await _checker(sf, producer, tmp_path)
    await _runs(sf, "worker", RunState.DLQ)
    await checker.check_once()
    assert await _status(sf, "dlq") == "active"
    ticket = await _ticket(sf, "dlq")
    assert ticket.title == "Dead-letter queue has runs" and ticket.assignee == "agent:pai"
    async with sf() as s:
        (await s.execute(select(Run))).scalars().first().state = RunState.SUCCEEDED
        await s.commit()
    await checker.check_once()
    assert await _status(sf, "dlq") == "resolved"
    assert (await _ticket(sf, "dlq")).state == "done"


async def test_failure_streak_only_for_enabled_agents(sf, producer, tmp_path):
    checker = await _checker(sf, producer, tmp_path)
    await _runs(sf, "worker", RunState.FAILED, RunState.TIMED_OUT, RunState.FAILED)
    await _runs(sf, "idle", RunState.FAILED, RunState.FAILED, RunState.FAILED)
    await checker.check_once()
    assert await _status(sf, "failure-streak:worker") == "active"
    assert await _status(sf, "failure-streak:idle") is None
    await _runs(sf, "worker", RunState.SUCCEEDED)
    await checker.check_once()
    assert await _status(sf, "failure-streak:worker") == "resolved"


async def test_kafka_down_lag_and_unknown_lag(sf, producer, tmp_path, monkeypatch):
    checker = await _checker(sf, producer, tmp_path)
    state = {"reachable": False, "lag": None, "error": "refused"}

    async def fake(settings, session_factory):
        return dict(state)
    monkeypatch.setattr(health_checker, "kafka_status", fake)
    await checker.check_once()
    assert await _status(sf, "kafka-down") == "active"
    state.update(reachable=True, lag=51)
    await checker.check_once()
    assert await _status(sf, "kafka-down") == "resolved"
    assert await _status(sf, "kafka-lag") == "active"
    state.update(lag=None)   # unknown is not healthy
    await checker.check_once()
    assert await _status(sf, "kafka-lag") == "active"
    state.update(lag=50)
    await checker.check_once()
    assert await _status(sf, "kafka-lag") == "resolved"


async def test_backup_stale_resolves_and_missing_directory_is_unknown(sf, producer, tmp_path):
    directory = tmp_path / "backups"
    checker = await _checker(sf, producer, tmp_path, backup_output_dir=str(directory))
    await checker.check_once()
    assert await _status(sf, "backup-stale") is None   # no directory: unknown
    directory.mkdir()
    dump = directory / "ap.sql.gz"
    dump.write_bytes(b"x")
    old = time.time() - 48 * 3600
    os.utime(dump, (old, old))
    await checker.check_once()
    assert await _status(sf, "backup-stale") == "active"
    os.utime(dump, None)
    await checker.check_once()
    assert await _status(sf, "backup-stale") == "resolved"


async def _deny(sf, ts):
    async with sf() as s:
        s.add(ToolAudit(tool="memory", agent="worker", action="read", run_id="r1",
                        args_digest="sig", decision="deny:api", ts=ts))
        await s.commit()


async def test_tool_denials_open_and_unverified_recovery_stays_active(sf, producer, tmp_path):
    checker = await _checker(sf, producer, tmp_path)
    await _deny(sf, utcnow() - timedelta(hours=2))
    await checker.check_once()
    assert await _status(sf, "tool-denials:memory") == "active"
    async with sf() as s:
        await s.execute(delete(ToolAudit))
        await s.commit()
    await checker.check_once()   # counters vanished, but nothing proves recovery
    assert await _status(sf, "tool-denials:memory") == "active"


async def test_closed_tool_denial_ticket_reopens_only_on_new_evidence(sf, producer, tmp_path):
    checker = await _checker(sf, producer, tmp_path)
    await _deny(sf, utcnow() - timedelta(hours=2))
    await checker.check_once()
    ticket = await _ticket(sf, "tool-denials:memory")
    async with sf() as s:
        row = await s.get(Ticket, ticket.id)
        row.state, row.closed_at = "done", utcnow() - timedelta(hours=1)
        await s.commit()
    for _ in range(3):   # same evidence: the disposition stands
        await checker.check_once()
        assert (await _ticket(sf, "tool-denials:memory")).state == "done"
    await _deny(sf, utcnow())
    await checker.check_once()
    assert (await _ticket(sf, "tool-denials:memory")).state == "open"


async def test_a_raising_check_does_not_stop_the_others(sf, producer, tmp_path, monkeypatch):
    checker = await _checker(sf, producer, tmp_path)
    await _runs(sf, "worker", RunState.DLQ)

    async def boom(ctx):
        raise RuntimeError("probe exploded")
    monkeypatch.setattr(health_checker, "CHECKS", (boom, health_checker.check_dlq_and_streaks))
    await checker.check_once()
    assert await _status(sf, "dlq") == "active"
