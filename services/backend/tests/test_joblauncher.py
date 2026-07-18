from agentplatform.agents import Manifest
from agentplatform.config import Settings
from agentplatform.db import Run, RunState
from agentplatform.events import FakeProducer
from agentplatform.joblauncher import K8sJobLauncher, JobWatcher


def test_build_job_spec():
    launcher = K8sJobLauncher(batch=None, settings=Settings(runner_image="r:1", k8s_namespace="ap"))
    run = Run(agent="hello-world", trigger="manual", requested_by="t", prompt="say hi")
    run.id = "a" * 32
    job = launcher.build_job(run, Manifest(timeout_seconds=600))
    assert job.metadata.name == "run-aaaaaaaaaaaa"
    c = job.spec.template.spec.containers[0]
    env = {e.name: e.value for e in c.env}
    assert env["AP_RUN_ID"] == run.id and env["AP_AGENT"] == "hello-world"
    assert job.spec.active_deadline_seconds == 600
    assert job.spec.backoff_limit == 0
    mounts = {m.name: m.mount_path for m in c.volume_mounts}
    assert mounts == {"claude-credentials": "/secrets/claude", "agents": "/agents"}


class _Status:
    def __init__(self, active=None, succeeded=None, failed=None, conditions=None):
        self.active = active
        self.succeeded = succeeded
        self.failed = failed
        self.conditions = conditions or []


class _Condition:
    def __init__(self, reason):
        self.reason = reason


class _Job:
    def __init__(self, status):
        self.status = status


class FakeBatch:
    def __init__(self, status):
        self._status = status

    def read_namespaced_job(self, name, namespace):
        return _Job(self._status)


async def make_run(sf, agent="hello-world", state=RunState.DISPATCHED) -> str:
    async with sf() as s:
        run = Run(agent=agent, trigger="manual", requested_by="t", prompt="x", state=state)
        s.add(run)
        await s.commit()
        return run.id


async def test_poll_once_marks_timed_out_on_deadline_exceeded(sf):
    rid = await make_run(sf, state=RunState.RUNNING)
    batch = FakeBatch(_Status(failed=1, conditions=[_Condition(reason="DeadlineExceeded")]))
    producer = FakeProducer()
    watcher = JobWatcher(batch, Settings(), sf, producer)
    await watcher.poll_once()
    async with sf() as s:
        run = await s.get(Run, rid)
    assert run.state == RunState.TIMED_OUT
    assert run.finished_at is not None
    assert producer.published[-1][2]["state"] == RunState.TIMED_OUT
