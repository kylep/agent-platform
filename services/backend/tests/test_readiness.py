"""The readiness gate (docs/design/10 phase 2): derived deps, blocked state,
block-before-dispatch with try-before-block."""
import pytest

from agentplatform import readiness
from agentplatform.agents import Manifest
from agentplatform.skills import SkillStore


@pytest.fixture
def skills(tmp_path):
    d = tmp_path / "poster"; d.mkdir()
    (d / "SKILL.md").write_text("---\nname: poster\n---\nbody")
    return SkillStore(tmp_path)


def test_deps_derived_only_from_explicit_agent_bindings(skills):
    m = Manifest(skills=["poster"], secrets=["direct-secret"])
    deps = readiness.deps_for(m, skills)
    assert readiness.Dep("direct-secret", None, "present", "required") in deps
    assert deps == [readiness.Dep("direct-secret", None, "present", "required")]


def test_codex_runtime_requires_its_oauth_state(skills):
    deps = readiness.deps_for(Manifest(runtime="codex"), skills)
    assert deps == [readiness.Dep("codex-credentials", None, "present", "required")]


def test_unavailable_skill_blocks_instead_of_running_without_instructions(skills):
    assert readiness.blocking_reason(Manifest(skills=["missing"]), skills, {}) == \
        "blocked: skill `missing` unavailable or invalid"


def test_blocking_reason_states_and_severities(skills):
    m = Manifest(skills=["poster"], secrets=["hook-url"])
    assert readiness.blocking_reason(m, skills, {}) == \
        "blocked: secret `hook-url` is not set"
    assert readiness.blocking_reason(m, skills, {"hook-url": "unprobed"}) is None
    assert readiness.blocking_reason(m, skills, {"hook-url": "valid"}) is None
    m2 = Manifest(secrets=["direct-secret"])
    assert "is not set" in readiness.blocking_reason(m2, skills, {})
    assert readiness.blocking_reason(m2, skills, {"direct-secret": "unprobed"}) is None


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


async def _mk_dispatcher(sf, producer, skills, verifier):
    from agentplatform.agents import AgentStore
    from agentplatform.config import Settings
    from agentplatform.db import AgentDef
    from agentplatform.dispatcher import Dispatcher, FakeLauncher
    async with sf() as s:
        s.add(AgentDef(name="hooked", skills=["poster"], secrets=["hook-url"]))
        await s.commit()
    store = AgentStore(sf)
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


async def test_dispatch_blocked_records_failed_run_with_reason(sf, producer, skills):
    from agentplatform.db import Run, RunState
    verifier = StubVerifier(fresh="missing")
    disp, launcher = await _mk_dispatcher(sf, producer, skills, verifier)
    run_id = await _queue_run(sf)
    await disp.handle({"type": "run", "run_id": run_id})
    async with sf() as s:
        run = await s.get(Run, run_id)
    assert run.state == RunState.REJECTED
    assert run.error == "blocked: secret `hook-url` is not set"
    assert launcher.launched == []
    # try-before-block re-verified the offending secret
    assert verifier.calls == ["hook-url"]


async def test_try_before_block_recovers_transient_failure(sf, producer, skills):
    from agentplatform.db import Run, RunState, SecretMeta
    # recorded status says missing, but the on-demand re-verify passes now
    async with sf() as s:
        s.add(SecretMeta(name="hook-url", status="missing")); await s.commit()
    verifier = StubVerifier(fresh="valid")
    disp, launcher = await _mk_dispatcher(sf, producer, skills, verifier)
    run_id = await _queue_run(sf)
    await disp.handle({"type": "run", "run_id": run_id})
    async with sf() as s:
        run = await s.get(Run, run_id)
    assert run.state == RunState.DISPATCHED and launcher.launched == [run_id]


async def test_explicit_secrets_still_gate_without_skill_store(sf, producer):
    from agentplatform.db import Run, RunState
    disp, launcher = await _mk_dispatcher(sf, producer, None, None)
    disp.skills = None
    run_id = await _queue_run(sf)
    await disp.handle({"type": "run", "run_id": run_id})
    async with sf() as s:
        run = await s.get(Run, run_id)
    assert run.state == RunState.REJECTED


# --- API surface -------------------------------------------------------------

async def test_agents_listing_shows_blocked(admin_client, seed_agent, skills):
    admin_client._transport.app.state.skill_store = skills
    await seed_agent("hello-world", description="test", skills=["poster"],
                     secrets=["hook-url"])
    r = await admin_client.get("/api/agents")
    row = {a["name"]: a for a in r.json()}["hello-world"]
    assert row["blocked"] is True
    assert row["blocked_reason"] == \
        "blocked: secret `hook-url` is not set"
    assert row["quarantined"] is False
