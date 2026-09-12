"""The ticket store (docs/design/20, T3): the ONE place a ticket changes.

Everything a ticket does, it does through Relay — the card is a message, the
activity log is that message's thread, and an assignment is a mention the
router treats like any other. So these tests assert on the ROWS the store left
behind rather than on its return values: what the room ends up holding is the
whole contract, and a store that returned the right object while posting the
wrong message would be a ticket nobody can see.

Several of them are guards rather than features, and those are the ones worth
keeping honest: a card whose title says `@pai` must summon nobody; an agent
must act from its own run, or its posts land at hop 0 and the cap that stops
two agents handing a ticket back and forth is quietly off; a change is one
transaction, so a move that fails is a move that never happened; and a move
decides against the ticket as it is NOW, not as the caller last read it."""
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from agentplatform import ticket_store as store
from agentplatform.db import (Conversation, RelayMessage, Run, RunState, Ticket,
                              TicketEvent, utcnow)
from agentplatform.events import (TOPIC_RELAY_MESSAGES, TOPIC_TICKETS_EVENTS,
                                  FakeProducer)
from agentplatform.relay import SYSTEM_AUTHOR
from agentplatform.tickets import BUDGET_PREFIX, card_body


class BrokenProducer(FakeProducer):
    """A broker that is down. Every publish raises, and nothing about a ticket
    change may depend on one succeeding."""

    async def publish(self, *args, **kwargs):
        raise RuntimeError("broker down")


async def _project(sf, prefix: str) -> str:
    """A channel that is a project. The name is unique per call because
    `init_db` seeds the real #general and a channel slug is unique among
    channels."""
    async with sf() as s:
        conv = Conversation(connector="web", kind="channel", open=True,
                            name=f"proj-{uuid.uuid4().hex[:8]}",
                            title="#proj", ticket_prefix=prefix, ticket_seq=0)
        s.add(conv)
        await s.commit()
        return conv.id


async def _messages(sf, channel_id: str) -> list[RelayMessage]:
    async with sf() as s:
        return list((await s.execute(select(RelayMessage).where(
            RelayMessage.channel_id == channel_id)
            .order_by(RelayMessage.created_at))).scalars())


async def _events(sf, ticket_id: str) -> list[TicketEvent]:
    async with sf() as s:
        return list((await s.execute(select(TicketEvent).where(
            TicketEvent.ticket_id == ticket_id)
            .order_by(TicketEvent.created_at))).scalars())


async def _tickets(sf) -> list[Ticket]:
    async with sf() as s:
        return list((await s.execute(select(Ticket))).scalars())


async def _open(sf, producer, channel_id: str, *, actor="user:admin",
                title="Fix stale weather dedup", **kw) -> Ticket:
    async with sf() as s:
        conv = await s.get(Conversation, channel_id)
        return await store.create_ticket(s, producer, conv, actor=actor,
                                         title=title, **kw)


async def _run(sf, *, agent: str, depth: int = 0) -> Run:
    """The run an agent is speaking from. Agents may only act from one, so
    every agent-authored call in here carries one."""
    async with sf() as s:
        run = Run(agent=agent, trigger="relay", requested_by=f"agent:{agent}",
                  depth=depth, state=RunState.RUNNING, prompt="p")
        s.add(run)
        await s.commit()
        return run


def _ticket_events(producer) -> list[dict]:
    return [data for topic, _, data in producer.published
            if topic == TOPIC_TICKETS_EVENTS]


async def test_keys_are_sequential_per_prefix(sf, producer):
    a, b = await _project(sf, "ZZ"), await _project(sf, "QQ")
    keys = [(await _open(sf, producer, cid)).key for cid in (a, a, b, a)]
    assert keys == ["ZZ-1", "ZZ-2", "QQ-1", "ZZ-3"]


async def test_create_posts_exactly_one_card(sf, producer, seed_agent):
    await seed_agent("news", description="t")
    cid = await _project(sf, "ZZ")
    ticket = await _open(sf, producer, cid, actor="agent:news",
                         run=await _run(sf, agent="news"))

    rows = await _messages(sf, cid)
    assert len(rows) == 1
    card = rows[0]
    assert card.kind == "event"
    assert card.body == card_body(ticket)
    assert card.body == "🎫 ZZ-1 · Fix stale weather dedup — opened by news"
    assert card.card == {"type": "ticket", "key": "ZZ-1",
                         "title": "Fix stale weather dedup", "state": "open",
                         "priority": "p2", "assignee": None,
                         "url": "/tickets/ZZ-1"}
    assert card.author == "agent:news"
    assert (card.reply_to, card.thread_root, card.mentions) == (None, None, [])

    async with sf() as s:
        assert (await s.get(Ticket, ticket.id)).root_message_id == card.id
    events = await _events(sf, ticket.id)
    assert [e.kind for e in events] == ["created"]
    assert (events[0].actor, events[0].message_id) == ("agent:news", card.id)

    assert any(t == TOPIC_RELAY_MESSAGES for t, _, _ in producer.published)
    (published,) = _ticket_events(producer)
    assert published["event"]["kind"] == "created"
    assert published["ticket"]["key"] == "ZZ-1"
    assert published["ticket"]["state"] == "open"


async def test_a_card_summons_nobody(sf, producer, seed_agent):
    """A title is somebody else's text quoted by the platform. The card is
    `kind=event`, which the router never routes, and it carries no mentions —
    so `@pai` in a title is a word."""
    await seed_agent("pai", description="t")
    cid = await _project(sf, "ZZ")
    await _open(sf, producer, cid, title="@pai please look at this")
    card = (await _messages(sf, cid))[0]
    assert card.kind == "event" and card.mentions == []


async def test_an_agent_must_act_from_its_own_run(sf, producer, seed_agent):
    """The hop of everything posted here comes off the run. An agent actor with
    no run would post at hop 0 — a fresh chain every time — so the pairing is
    checked rather than assumed, in both directions."""
    for name in ("news", "pai"):
        await seed_agent(name, description="t")
    cid = await _project(sf, "ZZ")
    news, pai = await _run(sf, agent="news"), await _run(sf, agent="pai")

    with pytest.raises(store.TicketRuleError):
        await _open(sf, producer, cid, actor="agent:news")            # no run
    with pytest.raises(store.TicketRuleError):
        await _open(sf, producer, cid, actor="agent:news", run=pai)   # someone else's
    with pytest.raises(store.TicketRuleError):
        await _open(sf, producer, cid, actor="user:admin", run=news)  # a person has none
    assert await _tickets(sf) == []

    ticket = await _open(sf, producer, cid, actor="agent:news", run=news)
    async with sf() as s:
        row = await s.get(Ticket, ticket.id)
        for kw in ({}, {"run": pai}):
            with pytest.raises(store.TicketRuleError):
                await store.move_ticket(s, producer, row, actor="agent:news",
                                        to_state="in_progress", **kw)
            with pytest.raises(store.TicketRuleError):
                await store.comment_ticket(s, producer, row, actor="agent:news",
                                           body="hi", **kw)
            with pytest.raises(store.TicketRuleError):
                await store.assign_ticket(s, producer, row, actor="agent:news",
                                          assignee=None, **kw)
        await s.rollback()
    assert [e.kind for e in await _events(sf, ticket.id)] == ["created"]


async def test_move_edits_the_card_in_place(sf, producer, seed_agent):
    await seed_agent("news", description="t")
    cid = await _project(sf, "ZZ")
    ticket = await _open(sf, producer, cid)
    root = ticket.root_message_id
    async with sf() as s:
        row = await s.get(Ticket, ticket.id)
        await store.move_ticket(s, producer, row, actor="agent:news",
                                run=await _run(sf, agent="news"),
                                to_state="in_progress", reason="starting")

    rows = await _messages(sf, cid)
    assert len(rows) == 2
    card = next(r for r in rows if r.id == root)
    assert card.card["state"] == "in_progress"
    assert card.edited_at is not None
    # The card is the same message, still opened by whoever opened it.
    assert (card.author, card.kind, card.hop) == ("user:admin", "event", 0)

    system = next(r for r in rows if r.id != root)
    assert system.kind == "system" and system.author == SYSTEM_AUTHOR
    assert (system.reply_to, system.thread_root) == (root, root)
    assert system.body == "news moved ZZ-1 → in progress: starting"
    assert system.mentions == []

    async with sf() as s:
        row = await s.get(Ticket, ticket.id)
        assert row.state == "in_progress" and row.closed_at is None
    events = await _events(sf, ticket.id)
    assert [e.kind for e in events] == ["created", "moved"]
    assert (events[1].from_value, events[1].to_value) == ("open", "in_progress")
    assert events[1].message_id == system.id
    assert [p["event"]["kind"] for p in _ticket_events(producer)] == ["created", "moved"]


async def test_closing_and_reopening_track_closed_at(sf, producer):
    cid = await _project(sf, "ZZ")
    ticket = await _open(sf, producer, cid)
    async with sf() as s:
        row = await s.get(Ticket, ticket.id)
        await store.move_ticket(s, producer, row, actor="user:admin", to_state="done")
        assert row.closed_at is not None
        await store.move_ticket(s, producer, row, actor="user:admin", to_state="open")
        assert row.closed_at is None
    assert [e.kind for e in await _events(sf, ticket.id)] == ["created", "moved", "reopened"]
    body = (await _messages(sf, cid))[-1].body
    assert body == "admin reopened ZZ-1"


async def test_an_illegal_move_raises(sf, producer):
    cid = await _project(sf, "ZZ")
    ticket = await _open(sf, producer, cid)
    async with sf() as s:
        row = await s.get(Ticket, ticket.id)
        with pytest.raises(ValueError):
            await store.move_ticket(s, producer, row, actor="user:admin",
                                    to_state="open")            # already there
        await store.move_ticket(s, producer, row, actor="user:admin", to_state="done")
        with pytest.raises(ValueError):
            await store.move_ticket(s, producer, row, actor="user:admin",
                                    to_state="in_progress")     # closed: reopen first
        with pytest.raises(ValueError):
            await store.move_ticket(s, producer, row, actor="user:admin",
                                    to_state="shipped")         # not a state


async def test_a_move_decides_against_the_ticket_as_it_is_now(sf, producer):
    """Two movers racing. The loser holds a ticket object loaded before the
    winner's write, and deciding against THAT would let `can_move` pass on a
    state the ticket left — a `moved` event claiming to have come from `open`
    when the ticket was already `done`."""
    cid = await _project(sf, "ZZ")
    ticket = await _open(sf, producer, cid)
    async with sf() as s:
        stale = await s.get(Ticket, ticket.id)
        async with sf() as other:
            await store.move_ticket(other, producer, await other.get(Ticket, ticket.id),
                                    actor="user:admin", to_state="done")
        assert stale.state == "open"          # the loser's copy is out of date
        with pytest.raises(store.TicketRuleError):
            await store.move_ticket(s, producer, stale, actor="user:admin",
                                    to_state="in_progress")
        await s.rollback()
    assert [e.kind for e in await _events(sf, ticket.id)] == ["created", "moved"]


async def test_a_change_is_one_transaction(sf, producer, monkeypatch):
    """A failure anywhere before the commit leaves the ticket untouched: no
    half-moved row, no event the thread cannot account for, no rewritten card.
    `_finish` is the injection point because it is the LAST thing a change
    does — by then the state, the system row and the rewritten card are all
    staged, so anything that committed earlier shows up here as a move that
    survived a failure."""
    cid = await _project(sf, "ZZ")
    ticket = await _open(sf, producer, cid)
    before = len(producer.published)

    async def boom(*args, **kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(store, "_finish", boom)
    async with sf() as s:
        with pytest.raises(RuntimeError):
            await store.move_ticket(s, producer, await s.get(Ticket, ticket.id),
                                    actor="user:admin", to_state="in_progress")
        await s.rollback()

    async with sf() as s:
        row = await s.get(Ticket, ticket.id)
        assert row.state == "open" and row.closed_at is None
    assert [e.kind for e in await _events(sf, ticket.id)] == ["created"]
    rows = await _messages(sf, cid)
    assert len(rows) == 1 and rows[0].edited_at is None
    assert producer.published[before:] == []


async def test_a_broker_blip_costs_only_the_fan_out(sf, producer):
    """The row is the record. With every publish failing, the move still lands
    whole — row, event, system row and rewritten card — because the commit
    happens before anything leaves the process."""
    cid = await _project(sf, "ZZ")
    ticket = await _open(sf, producer, cid)
    async with sf() as s:
        await store.move_ticket(s, BrokenProducer(), await s.get(Ticket, ticket.id),
                                actor="user:admin", to_state="in_progress")
    async with sf() as s:
        assert (await s.get(Ticket, ticket.id)).state == "in_progress"
    assert [e.kind for e in await _events(sf, ticket.id)] == ["created", "moved"]
    rows = await _messages(sf, cid)
    assert len(rows) == 2
    card = next(r for r in rows if r.id == ticket.root_message_id)
    assert card.card["state"] == "in_progress" and card.edited_at is not None


async def test_assign_with_notify_by_a_human_is_a_hop_0_mention(sf, producer, seed_agent):
    await seed_agent("news", description="t")
    cid = await _project(sf, "ZZ")
    ticket = await _open(sf, producer, cid)
    root = ticket.root_message_id
    async with sf() as s:
        row = await s.get(Ticket, ticket.id)
        await store.assign_ticket(s, producer, row, actor="user:admin",
                                  assignee="agent:news", reason="closer to the data")

    mention = next(r for r in await _messages(sf, cid) if r.id != root)
    assert mention.kind == "text" and mention.author == "user:admin"
    assert mention.body == "@news you've been assigned ZZ-1: closer to the data"
    assert mention.mentions == ["news"]
    assert (mention.hop, mention.run_id) == (0, None)
    assert (mention.reply_to, mention.thread_root) == (root, root)

    card = next(r for r in await _messages(sf, cid) if r.id == root)
    assert card.card["assignee"] == "agent:news" and card.edited_at is not None
    events = await _events(sf, ticket.id)
    assert [e.kind for e in events] == ["created", "assigned"]
    assert (events[1].from_value, events[1].to_value) == (None, "agent:news")


async def test_assign_by_an_agent_run_carries_the_runs_hop(sf, producer, seed_agent):
    for name in ("news", "pai"):
        await seed_agent(name, description="t")
    cid = await _project(sf, "ZZ")
    ticket = await _open(sf, producer, cid)
    run = await _run(sf, agent="pai", depth=2)
    async with sf() as s:
        row = await s.get(Ticket, ticket.id)
        await store.assign_ticket(s, producer, row, actor="agent:pai",
                                  assignee="agent:news", run=run)

    mention = next(r for r in await _messages(sf, cid)
                   if r.id != ticket.root_message_id)
    assert mention.hop == 3 and mention.run_id == run.id
    assert mention.mentions == ["news"] and mention.author == "agent:pai"
    assert (await _events(sf, ticket.id))[1].run_id == run.id


async def test_assign_without_notify_posts_no_mention(sf, producer, seed_agent):
    await seed_agent("news", description="t")
    cid = await _project(sf, "ZZ")
    ticket = await _open(sf, producer, cid)
    async with sf() as s:
        row = await s.get(Ticket, ticket.id)
        await store.assign_ticket(s, producer, row, actor="user:admin",
                                  assignee="agent:news", notify=False)

    row = next(r for r in await _messages(sf, cid) if r.id != ticket.root_message_id)
    assert row.kind == "system" and row.author == SYSTEM_AUTHOR
    assert row.body == "admin assigned ZZ-1 to news"
    assert row.mentions == []


async def test_assigning_a_human_never_mentions(sf, producer, seed_agent):
    await seed_agent("news", description="t")
    cid = await _project(sf, "ZZ")
    ticket = await _open(sf, producer, cid)
    async with sf() as s:
        row = await s.get(Ticket, ticket.id)
        await store.assign_ticket(s, producer, row, actor="agent:news",
                                  run=await _run(sf, agent="news"),
                                  assignee="user:admin")
    row = next(r for r in await _messages(sf, cid) if r.id != ticket.root_message_id)
    assert row.kind == "system" and row.mentions == []


async def test_an_agent_assignee_has_to_exist(sf, producer, seed_agent):
    """Stored unchecked, a typo is a ticket that looks assigned and summons
    nobody — the worst of both, because everyone reading it thinks somebody
    else has it."""
    await seed_agent("news", description="t")
    await seed_agent("retired", description="t", enabled=False)
    cid = await _project(sf, "ZZ")
    for bad in ("agent:nwes", "agent:retired"):
        with pytest.raises(store.TicketRuleError):
            await _open(sf, producer, cid, assignee=bad)
    assert await _tickets(sf) == []

    ticket = await _open(sf, producer, cid)
    async with sf() as s:
        row = await s.get(Ticket, ticket.id)
        for bad in ("agent:nwes", "agent:retired"):
            with pytest.raises(store.TicketRuleError):
                await store.assign_ticket(s, producer, row, actor="user:admin",
                                          assignee=bad)
        # A human this platform has never seen is fine: a participant is not a
        # foreign key.
        await store.assign_ticket(s, producer, row, actor="user:admin",
                                  assignee="discord:12345")
    async with sf() as s:
        assert (await s.get(Ticket, ticket.id)).assignee == "discord:12345"


async def test_create_can_assign_in_one_go(sf, producer, seed_agent):
    await seed_agent("news", description="t")
    cid = await _project(sf, "ZZ")
    ticket = await _open(sf, producer, cid, assignee="agent:news")
    assert ticket.assignee == "agent:news"
    rows = await _messages(sf, cid)
    # The card is born assigned, so nothing edits it — but the assignee is
    # still summoned, because an agent that is never invoked never looks.
    card = next(r for r in rows if r.id == ticket.root_message_id)
    assert card.card["assignee"] == "agent:news" and card.edited_at is None
    mention = next(r for r in rows if r.id != ticket.root_message_id)
    assert mention.mentions == ["news"] and mention.thread_root == card.id
    assert [e.kind for e in await _events(sf, ticket.id)] == ["created", "assigned"]


async def test_update_edits_the_card_and_records_what_changed(sf, producer):
    cid = await _project(sf, "ZZ")
    ticket = await _open(sf, producer, cid)
    async with sf() as s:
        row = await s.get(Ticket, ticket.id)
        await store.update_ticket(s, producer, row, actor="user:admin",
                                  title="Fix the dedup", priority="p0")
    card = next(r for r in await _messages(sf, cid) if r.id == ticket.root_message_id)
    assert card.card["title"] == "Fix the dedup" and card.card["priority"] == "p0"
    assert card.body == "🎫 ZZ-1 · Fix the dedup — opened by admin"
    events = await _events(sf, ticket.id)
    assert [e.kind for e in events] == ["created", "edited"]
    assert events[1].to_value == "priority,title"


async def test_a_parent_is_real_in_the_same_project_and_above_the_ticket(sf, producer):
    here, elsewhere = await _project(sf, "ZZ"), await _project(sf, "QQ")
    parent = await _open(sf, producer, here, title="Roll up")
    other = await _open(sf, producer, elsewhere, title="Another project")
    child = await _open(sf, producer, here, title="Subtask", parent_id=parent.id)
    assert child.parent_id == parent.id

    with pytest.raises(store.TicketRuleError):
        await _open(sf, producer, here, parent_id="nosuchticket")
    with pytest.raises(store.TicketRuleError):
        await _open(sf, producer, here, parent_id=other.id)      # another project

    async with sf() as s:
        row = await s.get(Ticket, child.id)
        with pytest.raises(store.TicketRuleError):
            await store.update_ticket(s, producer, row, actor="user:admin",
                                      parent_id=child.id)        # itself
        with pytest.raises(store.TicketRuleError):
            await store.update_ticket(s, producer, row, actor="user:admin",
                                      parent_id=other.id)        # another project
        await s.rollback()
        # parent -> child -> parent is a loop every consumer that walks parents
        # would follow forever.
        row = await s.get(Ticket, parent.id)
        with pytest.raises(store.TicketRuleError):
            await store.update_ticket(s, producer, row, actor="user:admin",
                                      parent_id=child.id)
        await s.rollback()
    async with sf() as s:
        assert (await s.get(Ticket, parent.id)).parent_id is None


async def test_a_comment_is_a_reply_in_the_thread(sf, producer, seed_agent):
    await seed_agent("news", description="t")
    cid = await _project(sf, "ZZ")
    ticket = await _open(sf, producer, cid)
    async with sf() as s:
        row = await s.get(Ticket, ticket.id)
        await store.comment_ticket(s, producer, row, actor="user:admin",
                                   body="@news any luck?")
    comment = next(r for r in await _messages(sf, cid) if r.id != ticket.root_message_id)
    assert comment.kind == "text" and comment.author == "user:admin"
    assert comment.thread_root == ticket.root_message_id
    assert comment.mentions == ["news"]
    events = await _events(sf, ticket.id)
    assert [e.kind for e in events] == ["created", "commented"]
    assert events[1].message_id == comment.id


async def test_the_create_budget_counts_only_that_actors_creates(sf, producer, seed_agent):
    for name in ("news", "pai"):
        await seed_agent(name, description="t")
    news, pai = await _run(sf, agent="news"), await _run(sf, agent="pai")
    cid = await _project(sf, "ZZ")
    await _open(sf, producer, cid, actor="agent:news", run=news)
    await _open(sf, producer, cid, actor="agent:news", run=news)
    await _open(sf, producer, cid, actor="agent:pai", run=pai)
    ticket = await _open(sf, producer, cid, actor="user:admin")
    async with sf() as s:
        # A comment is not a create, and neither is anything else in the log.
        row = await s.get(Ticket, ticket.id)
        await store.comment_ticket(s, producer, row, actor="agent:news",
                                   run=news, body="hi")
        now = utcnow()
        assert await store.agent_create_budget_left(s, "news", 20, now) == 18
        assert await store.agent_create_budget_left(s, "pai", 20, now) == 19
        assert await store.agent_create_budget_left(s, "other", 20, now) == 20
        # Outside the trailing hour it is somebody else's hour.
        assert await store.agent_create_budget_left(
            s, "news", 20, now + timedelta(hours=2)) == 20
        # Never negative: over budget is over budget.
        assert await store.agent_create_budget_left(s, "news", 1, now) == 0


async def test_the_create_budget_refuses_and_writes_nothing(sf, producer, seed_agent):
    """Enforced inside the create, under the channel lock — a check the caller
    made first is a count that every one of N concurrent creates passes, and an
    agent in a retry loop is exactly that concurrency."""
    await seed_agent("news", description="t")
    run = await _run(sf, agent="news")
    cid = await _project(sf, "ZZ")
    for _ in range(2):
        await _open(sf, producer, cid, actor="agent:news", run=run, budget_limit=2)
    with pytest.raises(store.TicketBudgetError):
        await _open(sf, producer, cid, actor="agent:news", run=run, budget_limit=2)

    assert len(await _tickets(sf)) == 2
    assert len(await _messages(sf, cid)) == 2
    # A person is not on the agents' budget, and neither is another agent.
    await _open(sf, producer, cid, actor="user:admin", budget_limit=2)
    assert len(await _tickets(sf)) == 3


async def test_the_budget_notice_is_said_once_an_hour(sf, producer):
    cid = await _project(sf, "ZZ")
    ticket = await _open(sf, producer, cid)
    async with sf() as s:
        conv = await s.get(Conversation, cid)
        assert await store.say_budget_once(s, producer, conv, 20, utcnow()) is not None
        assert await store.say_budget_once(s, producer, conv, 20, utcnow()) is None
        # A COMMENT that happens to start with the same words is not the
        # platform's notice and must not suppress it.
        other = await _project(sf, "QQ")
        conv2 = await s.get(Conversation, other)
        row = await s.get(Ticket, ticket.id)
        await store.comment_ticket(s, producer, row, actor="user:admin",
                                   body=BUDGET_PREFIX + " (nice try)")
        assert await store.say_budget_once(s, producer, conv, 20, utcnow()) is None
        # And it is per room.
        assert await store.say_budget_once(s, producer, conv2, 20, utcnow()) is not None
    notice = [r for r in await _messages(sf, cid) if r.kind == "system"]
    assert len(notice) == 1
    assert notice[0].body.startswith(BUDGET_PREFIX)
    assert notice[0].author == SYSTEM_AUTHOR and notice[0].reply_to is None
