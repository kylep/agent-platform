"""The readiness gate (docs/design/10 phase 2): derived deps, blocked state,
block-before-dispatch with try-before-block."""
import pytest

from agentplatform import readiness
from agentplatform.agents import Manifest
from agentplatform.skills import SkillStore


@pytest.fixture
def skills(tmp_path):
    d = tmp_path / "poster"; d.mkdir()
    (d / "SKILL.md").write_text(
        "---\nname: poster\nsecrets:\n"
        "  - name: hook-url\n    state: verified\n    severity: required\n"
        "  - name: nice-to-have\n---\nbody")
    return SkillStore(tmp_path)


def test_deps_derived_from_manifest_and_skills(skills):
    m = Manifest(skills=["poster"], secrets=["direct-secret"])
    deps = readiness.deps_for(m, skills)
    assert readiness.Dep("direct-secret", None, "present", "required") in deps
    assert readiness.Dep("hook-url", "poster", "verified", "required") in deps
    # bare-name skill secrets default to present/optional
    assert readiness.Dep("nice-to-have", "poster", "present", "optional") in deps


def test_blocking_reason_states_and_severities(skills):
    m = Manifest(skills=["poster"])
    # verified-required: anything but valid blocks, with the exact reason
    assert readiness.blocking_reason(m, skills, {}) == \
        "blocked: skill `poster` disabled — secret `hook-url` is not set"
    assert "failed verification" in readiness.blocking_reason(m, skills, {"hook-url": "invalid"})
    assert "is not verified" in readiness.blocking_reason(m, skills, {"hook-url": "unprobed"})
    assert readiness.blocking_reason(m, skills, {"hook-url": "valid"}) is None
    # optional dep never blocks; present-required needs existence only
    m2 = Manifest(secrets=["direct-secret"])
    assert "is not set" in readiness.blocking_reason(m2, skills, {})
    assert readiness.blocking_reason(m2, skills, {"direct-secret": "unprobed"}) is None


def test_shipped_skills_declare_strictness():
    from tests.conftest import REPO_SKILLS
    store = SkillStore(REPO_SKILLS)
    git = store.get("git").skill.secrets[0]
    assert (git.name, git.state, git.severity) == ("github-token", "verified", "required")
    # (discord/linear became TOOLS in design/12 — their credentials now bind
    # per-call in the executor, not via skill strictness.)


# --- block-before-dispatch ---------------------------------------------------

class StubVerifier:
    """try-before-block double: verify_one returns a scripted status."""
    def __init__(self, fresh=None, present=False):
        self.fresh, self.present, self.calls = fresh, present, []
    async def verify_one(self, name):
        self.calls.append(name)
        return self.fresh
    async def exists(self, name):
        return self.present


async def _mk_dispatcher(sf, producer, skills, verifier, tmp_path):
    from agentplatform.agents import AgentStore
    from agentplatform.config import Settings
    from agentplatform.dispatcher import Dispatcher, FakeLauncher
    d = tmp_path / "agents" / "hooked"; d.mkdir(parents=True)
    (d / "agent.md").write_text("# hooked")
    (d / "manifest.yaml").write_text("skills: [poster]\n")
    store = AgentStore(tmp_path / "agents")
    launcher = FakeLauncher()
    disp = Dispatcher(Settings(), sf, producer, store, launcher,
                      skill_store=skills, verifier=verifier)
    return disp, launcher


async def _queue_run(sf, agent="hooked"):
    from agentplatform.db import Run, RunState
    async with sf() as s:
        run = Run(agent=agent, trigger="manual", requested_by="t", prompt="go",
                  state=RunState.QUEUED)
        s.add(run); await s.commit()
        return run.id


async def test_dispatch_blocked_records_failed_run_with_reason(sf, producer, skills, tmp_path):
    from agentplatform.db import Run, RunState
    verifier = StubVerifier(fresh="missing")
    disp, launcher = await _mk_dispatcher(sf, producer, skills, verifier, tmp_path)
    run_id = await _queue_run(sf)
    await disp.handle({"type": "run", "run_id": run_id})
    async with sf() as s:
        run = await s.get(Run, run_id)
    assert run.state == RunState.REJECTED
    assert run.error == "blocked: skill `poster` disabled — secret `hook-url` is not set"
    assert launcher.launched == []
    # try-before-block re-verified the offending secret
    assert verifier.calls == ["hook-url"]


async def test_try_before_block_recovers_transient_failure(sf, producer, skills, tmp_path):
    from agentplatform.db import Run, RunState, SecretMeta
    # recorded status says invalid, but the on-demand re-verify passes now
    async with sf() as s:
        s.add(SecretMeta(name="hook-url", status="invalid")); await s.commit()
    verifier = StubVerifier(fresh="valid")
    disp, launcher = await _mk_dispatcher(sf, producer, skills, verifier, tmp_path)
    run_id = await _queue_run(sf)
    await disp.handle({"type": "run", "run_id": run_id})
    async with sf() as s:
        run = await s.get(Run, run_id)
    assert run.state == RunState.DISPATCHED and launcher.launched == [run_id]


async def test_no_gate_without_skill_store(sf, producer, tmp_path):
    from agentplatform.db import Run, RunState
    disp, launcher = await _mk_dispatcher(sf, producer, None, None, tmp_path)
    disp.skills = None
    run_id = await _queue_run(sf)
    await disp.handle({"type": "run", "run_id": run_id})
    async with sf() as s:
        run = await s.get(Run, run_id)
    assert run.state == RunState.DISPATCHED


# --- API surface -------------------------------------------------------------

async def test_agents_listing_shows_blocked(admin_client, tmp_agents):
    # the fixture agent uses the repo's real git skill (github-token
    # verified/required); no secret is set → the listing shows blocked
    (tmp_agents / "hello-world" / "manifest.yaml").write_text(
        "description: test\nskills: [git]\n")
    r = await admin_client.get("/api/agents")
    row = {a["name"]: a for a in r.json()}["hello-world"]
    assert row["blocked"] is True
    assert row["blocked_reason"] == \
        "blocked: skill `git` disabled — secret `github-token` is not set"
    assert row["quarantined"] is False
