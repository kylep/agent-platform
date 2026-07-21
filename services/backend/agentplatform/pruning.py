"""Transcript retention. Run rows (metadata, summary, metrics) are kept
forever; the bulky per-frame `run_transcript_events` are pruned once they pass
their agent's retention window (per-agent manifest override, else the platform
default). A retention of <= 0 keeps an agent's transcripts forever."""
import asyncio
import logging
from datetime import timedelta

from sqlalchemy import delete, select

from agentplatform.db import Run, TranscriptEvent, utcnow

log = logging.getLogger("pruning")

_CHUNK = 500


class TranscriptPruner:
    def __init__(self, session_factory, agent_store, settings):
        self.sf = session_factory
        self.agents = agent_store
        self.settings = settings

    def retention_days(self, agent: str) -> int:
        """Effective retention for an agent: its manifest override if set, else
        the platform default."""
        info = self.agents.get(agent)
        if info and info.manifest and info.manifest.transcript_retention_days is not None:
            return info.manifest.transcript_retention_days
        return self.settings.transcript_retention_days

    async def prune_once(self, now=None) -> int:
        """Delete transcript events for runs past their agent's retention.
        Returns the number of event rows deleted."""
        now = now or utcnow()
        self.agents.reload()
        deleted = 0
        async with self.sf() as s:
            agents = (await s.execute(select(Run.agent).distinct())).scalars().all()
            for agent in agents:
                days = self.retention_days(agent)
                if days <= 0:
                    continue
                cutoff = now - timedelta(days=days)
                old_ids = (await s.execute(select(Run.id).where(
                    Run.agent == agent, Run.created_at < cutoff))).scalars().all()
                for i in range(0, len(old_ids), _CHUNK):
                    chunk = old_ids[i:i + _CHUNK]
                    res = await s.execute(delete(TranscriptEvent).where(
                        TranscriptEvent.run_id.in_(chunk)))
                    deleted += res.rowcount or 0
            await s.commit()
        if deleted:
            log.info("pruned %d transcript events", deleted)
        return deleted

    async def run_forever(self, interval_seconds: int = 86400) -> None:
        while True:
            try:
                await self.prune_once()
            except Exception:
                log.exception("prune_once failed")
            await asyncio.sleep(interval_seconds)
