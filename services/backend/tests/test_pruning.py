from datetime import timedelta

import pytest
from sqlalchemy import func, select

from agentplatform.agents import AgentStore
from agentplatform.config import Settings
from agentplatform.db import AgentDef, Run, TranscriptEvent, utcnow
from agentplatform.pruning import TranscriptPruner


async def _store(sf):
    """Three agents with different retention overrides, as `agent_defs` rows."""
    async with sf() as s:
        s.add(AgentDef(name="def-agent"))                                  # default (30)
        s.add(AgentDef(name="shortlived", transcript_retention_days=1))    # 1-day override
        s.add(AgentDef(name="keeper", transcript_retention_days=0))        # keep forever
        await s.commit()
    store = AgentStore(sf)
    await store.reload()
    return store


async def _run_with_events(sf, agent, *, age_days, n_events=2):
    async with sf() as s:
        r = Run(agent=agent, trigger="manual", requested_by="t", prompt="x",
                created_at=utcnow() - timedelta(days=age_days))
        s.add(r)
        await s.flush()
        for seq in range(n_events):
            s.add(TranscriptEvent(run_id=r.id, seq=seq, payload={"seq": seq}))
        await s.commit()
        return r.id


async def _event_count(sf, run_id) -> int:
    async with sf() as s:
        return (await s.execute(select(func.count()).select_from(TranscriptEvent)
                .where(TranscriptEvent.run_id == run_id))).scalar_one()


async def test_prunes_past_retention_keeps_recent(sf):
    old = await _run_with_events(sf, "def-agent", age_days=40)
    recent = await _run_with_events(sf, "def-agent", age_days=1, n_events=1)
    pruner = TranscriptPruner(sf, await _store(sf), Settings(transcript_retention_days=30))
    deleted = await pruner.prune_once()
    assert deleted == 2
    assert await _event_count(sf, old) == 0
    assert await _event_count(sf, recent) == 1


async def test_per_agent_override_shorter(sf):
    # 2 days old: kept under the 30-day default, pruned under shortlived's 1 day.
    stale = await _run_with_events(sf, "shortlived", age_days=2)
    pruner = TranscriptPruner(sf, await _store(sf), Settings(transcript_retention_days=30))
    await pruner.prune_once()
    assert await _event_count(sf, stale) == 0


async def test_retention_zero_keeps_forever(sf):
    ancient = await _run_with_events(sf, "keeper", age_days=999)
    pruner = TranscriptPruner(sf, await _store(sf), Settings(transcript_retention_days=30))
    await pruner.prune_once()
    assert await _event_count(sf, ancient) == 2


async def test_retention_days_resolution(sf):
    pruner = TranscriptPruner(sf, await _store(sf), Settings(transcript_retention_days=30))
    assert pruner.retention_days("def-agent") == 30
    assert pruner.retention_days("shortlived") == 1
    assert pruner.retention_days("keeper") == 0
    assert pruner.retention_days("unknown-agent") == 30  # falls back to default


# --- artifacts (docs/design/23) -------------------------------------------------
# A delete is `deleted_at` so a card naming a gone artifact still resolves to
# "deleted"; the pruner is what turns that into a DELETE, rows and bytes both,
# once the row has been gone long enough that nothing is still pointing at it.

async def _artifact(sf, *, deleted_days_ago=None) -> str:
    from agentplatform.db import Artifact, ArtifactBlob
    async with sf() as s:
        a = Artifact(name="a.bin", mime="application/octet-stream", size=1, sha256="x" * 64,
                     kind="file", owner="user:admin", source="upload",
                     deleted_at=(utcnow() - timedelta(days=deleted_days_ago)
                                 if deleted_days_ago is not None else None))
        s.add(a)
        await s.flush()
        s.add(ArtifactBlob(artifact_id=a.id, data=b"\x00"))
        await s.commit()
        return a.id


async def _artifact_rows(sf, artifact_id) -> tuple[int, int]:
    from agentplatform.db import Artifact, ArtifactBlob
    async with sf() as s:
        rows = (await s.execute(select(func.count()).select_from(Artifact)
                                .where(Artifact.id == artifact_id))).scalar_one()
        blobs = (await s.execute(select(func.count()).select_from(ArtifactBlob)
                                 .where(ArtifactBlob.artifact_id == artifact_id))).scalar_one()
        return rows, blobs


async def test_artifact_pruner_deletes_only_expired_soft_deleted_rows(sf):
    from agentplatform.pruning import ArtifactPruner
    live = await _artifact(sf)
    fresh = await _artifact(sf, deleted_days_ago=1)
    expired = await _artifact(sf, deleted_days_ago=40)
    pruner = ArtifactPruner(sf, Settings(artifacts_prune_days=30))
    assert await pruner.prune_once() == 1
    # The blob goes with the row: sqlite promises no cascade, and bytes with
    # no row are the one thing nobody could ever list to notice.
    assert await _artifact_rows(sf, expired) == (0, 0)
    assert await _artifact_rows(sf, fresh) == (1, 1)
    assert await _artifact_rows(sf, live) == (1, 1)
    assert await pruner.prune_once() == 0


async def test_artifact_pruner_zero_keeps_the_soft_deleted_forever(sf):
    from agentplatform.pruning import ArtifactPruner
    old = await _artifact(sf, deleted_days_ago=999)
    assert await ArtifactPruner(sf, Settings(artifacts_prune_days=0)).prune_once() == 0
    assert await _artifact_rows(sf, old) == (1, 1)


async def test_artifact_pruner_undresses_an_agent_still_wearing_the_artifact(sf, producer):
    """The soft delete already clears faces; the hard delete clears again for
    the row that got its image by a path the store never saw (a raw write, a
    restore), so no face outlives its bytes."""
    from agentplatform.db import AgentDef
    from agentplatform.pruning import ArtifactPruner
    expired = await _artifact(sf, deleted_days_ago=40)
    live = await _artifact(sf)
    async with sf() as s:
        s.add(AgentDef(name="news", image_artifact_id=expired))
        s.add(AgentDef(name="pai", image_artifact_id=live))
        await s.commit()
    assert await ArtifactPruner(sf, Settings(artifacts_prune_days=30),
                                producer=producer).prune_once() == 1
    async with sf() as s:
        assert (await s.get(AgentDef, "news")).image_artifact_id is None
        assert (await s.get(AgentDef, "pai")).image_artifact_id == live
    clears = [e["data"] for e in producer.envelopes if e["type"] == "artifacts.event"]
    assert [(c["event"], c["agent"], c["artifact"]) for c in clears] == [
        ("agent_image", "news", None)]


# --- App data (docs/design/39) ----------------------------------------------------
# The housekeeping the appdata modules define, run by the dispatcher: daily,
# each active App's retention and orphaned uploads, then old receipts; hourly,
# expired staging sets and long-expired tool-call credential rows.

async def _app_data_fixture(sf):
    from agentplatform.appdata import artifacts as app_artifacts
    from agentplatform.appdata.access import Caller
    from agentplatform.appdata.records import create_record

    from .test_appdata_artifacts import docs, make_app
    pai = Caller("agent:pai")
    ctx = await make_app(sf, [docs(retention={"max_records": 1})])
    for title in ("old", "new"):
        async with sf() as s:
            await create_record(s, ctx, pai, "docs", {"title": title})
    async with sf() as s:
        orphan = await app_artifacts.upload(s, ctx, pai, "docs", "file", b"x" * 10,
                                            name="o.bin")
    return ctx, orphan.id


async def _titles(sf, app_id):
    from agentplatform.appdata.models import AppDataRecord
    async with sf() as s:
        return sorted(r.doc.get("title") for r in (await s.execute(
            select(AppDataRecord).where(AppDataRecord.app_id == app_id))).scalars())


async def test_app_data_pruner_runs_retention_and_the_sweep_for_active_apps(sf):
    from agentplatform.appdata.models import AppDataApp
    from agentplatform.pruning import AppDataPruner

    from .test_appdata_artifacts import gone
    active, orphan = await _app_data_fixture(sf)
    retired, kept_orphan = await _app_data_fixture(sf)
    async with sf() as s:
        (await s.get(AppDataApp, retired.app_id)).status = "retired"
        await s.commit()
    out = await AppDataPruner(sf).prune_apps_once(now=utcnow() + timedelta(hours=25))
    assert out["apps"] == 1 and out["failed"] == []
    assert await _titles(sf, active.app_id) == ["new"]
    assert await gone(sf, orphan)
    # A retired App is read-only; nothing prunes it.
    assert await _titles(sf, retired.app_id) == ["new", "old"]
    assert not await gone(sf, kept_orphan)


async def test_app_data_pruner_drops_old_receipts_record_writes_included(sf):
    from agentplatform.appdata.models import AppDataBuildOp
    from agentplatform.pruning import AppDataPruner
    now = utcnow()
    async with sf() as s:
        for op, age in [("publish", 91), ("record_create", 91), ("record_delete", 120),
                        ("record_update", 89), ("draft", 1)]:
            s.add(AppDataBuildOp(principal="agent:pai", request_id=f"{op}-{age}", op=op,
                                 args_hash="h", created_at=now - timedelta(days=age)))
        await s.commit()
    out = await AppDataPruner(sf).prune_apps_once(now=now)
    assert out["build_ops"] == 3
    async with sf() as s:
        left = sorted((await s.execute(select(AppDataBuildOp.op))).scalars())
    assert left == ["draft", "record_update"]


async def test_one_app_failing_to_prune_doesnt_stop_the_rest(sf, monkeypatch):
    from agentplatform.appdata import retention
    from agentplatform.pruning import AppDataPruner
    first, _ = await _app_data_fixture(sf)
    second, _ = await _app_data_fixture(sf)
    broken = min(first.app_id, second.app_id)
    healthy = max(first.app_id, second.app_id)
    original = retention.prune_app

    async def flaky(session, ctx, **kw):
        if ctx.app_id == broken:
            raise RuntimeError("boom")
        return await original(session, ctx, **kw)

    monkeypatch.setattr(retention, "prune_app", flaky)
    out = await AppDataPruner(sf).prune_apps_once()
    assert out == {"apps": 1, "failed": [broken], "build_ops": 0}
    assert await _titles(sf, healthy) == ["new"]
    assert await _titles(sf, broken) == ["new", "old"]


async def test_app_data_pruner_expires_staging_sets_and_old_credentials(sf):
    from agentplatform.appdata.models import (AppDataStagedRecord, AppDataStagingSet,
                                              AppDataToolCall)
    from agentplatform.pruning import AppDataPruner
    now = utcnow()
    async with sf() as s:
        s.add(AppDataStagingSet(id="stale", app_id="a1", creator="agent:pai",
                                expires_at=now - timedelta(minutes=1)))
        s.add(AppDataStagingSet(id="fresh", app_id="a1", creator="agent:pai",
                                expires_at=now + timedelta(hours=1)))
        s.add(AppDataStagedRecord(set_id="stale", seq=0, collection="c", mode="insert",
                                  doc={}))
        for jti, expired_ago in [("old", timedelta(days=2)), ("recent", timedelta(hours=1)),
                                 ("live", -timedelta(minutes=5))]:
            s.add(AppDataToolCall(jti=jti, call_id=jti, kind="tool_call",
                                  expires_at=now - expired_ago))
        await s.commit()
    out = await AppDataPruner(sf).prune_hourly_once(now=now)
    assert out == {"staging_sets": 1, "tool_calls": 1}
    async with sf() as s:
        assert (await s.get(AppDataStagingSet, "stale")).state == "expired"
        assert (await s.get(AppDataStagingSet, "fresh")).state == "open"
        assert (await s.execute(select(func.count()).select_from(AppDataStagedRecord))
                ).scalar_one() == 0
        assert sorted((await s.execute(select(AppDataToolCall.jti))).scalars()) == [
            "live", "recent"]


async def test_app_data_pruner_runs_daily_and_hourly_and_survives_a_failure(sf, monkeypatch):
    import asyncio

    from agentplatform import pruning
    calls, sleeps = [], []

    async def daily():
        calls.append("daily")
        raise RuntimeError("a bad night")

    async def hourly():
        calls.append("hourly")

    real_sleep = asyncio.sleep

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        await real_sleep(0.01)

    pruner = pruning.AppDataPruner(sf)
    monkeypatch.setattr(pruner, "prune_apps_once", daily)
    monkeypatch.setattr(pruner, "prune_hourly_once", hourly)
    monkeypatch.setattr(pruning.asyncio, "sleep", fake_sleep)
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(pruner.run_forever(), timeout=0.2)
    assert {86400, 3600} <= set(sleeps)
    # The daily pass failing didn't stop it being tried again.
    assert calls.count("daily") >= 2 and "hourly" in calls


def test_the_dispatcher_runs_the_app_data_pruner():
    import inspect

    from agentplatform import dispatcher_main
    src = inspect.getsource(dispatcher_main.main)
    assert "AppDataPruner(session_factory)" in src
    assert "app_data_pruner.run_forever()" in src
