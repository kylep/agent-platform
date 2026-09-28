"""Stockmarket app: brief parsing/clamping, ingest, and the browse API.
Runs on sqlite (the engine's schema translation only applies on postgres)."""
import json

import httpx
import pytest
from sqlalchemy import select

from stockmarketapp import brief as bf
from stockmarketapp import db
from stockmarketapp.api import stride, window_start
from stockmarketapp.db import (Bar, Brief, Symbol, Watch, init_db, make_engine,
                               make_session_factory, seed_indexes)
from stockmarketapp.ingest import ingest_brief

pytest_plugins = ("pytest_asyncio",)


@pytest.fixture
async def sf():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    factory = make_session_factory(engine)
    await seed_indexes(factory)
    yield factory
    await engine.dispose()


BRIEF = json.dumps({
    "day": "2026-08-06",
    "indexes": [
        {"symbol": "QQQ", "return_pct": -1.42, "note": "Led lower by chips."},
        {"symbol": "SPY", "return_pct": -0.81, "note": "Broad but shallow."},
        {"symbol": "XIU.TO", "return_pct": 0.22, "note": "Energy held it up."},
    ],
    "movers": [
        {"symbol": "NVDA", "index": "QQQ", "contrib_bps": -22.0, "note": "Guided light."},
    ],
    "body": "US indexes fell while Toronto edged up.",
    "tags": ["earnings", "broad-market"],
})


# --- brief parsing -----------------------------------------------------------

def test_parse_brief_tolerates_fences_and_prose():
    assert bf.parse_brief(f"here you go\n```json\n{BRIEF}\n```\n")["day"] == "2026-08-06"
    assert bf.parse_brief("no json here") is None
    assert bf.parse_brief(None) is None


def test_clean_brief_keeps_the_good_parts():
    out = bf.clean_brief(json.loads(BRIEF))
    assert out["day"] == "2026-08-06"
    assert [i["symbol"] for i in out["indexes"]] == ["QQQ", "SPY", "XIU.TO"]
    assert out["movers"][0]["contrib_bps"] == -22.0
    assert out["tags"] == ["earnings", "broad-market"]


def test_clean_brief_rejects_shells_and_bad_dates():
    assert bf.clean_brief({"day": "2026-08-06"}) is None          # nothing in it
    assert bf.clean_brief({"day": "yesterday", "body": "x"}) is None
    assert bf.clean_brief({"day": "2026-08-06", "body": "x"})["indexes"] == []


def test_unknown_tags_are_dropped_not_created():
    """Unlike the news app's auto-created topics, the tag set is closed — an
    invented nineteenth reason a market moved is a mistake, not a new lane."""
    assert bf.clean_tags(["earnings", "vibes", "EARNINGS", "macro"]) == \
        ["earnings", "macro"]
    assert bf.clean_tags(["a", "b", "c", "d"]) == []
    assert bf.clean_tags("earnings") == []
    # Capped even when every tag is real.
    assert len(bf.clean_tags(bf.TAGS)) == bf.MAX_TAGS


def test_absurd_numbers_are_dropped():
    idx = bf.clean_indexes([
        {"symbol": "QQQ", "return_pct": -1.42},
        {"symbol": "SPY", "return_pct": 4000},          # not a session
        {"symbol": "IWM", "return_pct": float("nan")},
        {"symbol": "DIA", "return_pct": "banana"},
        {"symbol": "bad ticker", "return_pct": 1.0},
    ])
    assert [i["symbol"] for i in idx] == ["QQQ"]


def test_movers_are_capped_and_sanitized():
    movers = bf.clean_movers([
        {"symbol": f"S{i}", "index": "QQQ", "contrib_bps": -float(i),
         "note": "hi @everyone <@123>"} for i in range(9)])
    assert len(movers) == bf.MAX_MOVERS
    assert "@​everyone" in movers[0]["note"] and "<@123>" not in movers[0]["note"]


def test_format_post_carries_numbers_prose_and_tags():
    post = bf.format_post(bf.clean_brief(json.loads(BRIEF)))
    assert "QQQ -1.42%" in post and "XIU.TO +0.22%" in post
    assert "US indexes fell" in post and "`earnings`" in post


# --- ingest ------------------------------------------------------------------

async def test_ingest_stores_one_row_per_session(sf):
    stored = await ingest_brief(sf, BRIEF, run_id="r1")
    assert stored["day"] == "2026-08-06"
    async with sf() as s:
        rows = (await s.execute(select(Brief))).scalars().all()
    assert len(rows) == 1 and rows[0].run_id == "r1"

    # A re-run for the same session corrects it rather than duplicating.
    revised = json.loads(BRIEF)
    revised["body"] = "Corrected."
    assert (await ingest_brief(sf, json.dumps(revised), run_id="r2"))["body"] == "Corrected."
    async with sf() as s:
        rows = (await s.execute(select(Brief))).scalars().all()
    assert len(rows) == 1 and rows[0].body == "Corrected." and rows[0].run_id == "r2"


async def test_ingest_garbage_is_noop(sf):
    assert await ingest_brief(sf, "not a brief") is None
    assert await ingest_brief(sf, json.dumps({"day": "2026-08-06"})) is None
    async with sf() as s:
        assert (await s.execute(select(Brief))).scalars().all() == []


async def test_seed_indexes_is_idempotent_and_non_destructive(sf):
    async with sf() as s:
        row = await s.get(Symbol, "QQQ")
        row.status = "ok"
        await s.commit()
    await seed_indexes(sf)
    async with sf() as s:
        rows = (await s.execute(select(Symbol))).scalars().all()
        assert len(rows) == 3
        # A restart must not order a pointless five-year re-backfill.
        assert (await s.get(Symbol, "QQQ")).status == "ok"


# --- postgres migration -------------------------------------------------------
# Tests run on sqlite; the pg branch is only exercised here by inspecting the
# statements it would run (compiled against the real postgresql dialect) —
# never against a live postgres.

def test_postgres_migration_adds_every_new_column_idempotently():
    stmts = db._postgres_column_migrations()
    for col in ("close_split_adj", "adj_close", "dividend", "split_ratio"):
        assert any(
            s == f"ALTER TABLE app_stockmarket.bars ADD COLUMN IF NOT EXISTS {col} FLOAT"
            for s in stmts), col
    assert "ALTER TABLE app_stockmarket.bars ALTER COLUMN volume TYPE BIGINT" in stmts
    assert ("ALTER TABLE app_stockmarket.symbols ADD COLUMN IF NOT EXISTS "
            "currency VARCHAR(3)") in stmts
    # IF NOT EXISTS (or a type re-assertion) throughout: safe to run twice,
    # and safe on a table that already has the column from a fresh create_all.
    assert all("IF NOT EXISTS" in s or "ALTER COLUMN" in s for s in stmts)


def test_migrate_postgres_is_a_noop_off_postgres():
    calls = []

    class FakeDialect:
        name = "sqlite"

    class FakeConn:
        dialect = FakeDialect()

        def exec_driver_sql(self, stmt):
            calls.append(stmt)

    db._migrate_postgres(FakeConn())
    assert calls == []


def test_migrate_postgres_runs_every_statement_on_postgres():
    calls = []

    class FakeDialect:
        name = "postgresql"

    class FakeConn:
        dialect = FakeDialect()

        def exec_driver_sql(self, stmt):
            calls.append(stmt)

    db._migrate_postgres(FakeConn())
    assert calls == db._postgres_column_migrations()


async def test_init_db_is_safe_to_run_twice():
    # create_all is idempotent and the postgres migration branch is skipped
    # entirely off postgres, so running init_db twice on the same engine
    # must not fail.
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    await init_db(engine)
    await engine.dispose()


# --- backtest tables -----------------------------------------------------------

async def test_backtest_tables_round_trip(sf):
    """The five new tables accept a row each and the composite keys hold —
    this is schema coverage; the engine/tool own the actual semantics."""
    from stockmarketapp.db import (BacktestDataset, BacktestEvent,
                                   BacktestExperiment, BacktestResult,
                                   BacktestSeries)
    async with sf() as s:
        s.add(BacktestExperiment(id="e" * 32, name="DCA vs lump sum",
                                 spec={"strategies": []}, description="desc",
                                 assumed=[{"path": "currency", "value": "CAD"}],
                                 caveats=["hindsight"], exclusions=[],
                                 dataset_sha="d" * 64, engine_version="1.0.0",
                                 caller="kyle", run_id="r1", report_id="backtest/2026-09-28/12-00"))
        s.add(BacktestDataset(sha="d" * 64, rows_gz=b"gz-bytes", symbols=["QQQ"],
                              day_from="2020-01-01", day_to="2026-01-01"))
        s.add(BacktestResult(experiment_id="e" * 32, strategy_id="s1",
                             label="Strategy 1", metrics={"xirr": 0.08}))
        s.add(BacktestSeries(experiment_id="e" * 32, strategy_id="s1",
                             day="2026-01-01", value=1000.0, contributed=900.0))
        s.add(BacktestEvent(experiment_id="e" * 32, strategy_id="s1",
                            day="2026-01-01", kind="buy", symbol="QQQ",
                            detail={"shares": 1.5}))
        await s.commit()
    async with sf() as s:
        exp = await s.get(BacktestExperiment, "e" * 32)
        assert exp.dataset_sha == "d" * 64 and exp.caveats == ["hindsight"]
        ds = await s.get(BacktestDataset, "d" * 64)
        assert ds.rows_gz == b"gz-bytes" and ds.symbols == ["QQQ"]
        res = await s.get(BacktestResult, {"experiment_id": "e" * 32, "strategy_id": "s1"})
        assert res.metrics == {"xirr": 0.08}
        series = await s.get(BacktestSeries, {"experiment_id": "e" * 32,
                                              "strategy_id": "s1", "day": "2026-01-01"})
        assert series.value == 1000.0 and series.contributed == 900.0
        events = (await s.execute(select(BacktestEvent))).scalars().all()
        assert len(events) == 1 and events[0].symbol == "QQQ"


# --- range + downsample helpers ----------------------------------------------

def test_window_start_anchors_on_the_archive_not_today():
    assert window_start("2026-08-06", "5D") == "2026-07-30"
    assert window_start("2026-08-06", "YTD") == "2026-01-01"
    assert window_start("2026-08-06", "1Y") == "2025-08-05"


def test_stride_thins_long_series_but_keeps_the_latest():
    points = [(f"d{i}", float(i)) for i in range(1300)]
    kept, thinned = stride(points, cap=400)
    assert thinned and len(kept) <= 401
    assert kept[-1] == points[-1]          # the newest close always survives
    short, thinned2 = stride(points[:50], cap=400)
    assert not thinned2 and short == points[:50]


# --- browse API --------------------------------------------------------------

@pytest.fixture
async def client(sf):
    from stockmarketapp.main import app
    app.state.sf = sf
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t",
                                 headers={"X-AP-User": "kyle",
                                          "X-AP-Role": "operator"}) as c:
        yield c


async def _bars(sf, symbol, closes, start_day=4):
    async with sf() as s:
        for i, c in enumerate(closes):
            s.add(Bar(symbol=symbol, day=f"2026-08-{start_day + i:02d}", close=c))
        await s.commit()


async def test_api_requires_gateway_identity(client):
    bare = httpx.AsyncClient(transport=httpx.ASGITransport(app=client._transport.app),
                             base_url="http://t")
    assert (await bare.get("/apps/stockmarket/api/summary")).status_code == 401
    await bare.aclose()


async def test_summary_reports_indexes_and_latest_session(sf, client):
    await _bars(sf, "QQQ", [100.0, 110.0])
    s = (await client.get("/apps/stockmarket/api/summary")).json()
    qqq = next(i for i in s["indexes"] if i["symbol"] == "QQQ")
    assert qqq["latest_close"] == 110.0 and qqq["change_pct"] == 10.0
    assert s["latest_day"] == "2026-08-05"
    assert s["watchlist"] == [] and "earnings" in s["tags"]


async def test_series_returns_closes_within_the_range(sf, client):
    await _bars(sf, "QQQ", [100.0, 101.0, 102.0])
    rows = (await client.get("/apps/stockmarket/api/series?symbols=QQQ&range=5D")).json()
    assert rows[0]["symbol"] == "QQQ" and len(rows[0]["points"]) == 3
    assert rows[0]["points"][-1] == ["2026-08-06", 102.0]
    assert rows[0]["downsampled"] is False
    assert (await client.get("/apps/stockmarket/api/series?symbols=QQQ&range=3D")
            ).status_code == 422


async def test_series_custom_from_to_window(sf, client):
    # Bars 2026-08-04 .. 2026-08-08; a custom window clips to a sub-range.
    await _bars(sf, "QQQ", [100.0, 101.0, 102.0, 103.0, 104.0])
    rows = (await client.get(
        "/apps/stockmarket/api/series?symbols=QQQ&day_from=2026-08-05&day_to=2026-08-07")).json()
    assert [d for d, _ in rows[0]["points"]] == ["2026-08-05", "2026-08-06", "2026-08-07"]
    # Reversed bounds are rejected.
    assert (await client.get(
        "/apps/stockmarket/api/series?symbols=QQQ&day_from=2026-08-07&day_to=2026-08-05")
        ).status_code == 422
    # Malformed date is rejected.
    assert (await client.get(
        "/apps/stockmarket/api/series?symbols=QQQ&day_from=nonsense&day_to=2026-08-07")
        ).status_code == 422


def test_render_daily_market_html():
    from stockmarketapp.report import render_daily_market
    b = Brief(day="2026-08-10", body="US indexes were quiet; XIU bucked the trend.",
              tags=["earnings", "geopolitics"],
              indexes=[{"symbol": "QQQ", "return_pct": -0.2, "note": "Slipped on chip weakness."},
                       {"symbol": "XIU.TO", "return_pct": 0.37, "note": "Energy pop in CNQ."}],
              movers=[{"symbol": "NVDA", "index": "QQQ", "contrib_bps": -18.4,
                       "note": "Fell 2.3% in profit-taking."}],
              run_id="r1")
    html, meta = render_daily_market(b)
    assert meta == {"indexes": 2, "movers": 1, "run_id": "r1"}
    assert "Market brief — 2026-08-10" in html
    assert "<h2>Summary</h2>" in html and "XIU bucked the trend" in html
    assert "<h2>By index</h2>" in html and "Energy pop in CNQ." in html
    assert "<h2>Movers</h2>" in html and "-18bp" in html
    assert "+0.37%" in html and "-0.20%" in html
    # The report uses only report-kit classes and escapes text (no raw markup).
    assert "class=\"rk-" in html and "style=" not in html


async def test_series_ignores_junk_symbols(sf, client):
    await _bars(sf, "QQQ", [100.0, 101.0])
    rows = (await client.get(
        "/apps/stockmarket/api/series?symbols=QQQ,not a ticker,")).json()
    assert [r["symbol"] for r in rows] == ["QQQ"]


async def test_watchlist_add_is_optimistic_and_pending(sf, client, monkeypatch):
    asked = []
    monkeypatch.setattr("stockmarketapp.api.request_backfill",
                        lambda symbol: asked.append(symbol) or _noop())
    r = await client.post("/apps/stockmarket/api/watchlist", json={"symbol": "nvda"})
    assert r.status_code == 201
    assert r.json()["symbol"] == "NVDA" and r.json()["status"] == "pending"
    assert asked == ["NVDA"]                       # backfill was requested

    s = (await client.get("/apps/stockmarket/api/summary")).json()
    assert [w["symbol"] for w in s["watchlist"]] == ["NVDA"]

    # Adding twice is idempotent and does not re-request a backfill.
    assert (await client.post("/apps/stockmarket/api/watchlist",
                              json={"symbol": "NVDA"})).status_code == 201
    assert asked == ["NVDA"]
    async with sf() as s2:
        assert len((await s2.execute(select(Watch))).scalars().all()) == 1


async def _noop():
    return None


async def test_watchlist_rejects_junk_and_needs_write_access(client):
    assert (await client.post("/apps/stockmarket/api/watchlist",
                              json={"symbol": "not a ticker"})).status_code == 422
    reader = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=client._transport.app),
        base_url="http://t", headers={"X-AP-User": "agent", "X-AP-Role": "reader"})
    # Agents reach this app through query_app as `reader` — they may look, not
    # enqueue backfill runs.
    assert (await reader.post("/apps/stockmarket/api/watchlist",
                              json={"symbol": "NVDA"})).status_code == 403
    assert (await reader.get("/apps/stockmarket/api/summary")).status_code == 200
    await reader.aclose()


async def test_watchlist_remove_keeps_the_bars(sf, client, monkeypatch):
    monkeypatch.setattr("stockmarketapp.api.request_backfill", lambda symbol: _noop())
    await client.post("/apps/stockmarket/api/watchlist", json={"symbol": "NVDA"})
    await _bars(sf, "NVDA", [10.0, 11.0])
    assert (await client.delete("/apps/stockmarket/api/watchlist/NVDA")).status_code == 204
    s = (await client.get("/apps/stockmarket/api/summary")).json()
    assert s["watchlist"] == []
    async with sf() as s2:
        # Re-adding should be instant, not another five-year backfill.
        assert len((await s2.execute(select(Bar).where(Bar.symbol == "NVDA")))
                   .scalars().all()) == 2


async def test_watchlists_are_per_user(sf, client, monkeypatch):
    monkeypatch.setattr("stockmarketapp.api.request_backfill", lambda symbol: _noop())
    await client.post("/apps/stockmarket/api/watchlist", json={"symbol": "NVDA"})
    other = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=client._transport.app),
        base_url="http://t", headers={"X-AP-User": "someone-else",
                                      "X-AP-Role": "operator"})
    assert (await other.get("/apps/stockmarket/api/summary")).json()["watchlist"] == []
    await other.aclose()


async def _add_symbol(sf, symbol, kind, status="ok"):
    async with sf() as s:
        s.add(Symbol(symbol=symbol, label="", kind=kind, status=status))
        await s.commit()


async def test_summary_watchlist_hides_research_and_fx_symbols(sf, client):
    await _add_symbol(sf, "ACME.RS", "research")
    await _add_symbol(sf, "CAD=X", "fx")
    async with sf() as s:
        s.add(Watch(user="kyle", symbol="ACME.RS"))
        s.add(Watch(user="kyle", symbol="CAD=X"))
        s.add(Watch(user="kyle", symbol="NVDA"))
        await s.commit()
    await _add_symbol(sf, "NVDA", "watch")
    s = (await client.get("/apps/stockmarket/api/summary")).json()
    assert [w["symbol"] for w in s["watchlist"]] == ["NVDA"]


async def test_series_drops_research_and_fx_symbols(sf, client):
    await _add_symbol(sf, "ACME.RS", "research")
    await _bars(sf, "QQQ", [100.0, 101.0])
    await _bars(sf, "ACME.RS", [10.0, 11.0])
    rows = (await client.get(
        "/apps/stockmarket/api/series?symbols=QQQ,ACME.RS")).json()
    assert [r["symbol"] for r in rows] == ["QQQ"]


async def test_watchlist_add_rejects_research_and_fx_kinds(sf, client):
    await _add_symbol(sf, "ACME.RS", "research")
    r = await client.post("/apps/stockmarket/api/watchlist",
                          json={"symbol": "ACME.RS"})
    assert r.status_code == 422
    async with sf() as s:
        assert (await s.execute(select(Watch))).scalars().all() == []


async def test_briefs_movers_hide_research_and_fx_symbols(sf, client):
    await _add_symbol(sf, "NVDA", "research")
    await ingest_brief(sf, BRIEF, run_id="r1")
    rows = (await client.get("/apps/stockmarket/api/briefs")).json()
    assert rows[0]["movers"] == []


async def test_briefs_endpoint_filters_by_day_and_tag(sf, client):
    await ingest_brief(sf, BRIEF, run_id="r1")
    rows = (await client.get("/apps/stockmarket/api/briefs")).json()
    assert len(rows) == 1 and rows[0]["day"] == "2026-08-06"
    assert (await client.get("/apps/stockmarket/api/briefs?day=2026-08-06")).json()
    assert (await client.get("/apps/stockmarket/api/briefs?day=2026-01-01")).json() == []
    assert (await client.get("/apps/stockmarket/api/briefs?tag=earnings")).json()
    assert (await client.get("/apps/stockmarket/api/briefs?tag=rates")).json() == []


async def test_ingest_persists_report_and_event_without_external_broadcast(sf, monkeypatch):
    from stockmarketapp import ingest
    sent, reports = [], []

    class Producer:
        async def send_and_wait(self, topic, value):
            sent.append(topic)

    async def report(factory, day):
        async with factory() as session:
            assert (await session.get(Brief, day)).body == "US indexes fell while Toronto edged up."
        reports.append(day)

    monkeypatch.setattr(ingest, "write_daily_market_report", report)
    await ingest.IngestLoop(sf, "kafka:9092").handle(Producer(), json.dumps({
        "result": BRIEF, "run_id": "market-test"}).encode())
    assert sent == [ingest.TOPIC_POSTED]
    assert reports == ["2026-08-06"]
