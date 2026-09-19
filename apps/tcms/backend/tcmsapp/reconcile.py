"""The reconciler: what the `tcms` tool recorded, announced.

The tool writes rows and nothing else (it holds the DB secret and no Kafka, no
platform key). Every 30 s this loop finds `test_runs` with `published_at IS
NULL`, publishes one `app.tcms.run.recorded` envelope per run (the running
app's envelope shape), posts the one-line note into `#qa` with the app's own
key through the platform API (`/api/relay/notify`, a system row: the key is a
member of no room), and stamps `published_at` — so a run is
announced once however many pods or ticks see it. The room post is
best-effort: a room that refuses or an API that is down is logged, and the run
is still stamped, because re-publishing the envelope every tick until the
room answers would be the worse failure. The publish itself is not
best-effort: if Kafka is down the run stays unstamped and the next tick
retries.

Once a day the retention prune deletes runs older than `TCMS_RETENTION_DAYS`
with their results and coverage rows.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from datetime import datetime, timedelta, timezone

import httpx

from tcmsapp import schema
from tcmsapp.api import run_view
from tcmsapp.db import execute, query

log = logging.getLogger("tcms-reconcile")

TOPIC_RECORDED = "app.tcms.run.recorded"
TICK_SECONDS = 30
PRUNE_EVERY = timedelta(days=1)
DEFAULT_RETENTION_DAYS = 90
_HEX = re.compile(r"[0-9a-f]+")


def _envelope(type_: str, key: str, data: dict) -> bytes:
    return json.dumps({
        "type": type_, "schema_version": 1, "id": uuid.uuid4().hex,
        "ts": datetime.now(timezone.utc).isoformat(), "key": key,
        "source": "app-tcms", "data": data,
    }).encode()


def _hex_prefix(value, n: int) -> str:
    """The first `n` hex characters of a sha or run id, or nothing: both are
    machine-written, but they cross a trust boundary into the room's most
    trusted voice, so only what a sha looks like gets through."""
    m = _HEX.match(str(value or "").lower())
    return m.group(0)[:n] if m else ""


def _duration(seconds: float) -> str:
    total = round(seconds)
    m, s = divmod(total, 60)
    return f"{m} m {s} s" if m else f"{s} s"


def format_note(run: dict) -> str:
    """The `#qa` line: `🧪 test run 4f2e… on a1b2c3d · 912 pass · 2 fail · 1
    flaky · 4 m 12 s · [open](/apps/tcms/runs/…)`. Zero counts are left out
    except `pass`, so a clean run reads short."""
    t = run["totals"]
    parts = [f"{t['pass']} pass"]
    parts += [f"{t[k]} {k}" for k in ("fail", "skip", "flaky", "error") if t.get(k)]
    if run.get("unlinked"):
        parts.append(f"{run['unlinked']} unlinked")
    rid = _hex_prefix(run.get("run_id"), 8)
    sha = _hex_prefix(run.get("commit_sha"), 7)
    head = "🧪 test run" + (f" {rid}…" if rid else "") + (f" on {sha}" if sha else " on ?")
    return " · ".join([head, *parts, _duration(run["seconds"]),
                       f"[open](/apps/tcms/runs/{int(run['id'])})"])


def make_api_client() -> httpx.AsyncClient | None:
    """The platform API as the app key, or None until the key is provisioned
    (the running app's report writer makes the same call)."""
    token = os.environ.get("AP_API_TOKEN", "")
    base = os.environ.get("AP_API_URL", "")
    if not token or not base:
        log.warning("no AP_API_TOKEN/AP_API_URL — run notes will not reach #qa")
        return None
    return httpx.AsyncClient(base_url=base, timeout=20,
                             headers={"Authorization": f"Bearer {token}"})


class Reconciler:
    def __init__(self, sf, producer, api: httpx.AsyncClient | None, *,
                 channel: str = "qa", retention_days: int = DEFAULT_RETENTION_DAYS):
        self.sf = sf
        self.producer = producer
        self.api = api
        self.channel = channel
        self.retention_days = retention_days

    # --- the room -------------------------------------------------------------

    async def post_note(self, note: str) -> bool:
        """The line into `#qa` through `POST /api/relay/notify`: a system row
        the app key may write without being a member of the room. The channel
        goes by name; the API resolves it and 404s a room it does not have."""
        if self.api is None:
            return False
        try:
            r = await self.api.post("/api/relay/notify",
                                    json={"channel": self.channel, "text": note})
            r.raise_for_status()
            return True
        except httpx.HTTPError as e:
            log.warning("posting to #%s failed: %s", self.channel, e)
            return False

    # --- the tick ---------------------------------------------------------------

    async def tick(self) -> int:
        """Announce every unpublished run; returns how many were stamped."""
        async with self.sf() as s:
            pending = [run_view(r) for r in await query(s, schema.UNPUBLISHED_RUNS)]
        done = 0
        for run in pending:
            data = {"test_run_id": run["id"], "commit_sha": run["commit_sha"],
                    "branch": run["branch"], "run_id": run["run_id"], "agent": run["agent"],
                    "totals": run["totals"], "seconds": run["seconds"],
                    "verify_ok": run["verify_ok"], "unlinked": run["unlinked"]}
            try:
                await self.producer.send_and_wait(
                    TOPIC_RECORDED, _envelope("tcms.run.recorded", str(run["id"]), data),
                    key=str(run["id"]).encode())
            except Exception:
                log.exception("publishing run %s failed; will retry", run["id"])
                continue
            if not await self.post_note(format_note(run)):
                log.info("run %s announced on Kafka only", run["id"])
            async with self.sf() as s:
                await execute(s, schema.MARK_PUBLISHED,
                              (datetime.now(timezone.utc), run["id"]))
                await s.commit()
            done += 1
        return done

    async def prune(self, now: datetime | None = None) -> int:
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=self.retention_days)
        async with self.sf() as s:
            await execute(s, schema.PRUNE_RESULTS, (cutoff,))
            await execute(s, schema.PRUNE_COVERAGE, (cutoff,))
            r = await execute(s, schema.PRUNE_RUNS, (cutoff,))
            await s.commit()
        n = r.rowcount if r.rowcount is not None and r.rowcount >= 0 else 0
        if n:
            log.info("pruned %d test runs older than %d days", n, self.retention_days)
        return n

    async def run_forever(self, bootstrap: str) -> None:
        from aiokafka import AIOKafkaProducer
        last_prune = datetime.min.replace(tzinfo=timezone.utc)
        while True:
            try:
                self.producer = AIOKafkaProducer(bootstrap_servers=bootstrap)
                await self.producer.start()
                try:
                    while True:
                        await self.tick()
                        if datetime.now(timezone.utc) - last_prune >= PRUNE_EVERY:
                            await self.prune()
                            last_prune = datetime.now(timezone.utc)
                        await asyncio.sleep(TICK_SECONDS)
                finally:
                    await self.producer.stop()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("reconciler crashed; restarting in 10s")
                await asyncio.sleep(10)
