"""Stockmarket app: brief parsing/clamping, ingest, and the browse API.
Runs on sqlite (the engine's schema translation only applies on postgres)."""
import json

import httpx
import pytest
from sqlalchemy import select

from stockmarketapp import brief as bf
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


async def test_briefs_endpoint_filters_by_day_and_tag(sf, client):
    await ingest_brief(sf, BRIEF, run_id="r1")
    rows = (await client.get("/apps/stockmarket/api/briefs")).json()
    assert len(rows) == 1 and rows[0]["day"] == "2026-08-06"
    assert (await client.get("/apps/stockmarket/api/briefs?day=2026-08-06")).json()
    assert (await client.get("/apps/stockmarket/api/briefs?day=2026-01-01")).json() == []
    assert (await client.get("/apps/stockmarket/api/briefs?tag=earnings")).json()
    assert (await client.get("/apps/stockmarket/api/briefs?tag=rates")).json() == []
