"""The wiki store (docs/design/21, T3): the ONE place a page changes.

A page is a row, its history, its links and a diff card in `#wiki`, and those
four only ever agree because every write goes through here. So these tests
assert on what the write LEFT BEHIND — the version row, the link rows, the
message in the room, the Kafka payload — rather than on return values: a store
that returned the right page while posting no card is a wiki nobody watches.

The guards are the half worth keeping honest. Optimistic concurrency is only
real if the loser writes nothing at all; `append` is only the safe shape if it
never conflicts; an agent must act from its own run or the hop on everything it
posts is a lie; and the whole change is one transaction, so a write that failed
is a write that never happened."""
import pytest
from sqlalchemy import func, select

from agentplatform import wiki_store as store
from agentplatform.db import (Conversation, RelayMessage, Run, RunState,
                              WikiLink, WikiPage, WikiVersion, utcnow)
from agentplatform.events import (TOPIC_RELAY_MESSAGES, TOPIC_WIKI_EVENTS,
                                  FakeProducer)
from agentplatform.relay import SYSTEM_AUTHOR
from agentplatform.wiki import BUDGET_PREFIX


class BrokenProducer(FakeProducer):
    """A broker that is down. Every publish raises, and nothing about a page
    may depend on one succeeding."""

    async def publish(self, *args, **kwargs):
        raise RuntimeError("broker down")


async def _run(sf, *, agent: str, depth: int = 0) -> Run:
    async with sf() as s:
        run = Run(agent=agent, trigger="relay", requested_by=f"agent:{agent}",
                  depth=depth, state=RunState.RUNNING, prompt="p")
        s.add(run)
        await s.commit()
        return run


async def _room(sf) -> Conversation:
    """The seeded `#wiki` channel every card lands in."""
    async with sf() as s:
        return (await s.execute(select(Conversation).where(
            Conversation.kind == "channel", Conversation.name == "wiki"))).scalars().one()


async def _messages(sf, channel_id: str) -> list[RelayMessage]:
    async with sf() as s:
        return list((await s.execute(select(RelayMessage).where(
            RelayMessage.channel_id == channel_id)
            .order_by(RelayMessage.created_at))).scalars())


async def _versions(sf, page_id: str) -> list[WikiVersion]:
    async with sf() as s:
        return list((await s.execute(select(WikiVersion).where(
            WikiVersion.page_id == page_id).order_by(WikiVersion.version))).scalars())


async def _links(sf, page_id: str) -> set[str]:
    async with sf() as s:
        return set((await s.execute(select(WikiLink.to_slug).where(
            WikiLink.from_page_id == page_id))).scalars())


async def _pages(sf) -> list[WikiPage]:
    async with sf() as s:
        return list((await s.execute(select(WikiPage).order_by(WikiPage.slug))).scalars())


async def _create(sf, producer, *, slug="deploying", actor="user:admin",
                  title="Deploying", body="How a change reaches the NUC.", **kw):
    async with sf() as s:
        return await store.create_page(s, producer, actor=actor, slug=slug,
                                       title=title, body=body, **kw)


def _wiki_events(producer) -> list[dict]:
    return [data for topic, _, data in producer.published if topic == TOPIC_WIKI_EVENTS]


async def test_create_makes_v1_a_card_and_an_event(sf, producer, seed_agent):
    await seed_agent("pai", description="t")
    run = await _run(sf, agent="pai")
    room = await _room(sf)
    before = len(await _messages(sf, room.id))

    page = await _create(sf, producer, actor="agent:pai", run=run,
                         body="Sync, then [[home]].", reason="first pass",
                         tags=["ops"], url_base="https://ap.example")

    assert (page.version, page.slug, page.created_by) == (1, "deploying", "agent:pai")
    assert page.updated_by == "agent:pai" and page.summary == "Sync, then home."
    versions = await _versions(sf, page.id)
    assert [v.version for v in versions] == [1]
    assert (versions[0].author, versions[0].run_id) == ("agent:pai", run.id)
    assert versions[0].reason == "first pass" and versions[0].body == "Sync, then [[home]]."
    assert await _links(sf, page.id) == {"home"}

    rows = await _messages(sf, room.id)
    assert len(rows) == before + 1
    card = rows[-1]
    assert card.kind == "event" and card.mentions == [] and card.author == "agent:pai"
    assert card.card == {"type": "wiki", "slug": "deploying", "title": "Deploying",
                         "version": 1, "author": "agent:pai", "reason": "first pass",
                         "added": 1, "removed": 0,
                         "url": "https://ap.example/wiki/deploying"}
    assert card.body == '📖 [[deploying]] v1 · pai: "first pass" (+1 −0)'
    assert card.hop == 1 and card.run_id == run.id

    assert any(t == TOPIC_RELAY_MESSAGES for t, _, _ in producer.published)
    (event,) = _wiki_events(producer)
    assert event["event"] == "created" and event["version"] == 1
    assert (event["author"], event["run_id"]) == ("agent:pai", run.id)
    assert (event["added"], event["removed"]) == (1, 0)
    assert event["page"]["slug"] == "deploying" and event["reason"] == "first pass"


async def test_a_card_summons_nobody(sf, producer, seed_agent):
    """A title and a reason are somebody else's words quoted by the platform."""
    await seed_agent("pai", description="t")
    room = await _room(sf)
    await _create(sf, producer, title="@pai look", reason="@all please review")
    card = (await _messages(sf, room.id))[-1]
    assert card.kind == "event" and card.mentions == []


async def test_a_write_with_the_right_base_makes_v2(sf, producer):
    page = await _create(sf, producer, body="one\n\nlinks: [[home]]")
    async with sf() as s:
        page = await store.write_page(s, producer, "deploying", actor="user:admin",
                                      body="one\ntwo\n\nlinks: [[standup]]",
                                      base_version=1, reason="more")
    assert page.version == 2
    assert [v.version for v in await _versions(sf, page.id)] == [1, 2]
    # Links are rewritten, not accumulated: a body that stopped naming a slug
    # has no link to it.
    assert await _links(sf, page.id) == {"standup"}
    assert page.summary == "one two"

    event = _wiki_events(producer)[-1]
    assert event["event"] == "edited" and event["version"] == 2
    assert (event["added"], event["removed"]) == (2, 1)


async def test_a_write_needs_a_base_version(sf, producer):
    """On an existing page the base is how a writer proves it read the row."""
    await _create(sf, producer)
    async with sf() as s:
        with pytest.raises(store.WikiRuleError):
            await store.write_page(s, producer, "deploying", actor="user:admin",
                                   body="clobbered")
        await s.rollback()
    assert [p.body for p in await _pages(sf) if p.slug == "deploying"] != ["clobbered"]


async def test_a_stale_base_conflicts_and_writes_nothing(sf, producer):
    page = await _create(sf, producer, body="v1 body")
    async with sf() as s:
        await store.write_page(s, producer, "deploying", actor="user:admin",
                               body="v2 body", base_version=1, reason="winner")
    before = len(producer.published)

    async with sf() as s:
        with pytest.raises(store.WikiConflictError) as caught:
            await store.write_page(s, producer, "deploying", actor="user:admin",
                                   body="loser body", base_version=1)
        await s.rollback()
    assert caught.value.current_version == 2
    assert caught.value.current_summary == "v2 body"
    assert isinstance(caught.value, store.WikiRuleError)

    async with sf() as s:
        assert (await s.get(WikiPage, page.id)).body == "v2 body"
    assert [v.version for v in await _versions(sf, page.id)] == [1, 2]
    assert producer.published[before:] == []


async def test_append_never_conflicts_and_creates_when_absent(sf, producer):
    room = await _room(sf)
    page = await store_append(sf, producer, "release-notes", "first note")
    assert (page.version, page.title) == (1, "Release Notes")
    assert page.body == "first note"

    page = await store_append(sf, producer, "release-notes", "second note")
    assert page.version == 2 and page.body == "first note\n\nsecond note"
    assert [v.version for v in await _versions(sf, page.id)] == [1, 2]
    assert _wiki_events(producer)[-1]["event"] == "appended"
    assert len([r for r in await _messages(sf, room.id) if r.kind == "event"]) == 2


async def store_append(sf, producer, slug, body, **kw):
    async with sf() as s:
        return await store.append_page(s, producer, slug, actor="user:admin",
                                       body=body, **kw)


async def test_archive_hides_a_page_from_wanted_and_backlinks(sf, producer):
    await _create(sf, producer, slug="runbook", title="Runbook",
                  body="see [[rollbacks]] and [[home]]")
    async with sf() as s:
        assert [p.slug for p in await store.backlinks(s, "home")] == ["runbook"]
        assert ("rollbacks", ["runbook"]) in [
            (w["slug"], w["linked_from"]) for w in await store.wanted(s)]

    async with sf() as s:
        page = await store.archive_page(s, producer, "runbook", actor="user:admin",
                                        reason="superseded")
    assert page.archived_at is not None
    async with sf() as s:
        assert await store.backlinks(s, "home") == []
        assert "rollbacks" not in [w["slug"] for w in await store.wanted(s)]
        # The seeded home page's own red links are untouched by any of it.
        assert "standup" in [w["slug"] for w in await store.wanted(s)]

    event = _wiki_events(producer)[-1]
    assert event["event"] == "archived" and (event["added"], event["removed"]) == (0, 0)
    assert event["page"]["archived_at"] is not None
    room = await _room(sf)
    assert (await _messages(sf, room.id))[-1].card["reason"] == "superseded"

    # And an archived page stops answering to its slug: `home` links to
    # [[deploying]], so archiving it puts deploying back on the wanted list.
    await _create(sf, producer, slug="deploying", title="Deploying", body="x")
    async with sf() as s:
        assert "deploying" not in [w["slug"] for w in await store.wanted(s)]
        await store.archive_page(s, producer, "deploying", actor="user:admin")
    async with sf() as s:
        assert ("deploying", ["home"]) in [
            (w["slug"], w["linked_from"]) for w in await store.wanted(s)]

    # Writing to an archived page is refused — restore it first.
    async with sf() as s:
        with pytest.raises(store.WikiRuleError):
            await store.write_page(s, producer, "runbook", actor="user:admin",
                                   body="x", base_version=1)
        with pytest.raises(store.WikiRuleError):
            await store.append_page(s, producer, "runbook", actor="user:admin", body="x")
        await s.rollback()


async def test_archiving_twice_is_refused_under_the_lock(sf, producer):
    """The second DELETE is not a no-op: it would post a second card and a
    second event for a departure that already happened. The check is made under
    the row lock rather than by the caller, because two DELETEs that both read a
    live page both pass anything asked before it."""
    page = await _create(sf, producer, slug="runbook", title="Runbook", body="a")
    async with sf() as s:
        await store.archive_page(s, producer, "runbook", actor="user:admin")
    events, room = len(_wiki_events(producer)), await _room(sf)
    cards = len(await _messages(sf, room.id))
    async with sf() as s:
        with pytest.raises(store.WikiRuleError, match="already archived"):
            await store.archive_page(s, producer, "runbook", actor="user:admin")
        await s.rollback()
    assert len(_wiki_events(producer)) == events
    assert len(await _messages(sf, room.id)) == cards
    assert [v.version for v in await _versions(sf, page.id)] == [1]
    # ...and the mirror image: un-archiving a live page is refused too.
    async with sf() as s:
        await store.restore_page(s, producer, "runbook", actor="user:admin")
    async with sf() as s:
        with pytest.raises(store.WikiRuleError, match="not archived"):
            await store.restore_page(s, producer, "runbook", actor="user:admin")
        await s.rollback()


async def test_un_archiving_makes_no_version(sf, producer):
    page = await _create(sf, producer, slug="runbook", title="Runbook", body="a")
    async with sf() as s:
        await store.archive_page(s, producer, "runbook", actor="user:admin")
    async with sf() as s:
        page = await store.restore_page(s, producer, "runbook", actor="user:admin")
    assert page.archived_at is None and page.version == 1
    assert [v.version for v in await _versions(sf, page.id)] == [1]
    assert _wiki_events(producer)[-1]["event"] == "restored"


async def test_restore_to_a_version_makes_a_new_version(sf, producer):
    page = await _create(sf, producer, body="one", title="Deploying")
    async with sf() as s:
        await store.write_page(s, producer, "deploying", actor="user:admin",
                               body="two", title="Deploying v2", base_version=1)
    async with sf() as s:
        page = await store.restore_page(s, producer, "deploying", version=1,
                                        actor="user:admin")
    assert page.version == 3
    assert (page.body, page.title) == ("one", "Deploying")
    versions = await _versions(sf, page.id)
    assert [v.version for v in versions] == [1, 2, 3]
    assert versions[2].reason == "restored v1" and versions[2].body == "one"
    event = _wiki_events(producer)[-1]
    assert event["event"] == "restored" and event["version"] == 3
    assert (event["added"], event["removed"]) == (1, 1)

    # Rolling back is also how an archived page comes back at the version it
    # should have stayed at — one intention, one call.
    async with sf() as s:
        await store.archive_page(s, producer, "deploying", actor="user:admin")
    async with sf() as s:
        page = await store.restore_page(s, producer, "deploying", version=2,
                                        actor="user:admin")
    assert page.archived_at is None and (page.version, page.body) == (4, "two")


async def test_promote_records_its_provenance(sf, producer, seed_agent):
    await seed_agent("pai", description="t")
    run = await _run(sf, agent="pai")
    memory = {"id": "m" * 32, "agent": "pai", "key": "kyles-location",
              "content": "Kyle lives in Whitby."}
    async with sf() as s:
        page = await store.promote_memory(s, producer, memory, actor="agent:pai",
                                          run=run, slug="kyles-location",
                                          title="Kyle's location")
    assert page.source_memory_id == memory["id"]
    assert page.tags == ["memory", "pai"]
    assert page.body.startswith("Kyle lives in Whitby.")
    assert "Promoted from pai's memory `kyles-location` on " in page.body
    versions = await _versions(sf, page.id)
    assert versions[0].reason == "promoted from memory"
    assert _wiki_events(producer)[-1]["event"] == "promoted"

    # Promoting the same memory again rewrites its own page, while the page is
    # still nothing but the memory...
    async with sf() as s:
        page = await store.promote_memory(s, producer, {**memory, "content": "Whitby, ON."},
                                          actor="agent:pai", run=run,
                                          slug="kyles-location",
                                          title="Kyle's location")
    assert page.version == 2 and page.body.startswith("Whitby, ON.")
    assert [v.reason for v in await _versions(sf, page.id)] == [
        store.PROMOTE_REASON, store.PROMOTE_REASON]
    # ...but a slug that belongs to somebody else's page is taken.
    async with sf() as s:
        with pytest.raises(store.WikiRuleError):
            await store.promote_memory(s, producer, {**memory, "id": "n" * 32},
                                       actor="agent:pai", run=run,
                                       slug="kyles-location", title="x")
        await s.rollback()


async def test_a_promotion_never_overwrites_an_edit(sf, producer, seed_agent):
    """Once somebody has edited a promoted page, the page is the shared version
    of the fact and the memory is only where it started. Re-promoting would
    overwrite that work with a note the editor has already improved on, and
    without a base_version anyone could have named — so it conflicts."""
    await seed_agent("pai", description="t")
    run = await _run(sf, agent="pai")
    memory = {"id": "m" * 32, "agent": "pai", "key": "kyles-location",
              "content": "Kyle lives in Whitby."}
    async with sf() as s:
        page = await store.promote_memory(s, producer, memory, actor="agent:pai",
                                          run=run, slug="kyles-location",
                                          title="Kyle's location")
    async with sf() as s:
        await store.write_page(s, producer, "kyles-location", actor="user:admin",
                               body="Whitby, Ontario — moved there in 2019.",
                               base_version=1, reason="add the year")
    before = len(producer.published)

    async with sf() as s:
        with pytest.raises(store.WikiConflictError) as caught:
            await store.promote_memory(s, producer, memory, actor="agent:pai",
                                       run=run, slug="kyles-location",
                                       title="Kyle's location")
        await s.rollback()
    assert caught.value.current_version == 2
    assert "edited since it was promoted" in str(caught.value)

    async with sf() as s:
        row = await s.get(WikiPage, page.id)
        assert (row.version, row.body) == (2, "Whitby, Ontario — moved there in 2019.")
    assert [v.version for v in await _versions(sf, page.id)] == [1, 2]
    assert producer.published[before:] == []


async def test_an_agent_must_act_from_its_own_run(sf, producer, seed_agent):
    for name in ("news", "pai"):
        await seed_agent(name, description="t")
    news, pai = await _run(sf, agent="news"), await _run(sf, agent="pai")

    with pytest.raises(store.WikiRuleError):
        await _create(sf, producer, actor="agent:news")             # no run
    with pytest.raises(store.WikiRuleError):
        await _create(sf, producer, actor="agent:news", run=pai)    # someone else's
    with pytest.raises(store.WikiRuleError):
        await _create(sf, producer, actor="user:admin", run=news)   # a person has none
    assert [p.slug for p in await _pages(sf)] == ["home"]


async def test_the_caps_are_refused(sf, producer):
    with pytest.raises(store.WikiRuleError):
        await _create(sf, producer, slug="Not A Slug")
    with pytest.raises(store.WikiRuleError):
        await _create(sf, producer, title="")
    with pytest.raises(store.WikiRuleError):
        await _create(sf, producer, title="t" * 121)
    with pytest.raises(store.WikiRuleError):
        await _create(sf, producer, reason="r" * 201)
    with pytest.raises(store.WikiRuleError):
        await _create(sf, producer, tags=[f"t{i}" for i in range(21)])
    with pytest.raises(store.WikiRuleError):
        await _create(sf, producer, tags=["t" * 33])
    with pytest.raises(store.WikiRuleError):
        await _create(sf, producer, body="x" * 40, max_body_bytes=39)
    # Bytes, not characters: a page of emoji is four times its length.
    with pytest.raises(store.WikiRuleError):
        await _create(sf, producer, body="🧀" * 10, max_body_bytes=39)
    assert [p.slug for p in await _pages(sf)] == ["home"]

    await _create(sf, producer)
    with pytest.raises(store.WikiExistsError):
        await _create(sf, producer)          # the slug is taken


async def test_a_lost_create_race_is_a_rule_error(sf, producer, monkeypatch):
    """The slug lookup is the courteous refusal; the unique index is the guard.
    Two creates of the same new slug race whenever `#wiki` is not there to
    serialise them, and the loser must be told in the platform's own words —
    an IntegrityError escaping here is a 500 for a write somebody merely lost,
    and it would take the caller's whole transaction with it.

    Both doors raise the SAME class, which is what lets the API answer a lost
    race exactly as it answers a slug that was already taken when it looked."""
    await _create(sf, producer, slug="deploying")

    async def _no_page(*args, **kwargs):
        return None

    monkeypatch.setattr(store, "_page_by_slug", _no_page)
    async with sf() as s:
        with pytest.raises(store.WikiExistsError):
            await store.create_page(s, producer, actor="user:admin",
                                    slug="deploying", title="Deploying", body="b")
        # The savepoint rolled back, not the transaction: the session the
        # caller handed in is still usable.
        assert (await s.execute(
            select(func.count()).select_from(WikiPage))).scalar() == 2
    assert [p.slug for p in await _pages(sf)] == ["deploying", "home"]


async def test_the_write_budget_counts_only_that_actors_versions(sf, producer, seed_agent):
    for name in ("news", "pai"):
        await seed_agent(name, description="t")
    news, pai = await _run(sf, agent="news"), await _run(sf, agent="pai")
    await _create(sf, producer, slug="a", actor="agent:news", run=news)
    await _create(sf, producer, slug="b", actor="agent:news", run=news)
    await _create(sf, producer, slug="c", actor="agent:pai", run=pai)
    await _create(sf, producer, slug="d", actor="user:admin")
    now = utcnow()
    async with sf() as s:
        assert await store.agent_write_budget_left(s, "news", 20, now) == 18
        assert await store.agent_write_budget_left(s, "pai", 20, now) == 19
        assert await store.agent_write_budget_left(s, "other", 20, now) == 20
        from datetime import timedelta
        assert await store.agent_write_budget_left(
            s, "news", 20, now + timedelta(hours=2)) == 20
        assert await store.agent_write_budget_left(s, "news", 1, now) == 0


async def test_the_write_budget_refuses_and_writes_nothing(sf, producer, seed_agent):
    await seed_agent("news", description="t")
    run = await _run(sf, agent="news")
    for slug in ("a", "b"):
        await _create(sf, producer, slug=slug, actor="agent:news", run=run,
                      budget_limit=2)
    with pytest.raises(store.WikiBudgetError):
        await _create(sf, producer, slug="c", actor="agent:news", run=run,
                      budget_limit=2)
    # An append against the same budget is refused too, and a person is not
    # metered at all.
    with pytest.raises(store.WikiBudgetError):
        await store_append_as(sf, producer, "a", actor="agent:news", run=run,
                              budget_limit=2)
    await _create(sf, producer, slug="c", actor="user:admin", budget_limit=2)
    assert [p.slug for p in await _pages(sf)] == ["a", "b", "c", "home"]


async def store_append_as(sf, producer, slug, **kw):
    async with sf() as s:
        return await store.append_page(s, producer, slug, body="more", **kw)


async def test_the_budget_notice_is_said_once_an_hour(sf, producer):
    room = await _room(sf)
    async with sf() as s:
        conv = await s.get(Conversation, room.id)
        assert await store.say_budget_once(s, producer, conv, 30, utcnow()) is not None
        assert await store.say_budget_once(s, producer, conv, 30, utcnow()) is None
    notices = [r for r in await _messages(sf, room.id) if r.kind == "system"]
    assert len(notices) == 1
    assert notices[0].body.startswith(BUDGET_PREFIX)
    assert notices[0].author == SYSTEM_AUTHOR


async def test_history_counts_each_write(sf, producer):
    page = await _create(sf, producer, body="one")
    async with sf() as s:
        await store.write_page(s, producer, "deploying", actor="user:admin",
                               body="one\ntwo", base_version=1, reason="add two")
        await store.write_page(s, producer, "deploying", actor="user:admin",
                               body="two", base_version=2, reason="drop one")
    async with sf() as s:
        rows = await store.history(s, await s.get(WikiPage, page.id), limit=10)
    assert [r["version"] for r in rows] == [3, 2, 1]
    assert [(r["added"], r["removed"]) for r in rows] == [(0, 1), (1, 0), (1, 0)]
    assert rows[0]["reason"] == "drop one"
    # The window is honoured and the counts inside it are still right, because
    # the version before the window is read to compute them.
    async with sf() as s:
        rows = await store.history(s, await s.get(WikiPage, page.id), limit=1)
    assert [(r["version"], r["added"], r["removed"]) for r in rows] == [(3, 0, 1)]
    # An empty window is empty, and a negative one is an empty window and not
    # the whole history: the clamp has to reach the slice as well as the query.
    async with sf() as s:
        row = await s.get(WikiPage, page.id)
        assert await store.history(s, row, limit=0) == []
        assert await store.history(s, row, limit=-1) == []


async def test_a_write_is_one_transaction(sf, producer, monkeypatch):
    """A failure anywhere before the commit leaves the page untouched: no half
    version, no rewritten links, no card in the room. `_finish` is the
    injection point because by then everything else is staged."""
    page = await _create(sf, producer, body="one [[home]]")
    room = await _room(sf)
    before_msgs = len(await _messages(sf, room.id))
    before_pub = len(producer.published)

    async def boom(*args, **kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(store, "_finish", boom)
    async with sf() as s:
        with pytest.raises(RuntimeError):
            await store.write_page(s, producer, "deploying", actor="user:admin",
                                   body="two [[standup]]", base_version=1)
        await s.rollback()

    async with sf() as s:
        row = await s.get(WikiPage, page.id)
        assert (row.body, row.version) == ("one [[home]]", 1)
    assert [v.version for v in await _versions(sf, page.id)] == [1]
    assert await _links(sf, page.id) == {"home"}
    assert len(await _messages(sf, room.id)) == before_msgs
    assert producer.published[before_pub:] == []


async def test_a_broker_blip_costs_only_the_fan_out(sf, producer):
    """The row is the record. With every publish failing the write still lands
    whole — page, version, links and the card in the room."""
    page = await _create(sf, producer, body="one")
    room = await _room(sf)
    async with sf() as s:
        await store.write_page(s, BrokenProducer(), "deploying", actor="user:admin",
                               body="two [[home]]", base_version=1, reason="r")
    async with sf() as s:
        row = await s.get(WikiPage, page.id)
        assert (row.body, row.version) == ("two [[home]]", 2)
    assert await _links(sf, page.id) == {"home"}
    assert len([r for r in await _messages(sf, room.id) if r.kind == "event"]) == 2


async def test_a_missing_wiki_room_costs_the_card_not_the_write(sf, producer):
    """The room is where the wiki is watched, not where it lives: an admin who
    archived `#wiki` gets a wiki that still works and no cards."""
    async with sf() as s:
        conv = (await s.execute(select(Conversation).where(
            Conversation.name == "wiki"))).scalars().one()
        conv.archived_at = utcnow()
        await s.commit()
    page = await _create(sf, producer, body="one")
    assert page.version == 1
    assert [t for t, _, _ in producer.published] == [TOPIC_WIKI_EVENTS]


async def test_slugs_are_validated_not_normalised(sf, producer):
    """The doors slugify; the store refuses. A store that lower-cased would
    make `Deploying` and `deploying` the same page through one door and two
    through another."""
    for bad in ("Deploying", "-leading", "with space", "", "x" * 65, "home\nrm"):
        with pytest.raises(store.WikiRuleError):
            await _create(sf, producer, slug=bad)
    assert [p.slug for p in await _pages(sf)] == ["home"]


async def test_page_and_version_views_are_just_the_columns(sf, producer):
    page = await _create(sf, producer, body="one", tags=["ops"], reason="why")
    view = store.page_view(page)
    assert view["slug"] == "deploying" and view["tags"] == ["ops"]
    assert view["version"] == 1 and view["source_memory_id"] is None
    assert isinstance(view["created_at"], str) and view["archived_at"] is None
    version = (await _versions(sf, page.id))[0]
    assert store.version_view(version) == {
        "id": version.id, "page_id": page.id, "version": 1, "title": "Deploying",
        "body": "one", "author": "user:admin", "run_id": None, "reason": "why",
        "created_at": version.created_at.isoformat()}


async def test_a_page_that_vanished_is_a_rule_error(sf, producer):
    with pytest.raises(store.WikiRuleError):
        async with sf() as s:
            await store.archive_page(s, producer, "nope", actor="user:admin")
    with pytest.raises(store.WikiRuleError):
        async with sf() as s:
            await store.restore_page(s, producer, "nope", actor="user:admin")
    with pytest.raises(store.WikiRuleError):
        async with sf() as s:
            await store.restore_page(s, producer, "home", version=99,
                                     actor="user:admin")
