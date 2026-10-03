"""Transcript retention. Run rows (metadata, summary, metrics) are kept
forever; the bulky per-frame `run_transcript_events` are pruned once they pass
their agent's retention window (per-agent manifest override, else the platform
default). A retention of <= 0 keeps an agent's transcripts forever."""
import asyncio
import logging
from datetime import timedelta

from sqlalchemy import delete, or_, select

from agentplatform.db import (
    LiveIntent,
    LiveInvocation,
    LiveSnapshot,
    LoginSession,
    Run,
    TranscriptEvent,
    utcnow,
)

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
        await self.agents.reload()
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


class ReportPruner:
    """Report retention (docs/design/11): each report type declares
    `retention_days` in its report.yaml (0 = keep forever); instances whose
    date falls outside the window are deleted. Dates are ISO strings, so the
    cutoff compare is lexicographic."""

    def __init__(self, session_factory, registry):
        self.sf = session_factory
        self.registry = registry

    async def prune_once(self, now=None) -> int:
        from datetime import timedelta

        from agentplatform.db import Report
        now = now or utcnow()
        self.registry.reload()
        deleted = 0
        async with self.sf() as s:
            for info in self.registry.list():
                if not info.spec or info.spec.retention_days <= 0:
                    continue
                cutoff = (now - timedelta(days=info.spec.retention_days)).date().isoformat()
                res = await s.execute(delete(Report).where(
                    Report.type == info.name, Report.date < cutoff))
                deleted += res.rowcount or 0
            await s.commit()
        if deleted:
            log.info("pruned %d reports", deleted)
        return deleted

    async def run_forever(self, interval_seconds: int = 86400) -> None:
        while True:
            try:
                await self.prune_once()
            except Exception:
                log.exception("report prune failed")
            await asyncio.sleep(interval_seconds)


class ArtifactPruner:
    """Artifact retention (docs/design/23): a delete is `deleted_at`, so a
    `[[artifact:]]` card naming a gone artifact resolves to "deleted" rather
    than to nothing; this is what finally removes the row — and its bytes,
    which are a separate table with no cascade promised on every dialect —
    once it has been gone `artifacts_prune_days`. A window of <= 0 keeps the
    soft-deleted rows forever. Live rows are never touched."""

    def __init__(self, session_factory, settings, producer=None):
        self.sf = session_factory
        self.settings = settings
        self.producer = producer

    async def prune_once(self, now=None) -> int:
        """Hard-delete soft-deleted artifacts past the window. Returns the
        number of artifact rows deleted."""
        from agentplatform import artifact_store
        from agentplatform.db import Artifact, ArtifactBlob
        days = self.settings.artifacts_prune_days
        if days <= 0:
            return 0
        cutoff = (now or utcnow()) - timedelta(days=days)
        deleted = 0
        undressed: list[str] = []
        async with self.sf() as s:
            expired = (await s.execute(select(Artifact.id).where(
                Artifact.deleted_at.isnot(None), Artifact.deleted_at < cutoff))).scalars().all()
            for i in range(0, len(expired), _CHUNK):
                chunk = expired[i:i + _CHUNK]
                # The soft delete already cleared these faces; this catches a
                # row that was dressed by a path the store never saw.
                undressed += await artifact_store.unlink_agent_images(s, chunk)
                await s.execute(delete(ArtifactBlob).where(ArtifactBlob.artifact_id.in_(chunk)))
                res = await s.execute(delete(Artifact).where(Artifact.id.in_(chunk)))
                deleted += res.rowcount or 0
            await s.commit()
        await artifact_store.publish_face_clears(self.producer, undressed)
        if deleted:
            log.info("pruned %d artifacts deleted more than %d days ago", deleted, days)
        return deleted

    async def run_forever(self, interval_seconds: int = 86400) -> None:
        while True:
            try:
                await self.prune_once()
            except Exception:
                log.exception("artifact prune failed")
            await asyncio.sleep(interval_seconds)


class LiveDataPruner:
    """Physically remove expired private snapshots and short-lived call data."""

    def __init__(self, session_factory):
        self.sf = session_factory

    async def prune_once(self, now=None) -> int:
        now = now or utcnow()
        async with self.sf() as session:
            snapshots = await session.execute(delete(LiveSnapshot).where(or_(
                LiveSnapshot.expires_at <= now,
                LiveSnapshot.deleted_at.isnot(None))))
            intents = await session.execute(delete(LiveIntent).where(
                LiveIntent.expires_at < now - timedelta(days=30)))
            receipts = await session.execute(delete(LiveInvocation).where(
                LiveInvocation.created_at < now - timedelta(days=90)))
            await session.commit()
        count = sum(r.rowcount or 0 for r in (snapshots, intents, receipts))
        if count:
            log.info("pruned %d expired Live App records", count)
        return count

    async def run_forever(self, interval_seconds: int = 3600) -> None:
        while True:
            try:
                await self.prune_once()
            except Exception:
                log.exception("live data prune failed")
            await asyncio.sleep(interval_seconds)


class SessionPruner:
    """Browser sign-ins (docs/design/40): delete `login_sessions` rows that
    expired or were revoked more than a week ago. A dead row already refuses
    its cookie; the week only keeps recent sign-outs around to look at."""

    GRACE = timedelta(days=7)

    def __init__(self, session_factory):
        self.sf = session_factory

    async def prune_once(self, now=None) -> int:
        cutoff = (now or utcnow()) - self.GRACE
        async with self.sf() as s:
            res = await s.execute(delete(LoginSession).where(or_(
                LoginSession.expires_at < cutoff,
                LoginSession.revoked_at < cutoff)))
            await s.commit()
        count = res.rowcount or 0
        if count:
            log.info("pruned %d dead login sessions", count)
        return count

    async def run_forever(self, interval_seconds: int = 86400) -> None:
        await _every(self.prune_once, interval_seconds, "session prune")


class AppDataPruner:
    """App data housekeeping (docs/design/39). Daily: each active App's
    retention and its unreferenced artifact uploads (`retention.prune_app`),
    then builder and record-write receipts past their retention. Hourly:
    staging sets past their 24 hours, and tool-call credential rows a day
    past expiry. One App failing to prune is logged and the rest still run."""

    def __init__(self, session_factory):
        self.sf = session_factory

    async def prune_apps_once(self, now=None) -> dict:
        from agentplatform.appdata import quotas
        from agentplatform.appdata.models import AppDataApp
        from agentplatform.appdata.records import load_app
        from agentplatform.appdata.retention import prune_app
        now = now or utcnow()
        async with self.sf() as s:
            app_ids = (await s.execute(select(AppDataApp.id).where(
                AppDataApp.status == "active").order_by(AppDataApp.id))).scalars().all()
        pruned, failed = 0, []
        for app_id in app_ids:
            # A session per App: prune_app commits chunk by chunk, and a
            # failure mustn't leave the next App a broken transaction.
            async with self.sf() as s:
                try:
                    await prune_app(s, await load_app(s, app_id), now=now)
                    pruned += 1
                except Exception:
                    await s.rollback()
                    failed.append(app_id)
                    log.exception("app data prune failed for App %s", app_id)
        async with self.sf() as s:
            receipts = await quotas.prune_build_ops(s, now=now)
        if receipts or failed:
            log.info("app data prune: %d Apps, %d failed, %d receipts dropped",
                     pruned, len(failed), receipts)
        return {"apps": pruned, "failed": failed, "build_ops": receipts}

    async def prune_hourly_once(self, now=None) -> dict:
        from agentplatform.appdata import credentials
        from agentplatform.appdata.batch import prune_staging_sets
        now = now or utcnow()
        async with self.sf() as s:
            expired = await prune_staging_sets(s, now=now)
        async with self.sf() as s:
            calls = await credentials.prune(s, now=now)
            await s.commit()
        if expired or calls:
            log.info("app data sweep: %d staging sets expired, %d credential rows dropped",
                     expired, calls)
        return {"staging_sets": expired, "tool_calls": calls}

    async def run_forever(self, daily_seconds: int = 86400,
                          hourly_seconds: int = 3600) -> None:
        await asyncio.gather(_every(self.prune_apps_once, daily_seconds, "app data prune"),
                             _every(self.prune_hourly_once, hourly_seconds,
                                    "app data sweep"))


async def _every(fn, interval_seconds: int, label: str) -> None:
    while True:
        try:
            await fn()
        except Exception:
            log.exception("%s failed", label)
        await asyncio.sleep(interval_seconds)


async def sweep_orphaned_keys_forever(session_factory, interval_seconds: int = 900) -> None:
    """Containment + hygiene: revoke per-run API keys whose run already
    terminated but whose terminal-frame revocation never happened (crashed
    pod, lost frame), and drop long-revoked rows so the table doesn't grow
    ~dozens/day forever (a revoked hash has no audit value — secret access
    has its own audit trail)."""
    from datetime import timedelta

    from sqlalchemy import delete

    from agentplatform.apikeys import revoke_orphaned_run_keys
    from agentplatform.db import ApiKey, utcnow
    while True:
        try:
            async with session_factory() as s:
                n = await revoke_orphaned_run_keys(s)
                res = await s.execute(delete(ApiKey).where(
                    ApiKey.revoked_at.isnot(None),
                    ApiKey.revoked_at < utcnow() - timedelta(days=30)))
                await s.commit()
            if n or (res.rowcount or 0):
                log.info("api-key sweep: revoked %d orphaned, deleted %d long-revoked",
                         n, res.rowcount or 0)
        except Exception:
            log.exception("api-key sweep failed")
        await asyncio.sleep(interval_seconds)
