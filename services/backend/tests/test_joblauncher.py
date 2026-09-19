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
    """A conversation run gets the resume env, with exactly one AP_API_URL even
    when a platform api_token is also present."""
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


def test_build_job_session_token_without_a_conversation_has_no_user_message():
    """Every run carries a session token now (it fetches the agent definition),
    but AP_USER_MESSAGE stays conversation-only — an empty one would make the
    runner attempt a resume for a run that has no conversation to resume."""
    launcher = K8sJobLauncher(batch=None, settings=Settings(
        runner_image="r:1", k8s_namespace="ap", api_internal_url="http://api:8090"))
    run = Run(agent="hello-world", trigger="manual", requested_by="t", prompt="x")
    run.id = "a" * 32
    env = {e.name: e.value for e in launcher.build_job(
        run, Manifest(), session_token="ap_sess").spec.template.spec.containers[0].env}
    assert env["AP_SESSION_TOKEN"] == "ap_sess"
    assert env["AP_API_URL"] == "http://api:8090"
    assert "AP_USER_MESSAGE" not in env


def test_build_job_no_session_env_without_token():
    launcher = K8sJobLauncher(batch=None, settings=Settings(runner_image="r:1", k8s_namespace="ap"))
    run = Run(agent="hello-world", trigger="manual", requested_by="t", prompt="x")
    run.id = "a" * 32
    names = {e.name for e in launcher.build_job(run, Manifest()).spec.template.spec.containers[0].env}
    assert "AP_SESSION_TOKEN" not in names and "AP_USER_MESSAGE" not in names


async def test_launch_mints_a_session_token_for_every_run(sf):
    """docs/design/15: the session token is how a pod fetches its own agent
    definition, so EVERY run gets one (design/14 minted it only for
    conversations). AP_USER_MESSAGE stays conversation-only."""
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
        plain_run = await s.get(Run, plain_id)
    env = {e.name: e.value for e in (await _launch(plain_run)).spec.template.spec.containers[0].env}
    assert env["AP_SESSION_TOKEN"] and env["AP_API_URL"] == "http://api:8090"
    assert "AP_USER_MESSAGE" not in env

    async with sf() as s:
        for rid in (conv_id, plain_id):
            keys = (await s.execute(select(ApiKey).where(ApiKey.run_id == rid))).scalars().all()
            assert [k.role for k in keys] == ["session"]


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


async def test_a_system_agents_token_carries_the_run_it_acts_from(sf):
    """A system agent writes tickets, and every ticket write is refused unless
    the token names the run it is acting from. So its token is per-run like
    every other — one process-wide key with a null run left the agent design/20
    tells to open OPS tickets unable to open any."""
    from sqlalchemy import select
    from agentplatform.apikeys import hash_token
    from agentplatform.db import ApiKey

    class _FakeBatch:
        def __init__(self): self.job = None
        def create_namespaced_job(self, ns, job): self.job = job

    batch = _FakeBatch()
    launcher = K8sJobLauncher(batch=batch, settings=Settings(
        runner_image="r:1", k8s_namespace="ap", api_internal_url="http://api:8090"),
        session_factory=sf)
    async with sf() as s:
        s.add(run := Run(agent="health-monitor", trigger="schedule",
                         requested_by="scheduler", prompt="go"))
        await s.commit()
        run_id = run.id
    async with sf() as s:
        run = await s.get(Run, run_id)
    await launcher.launch(run, Manifest(system=True))

    env = {e.name: e.value for e in batch.job.spec.template.spec.containers[0].env}
    async with sf() as s:
        keys = {k.role: k for k in (await s.execute(select(ApiKey))).scalars()}
    key = keys["annotator"]
    assert (key.run_id, key.agent, key.name) == (run_id, "health-monitor",
                                                 "system:health-monitor")
    assert hash_token(env["AP_API_TOKEN"]) == key.key_hash
    assert env["AP_API_URL"] == "http://api:8090"


async def test_platform_token_role_ladder(sf, seed_agent):
    """Role ladder (docs/design/12, extended by docs/design/19): the WIDEST
    rung a grant set earns wins. Core broker tools forward the token to our API
    and earn `annotator`; the relay grant reaches only /api/relay/* as the
    agent itself and earns `relay`; the agent-definition tools carry their
    authority in the grant itself, so they promote nothing. Anything else
    custom is the whoami-only `tools` rung, and harness-only grants / no
    platform grant / an unknown agent earn no token at all. The grants are
    ROWS now (docs/design/15), not agent.md frontmatter."""
    from agentplatform.agents import AgentStore
    RELAY = "mcp__platform__relay"
    table = [
        ("stocky", ["mcp__platform__stocks"], [], "tools"),
        ("libby", ["mcp__platform__query_app", "mcp__platform__memory"], [], "annotator"),
        ("chatty", [RELAY], [], "relay"),
        ("nosy", [RELAY, "mcp__platform__runs_read"], [], "annotator"),
        # An agent tool is authorized per-write by the grant itself, so it
        # neither promotes nor demotes the rung relay already earned.
        ("editor", [RELAY, "mcp__platform__agents_edit"], [], "relay"),
        ("granter", ["mcp__platform__agents_grant"], [], "tools"),
        ("shelly", [], ["WebFetch"], None),
        ("openy", [], [], None),
    ]
    for name, platform, harness, _ in table:
        await seed_agent(name, platform_tools=platform, harness_tools=harness)
    launcher = K8sJobLauncher(batch=None, settings=Settings(runner_image="r:1", k8s_namespace="ap"),
                              agent_store=AgentStore(sf))
    for name, _, _, expected in table:
        assert await launcher._platform_token_role(name) == expected, name
    assert await launcher._platform_token_role("ghost") is None
    # The frozen JWT grant set comes off the same rows.
    assert launcher._frozen_tools("libby") == ["mcp__platform__query_app",
                                               "mcp__platform__memory"]
    assert launcher._frozen_tools("shelly") == []


async def test_a_relay_run_key_is_named_for_the_agent_that_holds_it(sf):
    """The per-run key's NAME is what the keys page and the audit trail show,
    so a relay-role key must read as one — `relay:<agent>` — rather than as an
    unlabelled key nobody can place."""
    from sqlalchemy import select
    from agentplatform.db import ApiKey
    launcher = K8sJobLauncher(batch=None, settings=Settings(runner_image="r:1", k8s_namespace="ap"),
                              session_factory=sf)
    run = Run(id="r1", agent="chatty", prompt="hi", state=RunState.QUEUED,
              trigger="manual", requested_by="admin")
    async with sf() as s:
        s.add(run)
        await s.commit()
    await launcher._invoke_token(run, role="relay")
    async with sf() as s:
        key = (await s.execute(select(ApiKey).where(ApiKey.run_id == "r1"))).scalar_one()
    assert (key.name, key.role, key.agent) == ("relay:chatty", "relay", "chatty")


def _dev_settings(**overrides):
    return Settings(runner_image="r:1", runner_dev_image="rd:1", k8s_namespace="ap",
                    git_remote_url="https://github.com/o/r.git", github_repo="o/r",
                    api_internal_url="http://api:8090", web_internal_url="http://ap-web:8090",
                    **overrides)


def _dev_run():
    run = Run(agent="engineer", trigger="relay", requested_by="t", prompt="fix ENG-12")
    run.id = "d" * 32
    return run


def test_dev_run_gets_the_workbench_profile():
    """docs/design/24: a `role: dev` run is a bigger pod on the dev image with
    an anonymous clone target — and NO repository credential of any kind."""
    launcher = K8sJobLauncher(batch=None, settings=_dev_settings(), github_app=_FakeApp())
    m = Manifest(role="dev", timeout_seconds=5400)
    assert launcher._is_dev(m) is True
    spec = launcher.build_job(_dev_run(), m, dev=True).spec.template.spec
    c = spec.containers[0]
    assert c.image == "rd:1"
    assert c.resources.requests == {"memory": "2Gi", "cpu": "500m"}
    assert c.resources.limits == {"memory": "6Gi", "cpu": "3"}
    env = {e.name: e.value for e in c.env}
    assert env["AP_WORKSPACE"] == "dev"
    assert env["AP_GIT_REMOTE_URL"] == "https://github.com/o/r.git"
    assert env["AP_DEFAULT_BRANCH"] == "main"
    assert env["AP_MAX_TURNS"] == "200"
    assert env["AP_VERIFY_TIMEOUT"] == "1800"
    assert env["AP_WEB_URL"] == "http://ap-web:8090"
    assert env["AP_PUBLISH_MAX_BYTES"] == str(16 * 1024 * 1024)
    assert env["PLAYWRIGHT_BROWSERS_PATH"] == "/ms-playwright"
    assert "AP_GITHUB_TOKEN" not in env and "AP_SELF_EDIT" not in env
    vols = {v.name: v for v in spec.volumes}
    assert vols["workspace"].empty_dir.size_limit == "8Gi"
    assert vols["dshm"].empty_dir.medium == "Memory"
    assert vols["dshm"].empty_dir.size_limit == "1Gi"
    mounts = {m.name: m.mount_path for m in c.volume_mounts}
    assert mounts["dshm"] == "/dev/shm" and mounts["workspace"] == "/workspace"


def test_dev_run_keeps_the_runner_cage():
    """Pod hardening is unchanged for the dev profile: the same block the
    hardening test above asserts, holding on a dev Job."""
    launcher = K8sJobLauncher(batch=None, settings=_dev_settings())
    spec = launcher.build_job(_dev_run(), Manifest(role="dev"), dev=True).spec.template.spec
    sc = spec.containers[0].security_context
    assert sc.allow_privilege_escalation is False
    assert sc.run_as_non_root is True
    assert sc.run_as_user == 1001 and sc.run_as_group == 1001
    assert sc.capabilities.drop == ["ALL"]
    assert sc.read_only_root_filesystem is True
    assert spec.security_context.seccomp_profile.type == "RuntimeDefault"
    assert spec.security_context.fs_group == 1001
    assert spec.automount_service_account_token is False


def test_dev_run_is_never_a_self_edit():
    launcher = K8sJobLauncher(batch=None, settings=_dev_settings(), github_app=_FakeApp())
    assert launcher._is_self_edit(Manifest(role="dev")) is False
    assert launcher._is_dev(Manifest(role="coder")) is False
    assert launcher._is_dev(Manifest(role="operator")) is False


def test_coder_job_is_unchanged_by_the_dev_profile():
    """The lean profile every other agent runs on — image, resources, the
    three plain emptyDirs, the self-edit env — is byte-for-byte what it was."""
    launcher = K8sJobLauncher(batch=None, settings=_dev_settings(), github_app=_FakeApp())
    run = Run(agent="platform-coder", trigger="manual", requested_by="t", prompt="edit x")
    run.id = "b" * 32
    spec = launcher.build_job(run, Manifest(role="coder", timeout_seconds=600),
                              self_edit_token="ghs_selfedit").spec.template.spec
    c = spec.containers[0]
    assert c.image == "r:1"
    assert c.resources.requests == {"memory": "1Gi", "cpu": "250m"}
    assert c.resources.limits == {"memory": "3Gi", "cpu": "2"}
    assert [v.name for v in spec.volumes] == ["claude-credentials", "agents", "home",
                                              "workspace", "tmp"]
    for name in ("home", "workspace", "tmp"):
        v = next(v for v in spec.volumes if v.name == name)
        assert v.empty_dir.size_limit is None and v.empty_dir.medium is None
    assert {m.name: m.mount_path for m in c.volume_mounts} == {
        "claude-credentials": "/secrets/claude", "agents": "/agents",
        "home": "/home/runner", "workspace": "/workspace", "tmp": "/tmp"}
    env = {e.name: e.value for e in c.env}
    assert env["AP_SELF_EDIT"] == "1" and env["AP_GITHUB_TOKEN"] == "ghs_selfedit"
    for name in ("AP_WORKSPACE", "AP_MAX_TURNS", "AP_VERIFY_TIMEOUT", "AP_WEB_URL",
                 "AP_PUBLISH_MAX_BYTES", "PLAYWRIGHT_BROWSERS_PATH"):
        assert name not in env


async def test_launch_never_mints_a_github_token_for_a_dev_run(sf):
    """The pod holds no repository credential: with a GitHub App configured
    and self-edit settings present, a dev launch must not touch the App."""
    class _RaisingApp:
        def installation_token(self):
            raise AssertionError("a dev run must never mint an installation token")

    class _FakeBatch:
        def __init__(self): self.job = None
        def create_namespaced_job(self, ns, job): self.job = job

    batch = _FakeBatch()
    launcher = K8sJobLauncher(batch=batch, settings=_dev_settings(), github_app=_RaisingApp(),
                              session_factory=sf)
    async with sf() as s:
        s.add(run := Run(agent="engineer", trigger="relay", requested_by="t", prompt="go"))
        await s.commit()
        run_id = run.id
    async with sf() as s:
        run = await s.get(Run, run_id)
    await launcher.launch(run, Manifest(role="dev"))
    c = batch.job.spec.template.spec.containers[0]
    env = {e.name: e.value for e in c.env}
    assert c.image == "rd:1" and env["AP_WORKSPACE"] == "dev"
    assert "AP_GITHUB_TOKEN" not in env and "AP_SELF_EDIT" not in env
    assert env["AP_SESSION_TOKEN"]
