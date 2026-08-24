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


async def _reachable(disp, ok: bool):
    async def _probe():
        return ok
    disp._kafka_reachable = _probe


async def _make_stale(sf, seconds=60):
    from datetime import timedelta
    from agentplatform.db import utcnow
    rid = await make_run(sf)
    async with sf() as s:
        run = await s.get(Run, rid)
        run.created_at = utcnow() - timedelta(seconds=seconds)
        await s.commit()
    return rid


async def test_sweep_queued_drains_stale_runs_when_kafka_up(sf, disp):
    await _reachable(disp, True)
    rid = await _make_stale(sf)
    drained = await disp.sweep_queued(older_than_seconds=15)
    assert drained == 1
    assert disp.launcher.launched == [rid]


async def test_sweep_holds_queued_runs_while_kafka_down(sf, disp):
    """The drain-on-recovery guarantee: with Kafka unreachable, a stale queued
    run is HELD (not launched into an outage where it would fail)."""
    await _reachable(disp, False)
    rid = await _make_stale(sf)
    drained = await disp.sweep_queued(older_than_seconds=15)
    assert drained == 0
    assert disp.launcher.launched == []
    async with sf() as s:                       # still queued, not failed
        assert (await s.get(Run, rid)).state == RunState.QUEUED
    # ...and once Kafka recovers, the next sweep drains it.
    await _reachable(disp, True)
    assert await disp.sweep_queued(older_than_seconds=15) == 1
    assert disp.launcher.launched == [rid]


async def test_sweep_ignores_fresh_queued_runs(sf, disp):
    await _reachable(disp, True)
    await make_run(sf)
    drained = await disp.sweep_queued(older_than_seconds=15)
    assert drained == 0
    assert disp.launcher.launched == []


async def test_agent_added_after_boot_is_dispatchable(sf, disp, seed_agent):
    # The store was loaded before this agent existed; handle() reloads first.
    await seed_agent("late-agent", description="late")
    rid = await make_run(sf, agent="late-agent")
    await disp.handle({"type": "run", "run_id": rid})
    assert disp.launcher.launched == [rid]


# --- pre-flight credential gate ----------------------------------------------

async def _set_cred(sf, status):
    from agentplatform.db import SecretMeta
    from agentplatform.secrets import CLAUDE_CREDENTIAL
    async with sf() as s:
        s.add(SecretMeta(name=CLAUDE_CREDENTIAL, status=status)); await s.commit()


async def test_invalid_credential_rejects_up_front(sf, disp):
    """After the first half-open probe is spent, a known-bad token rejects runs
    without launching a doomed pod."""
    await _set_cred(sf, "invalid")
    disp._cred_probe_at = 1e18            # probe window closed → hard block
    rid = await make_run(sf)
    await disp.handle({"type": "run", "run_id": rid})
    assert disp.launcher.launched == []
    async with sf() as s:
        run = await s.get(Run, rid)
        assert run.state == RunState.REJECTED and "invalid" in (run.error or "")


async def test_invalid_credential_half_open_lets_one_through(sf, disp):
    """When the recheck window opens, exactly one run is let through to re-probe,
    and the next is held again."""
    await _set_cred(sf, "invalid")
    disp._cred_probe_at = 0.0             # window open
    r1 = await make_run(sf)
    await disp.handle({"type": "run", "run_id": r1})
    assert disp.launcher.launched == [r1]     # probe allowed
    r2 = await make_run(sf)
    await disp.handle({"type": "run", "run_id": r2})
    assert disp.launcher.launched == [r1]     # next is held
    async with sf() as s:
        assert (await s.get(Run, r2)).state == RunState.REJECTED


async def test_valid_and_unprobed_credentials_dispatch(sf, disp):
    from agentplatform.db import SecretMeta
    from agentplatform.secrets import CLAUDE_CREDENTIAL
    for status in ("valid", "unprobed"):
        async with sf() as s:                 # fresh meta each iteration
            m = await s.get(SecretMeta, CLAUDE_CREDENTIAL)
            if m: await s.delete(m); await s.commit()
        await _set_cred(sf, status)
        rid = await make_run(sf)
        await disp.handle({"type": "run", "run_id": rid})
        async with sf() as s:                 # gate didn't block it
            assert (await s.get(Run, rid)).state == RunState.DISPATCHED
            done = await s.get(Run, rid); done.state = RunState.SUCCEEDED  # free the slot
            await s.commit()


async def test_gate_disabled_when_recheck_nonpositive(sf, agent_store):
    from agentplatform.config import Settings
    from agentplatform.events import FakeProducer
    d = Dispatcher(Settings(credential_recheck_seconds=0), sf, FakeProducer(), agent_store, FakeLauncher())
    await _set_cred(sf, "invalid")
    rid = await make_run(sf)
    await d.handle({"type": "run", "run_id": rid})
    assert d.launcher.launched == [rid]       # gate off → dispatches despite invalid
