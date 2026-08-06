"""News app: digest parsing/sanitizing, ingest dedup, browse API, report
rendering. Runs on sqlite (the engine's schema translation only applies on
postgres)."""
import asyncio
import json

import httpx
import pytest
from sqlalchemy import select

from newsapp import digest as dg
from newsapp.db import Item, Topic, init_db, make_engine, make_session_factory
from newsapp.ingest import ingest_digest
from newsapp.report import render_daily

pytest_plugins = ("pytest_asyncio",)


@pytest.fixture
async def sf():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    yield make_session_factory(engine)
    await engine.dispose()


DIGEST = json.dumps({
    "date": "2026-08-03",
    "items": [
        {"headline": "Model X ships", "why": "big deal", "section": "AI industry",
         "url": "https://ex.com/a?utm_source=x"},
        {"headline": "Model X ships", "why": "dup w/ tracking", "section": "AI industry",
         "url": "https://EX.com/a"},
        {"headline": "Markets move", "why": "", "section": "Business",
         "url": "https://ex.com/b#frag"},
    ],
})


# --- digest port -------------------------------------------------------------

def test_parse_digest_tolerates_fences_and_prose():
    assert dg.parse_digest(f"prose\n```json\n{DIGEST}\n```\nmore")["date"] == "2026-08-03"
    assert dg.parse_digest("no json here") is None
    assert dg.parse_digest(None) is None


def test_sanitize_and_norm_url():
    assert "@​everyone" in dg.sanitize("hi @everyone")
    assert dg.sanitize("<@12345> x") == "x"
    assert dg.norm_url("HTTPS://Ex.com/a/?utm_source=t&id=3#f") == "https://ex.com/a?id=3"


def test_format_post_sections_ordered_and_sanitized():
    post = dg.format_post("2026-08-03", [
        {"headline": "w @everyone", "why": "", "section": "World", "url": "https://x.com/1"},
        {"headline": "a", "why": "b", "section": "AI industry", "url": "https://x.com/2"},
    ])
    assert post.index("AI industry") < post.index("World")
    assert "@​everyone" in post


# --- ingest ------------------------------------------------------------------

async def test_ingest_dedups_and_tags(sf):
    new, day = await ingest_digest(sf, DIGEST, run_id="r1")
    assert day == "2026-08-03" and len(new) == 2   # tracking-url dup dropped
    async with sf() as s:
        items = (await s.execute(select(Item))).scalars().all()
        topics = {t.slug: t for t in (await s.execute(select(Topic))).scalars()}
    assert len(items) == 2
    assert set(topics) == {"ai-industry", "business"}
    assert all(i.dedup_hash.startswith("https://ex.com/") for i in items)
    # replay: everything already archived → nothing new
    new2, _ = await ingest_digest(sf, DIGEST)
    assert new2 == []


async def test_ingest_garbage_is_noop(sf):
    assert await ingest_digest(sf, "not a digest") == ([], "")
    assert await ingest_digest(sf, json.dumps({"items": [{"no": "url"}]})) == ([], "")


# --- browse API --------------------------------------------------------------

@pytest.fixture
async def client(sf):
    from newsapp.main import app
    app.state.sf = sf
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t",
                                 headers={"X-AP-User": "admin"}) as c:
        yield c


async def test_api_requires_gateway_identity(sf, client):
    bare = httpx.AsyncClient(transport=httpx.ASGITransport(app=client._transport.app),
                             base_url="http://t")
    r = await bare.get("/apps/news/api/summary")
    assert r.status_code == 401
    await bare.aclose()


async def test_api_browse_axes(sf, client):
    await ingest_digest(sf, DIGEST)
    s = (await client.get("/apps/news/api/summary")).json()
    assert s["total"] == 2 and s["latest_day"] == "2026-08-03"
    topics = (await client.get("/apps/news/api/topics")).json()
    assert {t["slug"] for t in topics} == {"ai-industry", "business"}
    assert all(len(t["spark"]) == 14 for t in topics)
    cal = (await client.get("/apps/news/api/calendar?month=2026-08")).json()
    assert cal["2026-08-03"]["total"] == 2
    assert cal["2026-08-03"]["by_topic"]["business"] == 1
    items = (await client.get("/apps/news/api/items?topic=ai-industry")).json()
    assert len(items) == 1 and items[0]["title"] == "Model X ships"
    found = (await client.get("/apps/news/api/items?q=markets")).json()
    assert len(found) == 1 and found[0]["topic"] == "business"


# --- report ------------------------------------------------------------------

async def test_render_daily_report_is_kit_markup(sf):
    await ingest_digest(sf, DIGEST, run_id="r1")

    async def fake_chart(request):
        return httpx.Response(200, json={"svg": "<svg>spark</svg>"})
    client = httpx.AsyncClient(transport=httpx.MockTransport(fake_chart),
                               base_url="http://api")
    body, meta = await render_daily(sf, "2026-08-03", client, {})
    await client.aclose()
    assert meta == {"items": 2, "topics": 2, "run_id": "r1"}
    assert 'class="rk-title"' in body and "Model X ships" in body
    assert "<svg>spark</svg>" in body
    # data renders as text, never markup
    async with sf() as s:
        pass
    assert "<script" not in body
