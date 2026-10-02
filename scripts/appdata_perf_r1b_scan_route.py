"""Time a full-million scan through the R1b API route on scratch Postgres.

This calls the FastAPI route handler directly, with its credential gate
replaced by a known scratch claim. The route's binding checks, persisted
per-execution cap, App scan lease and page queries all run unchanged.
Run after ``appdata_perf.py --prefix r1b_full`` has loaded the data.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

from fastapi import Request
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/backend"))

from agentplatform import runjwt
from agentplatform.api import app_data_scan as route
from agentplatform.appdata import credentials
from agentplatform.appdata.models import (
    AppDataApp,
    AppDataDefinition,
    AppDataToolCall,
)
from agentplatform.db import make_engine, make_session_factory


async def main(pg_url: str, prefix: str) -> dict:
    engine = make_engine(pg_url)
    sf = make_session_factory(engine)
    async with sf() as s:
        app = (await s.execute(select(AppDataApp).where(
            AppDataApp.name == f"{prefix}_tcms"))).scalar_one()
        app_id = app.id
        # The A12 App predates tool bindings. Add one only in this disposable
        # database; all of its original read views remain at version 1.
        version = (app.approved_version or 0) + 1
        s.add(AppDataDefinition(app_id=app_id, kind="tool", name="app_summary",
                                version=version, state="published", author="kyle",
                                body={"tool": "app_summary", "roles": {
                                    "source": {"collection": "results", "verbs": ["read"]}}}))
        app.approved_version = version
        app.authority_generation += 1
        await s.commit()
    keys = runjwt.generate_keypair()
    _, claim = credentials.mint_view_exec(
        keys["private_key"], call_id=uuid.uuid4().hex, principal="agent:perf",
        app_id=app_id, tool="app_summary", action="counts",
        sources={"source": "results"}, cnf_sa="scratch-views", ttl_seconds=180)
    async with sf() as s:
        await credentials.record(s, claim)
        await s.commit()
    state = SimpleNamespace(session_factory=sf, settings=SimpleNamespace(
        app_data_scan_max_seconds=60, app_data_scan_max_rows=1_000_000))
    request = Request({"type": "http", "app": SimpleNamespace(state=state)})
    original_gate = route._view_executor

    async def scratch_gate(_request):
        return claim

    route._view_executor = scratch_gate
    try:
        cursor, pages, rows = None, 0, 0
        start = time.perf_counter()
        while True:
            body = route.ScanIn(
                role="source", fields=["run"], limit=1000, cursor=cursor,
                filter=[{"field": "finished_at", "op": "lt",
                         "value": "2026-09-30T21:00:00+00:00"}],
                sort=[{"field": "finished_at", "dir": "asc"}])
            result = await route.app_data_scan(request, body)
            pages += 1
            rows += len(result["rows"])
            cursor = result["next_cursor"]
            if cursor is None:
                break
        elapsed = round(time.perf_counter() - start, 2)
        async with sf() as s:
            call = await s.get(AppDataToolCall, claim["jti"])
            reserved = call.scan_rows
        assert rows == reserved == 1_000_000, (rows, reserved)
        return {"rows": rows, "pages": pages, "seconds": elapsed,
                "max_seconds": 60, "reserved_rows": reserved,
                "auth": "known scratch claim; route credential gate bypassed"}
    finally:
        route._view_executor = original_gate
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--pg-url", required=True)
    parser.add_argument("--prefix", default="r1b_full")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(main(args.pg_url, args.prefix)), indent=2))
