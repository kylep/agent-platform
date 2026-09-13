"""Changing a ticket (docs/design/20): the one place it happens.

A ticket is two things at once — a row the board reads and a Relay thread
people read — and they are only ever the same ticket because every change goes
through here. Open one and a card lands in the project channel; move it and
that same card is rewritten in place while the thread gains a line saying who
moved it and why; assign it to an agent and the thread gains a mention, which
is how an agent that is never invoked comes to look at its queue. Three
writers (the REST API, the `tickets` tool through it, and the board) would
otherwise each have their own idea of what a move is, and the row and the room
would drift apart on the first one that forgot a step.

The seams that matter:

- Relay is the substrate. Every message here is written through
  `relay_store.post_relay_message` and published through
  `publish_relay_message` — never a bare insert, never a second producer — so
  the router, the SSE fan-out and the Discord bridge see a ticket's traffic as
  ordinary room traffic, with every guard they already apply.
- The platform's own sentences (the card, every system row) are `kind=event`
  and `kind=system`, which the router does not route, and they carry no
  mentions. A ticket titled `@pai fix this` is therefore a title, not a
  summons. The ONE message that can summon anybody is the assignment, and only
  when it names an agent the room can actually reach.
- The actor is the caller's participant string, resolved from its token by the
  API and never an argument the caller chooses — and when it is an agent, the
  Run it is speaking from comes with it (`_require_actor`). Whatever the actor
  posts carries `run.depth + 1` for an agent and 0 for a person, exactly as
  `api/relay.py` computes it; that sum is what makes two agents handing a
  ticket back and forth hit the hop cap.

Transactions: every public function makes exactly ONE commit, at the end, and
publishes after it. Nothing here commits in the middle — a change that landed
half-committed would be a ticket the board shows in a state the thread never
explains, and the caller would be told it failed. The caller passes a session
and (for a create) the channel row; it does not have to commit afterwards, and
it must not hold other uncommitted work across one of these calls."""
import logging
from datetime import timedelta

from sqlalchemy import func, select

from agentplatform.connectors import IMPLEMENTED as LIVE_CONNECTORS
from agentplatform.db import (Conversation, RelayMessage, Ticket, TicketEvent,
                              TicketPriority, TicketState, utcnow)
from agentplatform.events import TOPIC_TICKETS_EVENTS
from agentplatform.relay import (AGENT_PREFIX, SYSTEM_AUTHOR, agent_name,
                                 is_agent, is_participant, mentionable_in,
                                 parse_mentions, strip_room_mentions)
from agentplatform.relay_store import (edit_message_card, enabled_agents,
                                       explicit_members, outbound_for_message,
                                       post_relay_message, publish_relay_message)
# `one_line` is the pure layer's "somebody else's words, on one line, safe to
# quote": a reason reaches the thread through the system row AND through the
# assignment mention, and two sanitisers would be two answers to what a reason
# may contain. It is the platform's one answer, shared with the wiki.
from agentplatform.tickets import (BUDGET_PREFIX, CLOSED_STATES, REASON_LIMIT,
                                   budget_body, can_move, card_body, card_for,
                                   event_row_text, one_line, participant_label)

log = logging.getLogger("ticket_store")

# Nothing given for a field that may legitimately be set to None — clearing an
# assignee and leaving it alone are different edits.
_UNSET = object()
# The fields `update_ticket` will change. Ordered as the event records them
# (sorted), so `to_value` reads the same for the same edit every time.
EDITABLE = ("body", "due_at", "labels", "parent_id", "priority", "title")
# How far up a parent chain to look before calling it a cycle. Subtasks are a
# shallow convenience, not a tree — thirty-two is far past any honest nesting
# and still a walk that terminates on a corrupt chain rather than hanging.
MAX_ANCESTRY = 32
# The namespaces an assignee may carry: the two platform ones and the bridges
# that are actually WIRED (`web` is not one — a web user is a principal, and a
# principal is `user:`; `slack` is a placeholder with no bridge behind it, so
# `slack:whoever` is a name on the board that no notification can ever reach).
# Relay's grammar alone takes any lowercase namespace, which is right for a DM
# target a connector invented and wrong here: `hacker:injected` on a board reads
# as a person, and there is no such person to read it.
ASSIGNEE_NAMESPACES = {"agent", "user"} | (LIVE_CONNECTORS - {"web"})
# The refusal names what is accepted, computed from the set above: a message
# that listed the shapes by hand would go on naming `discord:` the day a bridge
# is added or removed.
ASSIGNEE_SHAPES = ", ".join(["agent:<name>", "user:<name>"] +
                           sorted(f"{ns}:<id>"
                                  for ns in ASSIGNEE_NAMESPACES - {"agent", "user"}))


class TicketRuleError(ValueError):
    """A change the ticket rules refuse: an illegal move, an unknown priority,
    a channel that is not a project. A ValueError because it is about the
    arguments and not about the world, which is what lets the API answer 400
    without knowing the store's exception classes."""


class TicketBudgetError(TicketRuleError):
    """An agent has opened all the tickets it may open this hour. Its own class
    because it is the one refusal the caller answers differently — 429, and a
    notice in the room (`say_budget_once`) — while every other rule error is a
    400 the agent should read and correct."""


def ticket_view(ticket) -> dict:
    """The ticket as every reader outside this module sees it: the Kafka
    payload, the API response and the board row are all this dict. Deliberately
    just the columns — the faces on `assignee`/`reporter` are the API's to add,
    because resolving them needs the agent roster and a payload that sometimes
    carries them and sometimes does not is worse than one that never does."""
    return {"id": ticket.id, "key": ticket.key, "channel_id": ticket.channel_id,
            "title": ticket.title, "body": ticket.body or "",
            "state": str(ticket.state), "priority": str(ticket.priority),
            "assignee": ticket.assignee, "reporter": ticket.reporter,
            "labels": list(ticket.labels or []), "parent_id": ticket.parent_id,
            "due_at": _iso(ticket.due_at), "run_id": ticket.run_id,
            "root_message_id": ticket.root_message_id,
            "created_at": _iso(ticket.created_at),
            "updated_at": _iso(ticket.updated_at),
            "last_activity_at": _iso(ticket.last_activity_at),
            "closed_at": _iso(ticket.closed_at)}


def ticket_event_view(event) -> dict:
    return {"id": event.id, "ticket_id": event.ticket_id, "actor": event.actor,
            "kind": event.kind, "from_value": event.from_value,
            "to_value": event.to_value, "reason": event.reason,
            "message_id": event.message_id, "run_id": event.run_id,
            "created_at": _iso(event.created_at)}


def _iso(ts):
    return ts.isoformat() if ts else None


def _require_actor(actor: str, run) -> None:
    """An agent speaks only from the run it is speaking in.

    The hop of everything posted here is derived from `run`, so an agent actor
    with no run would post at hop 0 — a fresh chain, with a full hop budget,
    every time. Two agents assigning a ticket back and forth would then never
    reach the cap that is supposed to stop them, and the loop guard would be
    off without anyone having turned it off. So the pairing is checked rather
    than trusted: the run must be the ACTOR's own (a caller holding someone
    else's run would otherwise launder its hop), and a human or connector actor
    must bring no run at all, because a person is by definition the start of a
    chain."""
    name = agent_name(actor or "")
    if name is None:
        if run is not None:
            raise TicketRuleError(f"{actor!r} is not an agent but was given a run")
        return
    if run is None:
        raise TicketRuleError(f"{actor} must act from its own run")
    if run.agent != name:
        raise TicketRuleError(f"{actor} cannot act from a run belonging to {run.agent}")


def actor_hop(run) -> int:
    """The hop of anything the actor is about to post. Same sum as
    `api/relay.py:_hop_of`, from the Run the caller already holds: `Run.depth`
    IS the hop of the mention that summoned it, so its own posts sit one
    further out. A person starts a fresh chain at 0."""
    return (run.depth or 0) + 1 if run is not None else 0


async def create_ticket(session, producer, conv, *, actor: str, title: str,
                        body: str = "", assignee: str | None = None,
                        priority: str = TicketPriority.P2, labels=None,
                        parent_id: str | None = None, due_at=None,
                        notify: bool = True, run=None, url_base: str = "",
                        budget_limit: int | None = None) -> Ticket:
    """Open a ticket in `conv` and announce it there. Returns the row.

    The key is allocated under the channel row so two agents opening at once
    cannot share a number, the card message becomes the ticket's thread root,
    and an `assignee` given here is assigned in the same breath — the card is
    born with the right face on it, so only the summons is left to post.

    `budget_limit` is the agent's hourly creation cap, enforced HERE rather
    than by the caller: a check made before the call is a count that N
    concurrent creates all pass, and an agent in a retry loop is exactly the
    concurrency that finds that hole. Under the channel lock the count and the
    insert are one decision, so the N+1st create is the one that is refused."""
    _require_actor(actor, run)
    title = (title or "").strip()
    if not title:
        raise TicketRuleError("a ticket needs a title")
    if conv.ticket_prefix is None:
        raise TicketRuleError(f"#{conv.name} is not a project")
    assignee = await _check_assignee(session, assignee)
    # The channel row lock first: it is what serialises two agents opening at
    # once, so both the budget count and the key come out of one queue.
    channel = await _lock_channel(session, conv)
    if budget_limit is not None:
        await _check_budget(session, actor, budget_limit)
    await _check_parent(session, parent_id, channel_id=conv.id, ticket_id=None)
    ticket = Ticket(key=_take_key(channel), channel_id=conv.id,
                    title=title, body=body or "", state=TicketState.OPEN.value,
                    priority=_priority(priority), assignee=assignee, reporter=actor,
                    labels=list(labels or []), parent_id=parent_id, due_at=due_at,
                    run_id=run.id if run is not None else None)
    session.add(ticket)
    await session.flush()
    card = await post_relay_message(
        session, conv, author=actor, body=card_body(ticket), kind="event",
        card=card_for(ticket, url_base=url_base),
        run_id=run.id if run is not None else None, hop=actor_hop(run))
    ticket.root_message_id = card.id
    msgs = [card]
    events = [await _add_event(session, ticket, actor, "created",
                               message_id=card.id, run=run)]
    if assignee:
        msg, event = await _announce_assignment(
            session, conv, ticket, actor=actor, assignee=assignee, reason=None,
            notify=notify, run=run)
        msgs.append(msg)
        events.append(event)
    await _finish(session, producer, conv, ticket, msgs, events)
    return ticket


async def move_ticket(session, producer, ticket, *, actor: str, to_state: str,
                      reason: str | None = None, run=None,
                      url_base: str = "") -> TicketEvent:
    """Move a ticket to `to_state`, or raise if the board does not allow it.

    Leaving a closed state is a `reopened` event rather than a `moved` one:
    they are the same write but not the same fact, and the board's "Today"
    strip reads better for knowing the difference."""
    _require_actor(actor, run)
    ticket = await _lock(session, ticket)
    from_state = str(ticket.state)
    if not can_move(from_state, to_state):
        raise TicketRuleError(f"cannot move {ticket.key} from {from_state} to {to_state}")
    ticket.state = to_state
    ticket.closed_at = utcnow() if to_state in CLOSED_STATES else None
    kind = "reopened" if from_state in CLOSED_STATES else "moved"
    conv, event = await _in_thread(session, ticket, actor, kind, from_value=from_state,
                                   to_value=to_state, reason=reason, run=run)
    card = await _sync_card(session, ticket, url_base=url_base)
    await _finish(session, producer, conv, ticket,
                  [await session.get(RelayMessage, event.message_id)], [event],
                  card=card)
    return event


async def assign_ticket(session, producer, ticket, *, actor: str,
                        assignee: str | None, reason: str | None = None,
                        notify: bool = True, run=None,
                        url_base: str = "") -> TicketEvent | None:
    """Give a ticket to somebody — an agent, a human, or nobody. Returns None
    when it is already theirs.

    Assignment is the ask (docs/design/20): with `notify`, an agent assignee is
    summoned by a real mention in the thread, authored by the actor and hopped
    like anything else the actor says, so hand-off ping-pong runs into the same
    cap that stops every other agent-to-agent chain. The ticket is assigned
    either way — a summons the router refuses for budget does not un-assign
    the work, it just means the assignee finds it at its next wake.

    Re-assigning it to whoever already has it changes nothing, exactly as an
    edit that edits nothing does: the event would record a move from a
    participant to itself, and the thread would gain a line — and a summons —
    for work that is already in the right hands."""
    _require_actor(actor, run)
    assignee = await _check_assignee(session, assignee)
    ticket = await _lock(session, ticket)
    if ticket.assignee == assignee:
        return None
    from_value, ticket.assignee = ticket.assignee, assignee
    conv = await _conv_of(session, ticket)
    msg, event = await _announce_assignment(session, conv, ticket, actor=actor,
                                            assignee=ticket.assignee, reason=reason,
                                            notify=notify, run=run,
                                            from_value=from_value)
    card = await _sync_card(session, ticket, url_base=url_base)
    await _finish(session, producer, conv, ticket, [msg], [event], card=card)
    return event


async def update_ticket(session, producer, ticket, *, actor: str,
                        reason: str | None = None, run=None, url_base: str = "",
                        title=_UNSET, body=_UNSET, priority=_UNSET, labels=_UNSET,
                        parent_id=_UNSET, due_at=_UNSET) -> TicketEvent | None:
    """Edit a ticket's fields. Returns None when nothing actually changed — an
    edit that changed nothing still has a caller who meant well, and telling the
    room "admin edited OPS-12" about it is noise with a name on it.

    The event records WHICH fields moved, not their values: a body is too long
    for `to_value` and a diff of it belongs in the thread, where the person who
    made the edit can explain it."""
    _require_actor(actor, run)
    ticket = await _lock(session, ticket)
    given = {"title": title, "body": body, "priority": priority, "labels": labels,
             "parent_id": parent_id, "due_at": due_at}
    changed = []
    for name in EDITABLE:
        value = given[name]
        if value is _UNSET:
            continue
        if name == "priority":
            value = _priority(value)
        elif name == "title":
            value = (value or "").strip()
            if not value:
                raise TicketRuleError("a ticket needs a title")
        elif name == "labels":
            value = list(value or [])
        elif name == "parent_id":
            value = value or None
            await _check_parent(session, value, channel_id=ticket.channel_id,
                                ticket_id=ticket.id)
        if value != getattr(ticket, name):
            setattr(ticket, name, value)
            changed.append(name)
    if not changed:
        return None
    conv, event = await _in_thread(session, ticket, actor, "edited",
                                   to_value=",".join(changed), reason=reason, run=run)
    card = await _sync_card(session, ticket, url_base=url_base)
    await _finish(session, producer, conv, ticket,
                  [await session.get(RelayMessage, event.message_id)], [event],
                  card=card)
    return event


async def comment_ticket(session, producer, ticket, *, actor: str, body: str,
                         run=None) -> TicketEvent:
    """Say something in the ticket's thread, as the actor.

    An ordinary Relay reply, mentions and all: a comment is the actor's own
    words, so `@news can you look?` on a ticket summons news exactly as it
    would anywhere else in the room, under the same hop and budget. The card
    is untouched — a comment changes no field — but it IS activity, which is
    what keeps a ticket being discussed off the stale list."""
    _require_actor(actor, run)
    text = (body or "").strip()
    if not text:
        raise TicketRuleError("a comment needs a body")
    ticket = await _lock(session, ticket)
    conv = await _conv_of(session, ticket)
    if is_agent(actor):
        # Same rule as a post through the API: an agent's text keeps its words
        # but loses the `@` on a room mention, so nothing it writes can page
        # everyone.
        text = strip_room_mentions(text)
    msg = await post_relay_message(
        session, conv, author=actor, body=text, reply_to=ticket.root_message_id,
        run_id=run.id if run is not None else None, hop=actor_hop(run),
        mentions=parse_mentions(text, await _room_agents(session, conv), actor))
    event = await _add_event(session, ticket, actor, "commented",
                             message_id=msg.id, run=run)
    await _finish(session, producer, conv, ticket, [msg], [event])
    return event


async def agent_create_budget_left(session, agent: str, limit: int, now) -> int:
    """How many more tickets `agent` may open this hour. Read-only: this is
    what the API reports and what a tool error quotes back. The refusal itself
    is `create_ticket`'s, under the channel lock, because a count taken out
    here is already stale by the time the caller acts on it.

    Counted from `ticket_events` rather than from `tickets`, because the count
    has to survive a ticket being deleted: the budget is about how hard an
    agent has been hammering the board in the last hour, and an agent that
    opened twenty and closed them all has still opened twenty. Only `created`
    rows count — moving and commenting are how work gets FINISHED, and capping
    those would pause a ticket mid-flight."""
    return max(0, limit - await _creates_since(session, AGENT_PREFIX + agent, now))


async def say_budget_once(session, producer, conv, limit: int, now) -> RelayMessage | None:
    """Tell a project channel that an agent has run out of ticket budget —
    at most once an hour, and None when it has already been said.

    Same shape as the router's own budget notice and for the same reason: an
    agent over budget is an agent retrying, so a notice per refusal would BE
    the flood the cap exists to stop. Matched on `kind == "system"` and not on
    the text alone, because a comment quoting the notice is somebody talking
    about the pause, not the platform declaring it, and letting one suppress
    the other is a room that goes quiet without ever saying why."""
    said = (await session.execute(select(RelayMessage.id).where(
        RelayMessage.channel_id == conv.id, RelayMessage.kind == "system",
        RelayMessage.body.like(BUDGET_PREFIX + "%"),
        RelayMessage.created_at >= now - timedelta(hours=1)).limit(1))).first()
    if said is not None:
        return None
    msg = await post_relay_message(session, conv, author=SYSTEM_AUTHOR,
                                   body=budget_body(limit), kind="system")
    await session.commit()
    await publish_relay_message(producer, conv, msg,
                                outbound=await outbound_for_message(session, conv, msg))
    return msg


# --- the parts every change shares -------------------------------------------


def _for_update(session, stmt):
    """`FOR UPDATE` where it means something. Postgres is where two writers
    actually race; sqlite is single-writer and its compiler drops the clause
    entirely, so it is asked for by dialect the way `api/relay.py` does."""
    return (stmt.with_for_update()
            if session.get_bind().dialect.name == "postgresql" else stmt)


async def _lock_channel(session, conv) -> Conversation:
    """The project's own row, locked. Everything that has to be serialised per
    project queues here: the key sequence, and the hourly create budget that
    would otherwise be a count N concurrent creates all pass."""
    return (await session.execute(_for_update(
        session, select(Conversation).where(Conversation.id == conv.id))
        .execution_options(populate_existing=True))).scalars().one()


def _take_key(channel) -> str:
    """The next key under this channel's prefix. Caller holds the channel lock:
    a key handed out twice is two tickets that are the same ticket to everyone
    who reads it."""
    channel.ticket_seq = (channel.ticket_seq or 0) + 1
    return f"{channel.ticket_prefix}-{channel.ticket_seq}"


async def _lock(session, ticket) -> Ticket:
    """Re-read a ticket under a row lock before changing it.

    Without it, two moves arriving together both read `open`, both pass
    `can_move`, and both write a `moved` event claiming to have come FROM open
    — a history that says the ticket left the same state twice, and a board
    whose final state is whichever write happened to land second."""
    row = (await session.execute(_for_update(
        session, select(Ticket).where(Ticket.id == ticket.id))
        .execution_options(populate_existing=True))).scalars().one_or_none()
    if row is None:
        raise TicketRuleError(f"ticket {ticket.key} no longer exists")
    return row


async def _creates_since(session, actor: str, now) -> int:
    return (await session.execute(select(func.count()).select_from(TicketEvent).where(
        TicketEvent.kind == "created", TicketEvent.actor == actor,
        TicketEvent.created_at >= now - timedelta(hours=1)))).scalar() or 0


async def _check_budget(session, actor: str, limit: int) -> None:
    """The hourly create cap, for agents only. A person opening tickets fast is
    a person doing triage; an agent doing it is usually a loop."""
    if not is_agent(actor):
        return
    used = await _creates_since(session, actor, utcnow())
    if used >= limit:
        raise TicketBudgetError(
            f"{participant_label(actor)} has opened {used} tickets in the last "
            f"hour (limit {limit}); try again later")


async def _check_assignee(session, assignee: str | None) -> str | None:
    """The assignee as it will be STORED: a participant string, or None.

    An agent assignee has to be an agent that exists and is enabled. Stored
    unchecked, a typo (`agent:nwes`) or a since-deleted agent is a ticket
    assigned to nobody that still LOOKS assigned: the board shows a name, the
    summons resolves to nothing, and the work sits there because everyone who
    reads it thinks somebody else has it. Humans are not checked — a
    participant may be a person this platform has never seen — and None is the
    unassign.

    A BARE name is read as an agent or refused. `assignee: "pai"` is what a
    model writes, and it is the same failure wearing a different hat: the board
    shows `pai`, `mine` matches nothing and the mention is never posted. A bare
    name is the only kind an agent holds, so there is exactly one honest
    reading of it — and when that reading is not an agent, the platform cannot
    tell a person from a typo, so it says which shapes it takes rather than
    guessing.

    A QUALIFIED name is held to Relay's own participant grammar, case and all.
    `Agent:pai` is not the agent namespace: read as a stranger's participant it
    files cleanly, shows a name on the board, and summons nobody — the bare-name
    failure again, wearing the prefix that hides it. `is_participant` is the
    same gate a DM target and a channel's member list pass, so a shape one door
    refuses is not quietly stored by this one, and the namespace has to be one
    the platform actually has."""
    raw = (assignee or "").strip()
    if not raw:
        return None
    if ":" not in raw:
        if raw not in await enabled_agents(session):
            raise TicketRuleError(f"assignee must be one of {ASSIGNEE_SHAPES}")
        return AGENT_PREFIX + raw
    if not is_participant(raw) or raw.split(":", 1)[0] not in ASSIGNEE_NAMESPACES:
        raise TicketRuleError(f"assignee must be one of {ASSIGNEE_SHAPES}")
    name = agent_name(raw)
    if name is not None and name not in await enabled_agents(session):
        raise TicketRuleError(f"unknown or disabled agent: {name}")
    return raw


async def _check_parent(session, parent_id: str | None, *, channel_id: str,
                        ticket_id: str | None) -> None:
    """A subtask's parent: a real ticket, in the same project, above it.

    Cross-project parents are refused because a channel IS the project and a
    ticket whose parent lives in a room the reader cannot see is a dead end on
    the board. The ancestry walk is the other half: a cycle makes every
    consumer that follows parents — the board's tree, a rollup, the prompt's
    ticket block — loop forever, and it only takes two tickets pointed at each
    other to make one."""
    if not parent_id:
        return
    if parent_id == ticket_id:
        raise TicketRuleError("a ticket cannot be its own parent")
    parent = await session.get(Ticket, parent_id)
    if parent is None:
        raise TicketRuleError(f"unknown parent ticket: {parent_id}")
    if parent.channel_id != channel_id:
        raise TicketRuleError(f"{parent.key} belongs to another project")
    seen, node = {ticket_id}, parent
    for _ in range(MAX_ANCESTRY):
        if node.id in seen:
            raise TicketRuleError(f"{parent.key} is already below this ticket")
        seen.add(node.id)
        if not node.parent_id:
            return
        node = await session.get(Ticket, node.parent_id)
        if node is None:
            return
    raise TicketRuleError(f"{parent.key} is nested too deeply")


def _priority(priority) -> str:
    value = str(priority or "")
    if value not in {p.value for p in TicketPriority}:
        raise TicketRuleError(f"unknown priority: {priority!r}")
    return value


async def _conv_of(session, ticket) -> Conversation:
    conv = await session.get(Conversation, ticket.channel_id)
    if conv is None:
        raise TicketRuleError(f"{ticket.key} has no channel")
    return conv


async def _room_agents(session, conv) -> set[str]:
    """The agents a mention posted in this room may resolve to. Read off the
    rows, not an AgentStore: the store has a session and nothing else, and a
    closed channel must not be able to summon an agent that is not in it."""
    return mentionable_in(conv, await enabled_agents(session),
                          await explicit_members(session, conv.id))


async def _add_event(session, ticket, actor: str, kind: str, *,
                     from_value=None, to_value=None, reason=None,
                     message_id=None, run=None) -> TicketEvent:
    event = TicketEvent(ticket_id=ticket.id, actor=actor, kind=kind,
                        from_value=from_value, to_value=to_value,
                        reason=(reason or None), message_id=message_id,
                        run_id=run.id if run is not None else None)
    session.add(event)
    # Flushed one at a time so a create that also assigns gets two events with
    # two timestamps, in the order they happened.
    await session.flush()
    return event


async def _in_thread(session, ticket, actor: str, kind: str, *, from_value=None,
                     to_value=None, reason=None, run=None):
    """The system row a change leaves in the ticket's thread, plus its event.

    Written in the PLATFORM's name, not the actor's: "news moved OPS-12 → in
    progress" is the room being told what happened, and a third-person sentence
    signed by its own subject reads like news wrote it about itself."""
    conv = await _conv_of(session, ticket)
    event = await _add_event(session, ticket, actor, kind, from_value=from_value,
                             to_value=to_value, reason=reason, run=run)
    msg = await post_relay_message(
        session, conv, author=SYSTEM_AUTHOR, kind="system",
        body=event_row_text(event, participant_label(actor), ticket.key),
        reply_to=ticket.root_message_id)
    event.message_id = msg.id
    await session.flush()
    return conv, event


async def _announce_assignment(session, conv, ticket, *, actor: str, assignee,
                               reason, notify: bool, run, from_value=None):
    """Tell the thread who has the ticket now — as a summons when there is
    somebody to summon, and as a system row otherwise.

    The mention is the whole mechanism (docs/design/20): it is a plain `text`
    message authored by the ACTOR, so the router picks it up like any other and
    applies every guard it has. Three cases fall back to the quiet system row,
    and they are one test: `parse_mentions` resolves nobody. Assigning to a
    human summons nothing because a human is not mentionable; assigning to
    yourself summons nothing because an agent never summons itself; and
    assigning an agent that is not in a closed room summons nothing because it
    cannot read the room anyway."""
    target = agent_name(assignee or "")
    body, mentions = "", []
    if notify and target:
        body = f"@{target} you've been assigned {ticket.key}"
        detail = one_line(reason, REASON_LIMIT)
        if detail:
            body = f"{body}: {detail}"
        mentions = parse_mentions(body, await _room_agents(session, conv), actor)
    if mentions:
        msg = await post_relay_message(
            session, conv, author=actor, body=body,
            reply_to=ticket.root_message_id, mentions=mentions,
            run_id=run.id if run is not None else None, hop=actor_hop(run))
        event = await _add_event(session, ticket, actor, "assigned",
                                 from_value=from_value, to_value=assignee,
                                 reason=reason, message_id=msg.id, run=run)
        return msg, event
    _, event = await _in_thread(session, ticket, actor, "assigned",
                                from_value=from_value, to_value=assignee,
                                reason=reason, run=run)
    return await session.get(RelayMessage, event.message_id), event


async def _sync_card(session, ticket, *, url_base: str) -> RelayMessage | None:
    """Bring the card in the room back in line with the row, if it has drifted.
    Staged, not published — `_finish` sends it once the change is committed.

    Returns None when nothing a reader can see changed — a body-only edit
    leaves the card identical, and re-publishing it would put an "edited" mark
    on a message whose text is the same. None too when the card is gone: a
    deleted card is a ticket that lost its announcement, not a ticket that
    stops working."""
    if not ticket.root_message_id:
        return None
    msg = await session.get(RelayMessage, ticket.root_message_id)
    if msg is None or msg.deleted_at is not None:
        return None
    card, body = card_for(ticket, url_base=url_base), card_body(ticket)
    if msg.card == card and msg.body == body:
        return None
    return await edit_message_card(session, msg, card=card, body=body)


async def _finish(session, producer, conv, ticket, msgs, events, *, card=None) -> None:
    """The single commit, then everyone is told. The row is the record — a
    broker that is down costs a ticket change its live update, never the change
    itself — and that ordering is the one Relay has used since design-19.

    The whole change lands in ONE transaction: the ticket, its events, its
    messages and the rewritten card. A commit in the middle would let a ticket
    move durably while the system row explaining it rolled back, leaving a
    board the thread cannot account for and a caller who was told it failed.

    The card goes first (a reader should see the new state before the line
    explaining it) and WITHOUT `outbound`: an edit is not a message a bridge
    can carry, so mirroring it would re-post the card to Discord every time."""
    ticket.last_activity_at = utcnow()
    await session.commit()
    if card is not None:
        await publish_relay_message(producer, conv, card)
    for msg in msgs:
        if msg is not None:
            await publish_relay_message(
                producer, conv, msg,
                outbound=await outbound_for_message(session, conv, msg))
    if producer is None:
        return
    view = ticket_view(ticket)
    for event in events:
        try:
            await producer.publish(TOPIC_TICKETS_EVENTS, ticket.id,
                                   {"event": ticket_event_view(event), "ticket": view},
                                   type="ticket.event")
        except Exception:
            log.warning("tickets.events publish failed for %s %s", ticket.key,
                        event.kind, exc_info=True)
