"""entrypoints.yaml — declared agent triggers (docs/design/10 phase 3)."""
from datetime import datetime, timedelta, timezone

from agentplatform.agents import AgentInfo, AgentStore, Entrypoints, Manifest


def _mk_agent(root, name, manifest="description: t\n", entrypoints=None):
    d = root / name
    d.mkdir(parents=True)
    (d / "agent.md").write_text(f"# {name}")
    (d / "manifest.yaml").write_text(manifest)
    if entrypoints is not None:
        (d / "entrypoints.yaml").write_text(entrypoints)
    return d


def test_store_loads_entrypoints_and_defaults(tmp_path):
    _mk_agent(tmp_path, "cronny",
              entrypoints='cron: ["0 13 * * *", "0 1 * * *"]\nwebhooks:\n  - path: newsflash\n')
    _mk_agent(tmp_path, "plain")
    store = AgentStore(tmp_path)
    c = store.get("cronny")
    assert c.crons() == ["0 13 * * *", "0 1 * * *"]
    assert c.webhook_paths() == ["newsflash"]
    p = store.get("plain")
    assert p.entrypoints == Entrypoints() and p.crons() == [] and p.error is None


def test_broken_entrypoints_quarantines(tmp_path):
    _mk_agent(tmp_path, "bad", entrypoints='cron: ["not-a-cron"]\n')
    info = AgentStore(tmp_path).get("bad")
    assert info.error is not None and "not-a-cron" in info.error


def test_crons_unions_deprecated_manifest_schedule():
    info = AgentInfo(name="x", manifest=Manifest(schedule="*/5 * * * *"), agent_md="",
                     entrypoints=Entrypoints(cron=["*/5 * * * *", "0 9 * * *"]))
    # deduped against the legacy field; invalid legacy exprs are dropped
    assert info.crons() == ["*/5 * * * *", "0 9 * * *"]
    legacy = AgentInfo(name="y", manifest=Manifest(schedule="garbage"), agent_md="")
    assert legacy.crons() == []


async def test_scheduler_multi_cron_arms_earliest(sf):
    from agentplatform.db import Schedule
    from agentplatform.events import FakeProducer
    from agentplatform.scheduler import Scheduler, as_utc

    class FakeStore:
        def __init__(self, infos): self._i = infos
        def reload(self): pass
        def list(self): return self._i

    info = AgentInfo(name="multi", manifest=Manifest(), agent_md="",
                     entrypoints=Entrypoints(cron=["0 12 * * *", "30 10 * * *"]))
    sch = Scheduler(sf, FakeStore([info]), FakeProducer())
    now = datetime(2026, 7, 30, 10, 0, tzinfo=timezone.utc)
    await sch.tick(now)   # first sight: arm, don't fire
    async with sf() as s:
        row = await s.get(Schedule, "multi")
    # earliest of 10:30 and 12:00 today
    assert as_utc(row.next_fire) == now + timedelta(minutes=30)


async def test_schedules_api_lists_entrypoint_crons(admin_client, tmp_agents):
    (tmp_agents / "hello-world" / "entrypoints.yaml").write_text('cron: ["*/10 * * * *"]\n')
    r = await admin_client.get("/api/schedules")
    rows = {x["agent"]: x for x in r.json()}
    assert rows["hello-world"]["cron"] == "*/10 * * * *"
