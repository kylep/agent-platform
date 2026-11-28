"""U4: per-run deadline override (plan 2026-11-27-village-game §6). A
reflection needs ~8 min and a planner ~70 min while pai's own definition says
180 s; only backend code may set the override."""
import uuid

import pytest

from agentplatform.agents import Manifest
from agentplatform.config import Settings
from agentplatform.db import Run
from agentplatform.joblauncher import K8sJobLauncher, run_timeout
from agentplatform.materialize import RUN_TIMEOUT_MAX, RUN_TIMEOUT_MIN, materialize_run


def _spec(**extra):
    return {"run_id": uuid.uuid4().hex, "agent": "hello-world", "prompt": "x",
            "trigger": "village", "requested_by": "village:test", **extra}


def _deadline(run, manifest):
    launcher = K8sJobLauncher(batch=None, settings=Settings(runner_image="r:1", k8s_namespace="ap"))
    return launcher.build_job(run, manifest).spec.active_deadline_seconds


async def test_override_is_stored_and_drives_the_job_deadline(sf, producer, seed_agent):
    await seed_agent("hello-world")
    spec = _spec()
    await materialize_run(sf, producer, spec, timeout_seconds=480)
    async with sf() as s:
        run = await s.get(Run, spec["run_id"])
    assert run.timeout_seconds == 480
    manifest = Manifest(timeout_seconds=180)
    assert _deadline(run, manifest) == 480
    # the run-JWT TTL in launch() uses the same helper
    assert run_timeout(run, manifest) == 480


async def test_default_is_unchanged(sf, producer, seed_agent):
    await seed_agent("hello-world")
    spec = _spec()
    await materialize_run(sf, producer, spec)
    async with sf() as s:
        run = await s.get(Run, spec["run_id"])
    assert run.timeout_seconds is None
    assert _deadline(run, Manifest(timeout_seconds=180)) == 180
    assert run_timeout(run, Manifest(timeout_seconds=600)) == 600


@pytest.mark.parametrize("bad", [RUN_TIMEOUT_MIN - 1, RUN_TIMEOUT_MAX + 1, 0, -5, True, "480", 4.5e2])
async def test_out_of_range_is_refused_before_any_row(sf, producer, seed_agent, bad):
    await seed_agent("hello-world")
    spec = _spec()
    with pytest.raises(ValueError):
        await materialize_run(sf, producer, spec, timeout_seconds=bad)
    async with sf() as s:
        assert await s.get(Run, spec["run_id"]) is None


@pytest.mark.parametrize("ok", [RUN_TIMEOUT_MIN, RUN_TIMEOUT_MAX])
async def test_bounds_are_inclusive(sf, producer, seed_agent, ok):
    await seed_agent("hello-world")
    spec = _spec()
    await materialize_run(sf, producer, spec, timeout_seconds=ok)
    async with sf() as s:
        assert (await s.get(Run, spec["run_id"])).timeout_seconds == ok


async def test_a_spec_key_cannot_set_it(sf, producer, seed_agent):
    """run.inbound passes Kafka payloads through as the spec, and any pod can
    produce to Kafka, so the spec key is ignored; only the keyword counts."""
    await seed_agent("hello-world")
    spec = _spec(timeout_seconds=7200)
    await materialize_run(sf, producer, spec)
    async with sf() as s:
        assert (await s.get(Run, spec["run_id"])).timeout_seconds is None


async def test_public_create_run_body_cannot_set_it(admin_client, sf):
    r = await admin_client.post("/api/runs", json={"agent": "hello-world", "prompt": "x",
                                                   "timeout_seconds": 7200})
    assert r.status_code == 200, r.text
    async with sf() as s:
        assert (await s.get(Run, r.json()["id"])).timeout_seconds is None
