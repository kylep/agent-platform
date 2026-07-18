import pytest
from agentplatform.config import Settings
from agentplatform.db import Run, RunState
from agentplatform.dispatcher import Dispatcher, FakeLauncher
from agentplatform.events import FakeProducer, TOPIC_RUN_DLQ

@pytest.fixture
def disp(sf, agent_store):
    return Dispatcher(Settings(global_concurrency=2), sf, FakeProducer(), agent_store, FakeLauncher())

async def make_run(sf, agent="hello-world", state=RunState.QUEUED) -> str:
    async with sf() as s:
        run = Run(agent=agent, trigger="manual", requested_by="t", prompt="x", state=state)
        s.add(run); await s.commit(); return run.id

async def test_dispatches_queued_run(sf, disp):
    rid = await make_run(sf)
    await disp.handle({"type": "run", "run_id": rid})
    assert disp.launcher.launched == [rid]
    async with sf() as s:
        assert (await s.get(Run, rid)).state == RunState.DISPATCHED

async def test_terminal_run_is_noop(sf, disp):
    rid = await make_run(sf, state=RunState.SUCCEEDED)
    await disp.handle({"type": "run", "run_id": rid})
    assert disp.launcher.launched == []

async def test_rejects_unknown_agent(sf, disp):
    rid = await make_run(sf, agent="ghost")
    await disp.handle({"type": "run", "run_id": rid})
    async with sf() as s:
        assert (await s.get(Run, rid)).state == RunState.REJECTED

async def test_launch_failure_goes_dlq(sf, disp):
    disp.launcher.fail_next = True
    rid = await make_run(sf)
    await disp.handle({"type": "run", "run_id": rid})
    async with sf() as s:
        assert (await s.get(Run, rid)).state == RunState.DLQ
    assert disp.producer.published[-1][0] == TOPIC_RUN_DLQ

async def test_cancel_active_run(sf, disp):
    rid = await make_run(sf, state=RunState.RUNNING)
    await disp.handle({"type": "cancel", "run_id": rid})
    assert disp.launcher.cancelled == [rid]
    async with sf() as s:
        assert (await s.get(Run, rid)).state == RunState.KILLED


async def test_sweep_queued_drains_stale_runs(sf, disp):
    from datetime import timedelta
    from agentplatform.db import utcnow
    rid = await make_run(sf)
    async with sf() as s:
        run = await s.get(Run, rid)
        run.created_at = utcnow() - timedelta(seconds=60)
        await s.commit()
    drained = await disp.sweep_queued(older_than_seconds=15)
    assert drained == 1
    assert disp.launcher.launched == [rid]


async def test_sweep_ignores_fresh_queued_runs(sf, disp):
    await make_run(sf)
    drained = await disp.sweep_queued(older_than_seconds=15)
    assert drained == 0
    assert disp.launcher.launched == []


async def test_agent_added_after_boot_is_dispatchable(sf, disp, tmp_path):
    # Store starts pointing at the (empty-of-this-agent) dir; agent lands later.
    late = disp.agents.root / "late-agent"
    late.mkdir(parents=True)
    (late / "agent.md").write_text("# late-agent")
    (late / "manifest.yaml").write_text("description: late\n")
    rid = await make_run(sf, agent="late-agent")
    await disp.handle({"type": "run", "run_id": rid})
    assert disp.launcher.launched == [rid]
