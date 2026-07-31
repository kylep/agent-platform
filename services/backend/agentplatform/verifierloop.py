"""Secret-validation heartbeat (docs/design/10). Re-runs every verifiable
secret's probe/script on a fixed cadence and records the result in
SecretMeta.status, so `valid` can't go stale-green — a token that rotates or
expires between uses is caught within one cadence. This is what the readiness
evaluator, the Secrets page, and the Dashboard read. claude-credentials
(`verify: run`) is exempt: the recorder marks it from run outcomes."""
import asyncio
import logging

from agentplatform.db import SecretMeta

log = logging.getLogger("verifier")


class SecretVerifier:
    def __init__(self, registry, secret_store, session_factory,
                 interval_seconds: int = 600):
        self.registry, self.store, self.sf = registry, secret_store, session_factory
        self.interval = interval_seconds

    async def verify_one(self, name: str) -> str | None:
        """Verify one secret now and record the result. Returns the fresh
        status, or None when nothing can be checked: the secret isn't
        verifiable (`verify: run`, unknown) — the recorded status stands —
        or the check itself errored (infra problem, not the secret's;
        flapping the status would lie)."""
        from agentplatform.secretverify import verify_secret
        info = self.registry.get(name)
        if info is None or info.spec is None or not info.spec.verifiable:
            return None
        try:
            data = await self.store.get(name)
            if data is None:
                status, detail = "missing", "not set"
            else:
                r = await verify_secret(info, data)
                status, detail = r.status, r.detail
        except Exception:
            log.exception("verify pass failed for %s", name)
            return None
        async with self.sf() as s:
            meta = await s.get(SecretMeta, name) or SecretMeta(name=name)
            if meta.status != status:
                log.info("secret %s: %s -> %s (%s)", name, meta.status, status, detail)
            meta.status = status
            s.add(meta)
            await s.commit()
        return status

    async def exists(self, name: str) -> bool:
        return await self.store.exists(name)

    async def verify_all(self) -> dict[str, str]:
        """One heartbeat pass; returns {secret: status} for what it checked."""
        # The synced checkout changes underneath us (agents-sync pulls git).
        self.registry.reload()
        results: dict[str, str] = {}
        for info in self.registry.list():
            if info.spec is None or not info.spec.verifiable:
                continue
            status = await self.verify_one(info.name)
            if status is not None:
                results[info.name] = status
        return results

    async def run_forever(self) -> None:
        if self.interval <= 0:
            return
        while True:
            try:
                await self.verify_all()
            except Exception:
                log.exception("verifier heartbeat failed")
            await asyncio.sleep(self.interval)
