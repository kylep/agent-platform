"""The wiki's REST surface (docs/design/21): the pages, the writes that change
one, and the live stream both read.

Every write here is a thin skin over `wiki_store`, which is the ONE place a page
changes — the row, its version, its links and the diff card in `#wiki` land in
one transaction there. What this module owns is the seam between a caller and
that store, and it is Tickets' (`api/tickets.py`) seam but for the fence:

- WHO is acting. The actor is `caller.participant`, resolved from the token by
  `require_relay_access`, and there is no author on the wire at all. An agent
  additionally has to be speaking from its own Run (`_run_of`), because the
  store stamps that run on the version — a page's provenance is the whole
  point — so a per-run token whose run has gone may read the wiki and write
  nothing.
- WHERE it may act: everywhere. A page belongs to the platform, not to a room,
  so there is no membership fence here and the SSE stream is unfiltered. `#wiki`
  is where writes are NARRATED, and a caller that cannot see that room still
  reads the pages it is about. That is the design's call (docs/design/21): the
  wiki is what everybody here shares.
- The hourly write budget is an agent's alone, enforced inside the store under
  the room lock, and answered twice over — a 429 the agent reads and one line
  in `#wiki` per hour so the humans can see a loop where the work is.

The `relay` role — the per-run participant role — reaches `/api/wiki/*` as well
as `/api/relay/*` and `/api/tickets/*` now, always as the agent the token names.
"""
import asyncio
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import cast, func, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import defer

from agentplatform import wiki_store as store
from agentplatform.agentspec import TOOL_WIKI
from agentplatform.api import agents as agents_api
from agentplatform.api import memory as memory_api
from agentplatform.api import relay as relay_api
from agentplatform.api import schemas as S
# Relay's seam and Tickets' two, reused rather than restated: the participant a
# token resolves to, the Run an agent is speaking from, and how a participant
# becomes a face. A second copy of any of them would be a second answer to who
# is writing.
from agentplatform.api.relay import READ, WRITE, Caller, require_relay_access
from agentplatform.api.tickets import _agents_in, _face, _readable, _run_of
from agentplatform.db import Memory, RelayMessage, WikiPage, WikiVersion, utcnow
from agentplatform.events import TOPIC_WIKI_EVENTS
from agentplatform.relay_feed import OVERFLOW, TopicFeed
from agentplatform.relay_store import channel_by_name, faces_for
from agentplatform.tickets import participant_label
from agentplatform.wiki import (SLUG_RE, budget_body, find_links, is_stale,
                                line_counts, slugify, unified_diff)
from agentplatform.wiki_store import (WikiBudgetError, WikiConflictError,
                                      WikiExistsError, WikiRuleError)

router = APIRouter()


def require_wiki_access(*roles: str):
    """`require_relay_access`, plus the grant this door is behind.

    The three participant grants share ONE role (`relay`), and for an agent
    caller the role tuple is not what is judged — Relay and Tickets bound an
    agent by room MEMBERSHIP instead, which the wiki has nothing to match
    because a page is not in a room. So without this an agent granted only
    `mcp__platform__relay` would read and rewrite every page on the platform
    through the API, and the `wiki` grant would bound nothing.

    The grant is therefore checked here, directly, and for reads as well as
    writes: the tool is the sanctioned door, and an agent nobody granted it does
    not get the wiki by holding the messenger. Humans are unaffected — their
    authority is the role, as everywhere else. Disabled and quarantined agents
    are refused for the reason membership refuses them in Relay: they are not
    participants here any more."""
    inner = require_relay_access(*roles)

    async def dep(request: Request) -> Caller:
        caller = await inner(request)
        if caller.agent is None:
            return caller
        # `_caller_platform_tools` and not the row: a run JWT froze its grants
        # at launch (docs/design/13 C), and this must be the same answer
        # /api/whoami gives — a grant added mid-run does not widen the run that
        # is already using its token.
        granted = await agents_api._caller_platform_tools(request, caller.agent)
        if caller.agent not in relay_api._agent_set(request):
            raise HTTPException(403, "unknown or disabled agent")
        if TOOL_WIKI not in granted:
            raise HTTPException(403, "this agent is not granted the wiki tool")
        return caller

    return dep


# One stream for the whole wiki: a page is not in a room, so there is no key to
# fan out by and a client watching "recent changes" wants all of it.
STREAM = "wiki"
# Site-relative, exactly as Tickets' is: the platform has no public-base-URL
# setting, the web client resolves it against wherever it is served, and the
# Discord mirror reads the card's body rather than its url.
URL_BASE = ""
# The list is a search result, not a paging scheme — a guard against a runaway
# query over a table whose rows carry a 64 KB body each.
LIST_LIMIT = 200
HISTORY_LIMIT = 50
# "cited in N messages": the window, how many are named, and how far back the
# scan goes. The scan is capped because the count is read off the messages
# themselves (see `_cited_in`), so a page quoted in thousands of messages
# reports the cap rather than reading a month of the room into memory.
CITED_DAYS, CITED_LAST, CITED_SCAN = 30, 3, 200
# How many agents the stats' budget block names: it answers "who is hammering
# the wiki right now", and a list of every agent at zero answers nothing.
BUDGET_AGENTS = 5


# --- resolving what the caller named -----------------------------------------

def _slug(slug: str) -> str:
    """The slug as given, or a 400. Checked at the door and not just in the
    store because it is interpolated into a URL and into `[[...]]`: every path
    segment built from model text passes the grammar first (the Tickets
    lesson)."""
    if not SLUG_RE.fullmatch(slug or ""):
        raise HTTPException(400, f"{slug!r} is not a wiki slug")
    return slug


async def _page_or_404(s, slug: str, *, archived_ok: bool = False) -> WikiPage:
    """A page by slug. An archived one reads as missing everywhere except its
    own history and versions: archiving takes a page out of the wiki without
    taking it out of the record (docs/design/21)."""
    page = (await s.execute(select(WikiPage).where(
        WikiPage.slug == slug))).scalars().first()
    if page is None or (page.archived_at is not None and not archived_ok):
        raise HTTPException(404, "unknown wiki page")
    return page


def _utc(when: datetime) -> datetime:
    """A caller's timestamp as UTC. sqlite stores the fields it is handed and
    forgets the offset, so `changed_since=...+05:00` would otherwise filter on a
    wall clock nobody keeps."""
    return when.astimezone(timezone.utc) if when.tzinfo is not None else when


def _matching(bind, q: str):
    """`q` as a `(filter, ranking)` pair — the one place search is defined.

    Postgres has the GIN tsvector `db.py` builds, so it answers by relevance;
    sqlite has no such index and falls back to a substring match ordered by
    recency, exactly the dialect fork the ticket board's `q` makes. A wiki
    search is over documents rather than headlines, which is why this one takes
    the stemmed index where Tickets deliberately does not.

    `plainto_tsquery` ANDs the words, and that is the choice here even though
    the PROMPT search (`wiki_store._pg_search`) deliberately ORs them: a search
    box is a person narrowing down, who can drop a word and look again, while
    the prompt search is handed a whole room's worth of talk with nobody to ask.
    ANDing also keeps the two dialects honest about each other — a substring
    match on the phrase is narrow in the same direction, where an OR on one side
    only would make the UI answer differently on a dev's sqlite than in prod."""
    if bind.dialect.name == "postgresql":
        vector = func.to_tsvector("english", WikiPage.title + " " + WikiPage.body)
        query = func.plainto_tsquery("english", q)
        return vector.op("@@")(query), func.ts_rank(vector, query).desc()
    needle = q.lower()
    # Escaped, as Relay escapes its own search: a `%` somebody typed is a
    # percent sign, not "match anything".
    return or_(func.lower(WikiPage.title).contains(needle, autoescape=True),
               func.lower(WikiPage.body).contains(needle, autoescape=True)), None


def _tagged(bind, tag: str):
    """`tag` as a WHERE clause, so the cap counts matching pages. Containment
    has no portable spelling: `@>` on postgres (the column is `json`, hence the
    cast to `jsonb`, which is the type that has the operator) and `json_each` on
    sqlite — the same fork `api/tickets.py` makes for labels."""
    if bind.dialect.name == "postgresql":
        return cast(WikiPage.tags, JSONB).contains([tag])
    each = func.json_each(WikiPage.tags).table_valued("value")
    return select(1).select_from(each).where(each.c.value == tag).exists()


# --- what a page looks like on the wire --------------------------------------

def _iso(ts):
    return ts.isoformat() if ts else None


async def _views(s, pages) -> list[dict]:
    faces = await faces_for(s, _agents_in([p.updated_by for p in pages]))
    return [{**store.page_view(p), "updated_by_face": _face(p.updated_by, faces)}
            for p in pages]


async def _one(s, page) -> dict:
    return (await _views(s, [page]))[0]


async def _cited_in(s, slug: str, readable: set[str] | None) -> dict:
    """How often the rooms have pointed at this page lately, and the last three
    that did.

    The LIKE is the candidate filter; `find_links` is the answer. A `[[slug]]`
    inside a code fence is somebody showing the syntax rather than citing the
    page — the same rule the body parser applies, and one only python can apply
    — so the count is over messages that really link it. Only `text` messages
    count: the wiki's own diff cards carry the slug too, and counting those
    would make "cited in 12 messages" mean "edited 12 times".

    The PAGE is the platform's, but the citations are Relay's, and Relay's fence
    applies to them: `readable` is the rooms this caller may see (None for a
    human, who sees the platform), so an agent is never told that a room it is
    not in was talking about this page — neither the channel and author in
    `last`, nor the count, which on its own would leak that a private room is
    discussing something.

    `count_capped` says the scan hit its limit, which makes the count a floor
    rather than a total. Reported rather than hidden: a client showing "cited in
    200 messages" when the real number is a thousand is wrong in a way nobody
    can see."""
    since = utcnow() - timedelta(days=CITED_DAYS)
    stmt = select(RelayMessage).where(
        RelayMessage.kind == "text", RelayMessage.deleted_at.is_(None),
        RelayMessage.created_at >= since,
        RelayMessage.body.contains(f"[[{slug}]]", autoescape=True))
    if readable is not None:
        stmt = stmt.where(RelayMessage.channel_id.in_(readable))
    rows = list((await s.execute(
        stmt.order_by(RelayMessage.created_at.desc(), RelayMessage.id.desc())
        .limit(CITED_SCAN))).scalars())
    citing = [m for m in rows if slug in find_links(m.body or "")]
    return {"count": len(citing), "count_capped": len(rows) >= CITED_SCAN,
            "last": [{"message_id": m.id, "channel_id": m.channel_id,
                      "author": m.author, "created_at": _iso(m.created_at)}
                     for m in citing[:CITED_LAST]]}


# --- writes -------------------------------------------------------------------
# Every write hands the store the caller's participant and, for an agent, the Run
# it is speaking from and its hourly budget. The refusals map the same way for
# all of them: a rule the caller can read and correct is a 400, a lost race is a
# 409 carrying what it takes to merge, and an exhausted budget is a 429 plus one
# line in the room, and a slug somebody already holds is a 409 whether the store
# saw it coming or lost the race for it. Archiving what is already archived is
# the one refusal a route asks for itself: the store makes it too, under the row
# lock, but only the route can say that "already gone" is a conflict with the
# page's state rather than a malformed request.

def _conflict(e: WikiConflictError) -> JSONResponse:
    """The 409 a writer can act on: what the page is at now, and the summary of
    what it says, so the caller can re-read and merge rather than guess. A
    JSONResponse and not an HTTPException because those two facts belong beside
    the message, not nested under it."""
    return JSONResponse(status_code=409,
                        content={"detail": str(e),
                                 "current_version": e.current_version,
                                 "current_summary": e.current_summary})


async def _say_budget(s, st, limit: int) -> None:
    """One line in `#wiki` per hour when an agent runs out of writes. A missing
    room costs the notice and nothing else — the record is the wiki, the room is
    the audience."""
    conv = await channel_by_name(s, store.WIKI_CHANNEL)
    if conv is not None:
        await store.say_budget_once(s, st.producer, conv, limit, utcnow())


async def _write(request: Request, caller: Caller, make):
    """Run one store write as this caller and answer its refusals.

    `make` is handed the session and the three things only this layer knows: who
    is writing, the run they are writing from, and what their budget is. Humans
    have no budget — a person editing forty pages in an hour is a working
    afternoon."""
    st = request.app.state
    limit = (st.settings.wiki_agent_writes_per_hour if caller.agent is not None
             else None)
    async with st.session_factory() as s:
        run = await _run_of(s, request, caller, writing=True)
        try:
            page = await make(s, actor=caller.participant, run=run, budget_limit=limit)
        except WikiBudgetError:
            # Rolled back first: the refusal happened mid-transaction, and the
            # notice is a message of its own that must not carry the write's
            # half-written state into the room with it.
            await s.rollback()
            await _say_budget(s, st, limit)
            raise HTTPException(429, budget_body(limit))
        except WikiConflictError as e:
            await s.rollback()
            return _conflict(e)
        except WikiExistsError as e:
            # A slug that was taken when the store looked and one taken by a
            # writer that landed in between are the same answer: somebody got
            # there first. 409 for both, or a create that merely lost a race
            # would read as a malformed request.
            await s.rollback()
            raise HTTPException(409, str(e))
        except WikiRuleError as e:
            await s.rollback()
            raise HTTPException(400, str(e))
        return await _one(s, page)


@router.post("/api/wiki/pages", status_code=201, response_model=S.WikiPageView)
async def create_wiki_page(request: Request, body: S.WikiPageIn,
                           caller: Caller = Depends(require_wiki_access(*WRITE))):
    """Write a page that does not exist yet, at v1. The author is the caller,
    from the token, so a prompt-injected agent gains nothing by asking to write
    as somebody else."""
    slug = _slug(body.slug)
    st = request.app.state
    return await _write(request, caller, lambda s, **kw: store.create_page(
        s, st.producer, slug=slug, title=body.title, body=body.body,
        tags=body.tags, reason=body.reason, url_base=URL_BASE, **kw))


@router.put("/api/wiki/pages/{slug}", response_model=S.WikiPageView)
async def write_wiki_page(request: Request, slug: str, body: S.WikiWriteIn,
                          caller: Caller = Depends(require_wiki_access(*WRITE))):
    """Replace a page's body, naming the version that was read. A mismatch is a
    409 carrying the current version and summary; the loser writes nothing."""
    slug = _slug(slug)
    st = request.app.state
    return await _write(request, caller, lambda s, **kw: store.write_page(
        s, st.producer, slug, body=body.body, title=body.title, tags=body.tags,
        base_version=body.base_version, reason=body.reason, url_base=URL_BASE, **kw))


@router.post("/api/wiki/pages/{slug}/append", response_model=S.WikiPageView)
async def append_wiki_page(request: Request, slug: str, body: S.WikiAppendIn,
                           caller: Caller = Depends(require_wiki_access(*WRITE))):
    """Add a section to the end of a page, creating it when it is not there —
    the shape an agent should prefer (docs/design/21), because appending never
    conflicts and a wanted page is an invitation to accept."""
    slug = _slug(slug)
    st = request.app.state
    return await _write(request, caller, lambda s, **kw: store.append_page(
        s, st.producer, slug, body=body.body, reason=body.reason,
        url_base=URL_BASE, **kw))


@router.delete("/api/wiki/pages/{slug}", response_model=S.WikiPageView)
async def archive_wiki_page(request: Request, slug: str,
                            caller: Caller = Depends(require_wiki_access(*WRITE))):
    """Archive, never delete: the page leaves search, the links and the prompt
    block and keeps every word of its history."""
    slug = _slug(slug)
    st = request.app.state
    async with st.session_factory() as s:
        page = await _page_or_404(s, slug, archived_ok=True)
        if page.archived_at is not None:
            raise HTTPException(409, f"{slug} is already archived")
    # Unmetered, and the store takes no budget here: archiving writes no
    # version, so there is nothing for an hourly cap on writes to count.
    return await _write(request, caller, lambda s, *, actor, run, budget_limit:
                        store.archive_page(s, st.producer, slug, actor=actor,
                                           run=run, url_base=URL_BASE))


@router.post("/api/wiki/pages/{slug}/restore", response_model=S.WikiPageView)
async def restore_wiki_page(request: Request, slug: str, body: S.WikiRestoreIn,
                            caller: Caller = Depends(require_wiki_access(*WRITE))):
    """Bring a page back — from the archive, or back to one of its own versions,
    which is a new version rather than a rewritten history."""
    slug = _slug(slug)
    st = request.app.state
    return await _write(request, caller, lambda s, **kw: store.restore_page(
        s, st.producer, slug, version=body.version, reason=body.reason,
        url_base=URL_BASE, **kw))


async def _memory_of(s, caller: Caller, body: S.WikiPromoteIn) -> Memory:
    """The memory this caller asked to promote, by id or by key.

    The namespace rule is one rule read two ways. By id, the row names its own
    namespace and a mismatch is a 403: the caller already holds the id, so
    there is no existence left to protect and "that is not yours" is the answer
    an agent can act on (the memory API's own 404 would send it looking for a
    better id). By key, the namespace is an INPUT, so a wrong one is refused
    before any lookup and a key that is simply not there is a 404 — the same
    404 a human gets, because a key in a namespace the caller may read either
    exists or does not.

    `memory_api._resolve_ns` is deliberately not reused: it reads the API
    KEY's agent, and the caller here is a run participant whose namespace is
    `caller.agent`."""
    if body.memory_id:
        row = await s.get(Memory, body.memory_id)
        if row is None:
            raise HTTPException(404, "unknown memory")
        if caller.agent is not None and row.agent != caller.agent:
            raise HTTPException(403, "an agent may promote only its own memories")
        return row
    if caller.agent is not None:
        if body.agent is not None and body.agent != caller.agent:
            raise HTTPException(403, "an agent may promote only its own memories")
        namespace = caller.agent
    elif body.agent:
        namespace = body.agent
    else:
        # A key is unique inside a namespace and nowhere else, and a human has
        # none of their own to fall back on.
        raise HTTPException(400, "promoting by key needs agent, whose memory it is")
    row = (await s.execute(select(Memory).where(
        Memory.agent == namespace, Memory.key == body.key))).scalars().first()
    if row is None:
        raise HTTPException(404, f"{namespace} has no memory keyed {body.key!r}")
    return row


@router.post("/api/wiki/promote", response_model=S.WikiPageView)
async def promote_memory(request: Request, body: S.WikiPromoteIn,
                         caller: Caller = Depends(require_wiki_access(*WRITE))):
    """Harden a memory into a page everybody can cite.

    A human promotes from any namespace and an agent only its own — a memory is
    a private note, and promoting somebody else's is publishing it for them. The
    row is read here rather than over HTTP (one process, one transaction), but
    through the memory API's own view so the shape the store is handed is the
    shape the memory endpoints serve.

    A memory is named by id or by `key`. The key is resolved HERE rather than
    by the caller, because the caller that wants it most cannot do it: an agent
    remembers a key, and `/api/memories` is `READ_ROLES`, which a participant
    token is not. Resolution is scoped the same way promotion is — an agent's
    own namespace, and a human's whichever they named.

    The slug and title come from the memory's key when the caller does not name
    them, which is what makes promoting the same memory twice land on the same
    page instead of a second copy."""
    st = request.app.state
    async with st.session_factory() as s:
        row = await _memory_of(s, caller, body)
        memory = memory_api._view(row)
    try:
        slug = _slug(body.slug or slugify(memory["key"] or ""))
    except ValueError as e:
        # A memory with no key and no slug given: nothing to name the page.
        raise HTTPException(400, str(e))
    title = body.title or (memory["key"] or slug)
    return await _write(request, caller, lambda s, **kw: store.promote_memory(
        s, st.producer, memory, slug=slug, title=title, url_base=URL_BASE, **kw))


# --- reads --------------------------------------------------------------------

@router.get("/api/wiki/pages", response_model=list[S.WikiPageView])
async def list_wiki_pages(request: Request, q: str | None = None,
                          tag: str | None = None,
                          changed_since: datetime | None = None,
                          source_memory_id: str | None = None,
                          limit: int = Query(LIST_LIMIT, ge=1, le=LIST_LIMIT),
                          caller: Caller = Depends(require_wiki_access(*READ))):
    """The wiki, filtered. Newest first, or by relevance when `q` is a search
    the dialect can rank. Archived pages are excluded from all of it: they are
    out of the wiki by definition, and their history is read by slug."""
    async with request.app.state.session_factory() as s:
        stmt = select(WikiPage).where(WikiPage.archived_at.is_(None))
        order = WikiPage.updated_at.desc()
        if q:
            clause, rank = _matching(s.get_bind(), q)
            stmt = stmt.where(clause)
            order = rank if rank is not None else order
        if tag:
            stmt = stmt.where(_tagged(s.get_bind(), tag))
        if changed_since:
            stmt = stmt.where(WikiPage.updated_at >= _utc(changed_since))
        if source_memory_id:
            stmt = stmt.where(WikiPage.source_memory_id == source_memory_id)
        rows = list((await s.execute(
            stmt.order_by(order, WikiPage.slug).limit(limit))).scalars())
        return await _views(s, rows)


@router.get("/api/wiki/wanted", response_model=list[S.WikiWantedRow])
async def list_wanted_pages(request: Request,
                            caller: Caller = Depends(require_wiki_access(*READ))):
    """The red links, most-asked first: the wiki's to-do list rather than a
    broken-link report (docs/design/21)."""
    async with request.app.state.session_factory() as s:
        return await store.wanted(s)


@router.get("/api/wiki/stats", response_model=S.WikiStats)
async def wiki_stats(request: Request,
                     caller: Caller = Depends(require_wiki_access(*READ))):
    """The state of the garden. `stale` is computed every time and stored
    nowhere — it is a function of a setting an operator can change, so a
    persisted number would be one that was true once."""
    settings = request.app.state.settings
    now = utcnow()
    day, hour = now - timedelta(hours=24), now - timedelta(hours=1)
    async with request.app.state.session_factory() as s:
        pages = (await s.execute(select(func.count()).select_from(WikiPage)
                                 .where(WikiPage.archived_at.is_(None)))).scalar_one()
        edits = (await s.execute(
            select(WikiVersion.author, func.count())
            .where(WikiVersion.created_at >= day)
            .group_by(WikiVersion.author))).all()
        # The bodies are deferred: staleness is a question about two timestamps,
        # and reading every page's 64 KB to answer it would make the dashboard
        # the most expensive page on the platform.
        live = list((await s.execute(select(WikiPage)
                                     .options(defer(WikiPage.body))
                                     .where(WikiPage.archived_at.is_(None)))).scalars())
        stale = sum(1 for p in live if is_stale(p, now, settings.wiki_stale_days))
        wanted = len(await store.wanted(s))
        written = (await s.execute(
            select(WikiVersion.author, func.count())
            .where(WikiVersion.created_at >= hour, WikiVersion.author.like("agent:%"))
            .group_by(WikiVersion.author))).all()
        faces = await faces_for(s, _agents_in([a for a, _ in edits]))
        limit = settings.wiki_agent_writes_per_hour
        busiest = sorted(written, key=lambda r: (-r[1], r[0]))[:BUDGET_AGENTS]
        # "How many more may it write" is the store's arithmetic, not this
        # route's: the same answer the tool quotes back at an agent it refused,
        # so the dashboard and the refusal can never disagree by one.
        left = {actor: await store.agent_write_budget_left(
            s, participant_label(actor), limit, now) for actor, _ in busiest}
    return {"pages": pages, "wanted": wanted, "stale": stale,
            "edits_24h": [{"author": author, "count": n, "face": _face(author, faces)}
                          for author, n in sorted(edits, key=lambda r: (-r[1], r[0]))],
            "budget": {"limit": limit,
                       "agents": [{"agent": participant_label(actor), "used": n,
                                   "left": left[actor]} for actor, n in busiest]}}


@router.get("/api/wiki/events", response_class=StreamingResponse)
async def wiki_stream(request: Request,
                      caller: Caller = Depends(require_wiki_access(*READ))):
    """The wiki, live: a `page` frame per write (the client upserts by slug), a
    heartbeat so nothing in between decides an idle stream is a dead one, and an
    `overflow` marker for a reader that fell too far behind to be caught up by
    anything but a refetch.

    Unfiltered for everybody, agents included: a page is not room-scoped, so
    there is no membership question to re-ask on the quiet tick the way the
    ticket board has to. Kafka-fed only — a page write is a change to the record
    rather than a line of conversation, and the reader refetches on reconnect."""
    feed = request.app.state.wiki_feed
    queue = feed.subscribe(STREAM)

    async def stream():
        try:
            while True:
                try:
                    # Read per wait: it is a module global a test turns down
                    # without patching the route.
                    event, data = await asyncio.wait_for(
                        queue.get(), relay_api.HEARTBEAT_SECONDS)
                except TimeoutError:
                    yield ": heartbeat\n\n"
                    continue
                if event == OVERFLOW:
                    # No cursor to hand back: the client resyncs by re-listing,
                    # which is one request and always correct.
                    yield relay_api._frame(OVERFLOW, {})
                    continue
                yield relay_api._frame(event, data)
        finally:
            feed.unsubscribe(STREAM, queue)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@router.get("/api/wiki/pages/{slug}", response_model=S.WikiPageDetail)
async def get_wiki_page(request: Request, slug: str,
                        caller: Caller = Depends(require_wiki_access(*READ))):
    """One page, with what points at it: the pages that link it and the room
    messages that cited it."""
    slug = _slug(slug)
    agents = relay_api._agent_set(request)
    async with request.app.state.session_factory() as s:
        page = await _page_or_404(s, slug)
        view = await _one(s, page)
        links = await store.backlinks(s, slug)
        cited = await _cited_in(s, slug, await _readable(s, caller, agents))
    return {"page": view, "cited_in": cited,
            "backlinks": [{"slug": p.slug, "title": p.title} for p in links]}


@router.get("/api/wiki/pages/{slug}/history", response_model=list[S.WikiHistoryRow])
async def wiki_page_history(request: Request, slug: str,
                            limit: int = Query(HISTORY_LIMIT, ge=1, le=LIST_LIMIT),
                            caller: Caller = Depends(require_wiki_access(*READ))):
    """A page's versions, newest first, each with what it changed. Readable for
    an archived page: the history is the record archiving keeps."""
    slug = _slug(slug)
    async with request.app.state.session_factory() as s:
        page = await _page_or_404(s, slug, archived_ok=True)
        rows = await store.history(s, page, limit)
        faces = await faces_for(s, _agents_in([r["author"] for r in rows]))
    return [{**row, "author_face": _face(row["author"], faces)} for row in rows]


@router.get("/api/wiki/pages/{slug}/versions/{version}", response_model=S.WikiDiffView)
async def get_wiki_version(request: Request, slug: str, version: int,
                           caller: Caller = Depends(require_wiki_access(*READ))):
    """One version's body and the diff that produced it. Computed on read from
    the two stored bodies, so nothing about a page's history depends on a patch
    having been written correctly at the time."""
    slug = _slug(slug)
    async with request.app.state.session_factory() as s:
        page = await _page_or_404(s, slug, archived_ok=True)
        rows = dict((v.version, v) for v in (await s.execute(
            select(WikiVersion).where(WikiVersion.page_id == page.id,
                                      WikiVersion.version.in_((version - 1, version)))
        )).scalars())
        row = rows.get(version)
        if row is None:
            raise HTTPException(404, f"{slug} has no v{version}")
        previous = rows[version - 1].body if version - 1 in rows else ""
        faces = await faces_for(s, _agents_in([row.author]))
    added, removed = line_counts(previous, row.body)
    return {"version": {**store.version_view(row),
                        "author_face": _face(row.author, faces)},
            "diff": unified_diff(previous, row.body, slug, version - 1, version),
            "added": added, "removed": removed}


def wiki_feed(session_factory=None) -> TopicFeed:
    """The API's live wiki fan-out. The whole `wiki.events` payload is the frame,
    not just the page: recent changes is a list of EDITS — face, slug, reason,
    ±lines — and the page alone carries none of the last three."""
    return TopicFeed(TOPIC_WIKI_EVENTS, event="page",
                     frame_of=lambda data: ((STREAM, data) if data.get("page") else None),
                     session_factory=session_factory)
