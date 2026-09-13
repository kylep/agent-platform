"""Wiki schema (docs/design/21): the page, version and link tables, and the
one-off seed that ships the `home` page and the `#wiki` room."""
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from agentplatform.db import (WIKI_HOME_BODY, WIKI_SEED_MARK, Base, Conversation,
                              SchemaMark, WikiLink, WikiPage, WikiVersion,
                              init_db, make_engine, make_session_factory)


@pytest.fixture
async def engine():
    """Tables but no wiki migration yet — the shape init_db finds on the first
    boot after the Wiki ships."""
    e = make_engine("sqlite+aiosqlite:///:memory:")
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield e
    await e.dispose()


@pytest.fixture
def sfx(engine):
    return make_session_factory(engine)


async def test_wiki_models_round_trip(sfx):
    async with sfx() as s:
        page = WikiPage(slug="deploying", title="Deploying", body="ship it",
                        summary="ship it", tags=["ops", "howto"],
                        created_by="user:admin", updated_by="agent:pai",
                        source_memory_id="m1")
        s.add(page); await s.commit()
        s.add(WikiVersion(page_id=page.id, version=1, title="Deploying",
                          body="ship it", author="user:admin", run_id="r1",
                          reason="first cut"))
        s.add(WikiLink(from_page_id=page.id, to_slug="standup"))
        await s.commit()
    async with sfx() as s:
        got = (await s.execute(select(WikiPage).where(WikiPage.slug == "deploying"))).scalar_one()
        assert len(got.id) == 32 and got.version == 1
        assert got.tags == ["ops", "howto"] and got.source_memory_id == "m1"
        assert (got.created_by, got.updated_by) == ("user:admin", "agent:pai")
        assert got.archived_at is None
        assert got.created_at is not None and got.updated_at is not None
        ver = (await s.execute(select(WikiVersion))).scalar_one()
        assert len(ver.id) == 32 and (ver.page_id, ver.version) == (got.id, 1)
        assert (ver.author, ver.run_id, ver.reason) == ("user:admin", "r1", "first cut")
        assert ver.created_at is not None
        link = (await s.execute(select(WikiLink))).scalar_one()
        assert (link.from_page_id, link.to_slug) == (got.id, "standup")


async def test_a_page_defaults_to_version_one_with_no_tags(sfx):
    async with sfx() as s:
        s.add(WikiPage(slug="home", title="Home", created_by="system:wiki",
                       updated_by="system:wiki"))
        await s.commit()
    async with sfx() as s:
        page = (await s.execute(select(WikiPage))).scalar_one()
        assert (page.version, page.tags, page.body, page.summary) == (1, [], "", "")


async def test_a_duplicate_version_is_refused(sfx):
    """The history is append-only and a version number names one write: two
    rows claiming v2 of a page is a lost edit, so the constraint is real."""
    async with sfx() as s:
        s.add(WikiVersion(page_id="p1", version=2, title="t", body="b",
                          author="agent:pai"))
        await s.commit()
    async with sfx() as s:
        s.add(WikiVersion(page_id="p1", version=2, title="t", body="other",
                          author="agent:news"))
        with pytest.raises(IntegrityError):
            await s.commit()


async def test_the_home_page_is_seeded(engine, sfx):
    await init_db(engine)
    async with sfx() as s:
        page = (await s.execute(select(WikiPage))).scalar_one()
        assert (page.slug, page.title, page.version) == ("home", "Home", 1)
        assert page.body == WIKI_HOME_BODY
        assert (page.created_by, page.updated_by) == ("system:wiki", "system:wiki")
        # The first paragraph, one line, inside the column.
        assert page.summary.startswith("This is the platform's wiki")
        assert "\n" not in page.summary and len(page.summary) <= 280
        ver = (await s.execute(select(WikiVersion))).scalar_one()
        assert (ver.page_id, ver.version, ver.author) == (page.id, 1, "system:wiki")
        assert ver.body == WIKI_HOME_BODY
        links = (await s.execute(select(WikiLink).order_by(WikiLink.to_slug))).scalars().all()
        assert [(l.from_page_id, l.to_slug) for l in links] == [
            (page.id, "deploying"), (page.id, "standup")]
        assert await s.get(SchemaMark, WIKI_SEED_MARK) is not None


async def test_the_wiki_channel_is_seeded(engine, sfx):
    await init_db(engine)
    async with sfx() as s:
        chan = (await s.execute(select(Conversation).where(
            Conversation.name == "wiki"))).scalar_one()
    assert (chan.kind, chan.open, chan.topic) == (
        "channel", True, "every edit, as a diff card")
    # A room, not a project: the ticket prefix is derived for API-created
    # channels (design-20) and the seed must not claim one.
    assert chan.ticket_prefix is None


async def test_a_home_page_that_already_exists_is_adopted(engine, sfx):
    """The mark can be absent while the page is not — a restored backup, a
    cleared mark — and `slug` is unique, so an unconditional insert would take
    down every service's boot on the one transaction they all run."""
    async with sfx() as s:
        s.add(WikiPage(id="p0", slug="home", title="Mine", body="my own words",
                       created_by="user:admin", updated_by="user:admin"))
        await s.commit()
    await init_db(engine)
    async with sfx() as s:
        page = (await s.execute(select(WikiPage))).scalar_one()
        assert (page.id, page.title, page.body) == ("p0", "Mine", "my own words")
        assert (await s.execute(select(func.count())
                .select_from(WikiVersion.__table__))).scalar_one() == 0
        assert (await s.execute(select(func.count())
                .select_from(WikiLink.__table__))).scalar_one() == 0
        assert await s.get(SchemaMark, WIKI_SEED_MARK) is not None


async def test_init_db_is_idempotent(engine, sfx):
    await init_db(engine)
    async def counts():
        async with sfx() as s:
            return [(await s.execute(select(func.count()).select_from(t))).scalar_one()
                    for t in (WikiPage.__table__, WikiVersion.__table__,
                              WikiLink.__table__, Conversation.__table__)]
    before = await counts()
    await init_db(engine)
    assert await counts() == before


async def test_the_mark_is_the_off_switch(engine, sfx):
    """Once seeded this never looks at the wiki tables again: a home page
    somebody deleted stays deleted, exactly as the relay and ticket seeds do."""
    await init_db(engine)
    async with sfx() as s:
        await s.execute(WikiPage.__table__.delete())
        await s.commit()
    await init_db(engine)
    async with sfx() as s:
        assert (await s.execute(select(func.count())
                .select_from(WikiPage.__table__))).scalar_one() == 0
