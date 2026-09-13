"""Tickets' REST surface (docs/design/20): the board's data, the writes that
change a ticket, and the live stream both read.

Every write here is a thin skin over `ticket_store`, which is the ONE place a
ticket changes — the row, the card in the room and the system row in its thread
land in one transaction there, and a route that inserted anything itself would
be a second definition of what a move is. What this module owns is the seam
between a caller and that store:

- WHO is acting. The actor is `caller.participant`, resolved from the token by
  `require_relay_access` exactly as Relay resolves it, and there is no
  `reporter` on the wire at all. An agent additionally has to be speaking from
  its own Run — the store derives every hop from it — so a per-run token whose
  run we cannot find may read the board and write nothing.
- WHERE it may act. An agent reads tickets only in the rooms it is a member of
  and writes only there; a human reads and writes the platform. The fence is
  Relay's `is_member`, not a role, for the same reason it is in Relay: a role
  says what kind of caller this is, membership says which rooms it is in.
- Faces and staleness are attached HERE, never stored: the board draws a card
  without a second call, and the stale badge is one definition
  (`tickets.is_stale`) read against the running setting.

The `relay` role is the per-run role a ticket-writing agent carries. It means
"participant" now: its scope is `/api/relay/*` AND `/api/tickets/*`, always as
the agent the token names, and it still promotes nothing."""
import asyncio
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import cast, func, or_, select
from sqlalchemy.dialects.postgresql import JSONB

from agentplatform import ticket_store as store
from agentplatform.api import relay as relay_api
from agentplatform.api import schemas as S
# Relay's own seam, reused rather than restated: `require_relay_access` is what
# resolves a token to a participant (and refuses a per-run token carrying a role
# that has no business in a room), and READ/WRITE are the role tuples that name
# the `relay` role beside the human scopes. Tickets widens WHERE that role
# reaches, not what it is. The module is imported too, for the handful of
# room-level helpers whose names are private to Relay but whose ANSWERS are the
# platform's: which rooms a caller can see, who is in one, how a frame is
# framed, and how long a quiet stream waits.
from agentplatform.api.relay import READ, WRITE, Caller, require_relay_access
from agentplatform.db import (ACTIVE_STATES, Conversation, RelayMessage, Run,
                              Ticket, TicketEvent, TicketState, utcnow)
from agentplatform.relay import agent_name, is_agent, is_member
from agentplatform.relay_feed import OVERFLOW, TopicFeed
from agentplatform.relay_store import channel_by_ref, faces_for, message_view
from agentplatform.events import TOPIC_TICKETS_EVENTS
from agentplatform.tickets import (CLOSED_STATES, budget_body, is_stale,
                                   participant_label)
from agentplatform.ticket_store import TicketBudgetError, TicketRuleError

router = APIRouter()

# The whole platform is one ticket stream, so the fan-out has one key. Relay
# subscribes per channel because a room is a room; the board is a board, and a
# client filtering by project in the browser is cheaper than a stream per
# project that every move would have to be published to twice.
STREAM = "tickets"
# The card's `url` is site-relative: the platform has no public-base-URL
# setting to build an absolute one from, the web client resolves it against
# wherever it is served, and the Discord mirror reads the body rather than the
# card anyway.
URL_BASE = ""
# The board asks for everything and filters in the browser, so the cap is a
# guard against a runaway query rather than a paging scheme.
LIST_LIMIT = 500
# How many agents the stats' budget block names. It answers "who is hammering
# the board right now", and a list of every agent at zero answers nothing.
BUDGET_AGENTS = 5
# Linked runs on the detail page: the recent ones, because the list is context
# for the thread rather than a run history, which /api/runs already is.
LINKED_RUNS = 10


# --- resolving what the caller named -----------------------------------------

def _labelled(bind, label: str):
    """`label` as a WHERE clause, so the cap counts matching rows.

    Filtered in python after the limit — which is what this used to do — a
    labelled ticket that sorted past the page vanished with no error and no
    flag, and `label=qa&limit=2` answered "no such tickets". Containment has no
    portable spelling, so there are two: `@>` on postgres (the column is `json`,
    hence the cast to `jsonb`, which is the type that has the operator) and
    `json_each` on sqlite, the same dialect fork `db.py` makes for DDL."""
    if bind.dialect.name == "postgresql":
        return cast(Ticket.labels, JSONB).contains([label])
    each = func.json_each(Ticket.labels).table_valued("value")
    return select(1).select_from(each).where(each.c.value == label).exists()


async def _readable(s, caller: Caller, agents: set[str]) -> set[str] | None:
    """The channels this caller may see tickets in, or None for "all of them".

    None rather than the whole set for a human on purpose: a human reads the
    platform, archived rooms included, and materialising that as a filter would
    quietly hide the tickets of a project somebody archived."""
    if caller.agent is None:
        return None
    rows, _ = await relay_api._visible(s, caller, agents)
    return {c.id for c in rows}


async def _channel_or_404(s, ref: str) -> Conversation:
    """A channel by id, by `#name`, or by the bare name — `channel_by_ref`'s
    reading of a reference, with the 404 a route owes a caller that named a
    room this platform does not have."""
    conv = await channel_by_ref(s, ref)
    if conv is None:
        raise HTTPException(404, "unknown channel")
    return conv


async def _may_write(s, conv: Conversation, caller: Caller, agents: set[str]) -> None:
    """The one gate every ticket write passes: the room still takes writes, and
    an agent is in it.

    Archiving is the operator's containment lever — it is how a room that has
    gone wrong is stopped — and Relay's own message route already refuses a
    post into an archived channel with a 404. A ticket write is a Relay write
    (a card, a system row, a mention), so it has to obey the same lever, or
    archiving a project would stop the conversation and leave the board it
    hangs off wide open. 404 rather than 409 to match Relay exactly.

    Reads are deliberately NOT gated here: a human still reads an archived
    project's tickets, because archiving is not deletion and the record is the
    point. An agent already cannot — `_readable` is built from
    `relay_api._visible`, which drops archived rooms — so an agent's read and
    its write stop at the same moment."""
    if conv.archived_at is not None:
        raise HTTPException(404, "this channel is archived")
    if caller.agent is not None and not is_member(
            conv, caller.participant, agents, await relay_api._explicit(s, conv.id)):
        raise HTTPException(403, "not a member of this channel")


async def _ticket_or_404(s, key: str, caller: Caller, agents: set[str]) -> Ticket:
    """A ticket by key or id, if this caller may see it.

    A room an agent is not in answers 404 rather than 403: that a ticket exists
    under `WAR-3` is itself something the private room was keeping."""
    row = (await s.execute(select(Ticket).where(Ticket.key == key))).scalars().first()
    row = row or await s.get(Ticket, key)
    if row is None:
        raise HTTPException(404, "unknown ticket")
    readable = await _readable(s, caller, agents)
    if readable is not None and row.channel_id not in readable:
        raise HTTPException(404, "unknown ticket")
    return row


async def _run_of(s, request: Request, caller: Caller, *, writing: bool) -> Run | None:
    """The Run an agent caller is speaking from.

    Same source as `api/relay.py`: the run is stamped on the per-run API key,
    so the caller cannot name its own. The store REFUSES an agent actor without
    one — everything it posts would land at hop 0, and the cap that stops two
    agents handing a ticket back and forth would be quietly off — so a token
    whose run has gone is refused here, as a 403, rather than reaching the
    store and surfacing as a 500."""
    if caller.agent is None:
        return None
    run_id = getattr(request.state, "api_key_run_id", None)
    run = await s.get(Run, run_id) if run_id else None
    if run is None and writing:
        raise HTTPException(403, "this token has no run to act from")
    return run


async def _parent_id(s, parent: str | None, channel_id: str) -> str | None:
    """A parent named by key or id, resolved to an id. Unknown is refused here
    rather than passed through, so `parent: "OPS-9000"` is a 400 that names the
    thing the caller got wrong."""
    if not parent:
        return None
    row = (await s.execute(select(Ticket).where(Ticket.key == parent))).scalars().first()
    row = row or await s.get(Ticket, parent)
    if row is None:
        raise HTTPException(400, f"unknown parent ticket: {parent}")
    return row.id


# --- what a ticket looks like on the wire ------------------------------------

def _face(participant: str | None, faces: dict[str, dict]) -> dict | None:
    """An agent's face, or None. Humans get None, exactly as a Relay message's
    author does: a participant may be a person this platform has never seen, so
    there is nothing to look an avatar up in."""
    return faces.get(agent_name(participant or "") or "") if is_agent(participant or "") \
        else None


def _agents_in(participants) -> set[str]:
    return {n for p in participants if (n := agent_name(p or "")) is not None}


def _view(ticket, faces: dict[str, dict], *, now, stale_days: int) -> dict:
    return {**store.ticket_view(ticket),
            "assignee_face": _face(ticket.assignee, faces),
            "reporter_face": _face(ticket.reporter, faces),
            "stale": is_stale(ticket, now, stale_days)}


async def _views(s, rows, settings) -> list[dict]:
    faces = await faces_for(s, _agents_in(
        [t.assignee for t in rows] + [t.reporter for t in rows]))
    now = utcnow()
    return [_view(t, faces, now=now, stale_days=settings.tickets_stale_days)
            for t in rows]


async def _runs_in(s, *where, limit: int | None = None) -> list:
    """Runs matching `where`, newest first. `limit` is the page the caller has
    room for; without one every match comes back, which is what lets the runs a
    ticket's own history names outrank the ones merely summoned in its thread."""
    stmt = select(Run).where(*where).order_by(Run.created_at.desc(), Run.id.desc())
    return list((await s.execute(stmt if limit is None else stmt.limit(limit))).scalars())


async def _one(s, ticket, settings) -> dict:
    return (await _views(s, [ticket], settings))[0]


# --- reads --------------------------------------------------------------------

@router.get("/api/tickets", response_model=list[S.TicketView])
async def list_tickets(request: Request, channel: str | None = None,
                       state: str | None = None, assignee: str | None = None,
                       label: str | None = None, q: str | None = None,
                       mine: bool = False,
                       limit: int = Query(LIST_LIMIT, ge=1, le=LIST_LIMIT),
                       caller: Caller = Depends(require_relay_access(*READ))):
    """The board, filtered. Ordered by priority and then by activity, which is
    the order a column is read in: the most urgent thing that moved most
    recently is at the top.

    `q` is a substring match on title and body. Deliberately ILIKE rather than
    the tsvector Relay's search uses: a ticket title is a headline, not a
    document, and `weather` must match `weather-dedup`, which a stemmed index
    would not. The GIN index in the design is there if a body search ever needs
    it."""
    agents = relay_api._agent_set(request)
    async with request.app.state.session_factory() as s:
        stmt = select(Ticket)
        readable = await _readable(s, caller, agents)
        if readable is not None:
            stmt = stmt.where(Ticket.channel_id.in_(readable))
        if channel:
            stmt = stmt.where(Ticket.channel_id == (await _channel_or_404(s, channel)).id)
        if state:
            stmt = stmt.where(Ticket.state == state)
        if assignee:
            # An exact match on the stored participant string: `agent:news`,
            # not `news`. The store is what normalises a bare name on a write
            # (and the `tickets` tool before a read), because a filter that
            # guessed would answer a different question than the one asked.
            stmt = stmt.where(Ticket.assignee == assignee)
        if mine:
            stmt = stmt.where(Ticket.assignee == caller.participant)
        if q:
            # Escaped, as Relay's search escapes its own: a `%` somebody typed
            # is a percent sign, not "match anything".
            needle = q.lower()
            stmt = stmt.where(or_(func.lower(Ticket.title).contains(needle, autoescape=True),
                                  func.lower(Ticket.body).contains(needle, autoescape=True)))
        if label:
            stmt = stmt.where(_labelled(s.get_bind(), label))
        rows = list((await s.execute(stmt.order_by(
            Ticket.priority, Ticket.last_activity_at.desc(), Ticket.id)
            .limit(limit))).scalars())
        return await _views(s, rows, request.app.state.settings)


@router.get("/api/tickets/projects", response_model=list[S.TicketProject])
async def list_ticket_projects(request: Request,
                               caller: Caller = Depends(require_relay_access(*READ))):
    """The channels that are projects, with the two counts a picker shows. A
    room with no prefix is simply not one — that is the whole definition."""
    agents = relay_api._agent_set(request)
    async with request.app.state.session_factory() as s:
        readable = await _readable(s, caller, agents)
        stmt = select(Conversation).where(Conversation.ticket_prefix.isnot(None),
                                          Conversation.archived_at.is_(None))
        if readable is not None:
            stmt = stmt.where(Conversation.id.in_(readable))
        rows = list((await s.execute(stmt.order_by(Conversation.name))).scalars())
        counts = dict(((cid, st), n) for cid, st, n in (await s.execute(
            select(Ticket.channel_id, Ticket.state, func.count())
            .where(Ticket.channel_id.in_([c.id for c in rows]))
            .group_by(Ticket.channel_id, Ticket.state))).all())
    return [{"id": c.id, "name": c.name, "title": c.title, "prefix": c.ticket_prefix,
             "open": counts.get((c.id, TicketState.OPEN.value), 0),
             "in_progress": counts.get((c.id, TicketState.IN_PROGRESS.value), 0)}
            for c in rows]


@router.get("/api/tickets/stats", response_model=S.TicketStats)
async def ticket_stats(request: Request,
                       caller: Caller = Depends(require_relay_access(*READ))):
    """What the board is worth looking at for. `stale` and `orphaned` are
    computed every time and stored nowhere: staleness is a function of a
    setting an operator can change, and orphanhood is a function of which
    agents exist right now — either one persisted would be a number that was
    true once."""
    settings = request.app.state.settings
    agents = relay_api._agent_set(request)
    now = utcnow()
    day, hour = now - timedelta(hours=24), now - timedelta(hours=1)
    async with request.app.state.session_factory() as s:
        readable = await _readable(s, caller, agents)

        def scoped(stmt):
            return stmt if readable is None else stmt.where(Ticket.channel_id.in_(readable))

        by_state = dict((await s.execute(scoped(
            select(Ticket.state, func.count()).group_by(Ticket.state)))).all())
        done = (await s.execute(scoped(select(func.count()).select_from(Ticket).where(
            Ticket.state == TicketState.DONE.value,
            Ticket.closed_at >= day)))).scalar_one()
        # In-progress rows are read rather than counted in SQL so `is_stale` —
        # the one definition of stale, shared with the card badge — decides.
        in_flight = list((await s.execute(scoped(select(Ticket).where(
            Ticket.state == TicketState.IN_PROGRESS.value)))).scalars())
        stale = sum(1 for t in in_flight
                    if is_stale(t, now, settings.tickets_stale_days))
        # Orphans are OPEN work only: a finished ticket whose assignee has since
        # been deleted is history, not something anybody needs to pick up.
        assigned = list((await s.execute(scoped(select(Ticket).where(
            Ticket.assignee.isnot(None),
            Ticket.state.notin_(tuple(CLOSED_STATES)))))).scalars())
        orphaned = sum(1 for t in assigned
                       if (name := agent_name(t.assignee)) is not None
                       and name not in agents)
        moved = (await s.execute(scoped(
            select(TicketEvent.actor, func.count())
            .select_from(TicketEvent).join(Ticket, Ticket.id == TicketEvent.ticket_id)
            .where(TicketEvent.kind.in_(("moved", "reopened")),
                   TicketEvent.created_at >= day)
            .group_by(TicketEvent.actor)))).all()
        opened = (await s.execute(scoped(
            select(TicketEvent.actor, func.count())
            .select_from(TicketEvent).join(Ticket, Ticket.id == TicketEvent.ticket_id)
            .where(TicketEvent.kind == "created", TicketEvent.created_at >= hour,
                   TicketEvent.actor.like("agent:%"))
            .group_by(TicketEvent.actor)))).all()
        faces = await faces_for(s, _agents_in([a for a, _ in moved] +
                                              [a for a, _ in opened]))
        limit = settings.tickets_agent_creates_per_hour
        # "How many more may it open" is the store's arithmetic, not this
        # route's: the same answer the tool quotes back at an agent it refused,
        # so the dashboard and the refusal can never disagree by one.
        busiest = sorted(opened, key=lambda r: (-r[1], r[0]))[:BUDGET_AGENTS]
        left = {actor: await store.agent_create_budget_left(
            s, participant_label(actor), limit, now) for actor, _ in busiest}
    return {"open": by_state.get(TicketState.OPEN.value, 0),
            "in_progress": by_state.get(TicketState.IN_PROGRESS.value, 0),
            "blocked": by_state.get(TicketState.BLOCKED.value, 0),
            "review": by_state.get(TicketState.REVIEW.value, 0),
            "done_24h": done, "stale": stale, "orphaned": orphaned,
            "moved_24h": [{"actor": actor, "label": participant_label(actor),
                           "count": n, "face": _face(actor, faces)}
                          for actor, n in sorted(moved, key=lambda r: (-r[1], r[0]))],
            "budget": {"creates_per_hour": limit,
                       "stale_days": settings.tickets_stale_days,
                       "agents": [{"agent": participant_label(actor), "used": n,
                                   "left": left[actor], "face": _face(actor, faces)}
                                  for actor, n in busiest]}}


@router.get("/api/tickets/events", response_class=StreamingResponse)
async def ticket_stream(request: Request,
                        caller: Caller = Depends(require_relay_access(*READ))):
    """The board, live: a `ticket` frame per change (the client upserts by id),
    a heartbeat so nothing in between decides an idle stream is a dead one, and
    an `overflow` marker for a reader that fell too far behind to be caught up
    by anything but a refetch.

    Kafka-fed only, unlike Relay's stream: a ticket change is a page of work
    rather than a line of conversation, and the reader is a board that refetches
    on reconnect, so the local hand-off Relay needs to stay live between pods
    would only buy a duplicate frame.

    An agent's stream is filtered to the rooms it belongs to, and re-asks that
    question on every quiet tick: membership can be taken away while a stream is
    open, and a socket that kept delivering afterwards is the one way an agent
    reads a room it was thrown out of."""
    feed = request.app.state.ticket_feed
    agents = relay_api._agent_set(request)
    async with request.app.state.session_factory() as s:
        readable = await _readable(s, caller, agents)
    queue = feed.subscribe(STREAM)

    async def stream():
        allowed = readable
        try:
            while True:
                try:
                    # Read per wait: it is a module global a test turns down
                    # without patching the route.
                    event, data = await asyncio.wait_for(
                        queue.get(), relay_api.HEARTBEAT_SECONDS)
                except TimeoutError:
                    if caller.agent is not None:
                        async with request.app.state.session_factory() as s:
                            allowed = await _readable(
                                s, caller, relay_api._agent_set(request))
                    yield ": heartbeat\n\n"
                    continue
                if event == OVERFLOW:
                    # No cursor to hand back: the board resyncs by re-listing,
                    # which is one request and always correct.
                    yield relay_api._frame(OVERFLOW, {})
                    continue
                if allowed is not None and data.get("channel_id") not in allowed:
                    continue
                yield relay_api._frame(event, data)
        finally:
            feed.unsubscribe(STREAM, queue)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@router.get("/api/tickets/{key}", response_model=S.TicketDetail)
async def get_ticket(request: Request, key: str,
                     caller: Caller = Depends(require_relay_access(*READ))):
    """One ticket: the row, its structured history, the id of the thread its
    discussion lives in, the runs it produced, and whoever is working on it
    right now. The messages themselves are Relay's to serve — the client asks
    for the thread under `root_message_id`, so a ticket's conversation has one
    endpoint and not two."""
    agents = relay_api._agent_set(request)
    async with request.app.state.session_factory() as s:
        ticket = await _ticket_or_404(s, key, caller, agents)
        events = list((await s.execute(select(TicketEvent).where(
            TicketEvent.ticket_id == ticket.id)
            .order_by(TicketEvent.created_at, TicketEvent.id))).scalars())
        # `Run.ticket_id` is set only for a run SUMMONED in the thread, so on
        # its own it misses the run that opened the ticket and every run that
        # moved it through the tool — the ones whose events are already on this
        # page, each one linking a run the panel above claimed did not exist.
        # The ticket's own history is what says which runs touched it.
        #
        # Those NAMED runs are never dropped, and the newest summoned runs fill
        # the rest of the page: a newest-first cap over the union would lose the
        # opener again the moment ten newer runs are summoned in the thread, and
        # the opener is the oldest run there is. So the list is `LINKED_RUNS`
        # unless the history itself names more than that.
        named = {e.run_id for e in events if e.run_id} | {ticket.run_id} - {None}
        runs = await _runs_in(s, Run.id.in_(named)) if named else []
        fill = LINKED_RUNS - len(runs)
        if fill > 0:
            summoned = [Run.ticket_id == ticket.id]
            if named:
                summoned.append(Run.id.notin_(named))
            runs += await _runs_in(s, *summoned, limit=fill)
        runs.sort(key=lambda r: (r.created_at, r.id), reverse=True)
        thinking = next((r for r in runs if r.state in ACTIVE_STATES), None)
        faces = await faces_for(s, _agents_in([e.actor for e in events]))
        view = await _one(s, ticket, request.app.state.settings)
    return {"ticket": view,
            "events": [{**store.ticket_event_view(e), "actor_face": _face(e.actor, faces)}
                       for e in events],
            "root_message_id": ticket.root_message_id,
            "runs": [{"id": r.id, "agent": r.agent, "state": str(r.state),
                      "trigger": r.trigger,
                      "created_at": r.created_at.isoformat() if r.created_at else None}
                     for r in runs],
            "thinking": None if thinking is None else {"run_id": thinking.id,
                                                       "agent": thinking.agent}}


# --- writes -------------------------------------------------------------------
# Every one of these hands the store the caller's participant and, for an agent,
# the Run it is speaking from. `TicketRuleError` is a 400 — the caller sent
# something the rules refuse and can read the message and correct it — except on
# the move route, where the only rules in play are the board's own transitions
# and the answer is a 409: "that is not a legal move from where this ticket is"
# is a conflict with the ticket's state, not a malformed request.

@router.post("/api/tickets", status_code=201, response_model=S.TicketView)
async def create_ticket(request: Request, body: S.TicketIn,
                        caller: Caller = Depends(require_relay_access(*WRITE))):
    """Open a ticket. The reporter is the caller, from the token — there is no
    reporter field on the wire, so a prompt-injected agent gains nothing by
    asking to file as somebody else.

    An agent's create is budgeted (`tickets_agent_creates_per_hour`), and the
    refusal is answered twice over: a 429 the agent reads, and one line in the
    project channel per hour so the humans watching the room can see that an
    agent is looping without the board filling up with the evidence."""
    st = request.app.state
    agents = relay_api._agent_set(request)
    limit = st.settings.tickets_agent_creates_per_hour
    async with st.session_factory() as s:
        conv = await _channel_or_404(s, body.channel)
        # Held as a plain string: the rollback below expires every instance in
        # the session, and reading `conv.id` off an expired one would be a
        # lazy load with no greenlet to run it in.
        channel_id = conv.id
        await _may_write(s, conv, caller, agents)
        run = await _run_of(s, request, caller, writing=True)
        parent_id = await _parent_id(s, body.parent, channel_id)
        try:
            ticket = await store.create_ticket(
                s, st.producer, conv, actor=caller.participant, title=body.title,
                body=body.body, assignee=body.assignee, priority=body.priority,
                labels=body.labels, parent_id=parent_id, due_at=body.due_at,
                notify=body.notify, run=run, url_base=URL_BASE,
                budget_limit=limit if caller.agent is not None else None)
        except TicketBudgetError:
            # Rolled back first: the refusal happened mid-transaction, and the
            # notice is a message of its own that must not carry the create's
            # half-written state into the room with it.
            await s.rollback()
            conv = await s.get(Conversation, channel_id)
            await store.say_budget_once(s, st.producer, conv, limit, utcnow())
            raise HTTPException(429, budget_body(limit))
        except TicketRuleError as e:
            await s.rollback()
            raise HTTPException(400, str(e))
        return await _one(s, ticket, st.settings)


@router.patch("/api/tickets/{key}", response_model=S.TicketView)
async def update_ticket(request: Request, key: str, body: S.TicketPatch,
                        caller: Caller = Depends(require_relay_access(*WRITE))):
    """Edit the fields. Only what the caller actually sent is touched — the
    unset fields are not passed to the store at all — so clearing a due date and
    leaving it alone are different requests rather than the same null."""
    st = request.app.state
    agents = relay_api._agent_set(request)
    async with st.session_factory() as s:
        ticket = await _ticket_or_404(s, key, caller, agents)
        conv = await _channel_or_404(s, ticket.channel_id)
        await _may_write(s, conv, caller, agents)
        run = await _run_of(s, request, caller, writing=True)
        given = body.model_fields_set
        fields = {name: getattr(body, name)
                  for name in ("title", "body", "priority", "labels", "due_at")
                  if name in given}
        if "parent" in given:
            fields["parent_id"] = await _parent_id(s, body.parent, ticket.channel_id)
        try:
            await store.update_ticket(s, st.producer, ticket, actor=caller.participant,
                                      reason=body.reason, run=run, url_base=URL_BASE,
                                      **fields)
        except TicketRuleError as e:
            await s.rollback()
            raise HTTPException(400, str(e))
        return await _one(s, ticket, st.settings)


@router.post("/api/tickets/{key}/move", response_model=S.TicketView)
async def move_ticket(request: Request, key: str, body: S.TicketMoveIn,
                      caller: Caller = Depends(require_relay_access(*WRITE))):
    st = request.app.state
    agents = relay_api._agent_set(request)
    async with st.session_factory() as s:
        ticket = await _ticket_or_404(s, key, caller, agents)
        conv = await _channel_or_404(s, ticket.channel_id)
        await _may_write(s, conv, caller, agents)
        run = await _run_of(s, request, caller, writing=True)
        try:
            await store.move_ticket(s, st.producer, ticket, actor=caller.participant,
                                    to_state=body.state, reason=body.reason, run=run,
                                    url_base=URL_BASE)
        except TicketRuleError as e:
            await s.rollback()
            raise HTTPException(409, str(e))
        return await _one(s, ticket, st.settings)


@router.post("/api/tickets/{key}/assign", response_model=S.TicketView)
async def assign_ticket(request: Request, key: str, body: S.TicketAssignIn,
                        caller: Caller = Depends(require_relay_access(*WRITE))):
    """Give a ticket to somebody. With `notify`, an agent assignee is summoned
    by a real mention in the ticket's thread (docs/design/20) — the ask IS the
    assignment — under every guard the router already applies to a mention."""
    st = request.app.state
    agents = relay_api._agent_set(request)
    async with st.session_factory() as s:
        ticket = await _ticket_or_404(s, key, caller, agents)
        conv = await _channel_or_404(s, ticket.channel_id)
        await _may_write(s, conv, caller, agents)
        run = await _run_of(s, request, caller, writing=True)
        try:
            await store.assign_ticket(s, st.producer, ticket, actor=caller.participant,
                                      assignee=body.to, reason=body.reason,
                                      notify=body.notify, run=run, url_base=URL_BASE)
        except TicketRuleError as e:
            await s.rollback()
            raise HTTPException(400, str(e))
        return await _one(s, ticket, st.settings)


@router.post("/api/tickets/{key}/comments", response_model=S.RelayMessage)
async def comment_on_ticket(request: Request, key: str, body: S.TicketCommentIn,
                            caller: Caller = Depends(require_relay_access(*WRITE))):
    """Say something in the ticket's thread. An ordinary Relay reply, mentions
    and all — which is why it answers with the message: the client appends it to
    the thread it is already showing."""
    st = request.app.state
    agents = relay_api._agent_set(request)
    async with st.session_factory() as s:
        ticket = await _ticket_or_404(s, key, caller, agents)
        conv = await _channel_or_404(s, ticket.channel_id)
        await _may_write(s, conv, caller, agents)
        run = await _run_of(s, request, caller, writing=True)
        try:
            event = await store.comment_ticket(s, st.producer, ticket,
                                               actor=caller.participant, body=body.body,
                                               run=run)
        except TicketRuleError as e:
            await s.rollback()
            raise HTTPException(400, str(e))
        msg = await s.get(RelayMessage, event.message_id)
        faces = await faces_for(s, _agents_in([msg.author]))
    return message_view(msg, face=_face(msg.author, faces))


def ticket_feed(session_factory=None) -> TopicFeed:
    """The API's live ticket fan-out. The payload on `tickets.events` is
    `{event, ticket}`; what a board wants is the ticket, which it upserts by
    id — the event is the store's record of WHY, and the thread already says
    that in words."""
    return TopicFeed(TOPIC_TICKETS_EVENTS, event="ticket",
                     frame_of=lambda data: ((STREAM, data["ticket"])
                                            if data.get("ticket") else None),
                     session_factory=session_factory)
