"""News app: digest parsing/sanitizing, ingest dedup + freshness gates, browse
API, report rendering. Runs on sqlite (the engine's schema translation only
applies on postgres)."""
import asyncio
import json

import httpx
import pytest
from sqlalchemy import inspect as sa_inspect, select, text

from newsapp import digest as dg
from newsapp.db import Item, Topic, init_db, make_engine, make_session_factory
from newsapp.ingest import (TOPIC_CHANNEL_POST, TOPIC_INGESTED, TOPIC_REJECTED,
                            IngestLoop, ingest_digest)
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
         "url": "https://ex.com/2026/08/model-x?utm_source=x", "published": "2026-08-03"},
        {"headline": "Model X ships", "why": "dup w/ tracking", "section": "AI industry",
         "url": "https://EX.com/2026/08/model-x", "published": "2026-08-03"},
        {"headline": "Markets move", "why": "", "section": "Business",
         "url": "https://ex.com/2026/08/markets-move#frag", "published": "2026-08-02"},
    ],
})


def _digest(date: str, *items: dict) -> str:
    return json.dumps({"date": date, "items": list(items)})


def _item(headline: str, url: str, published: str | None = "2026-08-03",
          section: str = "Security", why: str = "w") -> dict:
    it = {"headline": headline, "why": why, "section": section, "url": url}
    if published is not None:
        it["published"] = published
    return it


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


def test_format_post_footer_counts_filtered_items():
    post = dg.format_post("2026-08-03", [_item("a", "https://x.com/1")],
                          filtered={"stale": 2, "duplicate-story": 1})
    assert post.rstrip().endswith("filtered: 2 stale · 1 duplicate-story")
    assert "filtered" not in dg.format_post("2026-08-03", [_item("a", "https://x.com/1")])


# --- freshness gates (pure functions) ----------------------------------------

def test_parse_day_accepts_iso_dates_only():
    assert dg.parse_day("2026-08-28").isoformat() == "2026-08-28"
    assert dg.parse_day("Aug 28, 2026") is None
    assert dg.parse_day("") is None and dg.parse_day(None) is None


@pytest.mark.parametrize("url", [
    "https://openai.com/news/",
    "https://www.anthropic.com/news",
    "https://www.cp24.com/",
    "https://www.postgresql.org/support/security",
    "https://www.postgresql.org/about/newsarchive",
    "https://globalnews.ca/toronto/",
    "https://www.cp24.com/local/toronto/",
    "https://releasebot.io/updates/kubernetes",           # aggregator host
    "https://sharkstriker.com/blog/august-2026-data-breaches/",
    "https://en.wikipedia.org/wiki/2026_Toronto_mayoral_election",
])
def test_hub_urls_are_recognized(url):
    assert dg.is_hub_url(url), url


@pytest.mark.parametrize("url", [
    "https://techcrunch.com/2026/08/24/hugging-face-reportedly-in-talks-to-be-acquired-for-13b/",
    "https://www.postgresql.org/about/news/postgresql-186-1711-1615-1519-and-1424-released-3400/",
    "https://www.securityweek.com/august-2026-patch-tuesday-microsoft-fixes-421-cves-one-exploited-zero-day/",
    "https://weather.gc.ca/en/location/index.html?coords=43.898%2C-78.939",
    "https://www.anthropic.com/news/claude-design-anthropic-labs",
    "https://cloudnative-pg.io/releases/cloudnative-pg-1-29.1-released/",
])
def test_article_urls_are_not_hubs(url):
    assert not dg.is_hub_url(url), url


@pytest.mark.parametrize("a,b", [
    ("Apollo Global Management confirms data breach amid hacking wave hitting financial firms",
     "Private equity giant Apollo confirms data breach amid hacking wave hitting financial firms"),
    ("Microsoft's August Patch Tuesday fixes 421 CVEs, including an exploited Windows zero-day",
     "Microsoft patches Windows zero-day exploited by North Korean hackers"),
    ("Toronto mayoral race heats up ahead of October 26 vote as Chow faces challenger Bradford",
     "Toronto mayoral race heats up as Chow faces Bradford challenge"),
    # a shared CVE id is the story, whatever the wording
    ("Critical CloudNativePG flaw (CVE-2026-44477) allows PostgreSQL superuser takeover",
     "CloudNativePG patches CVE-2026-44477 in metrics exporter"),
])
def test_same_story_detected_across_wordings(a, b):
    assert dg.same_story(a, b)


@pytest.mark.parametrize("a,b", [
    ("OpenAI launches ChatGPT for Teens",
     "OpenAI launches GPT-5.3-Codex, its most capable agentic coding model yet"),
    ("Iran compiles conditions for reopening Strait of Hormuz as US intensifies economic pressure",
     "US says Strait of Hormuz mines cleared as Iran tensions persist"),
    ("Anthropic launches Claude Design", "Anthropic closes $65B Series H"),
])
def test_different_stories_are_not_conflated(a, b):
    assert not dg.same_story(a, b)


# --- ingest ------------------------------------------------------------------

async def test_ingest_dedups_and_tags(sf):
    res = await ingest_digest(sf, DIGEST, run_id="r1")
    assert res.day == "2026-08-03" and len(res.new) == 2   # tracking-url dup dropped
    async with sf() as s:
        items = (await s.execute(select(Item))).scalars().all()
        topics = {t.slug: t for t in (await s.execute(select(Topic))).scalars()}
    assert len(items) == 2
    assert set(topics) == {"ai-industry", "business"}
    assert all(i.dedup_hash.startswith("https://ex.com/") for i in items)
    assert {i.published for i in items} == {"2026-08-03", "2026-08-02"}
    # replay: everything already archived → nothing new
    res2 = await ingest_digest(sf, DIGEST)
    assert res2.new == []


async def test_ingest_garbage_is_noop(sf):
    res = await ingest_digest(sf, "not a digest")
    assert (res.new, res.day) == ([], "")
    res = await ingest_digest(sf, json.dumps({"items": [{"no": "url"}]}))
    assert (res.new, res.day) == ([], "")


async def test_ingest_rejects_stale_and_undated_items(sf):
    res = await ingest_digest(sf, _digest(
        "2026-08-28",
        _item("CloudNativePG RCE", "https://a.com/2026/05/08/cnpg", published="2026-05-08"),
        _item("Series F", "https://b.com/2025/09/02/round", published="2025-09-02"),
        _item("Undated thing", "https://c.com/2026/08/28/x", published=None),
        _item("Fresh enough", "https://d.com/2026/08/27/y", published="2026-08-27"),
    ), max_age_days=2)
    assert [it["headline"] for it in res.new] == ["Fresh enough"]
    assert {(it["headline"], why) for it, why in res.rejected} == {
        ("CloudNativePG RCE", "stale"), ("Series F", "stale"), ("Undated thing", "undated")}
    async with sf() as s:
        assert (await s.execute(select(Item.title))).scalars().all() == ["Fresh enough"]


async def test_ingest_rejects_hub_urls(sf):
    res = await ingest_digest(sf, _digest(
        "2026-08-28",
        _item("OpenAI chip", "https://openai.com/news/", published="2026-08-28"),
        _item("Real article", "https://openai.com/index/jalapeno-chip/", published="2026-08-28"),
    ))
    assert [it["headline"] for it in res.new] == ["Real article"]
    assert res.rejected == [(res.rejected[0][0], "hub-url")]
    assert res.rejected[0][0]["headline"] == "OpenAI chip"


async def test_ingest_rejects_same_story_under_a_new_url(sf):
    first = await ingest_digest(sf, _digest(
        "2026-08-26",
        _item("Apollo Global Management confirms data breach amid hacking wave hitting financial firms",
              "https://techcrunch.com/2026/08/21/apollo-breach/", published="2026-08-26")))
    assert len(first.new) == 1
    again = await ingest_digest(sf, _digest(
        "2026-08-27",
        _item("Private equity giant Apollo confirms data breach amid hacking wave hitting financial firms",
              "https://bleepingcomputer.com/news/apollo-breach/", published="2026-08-27"),
        _item("Unrelated Redis CVE batch", "https://redis.io/blog/cve-2026-1/", published="2026-08-27")))
    assert [it["headline"] for it in again.new] == ["Unrelated Redis CVE batch"]
    assert [why for _, why in again.rejected] == ["duplicate-story"]


async def test_ingest_story_dedup_window_is_bounded(sf):
    """A headline resurfacing after the window is a new development, not a dup."""
    await ingest_digest(sf, _digest(
        "2026-07-01", _item("Redis patches critical RCE", "https://r.io/1", published="2026-07-01")))
    later = await ingest_digest(sf, _digest(
        "2026-08-27", _item("Redis patches critical RCE again", "https://r.io/2", published="2026-08-27")))
    assert len(later.new) == 1 and later.rejected == []


class _Producer:
    def __init__(self):
        self.sent: list[tuple[str, dict]] = []

    async def send_and_wait(self, topic, value):
        self.sent.append((topic, json.loads(value)))


async def test_handle_emits_rejected_events_and_footer(sf):
    loop = IngestLoop(sf, "kafka:9092", channel="news")
    producer = _Producer()
    digest = _digest(
        "2026-08-28",
        _item("Fresh", "https://d.com/2026/08/28/y", published="2026-08-28"),
        _item("Old", "https://a.com/2026/05/08/z", published="2026-05-08"))
    await loop.handle(producer, json.dumps({"result": digest, "run_id": "r9"}).encode())
    by_topic = {}
    for topic, env in producer.sent:
        by_topic.setdefault(topic, []).append(env)
    assert [e["data"]["headline"] for e in by_topic[TOPIC_INGESTED]] == ["Fresh"]
    rejected = by_topic[TOPIC_REJECTED]
    assert len(rejected) == 1 and rejected[0]["type"] == "news.item.rejected"
    assert rejected[0]["data"] == {"day": "2026-08-28", "headline": "Old",
                                   "url": "https://a.com/2026/05/08/z",
                                   "published": "2026-05-08", "reason": "stale",
                                   "run_id": "r9"}
    post = by_topic[TOPIC_CHANNEL_POST][0]["data"]["text"]
    assert "Fresh" in post and "Old" not in post
    assert post.rstrip().endswith("filtered: 1 stale")


async def test_handle_with_only_rejections_posts_nothing(sf):
    loop = IngestLoop(sf, "kafka:9092")
    producer = _Producer()
    await loop.handle(producer, json.dumps({"result": _digest(
        "2026-08-28", _item("Old", "https://a.com/1", published="2026-01-01"))}).encode())
    topics = [t for t, _ in producer.sent]
    assert TOPIC_REJECTED in topics and TOPIC_CHANNEL_POST not in topics


# --- schema migration --------------------------------------------------------

async def test_init_db_adds_missing_columns():
    """create_all never adds columns to a live table; init_db must backfill
    `published` on an archive created before it existed."""
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(text(
            "CREATE TABLE items (id VARCHAR(32) PRIMARY KEY, title VARCHAR(512), "
            "url VARCHAR(512), source VARCHAR(128), summary TEXT, topic_id INTEGER, "
            "day VARCHAR(10), run_id VARCHAR(32), dedup_hash VARCHAR(512) UNIQUE, "
            "raw JSON, ingested_at DATETIME)"))
    await init_db(engine)
    async with engine.connect() as conn:
        cols = await conn.run_sync(
            lambda c: {col["name"] for col in sa_inspect(c).get_columns("items")})
    await engine.dispose()
    assert "published" in cols


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
    assert items[0]["published"] == "2026-08-03"
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
