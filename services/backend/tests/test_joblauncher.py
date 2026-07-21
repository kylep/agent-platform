from kubernetes.client.rest import ApiException

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


def test_build_job_hardens_security_context():
    launcher = K8sJobLauncher(batch=None, settings=Settings(runner_image="r:1", k8s_namespace="ap"))
    run = Run(agent="hello-world", trigger="manual", requested_by="t", prompt="x"); run.id = "a" * 32
    spec = launcher.build_job(run, Manifest()).spec.template.spec
    sc = spec.containers[0].security_context
    assert sc.allow_privilege_escalation is False
    assert sc.run_as_non_root is True
    assert sc.capabilities.drop == ["ALL"]
    assert spec.security_context.seccomp_profile.type == "RuntimeDefault"


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


class NotFoundBatch:
    def read_namespaced_job(self, name, namespace):
        raise ApiException(status=404)


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


async def test_poll_once_does_not_clobber_already_killed_run_on_job_404(sf):
    # Run was cancelled (killed) out-of-band and its Job deleted. The watcher
    # must not overwrite the terminal "killed" state with "failed: job disappeared".
    rid = await make_run(sf, state=RunState.KILLED)
    batch = NotFoundBatch()
    producer = FakeProducer()
    watcher = JobWatcher(batch, Settings(), sf, producer)
    await watcher.poll_once()
    async with sf() as s:
        run = await s.get(Run, rid)
    assert run.state == RunState.KILLED
    assert producer.published == []


async def test_poll_once_still_transitions_dispatched_to_running(sf):
    rid = await make_run(sf, state=RunState.DISPATCHED)
    batch = FakeBatch(_Status(active=1))
    producer = FakeProducer()
    watcher = JobWatcher(batch, Settings(), sf, producer)
    await watcher.poll_once()
    async with sf() as s:
        run = await s.get(Run, rid)
    assert run.state == RunState.RUNNING
    assert producer.published[-1][2]["state"] == RunState.RUNNING


class _FakeApp:
    def installation_token(self):
        return "ghs_selfedit"


def _selfedit_settings():
    return Settings(runner_image="r:1", k8s_namespace="ap",
                    git_remote_url="https://github.com/o/r.git", github_repo="o/r")


def test_self_edit_env_injected_for_coder_run():
    launcher = K8sJobLauncher(batch=None, settings=_selfedit_settings(), github_app=_FakeApp())
    run = Run(agent="platform-coder", trigger="manual", requested_by="t", prompt="edit x")
    run.id = "b" * 32
    m = Manifest(role="coder", timeout_seconds=600)
    assert launcher._is_self_edit(m) is True
    job = launcher.build_job(run, m, self_edit_token="ghs_selfedit")
    env = {e.name: e.value for e in job.spec.template.spec.containers[0].env}
    assert env["AP_SELF_EDIT"] == "1" and env["AP_GITHUB_TOKEN"] == "ghs_selfedit"
    assert env["AP_GIT_REMOTE_URL"] == "https://github.com/o/r.git" and env["AP_GITHUB_REPO"] == "o/r"


def test_non_coder_run_is_not_self_edit():
    launcher = K8sJobLauncher(batch=None, settings=_selfedit_settings(), github_app=_FakeApp())
    assert launcher._is_self_edit(Manifest(role="operator")) is False
    # and no self-edit env when no token passed
    run = Run(agent="hello-world", trigger="manual", requested_by="t", prompt="hi"); run.id = "c" * 32
    env = {e.name: e.value for e in launcher.build_job(run, Manifest()).spec.template.spec.containers[0].env}
    assert "AP_SELF_EDIT" not in env


def test_self_edit_off_without_app():
    launcher = K8sJobLauncher(batch=None, settings=_selfedit_settings(), github_app=None)
    assert launcher._is_self_edit(Manifest(role="coder")) is False


def _skill_store(tmp_path, name="git", secrets=("github-token",)):
    from agentplatform.skills import SkillStore
    d = tmp_path / name
    d.mkdir(parents=True)
    sec = "".join(f"  - {s}\n" for s in secrets)
    (d / "SKILL.md").write_text(f"---\nname: {name}\nsecrets:\n{sec}---\nbody")
    return SkillStore(tmp_path)


def test_bound_secrets_union_of_manifest_and_skills(tmp_path):
    launcher = K8sJobLauncher(batch=None, settings=Settings(runner_image="r:1", k8s_namespace="ap"),
                              skill_store=_skill_store(tmp_path))
    m = Manifest(skills=["git"], secrets=["extra", "github-token"])  # dedupe github-token
    assert launcher.bound_secrets(m) == ["extra", "github-token"]


def test_build_job_binds_secrets_via_envfrom(tmp_path):
    launcher = K8sJobLauncher(batch=None, settings=Settings(runner_image="r:1", k8s_namespace="ap"),
                              skill_store=_skill_store(tmp_path))
    run = Run(agent="a", trigger="manual", requested_by="t", prompt="x"); run.id = "e" * 32
    job = launcher.build_job(run, Manifest(skills=["git"], secrets=["extra"]))
    refs = job.spec.template.spec.containers[0].env_from
    bound = {e.secret_ref.name: e.secret_ref.optional for e in refs}
    assert bound == {"extra": True, "github-token": True}


def test_build_job_no_secrets_means_no_envfrom(tmp_path):
    launcher = K8sJobLauncher(batch=None, settings=Settings(runner_image="r:1", k8s_namespace="ap"),
                              skill_store=_skill_store(tmp_path))
    run = Run(agent="a", trigger="manual", requested_by="t", prompt="x"); run.id = "f" * 32
    job = launcher.build_job(run, Manifest())  # no skills, no secrets
    assert job.spec.template.spec.containers[0].env_from is None


async def test_system_token_minted_cached_and_injected(sf):
    from sqlalchemy import select
    from agentplatform.db import ApiKey
    launcher = K8sJobLauncher(batch=None, settings=Settings(runner_image="r:1", k8s_namespace="ap"),
                              session_factory=sf)
    t1 = await launcher._system_token("run-summarizer")
    t2 = await launcher._system_token("run-summarizer")
    assert t1 == t2 and t1.startswith("ap_")
    async with sf() as s:
        keys = (await s.execute(select(ApiKey))).scalars().all()
    assert len(keys) == 1 and keys[0].role == "annotator" and keys[0].agent == "run-summarizer"
    run = Run(agent="run-summarizer", trigger="schedule", requested_by="scheduler", prompt="go"); run.id = "d" * 32
    env = {e.name: e.value for e in launcher.build_job(run, Manifest(system=True), api_token=t1).spec.template.spec.containers[0].env}
    assert env["AP_API_TOKEN"] == t1 and env["AP_API_URL"].startswith("http://agent-platform-api")
