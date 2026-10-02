"""R1b tool-view cache and materialization timings on a scratch Postgres.

Run after ``appdata_perf.py`` has created the app_data tables. This adds a
TCMS-shaped 19k-result App, then runs a real reviewed tool-view read and one
materialization refresh. The fake views-pool transport performs the tool's
scan and aggregation in-process; no network hop is included in these times.
"""
from __future__ import annotations

import argparse
import asyncio
import json as jsonlib
import sys
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/backend"))

from agentplatform import maintenance_mode, runjwt
from agentplatform.appdata import materialized, quotas, toolviews
from agentplatform.appdata.access import Caller
from agentplatform.appdata.definitions import ViewDef
from agentplatform.appdata.models import AppDataApp, AppDataDefinition
from agentplatform.appdata.records import load_app
from agentplatform.appdata.views import run_view, scan_view
from agentplatform.db import make_engine, make_session_factory
from agentplatform.toolregistry import ToolRegistry
from appdata_perf import OWNER, load, result_records


async def main(pg_url: str) -> dict:
    engine = make_engine(pg_url)
    sf = make_session_factory(engine)
    app_id = uuid.uuid4().hex
    result_collection = {
        "collection": "results", "write_mode": "immutable",
        "fields": {
            "field": {"type": "string", "required": True},
            "run": {"type": "string", "required": True},
            "ref": {"type": "string", "required": True},
            "status": {"type": "enum", "values": ["passed", "failed", "skipped", "error"]},
            "duration": {"type": "number", "min": 0},
            "finished_at": {"type": "datetime", "required": True},
        },
        "indexed": ["field", "run", "finished_at"],
    }
    tool = {"tool": "app_summary", "roles": {
        "source": {"collection": "results", "verbs": ["read"]}}}
    base_view = {"tool": "app_summary", "action": "counts", "sources": ["source"],
                 "params": {"field": {"type": "string", "default": "status"}}}
    materialized_view = {**base_view, "view": "status_materialized",
                         "materialize": {"every": "5m", "domain": "field"}}
    cached_view = {**base_view, "view": "status_live"}
    async with sf() as s:
        s.add(AppDataApp(id=app_id, name=f"r1b_perf_{app_id[:10]}", owner_kind="agent",
                         owner_id="perf", approved_version=1))
        for kind, name, body in (
            ("collection", "results", result_collection),
            ("tool", "app_summary", tool),
            ("view", "status_materialized", materialized_view),
            ("view", "status_live", cached_view),
        ):
            s.add(AppDataDefinition(app_id=app_id, kind=kind, name=name,
                                    version=1, body=body, state="published", author="kyle"))
        await s.commit()
    end = datetime(2026, 9, 30, 21, tzinfo=timezone.utc)
    records = ({**row, "field": "status"}
               for row in result_records(38, 500, end))
    await load(sf, records, app_id, "results", "materialization")

    state = SimpleNamespace(
        tool_registry=ToolRegistry(ROOT / "tools"),
        settings=SimpleNamespace(
            tool_executor_views_url="http://scratch-views-pool",
            tool_executor_views_service_account="scratch-views",
            app_data_view_cache_max_bytes=8 * 1024 * 1024),
        _tool_call_keys=runjwt.generate_keypair(),
    )
    calls = 0

    class LocalViewsPool:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _path, *, json):
            nonlocal calls
            calls += 1
            assert json["tool"] == "app_summary"
            counts = Counter()
            async with sf() as s:
                ctx = await load_app(s, app_id)
                view = ViewDef.model_validate({
                    "view": "benchmark_scan", "collection": "results",
                    "fields": [json["args"]["field"]], "paging": True})
                async with quotas.scan_budget(s, ctx) as budget:
                    async for row in scan_view(s, ctx, OWNER.caller, view, {}, budget=budget):
                        counts[row["values"][json["args"]["field"]]] += 1
            rows = [{"value": value, "count": count}
                    for value, count in counts.most_common(20)]
            return httpx.Response(200, json={"ok": True,
                                              "output": jsonlib.dumps({"rows": rows})})

    async def allowed(_session):
        return True

    original_client = toolviews.httpx.AsyncClient
    original_allowed = maintenance_mode.materialization_allowed
    toolviews.httpx.AsyncClient = LocalViewsPool
    maintenance_mode.materialization_allowed = allowed
    try:
        async def read_live() -> float:
            started = time.perf_counter()
            async with sf() as s:
                ctx = await load_app(s, app_id)
                await run_view(s, ctx, Caller("agent:perf"), "status_live", {"field": "status"},
                               app_state=state)
            return round((time.perf_counter() - started) * 1000, 1)

        miss_ms = await read_live()
        assert calls == 1
        hit_ms = await read_live()
        assert calls == 1, "cache hit unexpectedly called the views pool"
        started = time.perf_counter()
        refreshed = await materialized.refresh_due(sf, state)
        refresh_ms = round((time.perf_counter() - started) * 1000, 1)
        assert refreshed == 1 and calls == 2
        async with sf() as s:
            ctx = await load_app(s, app_id)
            await run_view(s, ctx, Caller("agent:perf"), "status_materialized",
                           {"field": "status"}, app_state=state)
        return {"records": 19_000, "cache_miss_ms": miss_ms,
                "cache_hit_ms": hit_ms, "refresh_ms": refresh_ms,
                "refreshes": refreshed, "pool_calls": calls,
                "transport": "in-process scan and aggregation; no HTTP hop"}
    finally:
        toolviews.httpx.AsyncClient = original_client
        maintenance_mode.materialization_allowed = original_allowed
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--pg-url", required=True)
    arguments = parser.parse_args()
    print(jsonlib.dumps(asyncio.run(main(arguments.pg_url)), indent=2))
