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
    assert job.spec.ttl_seconds_after_finished == 3600   # finished Jobs+pods GC'd
    mounts = {m.name: m.mount_path for m in c.volume_mounts}
    assert mounts == {"claude-credentials": "/secrets/claude", "agents": "/agents",
                      "home": "/home/runner", "workspace": "/workspace", "tmp": "/tmp"}


def test_build_job_hardens_security_context():
    launcher = K8sJobLauncher(batch=None, settings=Settings(runner_image="r:1", k8s_namespace="ap"))
    run = Run(agent="hello-world", trigger="manual", requested_by="t", prompt="x"); run.id = "a" * 32
    spec = launcher.build_job(run, Manifest()).spec.template.spec
    sc = spec.containers[0].security_context
    assert sc.allow_privilege_escalation is False
    assert sc.run_as_non_root is True
    assert sc.run_as_user == 1001 and sc.run_as_group == 1001
    assert sc.capabilities.drop == ["ALL"]
    assert sc.read_only_root_filesystem is True
    assert spec.security_context.seccomp_profile.type == "RuntimeDefault"
    assert spec.security_context.fs_group == 1001
    # The runner never calls the k8s API — no SA token in the agent-code pod.
    assert spec.automount_service_account_token is False
    # CPU + memory limits contain a runaway agent on the single node.
    limits = spec.containers[0].resources.limits
    assert limits["cpu"] == "2" and limits["memory"] == "3Gi"


def test_build_job_session_env_wiring():
    """A conversation run (session_token passed) gets the resume env, with
    exactly one AP_API_URL even when a platform api_token is also present."""
    launcher = K8sJobLauncher(batch=None, settings=Settings(
        runner_image="r:1", k8s_namespace="ap", api_internal_url="http://api:8090"))
    run = Run(agent="hello-world", trigger="conversation", requested_by="t",
              prompt="built ctx", conversation_id="c1", user_message="hi again")
    run.id = "a" * 32
    env = {e.name: e.value for e in launcher.build_job(
        run, Manifest(), session_token="ap_sess", api_token="ap_api").spec
        .template.spec.containers[0].env}
    assert env["AP_SESSION_TOKEN"] == "ap_sess"
    assert env["AP_USER_MESSAGE"] == "hi again"
    assert env["AP_API_URL"] == "http://api:8090"
    urls = [e for e in launcher.build_job(
        run, Manifest(), session_token="ap_sess", api_token="ap_api").spec
        .template.spec.containers[0].env if e.name == "AP_API_URL"]
    assert len(urls) == 1


def test_build_job_no_session_env_without_token():
    launcher = K8sJobLauncher(batch=None, settings=Settings(runner_image="r:1", k8s_namespace="ap"))
    run = Run(agent="hello-world", trigger="manual", requested_by="t", prompt="x")
    run.id = "a" * 32
    names = {e.name for e in launcher.build_job(run, Manifest()).spec.template.spec.containers[0].env}
    assert "AP_SESSION_TOKEN" not in names and "AP_USER_MESSAGE" not in names


async def test_launch_mints_session_token_for_conversation(sf):
    """launch() mints a `session`-role per-run token for conversation runs and
    threads it (plus AP_USER_MESSAGE) into the pod; non-conversation runs don't."""
    from agentplatform.db import ApiKey
    from sqlalchemy import select

    class _FakeBatch:
        def __init__(self): self.job = None
        def create_namespaced_job(self, ns, job): self.job = job

    async def _launch(run):
        batch = _FakeBatch()
        launcher = K8sJobLauncher(batch=batch, settings=Settings(
            runner_image="r:1", k8s_namespace="ap", api_internal_url="http://api:8090"),
            session_factory=sf)
        await launcher.launch(run, Manifest())
        return batch.job

    async with sf() as s:
        conv_run = Run(agent="hello-world", trigger="conversation", requested_by="t",
                       prompt="ctx", conversation_id="c1", user_message="continue please")
        plain_run = Run(agent="hello-world", trigger="manual", requested_by="t", prompt="x")
        s.add(conv_run); s.add(plain_run); await s.commit()
        conv_id, plain_id = conv_run.id, plain_run.id

    async with sf() as s:
        conv_run = await s.get(Run, conv_id)
    env = {e.name: e.value for e in (await _launch(conv_run)).spec.template.spec.containers[0].env}
    assert env["AP_SESSION_TOKEN"] and env["AP_USER_MESSAGE"] == "continue please"
    async with sf() as s:
        keys = (await s.execute(select(ApiKey).where(ApiKey.run_id == conv_id))).scalars().all()
        assert any(k.role == "session" for k in keys)

    async with sf() as s:
        plain_run = await s.get(Run, plain_id)
    names = {e.name for e in (await _launch(plain_run)).spec.template.spec.containers[0].env}
    assert "AP_SESSION_TOKEN" not in names


def test_claude_proxy_removes_token_from_pod():
    """Token brokering (docs/design/09): with a claude-proxy configured the pod
    gets the proxy URL and never mounts the claude-credentials secret."""
    launcher = K8sJobLauncher(batch=None, settings=Settings(
        runner_image="r:1", k8s_namespace="ap",
        claude_proxy_url="http://agent-platform-claude-proxy:8000"))
    run = Run(agent="hello-world", trigger="manual", requested_by="t", prompt="x"); run.id = "a" * 32
    spec = launcher.build_job(run, Manifest()).spec.template.spec
    env = {e.name: e.value for e in spec.containers[0].env}
    assert env["AP_CLAUDE_PROXY_URL"] == "http://agent-platform-claude-proxy:8000"
    mounts = {m.name for m in spec.containers[0].volume_mounts}
    vols = {v.name for v in spec.volumes}
    assert "claude-credentials" not in mounts and "claude-credentials" not in vols


def test_no_claude_proxy_keeps_legacy_token_mount():
    launcher = K8sJobLauncher(batch=None, settings=Settings(runner_image="r:1", k8s_namespace="ap"))
    run = Run(agent="hello-world", trigger="manual", requested_by="t", prompt="x"); run.id = "a" * 32
    spec = launcher.build_job(run, Manifest()).spec.template.spec
    env = {e.name: e.value for e in spec.containers[0].env}
    assert "AP_CLAUDE_PROXY_URL" not in env
    assert {m.name for m in spec.containers[0].volume_mounts} >= {"claude-credentials"}
    assert {v.name for v in spec.volumes} >= {"claude-credentials"}


def test_writable_scratch_volumes_are_emptydirs():
    """Read-only rootfs needs the three writable paths backed by emptyDirs."""
    launcher = K8sJobLauncher(batch=None, settings=Settings(runner_image="r:1", k8s_namespace="ap"))
    run = Run(agent="hello-world", trigger="manual", requested_by="t", prompt="x"); run.id = "a" * 32
    vols = {v.name: v for v in launcher.build_job(run, Manifest()).spec.template.spec.volumes}
    for name in ("home", "workspace", "tmp"):
        assert vols[name].empty_dir is not None


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


def test_platform_token_role_ladder(tmp_path):
    """Role ladder (docs/design/12): custom-only tools → whoami-only `tools`
    role; any CORE broker tool → annotator (it forwards the token to our API);
    claude-only tools / no tools line / unknown agent → no token."""
    from agentplatform.agents import AgentStore
    for name, line in [("stocky", "tools: mcp__platform__stocks\n"),
                       ("libby", "tools: mcp__platform__query_app, mcp__platform__memory\n"),
                       ("shelly", "tools: WebFetch\n"),
                       ("openy", "")]:
        d = tmp_path / name
        d.mkdir()
        fm = f"---\nname: {name}\n{line}---\nbody" if line else "body"
        (d / "agent.md").write_text(fm)
        (d / "manifest.yaml").write_text("description: t\n")
    launcher = K8sJobLauncher(batch=None, settings=Settings(runner_image="r:1", k8s_namespace="ap"),
                              agent_store=AgentStore(tmp_path))
    assert launcher._platform_token_role("stocky") == "tools"
    assert launcher._platform_token_role("libby") == "annotator"
    assert launcher._platform_token_role("shelly") is None
    assert launcher._platform_token_role("openy") is None
    assert launcher._platform_token_role("ghost") is None
