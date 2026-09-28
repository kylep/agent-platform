"""Stockmarket app: brief parsing/clamping, ingest, and the browse API.
Runs on sqlite (the engine's schema translation only applies on postgres)."""
import json

import httpx
import pytest
from sqlalchemy import select

from stockmarketapp import backtests as bt
from stockmarketapp import brief as bf
from stockmarketapp import db
from stockmarketapp import report as report_mod
from stockmarketapp.api import stride, window_start
from stockmarketapp.db import (Bar, BacktestEvent, BacktestExperiment,
                               BacktestResult, BacktestSeries, Brief, Symbol,
                               Watch, init_db, make_engine,
                               make_session_factory, seed_indexes)
from stockmarketapp.ingest import BacktestIngestLoop, ingest_brief

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


# --- backtests (docs/design/35) ----------------------------------------------

async def _add_experiment(sf, experiment_id="a" * 32, name="DCA into QQQ",
                          strategies=(("s1", "Strategy A"),), exclusions=None,
                          caveats=None, assumed=None):
    async with sf() as s:
        s.add(BacktestExperiment(
            id=experiment_id, name=name, spec={"name": name}, description="A DCA plan.",
            assumed=assumed or [{"path": "base_currency", "value": "CAD"}],
            caveats=caveats or [{"code": "data", "text": "Yahoo data, unaudited."}],
            exclusions=exclusions or [], dataset_sha="f" * 64, engine_version="1.0.0",
            caller="stockmarket-data", run_id="run-1"))
        for sid, label in strategies:
            s.add(BacktestResult(experiment_id=experiment_id, strategy_id=sid, label=label,
                                 metrics={"final_value": 1000.0, "contributed": 900.0,
                                          "xirr": 0.12, "twr_annualized": 0.10,
                                          "max_drawdown": {"depth": -0.05},
                                          "trades": 4, "pick_timeline": list(range(500))}))
            for i, (day, value, contributed) in enumerate(
                    [("2026-01-05", 900.0, 900.0), ("2026-06-05", 1000.0, 900.0)]):
                s.add(BacktestSeries(experiment_id=experiment_id, strategy_id=sid, day=day,
                                     value=value, contributed=contributed))
            s.add(BacktestEvent(experiment_id=experiment_id, strategy_id=sid, day="2026-01-05",
                                kind="buy", symbol="QQQ", detail={"shares": "5"}))
        await s.commit()


async def test_ingest_notice_skips_malformed_or_unknown_id(sf):
    assert await bt.ingest_notice(sf, {}) is None
    assert await bt.ingest_notice(sf, {"experiment_id": "not-hex"}) is None
    assert await bt.ingest_notice(sf, {"experiment_id": "a" * 32}) is None  # not in the tables


async def test_ingest_notice_confirms_a_committed_experiment(sf):
    await _add_experiment(sf)
    assert await bt.ingest_notice(sf, {"experiment_id": "a" * 32}) == "a" * 32


async def test_backtest_ingest_loop_skips_unknown_experiment(sf, monkeypatch):
    from stockmarketapp import ingest as ingest_mod
    calls = []
    monkeypatch.setattr(ingest_mod, "write_backtest_report",
                        lambda factory, experiment_id: calls.append(experiment_id) or _noop())
    await BacktestIngestLoop(sf, "kafka:9092").handle(
        json.dumps({"data": {"experiment_id": "not-hex"}, "schema_version": 1}).encode())
    assert calls == []


async def test_backtest_ingest_loop_renders_report_idempotently(sf, monkeypatch):
    from stockmarketapp import ingest as ingest_mod
    await _add_experiment(sf)
    calls = []

    async def fake_write(factory, experiment_id):
        calls.append(experiment_id)

    monkeypatch.setattr(ingest_mod, "write_backtest_report", fake_write)
    raw = json.dumps({"type": "backtest.completed", "schema_version": 1,
                      "data": {"experiment_id": "a" * 32}}).encode()
    loop = BacktestIngestLoop(sf, "kafka:9092")
    await loop.handle(raw)
    await loop.handle(raw)   # a re-delivered notice is handled again, not fatally
    assert calls == ["a" * 32, "a" * 32]


class _FakeChartClient:
    """Stands in for the httpx client render_backtest posts charts through."""
    async def post(self, path, headers=None, json=None):
        assert path == "/api/report-kit/chart"
        return _FakeResp(200, {"svg": '<svg class="rk-chart"></svg>'})


class _FakeResp:
    def __init__(self, status_code, data):
        self.status_code = status_code
        self._data = data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=None)

    def json(self):
        return self._data


_CLASS_RE = __import__("re").compile(r'class="([^"]*)"')


async def test_render_backtest_escapes_hostile_name_and_uses_only_rk_classes(sf):
    hostile = '<script>alert(1)</script>'
    await _add_experiment(sf, name=hostile)
    html, meta = await bt_render(sf)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert meta == {"experiment_id": "a" * 32}
    for classes in _CLASS_RE.findall(html):
        for token in classes.split():
            assert token.startswith(("rk-", "ds-")), token


async def bt_render(sf):
    return await report_mod.render_backtest(sf, "a" * 32, _FakeChartClient(), {})


async def test_render_backtest_returns_none_for_unknown_experiment(sf):
    assert await report_mod.render_backtest(sf, "b" * 32, _FakeChartClient(), {}) is None


class _FakeReportsClient:
    """A minimal /api/reports + /api/report-kit/chart + /api/runs double, so
    write_backtest_report and the rerun action can be exercised without a
    network — same style as monkeypatching write_daily_market_report above,
    but this one needs to prove the slot-advancing logic itself."""

    def __init__(self):
        self.reports: dict[tuple, dict] = {}
        self._next = 1

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, path, headers=None, params=None):
        assert path == "/api/reports"
        rows = [v for (date, _t), v in self.reports.items() if date == params["date_from"]]
        return _FakeResp(200, rows)

    async def post(self, path, headers=None, json=None):
        if path == "/api/report-kit/chart":
            return _FakeResp(200, {"svg": '<svg class="rk-chart"></svg>'})
        if path == "/api/reports":
            key = (json["date"], json.get("time", ""))
            existing = self.reports.get(key)
            replaced = existing is not None
            rid = existing["id"] if replaced else f"report-{self._next}"
            if not replaced:
                self._next += 1
            self.reports[key] = {"id": rid, "time": json.get("time", ""),
                                 "meta": json.get("meta") or {}}
            return _FakeResp(201, {"id": rid, "replaced": replaced})
        if path == "/api/runs":
            return _FakeResp(201, {"id": "run-rerun-1"})
        raise AssertionError(f"unexpected path {path}")


async def test_write_backtest_report_stores_report_id_and_reuses_its_own_slot(
        sf, monkeypatch):
    await _add_experiment(sf)
    fake = _FakeReportsClient()
    monkeypatch.setenv("AP_API_TOKEN", "t")
    monkeypatch.setenv("AP_API_URL", "http://ap")
    monkeypatch.setattr(report_mod.httpx, "AsyncClient", lambda *a, **k: fake)

    await report_mod.write_backtest_report(sf, "a" * 32)
    async with sf() as s:
        exp = await s.get(BacktestExperiment, "a" * 32)
        first_report_id = exp.report_id
    assert first_report_id == "report-1"

    # A re-delivered notice re-renders in place: same slot, same report row.
    await report_mod.write_backtest_report(sf, "a" * 32)
    async with sf() as s:
        exp = await s.get(BacktestExperiment, "a" * 32)
    assert exp.report_id == first_report_id
    assert len(fake.reports) == 1


async def test_write_backtest_report_advances_past_a_taken_slot(sf, monkeypatch):
    await _add_experiment(sf, experiment_id="a" * 32, name="First")
    await _add_experiment(sf, experiment_id="b" * 32, name="Second")
    fake = _FakeReportsClient()
    monkeypatch.setenv("AP_API_TOKEN", "t")
    monkeypatch.setenv("AP_API_URL", "http://ap")
    monkeypatch.setattr(report_mod.httpx, "AsyncClient", lambda *a, **k: fake)

    await report_mod.write_backtest_report(sf, "a" * 32)
    await report_mod.write_backtest_report(sf, "b" * 32)
    times = {v["time"] for v in fake.reports.values()}
    assert len(times) == 2   # the second experiment did not overwrite the first


def test_pick_slot_helper_advances_minutes():
    assert report_mod._next_slot("00-00") == "00-01"
    assert report_mod._next_slot("00-59") == "01-00"
    assert report_mod._next_slot("23-59") == "00-00"


def test_fit_detail_cap_cascades_to_a_bound_even_past_the_field_caps():
    """A synthetic dict already past every per-field cap (a pathological
    count, not just long text) — proves the cascade itself shrinks it,
    independent of how unlikely that input is under the spec's bounds."""
    out = {
        "id": "a" * 32, "name": "n", "description": "d",
        "caveats": [{"code": f"c{i}", "text": "y" * 160} for i in range(200)],
        "assumed": [{"path": f"p{i}", "value": "v" * 120} for i in range(200)],
        "exclusions_summary": {"count": 0, "symbols": []},
        "report_id": None, "created_at": "2026-01-01T00:00:00",
        "strategies": [{"id": f"s{i}", "label": "Strategy " + "x" * 50,
                        "metrics": {"final_value": 1.0}} for i in range(8)],
    }
    assert bt._size(out) > bt.DETAIL_CAP
    fitted = bt._fit_detail_cap(out)
    assert bt._size(fitted) <= bt.DETAIL_CAP
    assert len(fitted["strategies"]) == 8   # a strategy is never dropped


# --- backtests browse API -----------------------------------------------------

async def test_backtests_list_search_escapes_like_wildcards(sf, client):
    await _add_experiment(sf, experiment_id="a" * 32, name="A_B plan")
    await _add_experiment(sf, experiment_id="b" * 32, name="AXB plan")
    rows = (await client.get("/apps/stockmarket/api/backtests?q=A_B")).json()
    assert [r["name"] for r in rows] == ["A_B plan"]


async def test_backtests_detail_stays_bounded_and_hides_pick_timeline(sf, client):
    await _add_experiment(sf)
    r = await client.get(f"/apps/stockmarket/api/backtests/{'a' * 32}")
    assert r.status_code == 200
    body = r.json()
    assert len(r.content) <= 8192
    assert "pick_timeline" not in body["strategies"][0]["metrics"]
    assert body["exclusions_summary"] == {"count": 0, "symbols": []}
    assert body["assumed"] and body["caveats"]
    # The raw spec never rides in the bounded detail response — the UI's
    # collapsible spec panel reads it from /backtests/{id}/spec instead.
    assert "spec" not in body


async def test_backtests_detail_404_for_unknown_id(client):
    r = await client.get(f"/apps/stockmarket/api/backtests/{'c' * 32}")
    assert r.status_code == 404


async def test_backtests_detail_worst_case_spec_stays_under_8kb(sf, client):
    """The reviewer's fixture: 8 strategies with 60-char labels and ~19
    caveats of ~400 chars each measured 20.5 KB uncapped. This must fit."""
    strategies = tuple((f"s{i}", "Strategy label " + "x" * 44) for i in range(8))
    caveats = [{"code": f"concentration-{i}", "text": "y" * 400} for i in range(16)] + [
        {"code": "hindsight", "text": "z" * 400},
        {"code": "taxes", "text": "z" * 400},
        {"code": "data", "text": "z" * 400},
    ]
    assumed = [{"path": f"strategies.{i}.holdings.mode", "value": "keep"} for i in range(8)] + [
        {"path": "base_currency", "value": "CAD"},
        {"path": "dividends.mode", "value": "reinvest"},
    ]
    await _add_experiment(sf, strategies=strategies, caveats=caveats, assumed=assumed)
    r = await client.get(f"/apps/stockmarket/api/backtests/{'a' * 32}")
    assert r.status_code == 200
    assert len(r.content) <= 8192
    assert len(r.json()["strategies"]) == 8


async def test_backtests_metrics_returns_full_dict_for_one_strategy(sf, client):
    await _add_experiment(sf, strategies=(("s1", "A"), ("s2", "B")))
    r = await client.get(f"/apps/stockmarket/api/backtests/{'a' * 32}/metrics?strategy=s1")
    assert r.status_code == 200
    body = r.json()
    assert body["strategy_id"] == "s1"
    assert "pick_timeline" not in body["metrics"]
    assert body["metrics"]["max_drawdown"] == {"depth": -0.05}   # full, not flattened


async def test_backtests_metrics_requires_strategy_when_ambiguous(sf, client):
    await _add_experiment(sf, strategies=(("s1", "A"), ("s2", "B")))
    r = await client.get(f"/apps/stockmarket/api/backtests/{'a' * 32}/metrics")
    assert r.status_code == 422


async def test_backtests_metrics_defaults_to_the_only_strategy(sf, client):
    await _add_experiment(sf)
    r = await client.get(f"/apps/stockmarket/api/backtests/{'a' * 32}/metrics")
    assert r.status_code == 200 and r.json()["strategy_id"] == "s1"


async def test_backtests_metrics_404_for_unknown_id(client):
    r = await client.get(f"/apps/stockmarket/api/backtests/{'c' * 32}/metrics")
    assert r.status_code == 404


async def test_backtests_spec_returns_the_stored_spec(sf, client):
    await _add_experiment(sf)
    r = await client.get(f"/apps/stockmarket/api/backtests/{'a' * 32}/spec")
    assert r.status_code == 200
    assert r.json() == {"id": "a" * 32, "spec": {"name": "DCA into QQQ"}}


async def test_backtests_spec_404_for_unknown_id(client):
    r = await client.get(f"/apps/stockmarket/api/backtests/{'c' * 32}/spec")
    assert r.status_code == 404


async def test_backtests_events_paginate_and_filter_by_strategy(sf, client):
    await _add_experiment(sf, strategies=(("s1", "A"), ("s2", "B")))
    r = (await client.get(
        f"/apps/stockmarket/api/backtests/{'a' * 32}/events?strategy=s1")).json()
    assert len(r["events"]) == 1 and r["events"][0]["strategy_id"] == "s1"
    assert r["has_more"] is False


async def test_backtests_series_requires_strategy_when_ambiguous(sf, client):
    await _add_experiment(sf, strategies=(("s1", "A"), ("s2", "B")))
    r = await client.get(f"/apps/stockmarket/api/backtests/{'a' * 32}/series")
    assert r.status_code == 422
    ok = await client.get(f"/apps/stockmarket/api/backtests/{'a' * 32}/series?strategy=s1")
    assert ok.status_code == 200
    assert ok.json()["strategy_id"] == "s1"
    assert len(ok.json()["points"]) == 2


async def test_backtests_series_defaults_to_the_only_strategy(sf, client):
    await _add_experiment(sf)
    r = await client.get(f"/apps/stockmarket/api/backtests/{'a' * 32}/series")
    assert r.status_code == 200 and r.json()["strategy_id"] == "s1"


async def test_backtests_rerun_requires_better_than_reader(sf, monkeypatch):
    from stockmarketapp import api as api_mod
    await _add_experiment(sf)

    async def fake_rerun(experiment_id):
        return "run-rerun-1"

    monkeypatch.setattr(api_mod, "request_backtest_rerun", fake_rerun)
    from stockmarketapp.main import app
    app.state.sf = sf
    transport = httpx.ASGITransport(app=app)
    reader = httpx.AsyncClient(transport=transport, base_url="http://t",
                               headers={"X-AP-User": "kyle", "X-AP-Role": "reader"})
    denied = await reader.post(f"/apps/stockmarket/api/backtests/{'a' * 32}/rerun")
    assert denied.status_code == 403
    await reader.aclose()

    operator = httpx.AsyncClient(transport=transport, base_url="http://t",
                                 headers={"X-AP-User": "kyle", "X-AP-Role": "operator"})
    allowed = await operator.post(f"/apps/stockmarket/api/backtests/{'a' * 32}/rerun")
    assert allowed.status_code == 202
    assert allowed.json() == {"run_id": "run-rerun-1", "requested": True}
    await operator.aclose()


async def test_backtests_rerun_404_for_unknown_id(client):
    r = await client.post(f"/apps/stockmarket/api/backtests/{'c' * 32}/rerun")
    assert r.status_code == 404


async def test_help_lists_backtest_paths(client):
    r = (await client.get("/apps/stockmarket/api/help")).json()
    assert "backtests" in r["paths"] and "backtests/{id}" in r["paths"]
    assert "backtests/{id}/metrics" in r["paths"] and "backtests/{id}/spec" in r["paths"]
    assert r["views"]["backtest"] == "/apps/stockmarket/#/backtests/<id>"
