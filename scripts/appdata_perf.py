"""The Release 1a performance gate for App data (design 39, "Batch writes").

Loads App-shaped volumes into a scratch Postgres through the real engine
(`appdata/lifecycle.py` builds and publishes the Apps, `appdata/batch.py`'s
batch jobs load the records) and times the read and write paths the design
names:

  a  stockmarket chart: one symbol's bars over a year, sorted by day
  b  watchlist: the latest bar of 20 symbols
  c  TCMS: one run's results filtered by status
  d  news: a `contains` search over 10k items
  e  backtest: a 140k-record staged batch-job commit
  f  materialization: an `app_data scan` of one week of results
  g  10 concurrent writers into one collection

Reads go through `published_view`, the function the API routes call, so the
App lookup and `load_app` are in every number; HTTP and the broker aren't.

    DOCKER_HOST=unix://$HOME/.rd/docker.sock docker run -d --name ap-perf-pg \\
        --memory=2g --cpus=2 -p 127.0.0.1:55432:5432 -e POSTGRES_PASSWORD=perf \\
        -e POSTGRES_DB=perf postgres:16-alpine -c shared_buffers=512MB
    cd services/backend && PYTHONPATH=. .venv/bin/python ../../scripts/appdata_perf.py \\
        --pg-url postgresql+asyncpg://postgres:perf@127.0.0.1:55432/perf

`--scale 0.01` runs the whole thing small, as a smoke test. `--skip-load`
reuses the Apps a previous run loaded (they're found by name). The database
should be a scratch one: the first load drops and recreates the app_data
tables.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import sys
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/backend"))

from sqlalchemy import select, text  # noqa: E402

from agentplatform.api.app_data import published_view  # noqa: E402
from agentplatform.appdata import batch as B  # noqa: E402
from agentplatform.appdata import lifecycle as L  # noqa: E402
from agentplatform.appdata import quotas  # noqa: E402
from agentplatform.appdata.access import RecordError  # noqa: E402
from agentplatform.appdata.models import AppDataApp  # noqa: E402
from agentplatform.appdata.records import create_record, load_app  # noqa: E402
from agentplatform.appdata.views import scan_view  # noqa: E402
from agentplatform.db import Base, make_engine, make_session_factory  # noqa: E402

OWNER = L.Actor("agent:perf")
CALLER = OWNER.caller
APP_TABLES = [t for name, t in Base.metadata.tables.items() if name.startswith("app_data_")]
STAGE_CALL = 5_000          # records per `batch_job stage` call (the per-call cap)
LOAD_SET = 100_000          # records per staging set while loading

# --- the Apps -------------------------------------------------------------------------------

BARS = {
    "collection": "bars", "write_mode": "immutable",
    "fields": {"symbol": {"type": "string", "required": True, "max": 12},
               "day": {"type": "date", "required": True},
               "open": {"type": "number"}, "high": {"type": "number"},
               "low": {"type": "number"}, "close": {"type": "number", "required": True},
               "volume": {"type": "int", "min": 0}},
    "indexed": ["symbol", "day"],
    "rules": [{"kind": "unique", "fields": ["symbol", "day"]}],
}
BACKTEST = {
    "collection": "backtest_events", "write_mode": "immutable",
    "fields": {"experiment": {"type": "string", "required": True, "max": 64},
               "strategy": {"type": "string", "required": True, "max": 64},
               "day": {"type": "date", "required": True},
               "equity": {"type": "number"}, "position": {"type": "number"},
               "signal": {"type": "enum", "values": ["buy", "sell", "hold"]}},
    "indexed": ["experiment", "strategy", "day"],
}
STOCK_VIEWS = [
    {"view": "chart", "collection": "bars",
     "params": {"symbol": {"type": "string", "required": True}},
     "filter": [{"field": "symbol", "op": "eq", "value": {"param": "symbol"}},
                {"field": "day", "op": "within_last", "value": "12m", "anchor": "max(day)"}],
     "sort": [{"field": "day", "dir": "asc"}], "limit": 200, "paging": True},
    {"view": "latest_bar", "collection": "bars",
     "params": {"symbol": {"type": "string", "required": True}},
     "filter": [{"field": "symbol", "op": "eq", "value": {"param": "symbol"}}],
     "sort": [{"field": "day", "dir": "desc"}], "limit": 1},
    {"view": "series", "collection": "backtest_events",
     "params": {"experiment": {"type": "string", "required": True},
                "strategy": {"type": "string", "required": True}},
     "filter": [{"field": "experiment", "op": "eq", "value": {"param": "experiment"}},
                {"field": "strategy", "op": "eq", "value": {"param": "strategy"}}],
     "sort": [{"field": "day", "dir": "asc"}], "limit": 200, "paging": True},
]

STATUSES = ["passed", "failed", "skipped", "error"]
RESULTS = {
    "collection": "results", "write_mode": "immutable",
    "fields": {"run": {"type": "string", "required": True, "max": 32},
               "ref": {"type": "string", "required": True, "max": 32},
               "status": {"type": "enum", "values": STATUSES, "required": True},
               "duration": {"type": "number", "min": 0},
               "finished_at": {"type": "datetime", "required": True}},
    "indexed": ["run", "ref", "finished_at"],
    "rules": [{"kind": "unique", "fields": ["run", "ref"]}],
}
TCMS_VIEWS = [
    {"view": "run_results", "collection": "results",
     "params": {"run": {"type": "string", "required": True},
                "status": {"type": "string", "required": True}},
     "filter": [{"field": "run", "op": "eq", "value": {"param": "run"}},
                {"field": "status", "op": "eq", "value": {"param": "status"}}],
     "sort": [{"field": "ref", "dir": "asc"}], "limit": 200, "paging": True},
    {"view": "recent_failures", "collection": "results",
     "params": {"status": {"type": "string", "required": True}},
     "filter": [{"field": "status", "op": "eq", "value": {"param": "status"}}],
     "sort": [{"field": "finished_at", "dir": "desc"}], "limit": 50},
    {"view": "ref_history", "collection": "results",
     "params": {"ref": {"type": "string", "required": True}},
     "filter": [{"field": "ref", "op": "eq", "value": {"param": "ref"}}],
     "sort": [{"field": "finished_at", "dir": "desc"}], "limit": 50},
    {"view": "latest_with_status", "collection": "results",
     "params": {"status": {"type": "string", "required": True}},
     "filter": [{"field": "status", "op": "eq", "value": {"param": "status"}}],
     "limit": 50},
    {"view": "window", "collection": "results",
     "params": {"start": {"type": "datetime", "required": True},
                "end": {"type": "datetime", "required": True}},
     "filter": [{"field": "finished_at", "op": "gte", "value": {"param": "start"}},
                {"field": "finished_at", "op": "lt", "value": {"param": "end"}}],
     "sort": [{"field": "finished_at", "dir": "asc"}], "limit": 200},
]

NEWS = {
    "collection": "items",
    "fields": {"title": {"type": "string", "required": True, "max": 300},
               "summary": {"type": "text"},
               "source": {"type": "string", "max": 64},
               "published": {"type": "datetime", "required": True}},
    "indexed": ["source", "published"],
}
NEWS_VIEWS = [
    {"view": "search", "collection": "items",
     "params": {"q": {"type": "string", "required": True}},
     "filter": [{"field": "title", "op": "contains", "value": {"param": "q"}}],
     "sort": [{"field": "published", "dir": "desc"}], "limit": 50},
    {"view": "search_summary", "collection": "items",
     "params": {"q": {"type": "string", "required": True}},
     "filter": [{"field": "summary", "op": "contains", "value": {"param": "q"}}],
     "sort": [{"field": "published", "dir": "desc"}], "limit": 50},
]

# --- data -------------------------------------------------------------------------------------


def tickers(n: int) -> list[str]:
    rng = random.Random(7)
    out: set[str] = set()
    while len(out) < n:
        out.add("".join(rng.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
                        for _ in range(rng.choice((3, 4, 4, 5)))))
    return sorted(out)


def weekdays(n: int, end: date = date(2026, 9, 30)) -> list[date]:
    out, day = [], end
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day)
        day -= timedelta(days=1)
    return out[::-1]


def bar_records(symbols: list[str], days: list[date]):
    """Day-major, the order daily ingestion writes them: one symbol's bars end
    up spread over the heap, the realistic (and slower) layout for a chart."""
    rng = random.Random(11)
    price = {s: rng.uniform(5, 500) for s in symbols}
    for day in days:
        iso = day.isoformat()
        for s in symbols:
            p = price[s] = max(1.0, price[s] * (1 + rng.gauss(0, 0.02)))
            yield {"symbol": s, "day": iso, "open": round(p * 0.995, 4),
                   "high": round(p * 1.01, 4), "low": round(p * 0.99, 4),
                   "close": round(p, 4), "volume": rng.randint(10_000, 50_000_000)}


def result_records(runs: int, per_run: int, end: datetime):
    """Run-major, as TCMS commits a run. Runs are spread evenly over a year."""
    rng = random.Random(13)
    step = timedelta(days=365) / max(runs, 1)
    for r in range(runs):
        finished = end - step * (runs - r)
        run = f"run-{r:06d}"
        for t in range(per_run):
            roll = rng.random()
            status = ("passed" if roll < 0.90 else "failed" if roll < 0.96
                      else "skipped" if roll < 0.99 else "error")
            yield {"run": run, "ref": f"TC-{t:05d}", "status": status,
                   "duration": round(rng.expovariate(1 / 2.5), 3),
                   "finished_at": (finished + timedelta(seconds=t)).isoformat()}


WORDS = ("market rally earnings guidance merger chip supply rate cut inflation outlook "
         "nvidia apple bank energy oil storm election court ruling strike launch quarterly "
         "record growth slump forecast startup funding layoffs ai model cloud data "
         "breach climate policy trade tariff deal ceo resigns shares jump fall").split()


def news_records(n: int, end: datetime):
    rng = random.Random(17)
    sources = [f"source-{i}" for i in range(40)]
    for i in range(n):
        title = " ".join(rng.choice(WORDS) for _ in range(rng.randint(6, 12))).capitalize()
        summary = " ".join(rng.choice(WORDS) for _ in range(rng.randint(40, 80)))
        yield {"title": title, "summary": summary, "source": rng.choice(sources),
               "published": (end - timedelta(minutes=53 * (n - i))).isoformat()}


def backtest_records(n: int, days: list[date], experiment: str):
    rng = random.Random(19)
    strategies = (n + len(days) - 1) // len(days)
    made = 0
    for k in range(strategies):
        equity = 100_000.0
        for day in days:
            if made == n:
                return
            equity *= 1 + rng.gauss(0.0003, 0.01)
            yield {"experiment": experiment, "strategy": f"strat-{k:03d}",
                   "day": day.isoformat(), "equity": round(equity, 2),
                   "position": round(rng.uniform(-1, 1), 3),
                   "signal": rng.choice(("buy", "sell", "hold"))}
            made += 1


def chunks(it, size: int):
    buf = []
    for item in it:
        buf.append(item)
        if len(buf) == size:
            yield buf
            buf = []
    if buf:
        yield buf

# --- helpers -----------------------------------------------------------------------------------


def rid() -> str:
    return uuid.uuid4().hex


def pct(values: list[float], p: float) -> float:
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, round(p / 100 * (len(ordered) - 1))))
    return ordered[k]


def summary(ms: list[float]) -> dict:
    return {"n": len(ms), "p50_ms": round(statistics.median(ms), 1),
            "p95_ms": round(pct(ms, 95), 1), "max_ms": round(max(ms), 1)}


async def build_app(sf, name: str, collections: list[dict], views: list[dict]) -> str:
    async with sf() as s:
        app_id = (await L.create(s, OWNER, request_id=rid(), name=name))["app_id"]
    for kind, bodies in (("collection", collections), ("view", views)):
        for body in bodies:
            async with sf() as s:
                await L.draft(s, OWNER, app_id, request_id=rid(), kind=kind, definition=body)
    async with sf() as s:
        await L.publish(s, OWNER, app_id, request_id=rid(), expected_approved_version=None)
    return app_id


async def ensure_views(sf, app_id: str, views: list[dict]) -> None:
    """Publish the views a reused App lacks (the gate grew since it loaded)."""
    async with sf() as s:
        ctx = await load_app(s, app_id)
        approved = (await s.get(AppDataApp, app_id)).approved_version
    missing = [v for v in views if v["view"] not in ctx.bundle.views]
    for body in missing:
        async with sf() as s:
            await L.draft(s, OWNER, app_id, request_id=rid(), kind="view", definition=body)
    if missing:
        async with sf() as s:
            await L.publish(s, OWNER, app_id, request_id=rid(),
                            expected_approved_version=approved)


class Results(dict):
    """Prints each result as it lands, so a crash keeps what was measured."""

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        print(f"{key}: {json.dumps(value)}", flush=True)


async def app_id_by_name(sf, name: str) -> str:
    async with sf() as s:
        return (await s.execute(select(AppDataApp.id).where(AppDataApp.name == name))
                ).scalar_one()


async def raise_quotas(sf, app_ids: list[str]) -> None:
    """The migrated Apps get measured quotas (design 39, "Storage"); this
    gate loads more than the defaults allow in an hour."""
    big = {"max_records": 10_000_000, "max_bytes": 8 * 1024 ** 3,
           "writes_per_hour": 50_000_000, "scan_rows_per_hour": 100_000_000}
    async with sf() as s:
        await quotas.set_quota(s, "owner", OWNER.principal, big, set_by="kyle", is_kyle=True)
        for app_id in app_ids:
            await quotas.set_quota(s, "app", app_id, big, set_by="kyle", is_kyle=True)


async def stage_and_commit(sf, app_id: str, collection: str, records, *,
                           mode: str = "insert") -> dict:
    """One batch job: open, stage in 5,000-record calls, commit. Returns the
    stage and commit times."""
    call = rid()
    async with sf() as s:
        ctx = await load_app(s, app_id)
        st = await B.open_staging_set(s, ctx, CALLER, [collection], call_id=call)
    t0 = time.perf_counter()
    staged = 0
    for batch in chunks(records, STAGE_CALL):
        async with sf() as s:
            await B.stage(s, ctx, CALLER, st["set_id"], collection, batch, mode=mode,
                          call_id=call)
        staged += len(batch)
    t1 = time.perf_counter()
    async with sf() as s:
        out = await B.commit_staging_set(s, CALLER, st["set_id"], call_id=call)
    t2 = time.perf_counter()
    assert out["inserted"] == staged, out
    return {"records": staged, "stage_s": round(t1 - t0, 2), "commit_s": round(t2 - t1, 2)}


async def load(sf, records, app_id: str, collection: str, label: str) -> dict:
    total = {"records": 0, "stage_s": 0.0, "commit_s": 0.0, "sets": 0}
    for part in chunks(records, LOAD_SET):
        out = await stage_and_commit(sf, app_id, collection, part)
        for k in ("records", "stage_s", "commit_s"):
            total[k] += out[k]
        total["sets"] += 1
        rate = out["records"] / max(out["commit_s"], 1e-9)
        print(f"  {label}: {total['records']:>9,} loaded "
              f"(set: stage {out['stage_s']}s, commit {out['commit_s']}s, "
              f"{rate:,.0f} rec/s)", flush=True)
    total["stage_s"] = round(total["stage_s"], 1)
    total["commit_s"] = round(total["commit_s"], 1)
    return total


async def view(sf, app: str, name: str, params: dict, cursor=None) -> dict:
    async with sf() as s:
        return await published_view(s, CALLER, app, name, params, cursor=cursor)


async def timed(fn, runs: int, warm: int = 3) -> tuple[dict, object]:
    for _ in range(warm):
        last = await fn(0)
    ms = []
    for i in range(runs):
        t = time.perf_counter()
        last = await fn(i)
        ms.append((time.perf_counter() - t) * 1000)
    return summary(ms), last


async def explain(sf, app_id: str, view_name: str, params: dict) -> str:
    """EXPLAIN ANALYZE of the first page a view runs, as views.py builds it."""
    from sqlalchemy.dialects import postgresql

    from agentplatform.appdata.records import R
    from agentplatform.appdata.views import _Query, _sort_keys, order_by, resolve_params
    async with sf() as s:
        ctx = await load_app(s, app_id)
        v = ctx.bundle.views[view_name]
        q = _Query(ctx, CALLER, v, resolve_params(v, params), datetime.now(timezone.utc))
        stmt = (select(R.id).where(*await q.conditions(s))
                .order_by(*order_by(q.c, _sort_keys(v))).limit(v.limit + 1))
        sql = str(stmt.compile(dialect=postgresql.dialect(),
                               compile_kwargs={"literal_binds": True}))
        rows = (await s.execute(text("EXPLAIN (ANALYZE, BUFFERS) " + sql))).all()
    return "\n".join(r[0] for r in rows)

# --- the gate ----------------------------------------------------------------------------------


async def main(args) -> dict:
    engine = make_engine(args.pg_url)
    sf = make_session_factory(engine)
    n_symbols = max(20, round(400 * args.scale))
    n_days = max(260, round(2_500 * min(1.0, args.scale * 10)))
    n_runs = max(10, round(2_000 * args.scale))
    per_run = 500
    n_news = max(500, round(10_000 * min(1.0, args.scale * 10)))
    n_backtest = max(5_000, round(140_000 * min(1.0, args.scale * 10)))
    symbols, days = tickers(n_symbols), weekdays(n_days)
    end = datetime(2026, 9, 30, 21, tzinfo=timezone.utc)
    out: dict = Results({"scale": args.scale, "volumes": {
        "bars": n_symbols * n_days, "results": n_runs * per_run, "news": n_news,
        "backtest_commit": n_backtest}})
    names = {k: f"{args.prefix}_{k}" for k in ("stockmarket", "tcms", "news")}

    if not args.skip_load:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sc: Base.metadata.drop_all(sc, tables=APP_TABLES))
            await conn.run_sync(lambda sc: Base.metadata.create_all(sc, tables=APP_TABLES))
        stock = await build_app(sf, names["stockmarket"], [BARS, BACKTEST], STOCK_VIEWS)
        tcms = await build_app(sf, names["tcms"], [RESULTS], TCMS_VIEWS)
        news = await build_app(sf, names["news"], [NEWS], NEWS_VIEWS)
        await raise_quotas(sf, [stock, tcms, news])
        print(f"loading {n_symbols * n_days:,} bars, {n_runs * per_run:,} results, "
              f"{n_news:,} news items", flush=True)
        t = time.perf_counter()
        out["load"] = {
            "bars": await load(sf, bar_records(symbols, days), stock, "bars", "bars"),
            "results": await load(sf, result_records(n_runs, per_run, end), tcms, "results",
                                  "results"),
            "news": await load(sf, news_records(n_news, end), news, "items", "news"),
        }
        out["load"]["wall_s"] = round(time.perf_counter() - t, 1)
        # What autovacuum would have done by the time anyone reads: visibility
        # map and statistics.
        async with engine.connect() as conn:
            conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.execute(text("VACUUM ANALYZE app_data_records"))
    else:
        stock = await app_id_by_name(sf, names["stockmarket"])
        tcms = await app_id_by_name(sf, names["tcms"])
        news = await app_id_by_name(sf, names["news"])
        await raise_quotas(sf, [stock, tcms, news])
        for app_id, views in ((stock, STOCK_VIEWS), (tcms, TCMS_VIEWS), (news, NEWS_VIEWS)):
            await ensure_views(sf, app_id, views)
    rng = random.Random(23)
    R = args.runs

    # (a) one symbol's chart: every page of a 12-month window, sorted by day.
    async def chart(i):
        sym = symbols[rng.randrange(len(symbols))]
        rows, cursor = [], None
        while True:
            page = await view(sf, stock, "chart", {"symbol": sym}, cursor)
            rows += page["rows"]
            cursor = page["next_cursor"]
            if cursor is None:
                return rows
    out["a_chart_year"], rows = await timed(chart, R)
    out["a_chart_year"]["rows"] = len(rows)

    async def chart_first(i):
        return await view(sf, stock, "chart", {"symbol": symbols[rng.randrange(len(symbols))]})
    out["a_chart_page1"], _ = await timed(chart_first, R)

    # (b) a 20-symbol watchlist: one latest-bar read per symbol.
    async def watchlist(i):
        picks = rng.sample(symbols, 20)
        return [await view(sf, stock, "latest_bar", {"symbol": s}) for s in picks]
    out["b_watchlist_20"], _ = await timed(watchlist, R)

    async def watchlist_parallel(i):
        picks = rng.sample(symbols, 20)
        return await asyncio.gather(*(view(sf, stock, "latest_bar", {"symbol": s})
                                      for s in picks))
    out["b_watchlist_20_parallel"], _ = await timed(watchlist_parallel, R)

    # (c) one run's results with one status.
    async def run_failed(i):
        return await view(sf, tcms, "run_results",
                          {"run": f"run-{rng.randrange(n_runs):06d}", "status": "failed"})
    out["c_run_by_status"], page = await timed(run_failed, R)
    out["c_run_by_status"]["rows"] = len(page["rows"])

    async def recent_failures(i):
        return await view(sf, tcms, "recent_failures",
                          {"status": rng.choice(["failed", "error"])})
    out["c2_recent_failures_any_run"], _ = await timed(recent_failures, R)

    async def ref_history(i):
        return await view(sf, tcms, "ref_history", {"ref": f"TC-{rng.randrange(per_run):05d}"})
    out["c3_ref_history_across_runs"], _ = await timed(ref_history, R)

    async def rare_status(i):
        return await view(sf, tcms, "latest_with_status", {"status": "error"})
    out["c4_rare_status_default_sort"], _ = await timed(rare_status, R)

    # (d) news search.
    async def search(i):
        return await view(sf, news, "search", {"q": rng.choice(WORDS)})
    out["d_contains_title"], _ = await timed(search, R)

    async def search_summary(i):
        return await view(sf, news, "search_summary",
                          {"q": " ".join(rng.sample(WORDS, 2))})
    out["d_contains_summary"], _ = await timed(search_summary, R)

    # (e) a backtest-shaped 140k-record batch job, then reading one series.
    experiment = f"exp-{rid()[:8]}"
    out["e_backtest_commit"] = await stage_and_commit(
        sf, stock, "backtest_events", backtest_records(n_backtest, days, experiment))

    async def series(i):
        rows, cursor = [], None
        while True:
            page = await view(sf, stock, "series",
                              {"experiment": experiment,
                               "strategy": f"strat-{rng.randrange(n_backtest // n_days):03d}"},
                              cursor)
            rows += page["rows"]
            cursor = page["next_cursor"]
            if cursor is None:
                return rows
    out["e2_backtest_series_paged"], rows = await timed(series, min(R, 20), warm=1)
    out["e2_backtest_series_paged"]["rows"] = len(rows)

    # (f) materialization: scan one week of results under a scan budget.
    async def scan_window(start: datetime, end_: datetime) -> dict:
        async with sf() as s:
            ctx = await load_app(s, tcms)
            v = ctx.bundle.views["window"]
            t = time.perf_counter()
            n, refused = 0, None
            try:
                async with quotas.scan_budget(s, ctx) as budget:
                    async with sf() as rs:
                        async for _ in scan_view(rs, ctx, CALLER, v,
                                                 {"start": start.isoformat(),
                                                  "end": end_.isoformat()}, budget=budget):
                            n += 1
            except RecordError as exc:
                # A breached budget is a result: how far it got, and why it stopped.
                refused = exc.code
            secs = time.perf_counter() - t
        return {"rows": n, "seconds": round(secs, 2), "rows_per_s": round(n / secs),
                "refused": refused}
    week_end = end - timedelta(days=30)
    out["f_scan_week"] = await scan_window(week_end - timedelta(days=7), week_end)
    out["f_scan_all"] = await scan_window(end - timedelta(days=400), end + timedelta(days=1))

    # (g) ten concurrent writers into one collection (results: unique rule,
    # so every write takes the collection's advisory lock).
    async def writer(w: int, n: int, latencies: list):
        async with sf() as s:
            ctx = await load_app(s, tcms)
        for k in range(n):
            t = time.perf_counter()
            async with sf() as s:
                await create_record(s, ctx, CALLER, "results", {
                    "run": f"conc-{w:02d}", "ref": f"TC-{k:05d}-{rid()[:6]}",
                    "status": "passed", "duration": 1.0,
                    "finished_at": end.isoformat()})
            latencies.append((time.perf_counter() - t) * 1000)
    lat: list[float] = []
    t = time.perf_counter()
    await asyncio.gather(*(writer(w, 50, lat) for w in range(10)))
    wall = time.perf_counter() - t
    out["g_concurrent_creates"] = {**summary(lat), "writes": len(lat),
                                   "writes_per_s": round(len(lat) / wall)}

    async def batcher(w: int, latencies: list):
        async with sf() as s:
            ctx = await load_app(s, stock)
        for k in range(5):
            recs = [{"experiment": f"conc-{w}", "strategy": f"s{k}", "day": d.isoformat(),
                     "equity": 1.0} for d in days[:500]]
            t = time.perf_counter()
            async with sf() as s:
                await B.batch(s, ctx, CALLER, "backtest_events", recs)
            latencies.append((time.perf_counter() - t) * 1000)
    lat = []
    t = time.perf_counter()
    await asyncio.gather(*(batcher(w, lat) for w in range(10)))
    wall = time.perf_counter() - t
    out["g_concurrent_batches_500"] = {**summary(lat), "batches": len(lat),
                                       "records_per_s": round(len(lat) * 500 / wall)}

    # (h) the daily bars ingest: one upsert of a day's bars for every symbol.
    async def daily(i):
        day = (days[-1] + timedelta(days=1 + i)).isoformat()
        async with sf() as s:
            ctx = await load_app(s, stock)
            return await B.batch(s, ctx, CALLER, "bars", [
                {"symbol": sym, "day": day, "close": 10.0 + i} for sym in symbols],
                mode="upsert")
    out["h_daily_upsert"], last = await timed(daily, 5, warm=0)
    out["h_daily_upsert"]["records"] = len(symbols)

    # (i) the builder's Apps list and an App's health: the record checks
    # scan every collection, so lists reuse them for an hour.
    L._record_checks.clear()
    kyle = L.Actor("kyle")
    for label in ("i_apps_list_cold", "i_apps_list_warm"):
        t = time.perf_counter()
        async with sf() as s:
            await L.list_apps(s, kyle)
        out[label] = {"ms": round((time.perf_counter() - t) * 1000, 1)}
    for app_id, label in ((stock, "i_health_stockmarket"), (tcms, "i_health_tcms")):
        t = time.perf_counter()
        async with sf() as s:
            await L.health(s, OWNER, app_id)
        out[label] = {"ms": round((time.perf_counter() - t) * 1000, 1)}

    # Plans for the read paths, so a regression shows which index it lost.
    if args.explain:
        out["plans"] = {
            "chart": await explain(sf, stock, "chart", {"symbol": symbols[0]}),
            "latest_bar": await explain(sf, stock, "latest_bar", {"symbol": symbols[0]}),
            "recent_failures": await explain(sf, tcms, "recent_failures",
                                             {"status": "failed"}),
            "ref_history": await explain(sf, tcms, "ref_history", {"ref": "TC-00042"}),
        }

    async with engine.connect() as conn:
        sizes = (await conn.execute(text(
            "SELECT pg_size_pretty(pg_total_relation_size('app_data_records')), "
            "pg_size_pretty(pg_relation_size('app_data_records')), "
            "pg_size_pretty(pg_indexes_size('app_data_records')), "
            "pg_size_pretty(pg_database_size(current_database()))"))).one()
    out["sizes"] = {"records_total": sizes[0], "records_heap": sizes[1],
                    "records_indexes": sizes[2], "database": sizes[3]}
    await engine.dispose()
    return out


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--pg-url", required=True)
    p.add_argument("--scale", type=float, default=1.0)
    p.add_argument("--runs", type=int, default=50)
    p.add_argument("--prefix", default="perf")
    p.add_argument("--skip-load", action="store_true")
    p.add_argument("--explain", action="store_true")
    p.add_argument("--out", help="write the results as JSON here too")
    a = p.parse_args()
    result = asyncio.run(main(a))
    print(json.dumps(result, indent=2))
    if a.out:
        Path(a.out).write_text(json.dumps(result, indent=2))
