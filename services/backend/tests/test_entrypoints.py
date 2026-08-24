"""Declared agent triggers (docs/design/10, now rows — docs/design/15): the
`entrypoints` JSON on an agent_defs row is what fires crons and opens webhook
paths."""
from datetime import datetime, timedelta, timezone

from agentplatform.agents import AgentInfo, AgentStore, Manifest
from agentplatform.db import AgentDef


async def test_store_reads_entrypoints_and_defaults(sf, seed_agent):
    await seed_agent("cronny", entrypoints={
        "crons": [{"schedule": "0 13 * * *"}, {"schedule": "0 1 * * *"}],
        "webhooks": [{"path": "newsflash"}]})
    await seed_agent("plain")
    store = AgentStore(sf)
    await store.reload()
    c = store.get("cronny")
    assert c.crons() == ["0 13 * * *", "0 1 * * *"]
    assert c.webhook_paths() == ["newsflash"]
    p = store.get("plain")
    assert p.crons() == [] and p.webhook_paths() == [] and p.error is None


async def test_timezone_survives_on_the_row(sf, seed_agent):
    """Three live agents pin their crons to a zone so daylight saving doesn't
    move them; the scheduler reads it off entrypoints."""
    await seed_agent("market", entrypoints={"crons": [{"schedule": "35 9 * * 1-5"}],
                                            "timezone": "America/Toronto"})
    store = AgentStore(sf)
    await store.reload()
    assert store.get("market").entrypoints.timezone == "America/Toronto"


async def test_broken_entrypoints_quarantines(sf):
    async with sf() as s:
        s.add(AgentDef(name="bad", entrypoints={"crons": [{"schedule": "not-a-cron"}]}))
        await s.commit()
    store = AgentStore(sf)
    await store.reload()
    info = store.get("bad")
    assert info.manifest is None and "not-a-cron" in info.error


def test_crons_dedupe_in_declaration_order():
    info = AgentInfo(name="x", manifest=Manifest(), agent_md="",
                     entrypoints={"crons": [{"schedule": "*/5 * * * *"},
                                            {"schedule": "0 9 * * *"},
                                            {"schedule": "*/5 * * * *"}]})
    assert info.crons() == ["*/5 * * * *", "0 9 * * *"]


async def test_scheduler_multi_cron_arms_earliest(sf):
    from agentplatform.db import Schedule
    from agentplatform.events import FakeProducer
    from agentplatform.scheduler import Scheduler, as_utc

    class FakeStore:
        def __init__(self, infos): self._i = infos
        async def reload(self): pass
        def list(self): return self._i

    info = AgentInfo(name="multi", manifest=Manifest(), agent_md="",
                     entrypoints={"crons": [{"schedule": "0 12 * * *"},
                                            {"schedule": "30 10 * * *"}]})
    sch = Scheduler(sf, FakeStore([info]), FakeProducer())
    now = datetime(2026, 7, 30, 10, 0, tzinfo=timezone.utc)
    await sch.tick(now)   # first sight: arm, don't fire
    async with sf() as s:
        row = await s.get(Schedule, "multi")
    # earliest of 10:30 and 12:00 today
    assert as_utc(row.next_fire) == now + timedelta(minutes=30)


async def test_schedules_api_lists_entrypoint_crons(admin_client, seed_agent):
    await seed_agent("hello-world",
                     entrypoints={"crons": [{"schedule": "*/10 * * * *"}]})
    r = await admin_client.get("/api/schedules")
    rows = {x["agent"]: x for x in r.json()}
    assert rows["hello-world"]["cron"] == "*/10 * * * *"
