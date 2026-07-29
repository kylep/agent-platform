from datetime import timedelta

from sqlalchemy import select

from agentplatform.db import SharedNews, utcnow
from agentplatform.newsprojector import (format_post, parse_digest, project, sanitize)

DIGEST = {
    "date": "2026-07-29",
    "items": [
        {"section": "AI industry", "headline": "H1", "why": "matters", "url": "https://a.example/1"},
        {"section": "Security", "headline": "H2", "why": "bad", "url": "https://b.example/2"},
    ],
}
import json
DIGEST_JSON = json.dumps(DIGEST)


def test_parse_digest_plain_fenced_and_prose():
    assert parse_digest(DIGEST_JSON)["date"] == "2026-07-29"
    assert parse_digest(f"```json\n{DIGEST_JSON}\n```")["items"][0]["headline"] == "H1"
    assert parse_digest(f"Here is the digest:\n{DIGEST_JSON}\nDone.") is not None
    assert parse_digest("not json at all") is None
    assert parse_digest("") is None


def test_sanitize_neutralizes_mentions():
    out = sanitize("ping @everyone and @here <@&12345> <@678> done")
    assert "@everyone" not in out and "@here" not in out
    assert "<@&12345>" not in out and "<@678>" not in out
    assert "everyone" in out and "done" in out


def test_format_post_orders_sections_and_wraps_urls():
    text = format_post("2026-07-29", DIGEST["items"])
    assert "AI industry" in text and "Security" in text
    assert text.index("AI industry") < text.index("Security")   # known-section order
    assert "<https://a.example/1>" in text                       # angle-wrapped


async def test_project_dedups_records_and_prunes(sf):
    # First run: both items are new → posts + records.
    async with sf() as s:
        text = await project(s, DIGEST_JSON)
    assert text and "H1" in text and "H2" in text
    async with sf() as s:
        urls = set((await s.execute(select(SharedNews.url))).scalars())
    assert urls == {"https://a.example/1", "https://b.example/2"}

    # Second run, same digest → everything is a dup → nothing to post.
    async with sf() as s:
        assert await project(s, DIGEST_JSON) is None

    # Mixed: one known + one new → only the new one posts.
    mixed = json.dumps({"date": "2026-07-30", "items": [
        DIGEST["items"][0],
        {"section": "World", "headline": "H3", "why": "new", "url": "https://c.example/3"}]})
    async with sf() as s:
        text = await project(s, mixed)
    assert text and "H3" in text and "H1" not in text


async def test_project_dedups_duplicate_urls_within_a_batch(sf):
    # A gatherer can list the same URL twice; the projector must not try to
    # insert the PK twice (that crashed the recorder live).
    dupe = json.dumps({"date": "d", "items": [
        {"section": "AI industry", "headline": "A", "why": "x", "url": "https://dup.example/1"},
        {"section": "World", "headline": "B", "why": "y", "url": "https://dup.example/1"}]})
    async with sf() as s:
        text = await project(s, dupe)
    assert text is not None
    async with sf() as s:
        rows = list((await s.execute(select(SharedNews.url))).scalars())
    assert rows == ["https://dup.example/1"]     # recorded exactly once


async def test_project_prunes_old_records(sf):
    async with sf() as s:
        s.add(SharedNews(url="https://old.example/x", posted_at=utcnow() - timedelta(days=30)))
        await s.commit()
    async with sf() as s:
        await project(s, DIGEST_JSON, days=14)     # any project call prunes >days
    async with sf() as s:
        urls = set((await s.execute(select(SharedNews.url))).scalars())
    assert "https://old.example/x" not in urls      # pruned


async def test_project_empty_or_invalid_returns_none(sf):
    async with sf() as s:
        assert await project(s, json.dumps({"date": "d", "items": []})) is None
        assert await project(s, "garbage") is None


# --- recorder integration: a gatherer result frame → discord.channel.post ---

async def _feed_result(sf, agent, frame_extra, rid="r1"):
    from agentplatform.db import Run
    from agentplatform.events import FakeProducer, TOPIC_CHANNEL_POST
    from agentplatform.recorder import Recorder
    producer = FakeProducer()
    rec = Recorder(sf, producer, news_gatherer_agent="news", news_channel="news")
    async with sf() as s:
        s.add(Run(id=rid, agent=agent, trigger="schedule", requested_by="job", prompt="go"))
        await s.commit()
    await rec._handle_transcript(rid, {"type": "result", "seq": 1, "result": DIGEST_JSON, **frame_extra})
    return [d for t, _, d in producer.published if t == TOPIC_CHANNEL_POST]


async def test_recorder_projects_gatherer_result(sf):
    posts = await _feed_result(sf, "news", {"is_error": False})
    assert len(posts) == 1 and posts[0]["channel"] == "news" and "H1" in posts[0]["text"]


async def test_recorder_ignores_other_agents_and_errors(sf):
    assert await _feed_result(sf, "pai", {"is_error": False}, rid="ra") == []   # not the gatherer
    assert await _feed_result(sf, "news", {"is_error": True}, rid="rb") == []    # failed run
