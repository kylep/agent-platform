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


# --- the wiki default grant backfill (docs/design/21) ------------------------
# The other half of "default-granted": creation covers every agent made from
# here on, this covers the ones that already exist. Its own mark, because the
# relay and tickets sweeps ran a release earlier and have already marked
# themselves — an agent that predates the wiki still has to be reached.

# `OTHER_SWEEPS` throughout this section: design-19's, design-20's and
# design-22's sweeps run over the same rows under their own marks, with their
# own tests, and letting them run here would have every assertion below carry
# grants it is not about.
OTHER_SWEEPS = dict(default_grant=False, tickets_grant=False, quota_grant=False)


async def _grants(sfx, name: str) -> list[str]:
    from agentplatform.db import AgentDef
    async with sfx() as s:
        return (await s.get(AgentDef, name)).platform_tools


async def test_wiki_grant_backfill_covers_the_agents_that_already_exist(engine, sfx):
    from agentplatform.db import AgentDef, AgentVersion, WIKI_GRANT_MARK
    async with sfx() as s:
        s.add(AgentDef(name="news", prompt="p", description="d",
                       platform_tools=["mcp__platform__relay"]))
        s.add(AgentDef(name="retired", prompt="p", description="d",
                       platform_tools=[], enabled=False))
        await s.commit()
    await init_db(engine, **OTHER_SWEEPS)
    assert await _grants(sfx, "news") == ["mcp__platform__relay",
                                          "mcp__platform__wiki"]
    assert await _grants(sfx, "retired") == []      # disabled agents are left alone
    async with sfx() as s:
        assert await s.get(SchemaMark, WIKI_GRANT_MARK) is not None
        versions = list((await s.execute(select(AgentVersion).where(
            AgentVersion.agent == "news"))).scalars())
    # The sweep continues the design-15 change log, attributed to itself, so an
    # operator can find out later why an agent holds a tool nobody granted it.
    assert [(v.changed_by, v.changed_via) for v in versions] == [
        ("platform:wiki-default-grant", "migration")]


async def test_wiki_grant_backfill_honours_the_setting_and_runs_once(engine, sfx):
    """Off means the sweep does not run AND does not mark itself, so turning it
    on later still backfills; on means exactly one pass, ever."""
    from agentplatform.db import AgentDef, WIKI_GRANT_MARK
    async with sfx() as s:
        s.add(AgentDef(name="news", prompt="p", description="d", platform_tools=[]))
        await s.commit()
    await init_db(engine, wiki_grant=False, **OTHER_SWEEPS)
    assert await _grants(sfx, "news") == []
    async with sfx() as s:
        assert await s.get(SchemaMark, WIKI_GRANT_MARK) is None
    await init_db(engine, **OTHER_SWEEPS)
    assert await _grants(sfx, "news") == ["mcp__platform__wiki"]
    # An admin taking it away afterwards is not undone by the next boot.
    async with sfx() as s:
        (await s.get(AgentDef, "news")).platform_tools = []
        await s.commit()
    await init_db(engine, **OTHER_SWEEPS)
    assert await _grants(sfx, "news") == []


# --- the librarian and the gardener (docs/design/21) -------------------------
# The wiki ships with somebody who tends it: a system agent that answers @wiki,
# and a Sunday-morning job that asks it what needs writing. Both are one-time
# seeds behind their own marks, so an admin who edits or deletes either one
# keeps their version.

async def test_the_wiki_agent_is_seeded(engine, sfx):
    from agentplatform.agentspec import TOOL_RELAY, TOOL_TICKETS, TOOL_WIKI, TOOL_QUOTA
    from agentplatform.db import (WIKI_AGENT_MARK, WIKI_AGENT_PROMPT, AgentDef,
                                  AgentVersion)
    await init_db(engine)
    async with sfx() as s:
        row = await s.get(AgentDef, "wiki")
        assert row is not None
        assert (row.system, row.enabled, row.can_invoke) == (True, True, False)
        assert row.prompt == WIKI_AGENT_PROMPT
        assert row.description.startswith("The wiki's librarian")
        assert row.platform_tools == [TOOL_RELAY, TOOL_TICKETS, TOOL_WIKI, TOOL_QUOTA]
        assert (row.harness_tools, row.skills, row.secrets) == ([], [], [])
        assert (row.model, row.role) == ("", "operator")
        # No triggers of its own: the librarian is summoned, not scheduled —
        # the gardener job below is what puts a rhythm on it.
        assert row.entrypoints == {"crons": [], "webhooks": [], "topics": [],
                                   "timezone": ""}
        versions = list((await s.execute(select(AgentVersion).where(
            AgentVersion.agent == "wiki"))).scalars())
        assert [(v.version, v.changed_by, v.changed_via) for v in versions] == [
            (1, "system:wiki", "migration")]
        assert versions[0].snapshot["platform_tools"] == [
            TOOL_RELAY, TOOL_TICKETS, TOOL_WIKI, TOOL_QUOTA]
        assert versions[0].snapshot["system"] is True
        assert await s.get(SchemaMark, WIKI_AGENT_MARK) is not None


async def test_an_existing_wiki_agent_is_adopted_not_overwritten(engine, sfx):
    """A human may have made one first, and a boot-time seed is the last thing
    that should have an opinion about somebody else's agent."""
    from agentplatform.db import WIKI_AGENT_MARK, AgentDef, AgentVersion
    async with sfx() as s:
        s.add(AgentDef(name="wiki", prompt="mine", description="mine",
                       platform_tools=[]))
        await s.commit()
    await init_db(engine)
    async with sfx() as s:
        row = await s.get(AgentDef, "wiki")
        assert (row.prompt, row.system) == ("mine", False)
        # The default-grant sweeps still reach it, as they reach every agent
        # that predates the wiki — but nothing here writes the librarian's own
        # definition over theirs.
        authors = list((await s.execute(select(AgentVersion.changed_by).where(
            AgentVersion.agent == "wiki"))).scalars())
        assert "system:wiki" not in authors
        assert await s.get(SchemaMark, WIKI_AGENT_MARK) is not None


async def test_the_wiki_agent_mark_is_the_off_switch(engine, sfx):
    from agentplatform.db import AgentDef
    await init_db(engine)
    async with sfx() as s:
        await s.delete(await s.get(AgentDef, "wiki"))
        await s.commit()
    await init_db(engine)
    async with sfx() as s:
        assert await s.get(AgentDef, "wiki") is None


async def test_the_gardener_job_is_seeded(engine, sfx):
    from agentplatform.db import WIKI_GARDENER_MARK, ScheduledJob
    await init_db(engine)
    async with sfx() as s:
        job = (await s.execute(select(ScheduledJob).where(
            ScheduledJob.name == "wiki-gardener"))).scalar_one()
        assert (job.cron, job.timezone) == ("0 10 * * 0", "America/Toronto")
        # A relay-post job, not an agent run: the summons has to come from the
        # platform, because an agent's own @mention carries a hop.
        assert (job.relay_channel, job.agent) == ("wiki", None)
        assert job.prompt.startswith("@wiki — which pages")
        assert (job.enabled, job.next_fire) == (True, None)
        assert await s.get(SchemaMark, WIKI_GARDENER_MARK) is not None


async def test_the_gardener_job_mark_is_the_off_switch(engine, sfx):
    from agentplatform.db import ScheduledJob
    await init_db(engine)
    async with sfx() as s:
        await s.execute(ScheduledJob.__table__.delete().where(
            ScheduledJob.__table__.c.name == "wiki-gardener"))
        await s.commit()
    await init_db(engine)
    async with sfx() as s:
        assert (await s.execute(select(func.count()).select_from(
            ScheduledJob.__table__).where(
                ScheduledJob.__table__.c.name == "wiki-gardener"))).scalar_one() == 0


async def test_the_librarian_seeds_are_idempotent(engine, sfx):
    from agentplatform.db import AgentDef, AgentVersion, ScheduledJob
    await init_db(engine)
    await init_db(engine)
    async def counts():
        async with sfx() as s:
            return [(await s.execute(select(func.count()).select_from(t))).scalar_one()
                    for t in (AgentDef.__table__, AgentVersion.__table__,
                              ScheduledJob.__table__)]
    assert await counts() == [1, 1, 2]      # the librarian, its v1, standup + gardener


async def test_a_gardener_job_that_already_exists_is_adopted(engine, sfx):
    """`scheduled_jobs.name` has no unique index, so the mark being absent
    while the row is not — a restored backup, a job an admin wrote by hand —
    must not leave #wiki with two gardeners asking the same question."""
    from agentplatform.db import WIKI_GARDENER_MARK, ScheduledJob
    async with sfx() as s:
        s.add(ScheduledJob(name="wiki-gardener", relay_channel="wiki",
                           cron="0 6 * * 1", prompt="mine"))
        await s.commit()
    await init_db(engine)
    async with sfx() as s:
        job = (await s.execute(select(ScheduledJob).where(
            ScheduledJob.name == "wiki-gardener"))).scalar_one()
        assert (job.cron, job.prompt) == ("0 6 * * 1", "mine")
        assert await s.get(SchemaMark, WIKI_GARDENER_MARK) is not None
