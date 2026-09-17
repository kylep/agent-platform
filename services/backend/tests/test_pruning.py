from datetime import timedelta

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
