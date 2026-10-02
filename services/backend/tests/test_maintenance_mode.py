"""Platform maintenance mode (design 39, Lifecycle -> Restore, steps 1 and 4)."""
import gzip
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from agentplatform import maintenance_mode as mm
from agentplatform.agents import AgentInfo, Manifest
from agentplatform.backup_export import RESTORE_MARKER_SQL, append_restore_marker
from agentplatform.db import Run, ScheduledJob, ScheduledTask, make_engine, \
    make_session_factory, init_db, utcnow
from agentplatform.events import FakeProducer, TOPIC_RUN_INBOUND
from agentplatform.scheduler import Scheduler, next_fire
from agentplatform.task_scheduler import fire_due_tasks


class FakeStore:
    def __init__(self, infos): self._infos = infos
    async def reload(self): pass
    def list(self): return self._infos


def _agent(name, cron):
    return AgentInfo(name=name, manifest=Manifest(), agent_md="",
                     entrypoints={"crons": [{"schedule": cron, "prompt": ""}]})


@pytest.fixture
def now():
    return datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


def _inbound(producer):
    return [v for t, _, v in producer.published if t == TOPIC_RUN_INBOUND]


async def test_default_mode_is_running_and_the_gate_passes(sf):
    async with sf() as s:
        assert (await mm.status(s))["mode"] == "running"
        assert not await mm.is_paused(s)
        await mm.require_running(s)


async def test_require_running_raises_under_restore(sf):
    async with sf() as s:
        await mm.enter_restore(s, "test")
        await s.commit()
    async with sf() as s:
        assert await mm.is_paused(s)
        with pytest.raises(mm.MaintenanceActive):
            await mm.require_running(s)


async def test_the_hooks_r2_and_m6_call_exist():
    """R2 tool actions and M6 service principals gate on require_running, and
    the materializer (B21) on materialization_allowed; both must stay put."""
    assert callable(mm.require_running)
    assert callable(mm.materialization_allowed)


async def test_materialization_is_refused_under_restore(sf):
    async with sf() as s:
        assert await mm.materialization_allowed(s)
        await mm.enter_restore(s, "test")
        await s.commit()
    async with sf() as s:
        assert not await mm.materialization_allowed(s)


async def test_no_cron_fires_under_restore_and_nothing_catches_up(sf, now):
    producer = FakeProducer()
    sch = Scheduler(sf, FakeStore([_agent("cronbot", "*/10 * * * *")]), producer)
    await sch.tick(now)                                     # arm
    async with sf() as s:
        await mm.enter_restore(s, "restore")
        await s.commit()
    due = next_fire("*/10 * * * *", now) + timedelta(seconds=1)
    await sch.tick(due)
    assert _inbound(producer) == []
    async with sf() as s:
        await mm.resume(s, "kyle")
        await s.commit()
    await sch.tick(due + timedelta(seconds=1))              # same period: no catch-up
    assert _inbound(producer) == []
    await sch.tick(next_fire("*/10 * * * *", due) + timedelta(seconds=1))
    assert len(_inbound(producer)) == 1                     # normal service resumed


async def test_no_job_fires_under_restore_and_nothing_catches_up(sf, now):
    async with sf() as s:
        s.add(ScheduledJob(id="j1", name="j", agent="a", cron="*/10 * * * *", prompt="go",
                           next_fire=now - timedelta(minutes=1)))
        await mm.enter_restore(s, "restore")
        await s.commit()
    producer = FakeProducer()
    sch = Scheduler(sf, FakeStore([]), producer)
    await sch.tick(now)
    assert _inbound(producer) == []
    async with sf() as s:
        job = await s.get(ScheduledJob, "j1")
        assert job.next_fire is not None and job.last_fire is None
        await mm.resume(s, "kyle")
        await s.commit()
    await sch.tick(now + timedelta(seconds=1))
    assert _inbound(producer) == []


async def test_no_task_fires_under_restore(admin_client, sf, producer, seed_agent):
    await seed_agent("reminder", runtime="codex", model="gpt-6-sol")
    task = (await admin_client.post("/api/tasks", json={
        "agent": "reminder", "prompt": "hi", "delay_minutes": 2})).json()
    async with sf() as s:
        row = await s.get(ScheduledTask, task["id"])
        row.run_at = utcnow() - timedelta(minutes=1)
        row.expires_at = utcnow() + timedelta(minutes=30)
        await mm.enter_restore(s, "restore")
        await s.commit()
    assert await fire_due_tasks(sf, producer) == 0
    async with sf() as s:
        assert (await s.execute(select(Run).where(Run.task_id == task["id"]))).first() is None
        await mm.resume(s, "kyle")
        await s.commit()
    assert await fire_due_tasks(sf, producer) == 1


async def test_a_dump_with_the_marker_restores_into_a_fresh_database_paused(tmp_path):
    dump = tmp_path / "database.sql.gz"
    dump.write_bytes(gzip.compress(b"-- plain pg_dump output\n"))
    append_restore_marker(dump)
    sql = gzip.decompress(dump.read_bytes()).decode()
    assert sql.startswith("-- plain pg_dump output\n") and RESTORE_MARKER_SQL in sql
    # Replay it against a fresh schema, as pg_restore/psql would.
    db = tmp_path / "fresh.db"
    engine = make_engine(f"sqlite+aiosqlite:///{db}")
    await init_db(engine)
    await engine.dispose()
    con = sqlite3.connect(db)
    con.executescript(sql)
    con.commit()
    con.close()
    engine = make_engine(f"sqlite+aiosqlite:///{db}")
    sf2 = make_session_factory(engine)
    async with sf2() as s:
        st = await mm.status(s)
        assert st["mode"] == "restore" and st["resumed_by"] is None and st["entered_at"]
    await engine.dispose()


async def test_the_source_database_mode_never_changes(sf, tmp_path):
    dump = tmp_path / "database.sql.gz"
    dump.write_bytes(gzip.compress(b"-- x\n"))
    append_restore_marker(dump)
    async with sf() as s:
        assert (await mm.status(s))["mode"] == "running"


async def test_only_kyles_session_resumes(client, admin_client, token_client, sf):
    async with sf() as s:
        await mm.enter_restore(s, "restore")
        await s.commit()
    # No credentials at all.
    assert (await token_client.get("/api/maintenance/status")).status_code == 401
    assert (await token_client.post("/api/maintenance/resume")).status_code == 401
    # Kyle's session reads and resumes.
    got = await admin_client.get("/api/maintenance/status")
    assert got.status_code == 200 and got.json()["mode"] == "restore"
    r = await admin_client.post("/api/maintenance/resume")
    assert r.status_code == 200 and r.json()["mode"] == "running"
    assert r.json()["resumed_by"] == "admin"


async def test_an_admin_api_key_reads_but_cannot_resume(admin_client, token_client, sf):
    key = (await admin_client.post("/api/api-keys", json={"name": "ci", "role": "admin"})).json()
    token = key["token"]
    hdr = {"Authorization": f"Bearer {token}"}
    async with sf() as s:
        await mm.enter_restore(s, "restore")
        await s.commit()
    assert (await token_client.get("/api/maintenance/status", headers=hdr)).status_code == 200
    assert (await token_client.post("/api/maintenance/resume", headers=hdr)).status_code == 403
    async with sf() as s:
        assert await mm.is_paused(s)
