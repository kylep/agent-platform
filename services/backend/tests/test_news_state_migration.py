"""News state copy and new writer retain the archive's crucial behavior."""
import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from agentplatform.appdata import news_cutover
from agentplatform.appdata.access import Access, Caller, RecordError
from agentplatform.appdata.definitions import validate_app
from agentplatform.appdata.lifecycle import page_for_web
from agentplatform.appdata.models import AppDataApp, AppDataRecord
from agentplatform.appdata.news_ingest import ingest_digest
from agentplatform.appdata.news_migration import bundle, convert_snapshot
from agentplatform.db import Base, make_engine, make_session_factory

NOW = datetime(2026, 10, 4, tzinfo=timezone.utc)


def snapshot():
    return {
        "topics": [{"id": 2, "slug": "world", "label": "World", "color": 3}],
        "items": [{"id": "a" * 32, "title": "Example story", "url": "https://example.com/story-1",
                   "source": "example.com", "summary": "The reason", "topic_id": 2,
                   "day": "2026-10-04", "published": "2026-10-04", "run_id": "b" * 32,
                   "dedup_hash": "https://example.com/story-1", "raw": {},
                   "ingested_at": NOW}],
    }


def test_definition_and_conversion():
    definitions = validate_app(bundle())
    assert set(definitions.collections) == {"topics", "items"}
    rows = convert_snapshot(snapshot())
    assert rows["items"][0].doc["topic"] == rows["topics"][0].id
    assert rows["items"][0].doc["title"] == "Example story"
    home = page_for_web(definitions.pages["home"], definitions)
    assert home["params"]["q"]["type"] == "string"
    assert next(block for block in home["components"]
                if block["kind"] == "calendar")["day_link"] == {
                    "page": "day", "param": "day"}


def test_olu_scheduled_reader_can_read_news_without_write_access():
    parsed = validate_app(bundle())
    for collection in parsed.collections.values():
        access = Access(collection, Caller("agent:olu"), "agent:news")
        access.require_rows()
        assert all(access.can_read(field) for field in collection.fields)
        for verb in ("create", "update", "delete"):
            with pytest.raises(RecordError, match="AD-FORBIDDEN"):
                access.require_verb(verb)


def test_missing_topic_and_duplicate_refused():
    source = snapshot()
    source["items"][0]["topic_id"] = 99
    with pytest.raises(ValueError, match="missing topic"):
        convert_snapshot(source)
    source = snapshot()
    source["items"].append(dict(source["items"][0]))
    with pytest.raises(ValueError, match="duplicate News"):
        convert_snapshot(source)


@pytest.fixture
async def sf(tmp_path):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path}/news.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = make_session_factory(engine)
    yield factory
    await engine.dispose()


async def test_cutover_then_dedup_and_fresh_story(sf, monkeypatch):
    db_url = str(sf.kw["bind"].url)
    monkeypatch.setenv("AP_DB_URL", db_url)

    async def source(_session):
        return snapshot()

    monkeypatch.setattr(news_cutover, "read_source", source)
    assert (await news_cutover.copy())["counts"] == {"topics": 1, "items": 1}
    assert (await news_cutover.copy(apply=True))["verified_records"] == 2
    digest = json.dumps({"date": "2026-10-04", "items": [
        {"headline": "Example story", "why": "Again", "url": "https://example.com/story-1",
         "section": "World", "published": "2026-10-04"},
        {"headline": "A different event", "why": "Fresh and useful",
         "url": "https://example.com/story-2", "section": "World",
         "published": "2026-10-04"}]})
    result = await ingest_digest(sf, digest, "c" * 32)
    assert [reason for _, reason in result["rejected"]] == ["duplicate-url"]
    assert len(result["new"]) == 1
    async with sf() as session:
        app = (await session.execute(select(AppDataApp).where(
            AppDataApp.name == "news"))).scalar_one()
        count = (await session.execute(select(func.count()).select_from(
            AppDataRecord).where(AppDataRecord.app_id == app.id,
                                 AppDataRecord.collection == "items"))).scalar_one()
        assert count == 2


async def test_freshness_gates_and_story_dedup(sf, monkeypatch):
    monkeypatch.setenv("AP_DB_URL", str(sf.kw["bind"].url))

    async def source(_session):
        return {"topics": [], "items": []}

    monkeypatch.setattr(news_cutover, "read_source", source)
    await news_cutover.copy(apply=True)
    def digest(day, *items):
        return json.dumps({"date": day, "items": list(items)})
    def item(title, url, published, section="Security"):
        return {"headline": title, "url": url, "published": published,
                "section": section, "why": "A useful explanation"}
    result = await ingest_digest(sf, digest("2026-10-04",
        item("Old advisory", "https://example.com/2026/09/old-advisory", "2026-09-20"),
        item("Undated advisory", "https://example.com/2026/10/undated-advisory", None),
        item("Hub page", "https://example.com/news", "2026-10-04"),
        item("Fresh security update", "https://example.com/2026/10/fresh-update", "2026-10-04")))
    assert [reason for _, reason in result["rejected"]] == ["stale", "undated", "hub-url"]
    assert len(result["new"]) == 1
    replay = await ingest_digest(sf, digest("2026-10-04",
        item("Fresh security update", "https://other.com/2026/10/fresh-update", "2026-10-04")))
    assert [reason for _, reason in replay["rejected"]] == ["duplicate-story"]
    weather = item("Today's forecast", "https://weather.gc.ca/en/location/index.html?coords=1",
                   "2026-10-04", "Weather")
    assert len((await ingest_digest(sf, digest("2026-10-04", weather)))["new"]) == 1
    weather["published"] = "2026-10-05"
    assert len((await ingest_digest(sf, digest("2026-10-05", weather)))["new"]) == 1
