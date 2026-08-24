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
