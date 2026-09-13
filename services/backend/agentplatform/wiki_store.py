"""Changing a page (docs/design/21): the one place it happens.

A page is four things at once — the row people read, the version history that
proves who said it, the `[[slug]]` links the wanted list and the backlinks are
computed from, and a diff card in `#wiki` — and they are only ever the same
page because every write comes through here. Three writers (the REST API, the
`wiki` tool through it, and a promotion from a memory) would otherwise each
have their own idea of what a write is, and a body would land whose summary is
stale, whose links point at what the paragraph before it said, and whose
history skips a version nobody can now diff against.

The seams that matter, and they are Tickets' (docs/design/20) deliberately:

- Relay is the substrate. The diff card and the budget notice are written with
  `relay_store.post_relay_message` and published with `publish_relay_message`
  — never a bare insert, never a second producer — so the SSE fan-out and the
  Discord bridge see wiki traffic as ordinary room traffic.
- The platform's own sentences are `kind=event` and `kind=system`, which the
  router does not route, and they carry no mentions. A page titled
  `@pai rewrite this` is therefore a title, not a summons: nothing the wiki
  writes can wake an agent.
- The actor is the caller's participant string, resolved from its token by the
  API and never an argument the caller chooses — and when it is an agent, the
  Run it is speaking from comes with it (`_require_actor`), which is what puts
  an honest hop on the card and a `run_id` on the version.
- The room is where the wiki is WATCHED, not where it lives. An archived or
  deleted `#wiki` costs a write its card and nothing else.

Transactions: every public function makes exactly ONE commit, at the end, and
publishes after it. A commit in the middle would leave a page whose body moved
while its links or its version row rolled back — the one inconsistency the
whole module exists to prevent — and the caller would be told it failed."""
import logging
import re
from datetime import timedelta
from types import SimpleNamespace

from sqlalchemy import Text, cast, delete, func, or_, select
from sqlalchemy.exc import IntegrityError

from agentplatform.config import get_settings
from agentplatform.db import (Conversation, RelayMessage, WikiLink, WikiPage,
                              WikiVersion, utcnow)
from agentplatform.events import TOPIC_WIKI_EVENTS
from agentplatform.relay import AGENT_PREFIX, SYSTEM_AUTHOR, agent_name, is_agent
from agentplatform.relay_store import (channel_by_name, outbound_for_message,
                                       post_relay_message, publish_relay_message)
from agentplatform.tickets import participant_label
from agentplatform.wiki import (BUDGET_PREFIX, REASON_LIMIT, SLUG_RE,
                                TITLE_LIMIT, budget_body, card_body, card_for,
                                find_links, line_counts, promoted_body,
                                summary_of)

log = logging.getLogger("wiki_store")

# The room every diff card is narrated into (db.py seeds it). By NAME, not by
# id: the channel is a row an admin can archive and re-make, and a write that
# held an id would post into a room nobody is in any more.
WIKI_CHANNEL = "wiki"
# Tags are a filter, not prose: twenty of them is already more than anyone
# scans, and a 32-character tag is a sentence wearing a chip's clothes.
TAGS_MAX, TAG_LIMIT = 20, 32
# The reason every promotion writes, and the MARK a re-promotion reads back: a
# page whose newest version was written by a promotion is still the memory's
# page, and one whose newest version was not has been edited by somebody since.
PROMOTE_REASON = "promoted from memory"
# How many of a room's words the prompt search matches on. A summons can carry
# a pasted log, and every word past this one is another OR clause on a query
# that runs on every mention in every room.
SEARCH_WORDS_MAX = 12
# How many matching pages the sqlite fallback will score. Its ranking happens
# in Python, so every candidate is a row that crosses the wire — and the block
# it feeds is five lines long. Newest-first, because a wiki big enough to hit
# this is one where the page somebody touched last week is the likelier answer.
SEARCH_CANDIDATES_MAX = 200


class WikiRuleError(ValueError):
    """A write the wiki's rules refuse: a slug that is not a slug, a body past
    the cap, a write to an archived page. A ValueError because it is about the
    arguments and not about the world, which is what lets the API answer 400
    without knowing this module's exception classes."""


class WikiExistsError(WikiRuleError):
    """The slug is taken. Its own class because it is the one rule error that is
    not "you sent something wrong" but "somebody got there first": the API
    answers it 409, and it has to answer the courteous lookup and the lost race
    on the unique index identically, or a create that merely lost a race would
    read as a malformed request."""


class WikiConflictError(WikiRuleError):
    """Somebody else wrote the page since the caller read it. Carries what the
    caller needs to merge rather than just saying no: the version it would have
    to name next, and the summary of what is there now — which is what the API
    turns into a 409 and the tool turns into "re-read and merge".

    `detail` replaces the sentence for the one conflict that is not about a
    stale base_version: a re-promotion over a page somebody has since edited,
    where "re-read and merge" is the wrong advice and "edit the page" is the
    right one."""

    def __init__(self, slug: str, current_version: int, current_summary: str,
                 detail: str | None = None):
        super().__init__(detail or
                         f"{slug} is at v{current_version}, not the version you read")
        self.slug = slug
        self.current_version = current_version
        self.current_summary = current_summary


class WikiBudgetError(WikiRuleError):
    """An agent has used all the page writes it may make this hour. Its own
    class because it is the one refusal the caller answers differently — 429,
    and a notice in the room (`say_budget_once`) — while every other rule error
    is a 400 the agent should read and correct."""


def page_view(page) -> dict:
    """The page as every reader outside this module sees it: the Kafka payload,
    the API response and the SSE row are all this dict. Deliberately just the
    columns — the author's face and whether a link is red are the API's to add,
    because resolving them needs the agent roster and the link table, and a
    payload that sometimes carries them and sometimes does not is worse than
    one that never does."""
    return {"id": page.id, "slug": page.slug, "title": page.title,
            "body": page.body or "", "summary": page.summary or "",
            "tags": list(page.tags or []), "version": page.version,
            "created_by": page.created_by, "updated_by": page.updated_by,
            "source_memory_id": page.source_memory_id,
            "created_at": _iso(page.created_at), "updated_at": _iso(page.updated_at),
            "archived_at": _iso(page.archived_at)}


def version_view(version) -> dict:
    return {"id": version.id, "page_id": version.page_id,
            "version": version.version, "title": version.title,
            "body": version.body or "", "author": version.author,
            "run_id": version.run_id, "reason": version.reason or "",
            "created_at": _iso(version.created_at)}


def _iso(ts):
    return ts.isoformat() if ts else None


def _require_actor(actor: str, run) -> None:
    """An agent writes only from the run it is writing in.

    The hop of the card comes off `run` and so does the `run_id` stamped on the
    version, which is the whole provenance claim the wiki makes: every sentence
    in it links back to the run that put it there. An agent actor with no run
    breaks both at once — a card at hop 0 with a full budget every time, and a
    version nobody can trace. So the pairing is checked rather than trusted:
    the run must be the ACTOR's own, and a human or connector actor must bring
    no run at all, because a person is by definition the start of a chain."""
    name = agent_name(actor or "")
    if name is None:
        if run is not None:
            raise WikiRuleError(f"{actor!r} is not an agent but was given a run")
        return
    if run is None:
        raise WikiRuleError(f"{actor} must write from its own run")
    if run.agent != name:
        raise WikiRuleError(f"{actor} cannot write from a run belonging to {run.agent}")


def actor_hop(run) -> int:
    """The hop of the card the actor is about to post. Same sum as
    `api/relay.py:_hop_of`: `Run.depth` IS the hop of the mention that summoned
    the run, so what it posts sits one further out. A person starts at 0."""
    return (run.depth or 0) + 1 if run is not None else 0


async def create_page(session, producer, *, actor: str, slug: str, title: str,
                      body: str = "", tags=None, reason: str = "", run=None,
                      url_base: str = "", budget_limit: int | None = None,
                      max_body_bytes: int | None = None,
                      source_memory_id: str | None = None,
                      event: str = "created") -> WikiPage:
    """Write a page that does not exist yet, at v1, and post its card.

    `event` is what the Kafka payload calls this write. It is only ever
    overridden by `promote_memory`, which creates a page like anything else but
    is a different thing HAPPENING — a memory graduating — and a consumer that
    saw `created` could not tell the two apart.

    `budget_limit` is the agent's hourly write cap, enforced HERE rather than
    by the caller: a check made before the call is a count that N concurrent
    writes all pass, and an agent in a retry loop is exactly that concurrency.
    Under the room lock the count and the insert are one decision, so the
    N+1st write is the one that is refused."""
    _require_actor(actor, run)
    slug = _check_slug(slug)
    title, tags, reason = (_check_title(title), _check_tags(tags),
                           _check_reason(reason))
    _check_body(body, max_body_bytes)
    conv = await _lock_room(session)
    await _check_budget(session, actor, budget_limit)
    if await _page_by_slug(session, slug) is not None:
        raise WikiExistsError(f"a page called {slug} already exists")
    now = utcnow()
    page = WikiPage(slug=slug, title=title, body=body or "",
                    summary=summary_of(body), tags=tags, version=1,
                    created_by=actor, updated_by=actor,
                    source_memory_id=source_memory_id,
                    created_at=now, updated_at=now)
    # The lookup above is the courteous refusal; THIS is the guard. Two creates
    # of the same new slug race whenever the room lock is not there to serialise
    # them — `#wiki` archived, or deleted — and the unique index is then the only
    # thing that knows. Inside a savepoint so the loser can be told in the
    # platform's own words without poisoning the caller's transaction: an
    # IntegrityError escaping here is a 500 for a write somebody merely lost.
    try:
        async with session.begin_nested():
            session.add(page)
            await session.flush()
    except IntegrityError as exc:
        raise WikiExistsError(f"a page called {slug} already exists") from exc
    version = await _add_version(session, page, actor=actor, run=run, reason=reason)
    await _rewrite_links(session, page)
    return await _finish(session, producer, conv, page, version, event=event,
                         counts=line_counts("", page.body), actor=actor, run=run,
                         url_base=url_base)


async def write_page(session, producer, slug: str, *, actor: str, body: str,
                     title: str | None = None, tags=None,
                     base_version: int | None = None, reason: str = "", run=None,
                     url_base: str = "", budget_limit: int | None = None,
                     max_body_bytes: int | None = None) -> WikiPage:
    """Replace a page's body (and optionally its title and tags) with `body`.

    `base_version` is the version the writer read, and on an existing page it
    is REQUIRED: a write with no base is a writer claiming the page has not
    moved without ever having looked, and the loser of two of those silently
    erases the winner. A mismatch is a `WikiConflictError` carrying what is
    there now, so the caller can merge instead of guessing.

    A slug nobody has written yet is a CREATE rather than a 404 — a wanted page
    is an invitation (docs/design/21), and the write that accepts it is this
    one. It needs no base for the same reason `append` never does: there is no
    version to have read."""
    _require_actor(actor, run)
    slug = _check_slug(slug)
    page = await _lock_page(session, slug)
    if page is None:
        if base_version is not None:
            raise WikiRuleError(f"there is no page called {slug} to write over")
        return await create_page(session, producer, actor=actor, slug=slug,
                                 title=title or _title_from(slug), body=body,
                                 tags=tags, reason=reason, run=run,
                                 url_base=url_base, budget_limit=budget_limit,
                                 max_body_bytes=max_body_bytes)
    if base_version is None:
        raise WikiRuleError(f"a write to {slug} must name the base_version it read")
    if base_version != page.version:
        raise WikiConflictError(slug, page.version, page.summary or "")
    return await _apply(session, producer, page, actor=actor, body=body,
                        title=title, tags=tags, reason=reason, run=run,
                        event="edited", url_base=url_base,
                        budget_limit=budget_limit, max_body_bytes=max_body_bytes)


async def append_page(session, producer, slug: str, *, actor: str, body: str,
                      reason: str = "", run=None, url_base: str = "",
                      budget_limit: int | None = None,
                      max_body_bytes: int | None = None) -> WikiPage:
    """Add a section to the end of a page, creating it when it is not there.

    The shape an agent should prefer (docs/design/21): it never conflicts,
    because appending is the one edit whose meaning does not depend on what the
    rest of the page currently says. Two agents appending at once both land,
    one after the other, under the row lock — the lock is what makes the
    read-modify-write of the body a single decision rather than a lost update."""
    _require_actor(actor, run)
    slug = _check_slug(slug)
    page = await _lock_page(session, slug)
    if page is None:
        return await create_page(session, producer, actor=actor, slug=slug,
                                 title=_title_from(slug), body=body,
                                 reason=reason, run=run, url_base=url_base,
                                 budget_limit=budget_limit,
                                 max_body_bytes=max_body_bytes)
    added = (body or "").strip()
    whole = f"{(page.body or '').rstrip()}\n\n{added}" if page.body else added
    return await _apply(session, producer, page, actor=actor, body=whole,
                        reason=reason, run=run, event="appended",
                        url_base=url_base, budget_limit=budget_limit,
                        max_body_bytes=max_body_bytes)


async def archive_page(session, producer, slug: str, *, actor: str,
                       reason: str = "archived", run=None,
                       url_base: str = "") -> WikiPage:
    """Take a page out of circulation: out of search, out of the prompt block,
    out of backlinks and the wanted list — and keep every word of it.

    No version row, because nothing was written: archiving is a fact about the
    page, not about its text, and a history entry whose body is identical to
    the one before it is a diff nobody can read. The card still goes up, so the
    room sees a page leave the same way it saw it arrive.

    Archiving an archived page is refused (under the row lock — see
    `_set_archived`): it is not a no-op, it is a second announcement of a
    departure that already happened."""
    return await _set_archived(session, producer, slug, at=utcnow(), actor=actor,
                               reason=reason, run=run, url_base=url_base,
                               event="archived")


async def restore_page(session, producer, slug: str, *, actor: str,
                       version: int | None = None, reason: str | None = None,
                       run=None, url_base: str = "",
                       budget_limit: int | None = None) -> WikiPage:
    """Bring a page back: from the archive, or from one of its own versions.

    With no `version` this is purely the un-archive, and it makes NO new
    version for the reason archiving made none — the text did not move.

    With one it is a roll-back, and that IS a write: a new version whose body
    and title are the old one's, so the history stays append-only and the
    version somebody rolled back FROM is still there to read. Both halves
    happen in one call when a page is archived and rolled back at once, because
    that is one intention and refusing it would only make the caller do it
    twice in the wrong order."""
    _require_actor(actor, run)
    slug = _check_slug(slug)
    page = await _lock_page(session, slug)
    if page is None:
        raise WikiRuleError(f"there is no page called {slug}")
    if version is None:
        if page.archived_at is None:
            raise WikiRuleError(f"{slug} is not archived")
        return await _set_archived(session, producer, slug, at=None, actor=actor,
                                   reason=reason or "restored", run=run,
                                   url_base=url_base, event="restored")
    old = await _version_of(session, page, version)
    if old is None:
        raise WikiRuleError(f"{slug} has no v{version}")
    # Cleared BEFORE the write rather than after it: `_apply` refuses an
    # archived page, and this is the one door that is allowed to bring one
    # back — rolling a page back is how you un-archive it to the version it
    # should have stayed at.
    page.archived_at = None
    return await _apply(session, producer, page, actor=actor, body=old.body,
                        title=old.title, reason=reason or f"restored v{version}",
                        run=run, event="restored", url_base=url_base,
                        budget_limit=budget_limit)


async def promote_memory(session, producer, memory: dict, *, actor: str, slug: str,
                         title: str, run=None, url_base: str = "",
                         budget_limit: int | None = None) -> WikiPage:
    """Harden one agent's private note into a page everybody can cite.

    The caller fetched the memory and checked that it belongs to whoever is
    promoting it — the memory tool owns `tool_memory` and the wiki must not
    reach into it (docs/design/21), so `memory` arrives here as a plain
    `{id, agent, key, content}` dict.

    The memory itself is left exactly where it is. Promotion is not a move: the
    agent keeps its note, the platform gains a fact, and `source_memory_id` is
    the thread between them — which is also what lets a second promotion of the
    SAME memory rewrite the page it already made. A slug that belongs to some
    other page is taken, because silently overwriting somebody else's words is
    the one thing a provenance line cannot undo.

    And a re-promotion is only a rewrite while the page is still nothing BUT
    the memory. Once anybody has edited it, the page has become the shared
    version of the fact and the memory is just where it started: promoting
    again would overwrite that work with a note the editor has already
    improved on, silently and without a base_version anyone could have named.
    So it conflicts instead, and says to edit the page. The newest version's
    reason is the whole test — a promotion always writes `PROMOTE_REASON`, and
    a version that does not carry it is somebody's edit."""
    _require_actor(actor, run)
    slug = _check_slug(slug)
    body = promoted_body(memory.get("content"), memory.get("agent") or "",
                         memory.get("key"), utcnow())
    tags = ["memory", memory.get("agent") or ""]
    page = await _lock_page(session, slug)
    if page is None:
        return await create_page(session, producer, actor=actor, slug=slug,
                                 title=title, body=body, tags=tags,
                                 reason=PROMOTE_REASON, run=run,
                                 url_base=url_base, budget_limit=budget_limit,
                                 source_memory_id=memory.get("id"),
                                 event="promoted")
    if page.source_memory_id != memory.get("id"):
        raise WikiExistsError(f"a page called {slug} already exists")
    latest = await _version_of(session, page, page.version)
    if latest is None or (latest.reason or "") != PROMOTE_REASON:
        raise WikiConflictError(
            slug, page.version, page.summary or "",
            detail=f"{slug} has been edited since it was promoted; edit the page "
                   f"directly rather than re-promoting over somebody's work")
    return await _apply(session, producer, page, actor=actor, body=body,
                        title=title, tags=tags, reason=PROMOTE_REASON,
                        run=run, event="promoted", url_base=url_base,
                        budget_limit=budget_limit)


async def history(session, page, limit: int) -> list[dict]:
    """A page's versions, newest first, each with what it changed.

    The counts are a fact about two versions, so the window is read one row
    DEEPER than it is returned: without the version before the oldest one
    shown, the bottom row of every history page would claim to have added the
    whole file. v1 is the exception and it is honest — before v1 there was
    nothing."""
    limit = max(0, limit)
    rows = list((await session.execute(select(WikiVersion).where(
        WikiVersion.page_id == page.id).order_by(WikiVersion.version.desc())
        .limit(limit + 1))).scalars())
    out = []
    for i, row in enumerate(rows[:limit]):
        previous = rows[i + 1].body if i + 1 < len(rows) else ""
        added, removed = line_counts(previous, row.body)
        out.append({**version_view(row), "added": added, "removed": removed})
    return out


async def backlinks(session, slug: str) -> list[WikiPage]:
    """The live pages pointing at `slug`, by title. Rows rather than views:
    a backlink list is a sidebar of titles, and which columns it shows is the
    reader's question, not this module's.

    Archived pages are not backlinks. Archiving is how a page stops counting —
    if it still pulled what it links to out of the wanted list and onto other
    pages' sidebars, the archive would be a page that keeps voting."""
    return list((await session.execute(
        select(WikiPage).join(WikiLink, WikiLink.from_page_id == WikiPage.id)
        .where(WikiLink.to_slug == slug, WikiPage.archived_at.is_(None))
        .order_by(WikiPage.title))).scalars())


async def wanted(session) -> list[dict]:
    """The red links: `[[slug]]`s that no live page answers to, most-asked
    first. This is the wiki's to-do list, and it is a feature rather than a
    broken-link report — a link to a page nobody has written is an invitation
    (docs/design/21), and the number of pages that wanted it is how the next
    writer picks which one to accept.

    A page in the archive does not answer to its slug, so archiving one puts
    every link to it back on this list — which is exactly the ask: something
    that was relied on is now missing."""
    live = set((await session.execute(select(WikiPage.slug).where(
        WikiPage.archived_at.is_(None)))).scalars())
    rows = (await session.execute(
        select(WikiLink.to_slug, WikiPage.slug)
        .join(WikiPage, WikiPage.id == WikiLink.from_page_id)
        .where(WikiPage.archived_at.is_(None)))).all()
    by_slug: dict[str, list[str]] = {}
    for to_slug, from_slug in rows:
        if to_slug not in live:
            by_slug.setdefault(to_slug, []).append(from_slug)
    # Ties broken by slug so two identical wikis produce the same list: this is
    # rendered as an ordered page, and an order the planner chose would shuffle
    # under the reader between refreshes.
    return [{"slug": slug, "linked_from": sorted(froms)}
            for slug, froms in sorted(by_slug.items(),
                                      key=lambda kv: (-len(kv[1]), kv[0]))]


async def search_for_prompt(session, text: str, *, limit: int) -> list[WikiPage]:
    """The live pages `text` is about, best match first — what the `<wiki>`
    block in a run's prompt is built from (docs/design/21).

    ONE query, always, and never the whole wiki: this runs on every summons in
    every room, so a wiki with a thousand pages must cost the same as one with
    ten. Archived pages are excluded for the reason they are excluded from the
    backlinks and the wanted list — archiving is how a page stops counting, and
    one that kept turning up in every prompt would be knowledge nobody can take
    back.

    Two dialects, deliberately not one, and they are the same search twice:
    the SQL finds the pages whose TITLE OR BODY answers, and the ranking then
    reads the TAGS too — a page filed under the word the room used is a better
    answer than one that merely contains it. Postgres does both in the query
    (the match against the GIN tsvector `_ensure_wiki_ddl` builds, the rank
    against a vector that also carries the tags); sqlite, which has no
    equivalent, narrows with LIKE the way the ticket board does and counts the
    overlap in Python. The two agree on what matches; they do not promise the
    same order, and nothing downstream depends on that."""
    words = _search_words(text)
    if limit <= 0 or not words:
        return []
    live = select(WikiPage).where(WikiPage.archived_at.is_(None))
    if session.get_bind().dialect.name == "postgresql":
        return list((await session.execute(
            _pg_search(live, words, limit))).scalars())
    candidates = (await session.execute(live.where(or_(*[
        clause for word in words
        for clause in (WikiPage.title.ilike(f"%{word}%"),
                       WikiPage.body.ilike(f"%{word}%"))]))
        .order_by(WikiPage.updated_at.desc(), WikiPage.slug)
        .limit(SEARCH_CANDIDATES_MAX))).scalars()
    scored = []
    for page in candidates:
        haystack = " ".join([page.title or "", *(page.tags or []),
                             page.body or ""]).lower()
        score = sum(1 for word in words if word in haystack)
        if score:
            # Ties broken by slug, not by insertion order: the prompt is pinned
            # by golden tests, and a block that shuffled between identical runs
            # would make every one of them a coin toss.
            scored.append((-score, page.slug, page))
    return [page for _, _, page in sorted(scored, key=lambda row: row[:2])[:limit]]


def _pg_search(live, words: list[str], limit: int):
    """The postgres half of `search_for_prompt`, as a statement, so a test can
    compile it without a postgres to run it against.

    Two vectors, on purpose. The MATCH is `_ensure_wiki_ddl`'s indexed
    expression VERBATIM — change one character of it and every summons in every
    room starts sequentially scanning the wiki. The RANK is free to be richer
    because it is only ever computed over the handful of rows the index already
    returned, so that is where the tags go: `::text` on the JSON array is
    enough, since to_tsvector splits on the brackets and the quotes and leaves
    the tags themselves standing as words."""
    query = func.plainto_tsquery("english", " ".join(words))
    indexed = func.to_tsvector("english", WikiPage.title + " " + WikiPage.body)
    ranked = func.to_tsvector("english", WikiPage.title + " "
                              + cast(WikiPage.tags, Text) + " " + WikiPage.body)
    # Ties broken by slug for the reason the other branch breaks them there.
    return (live.where(indexed.op("@@")(query))
            .order_by(func.ts_rank(ranked, query).desc(), WikiPage.slug)
            .limit(limit))


def _search_words(text: str) -> list[str]:
    """The words worth matching on, from whatever the room said: lowercased,
    four characters or more, deduped, and capped.

    Short words are dropped because they are noise — "the" matches half the
    wiki — and the cap is what keeps a pasted stack trace from turning into a
    hundred-clause OR. Alphanumerics only, which is also why no ILIKE escaping
    is needed below: `%` and `_` cannot survive the split."""
    seen: list[str] = []
    for word in re.findall(r"[a-z0-9]+", (text or "").lower()):
        if len(word) >= 4 and word not in seen:
            seen.append(word)
    return seen[:SEARCH_WORDS_MAX]


async def agent_write_budget_left(session, agent: str, limit: int, now) -> int:
    """How many more page writes `agent` may make this hour. Read-only: this is
    what the API reports and what a tool error quotes back. The refusal itself
    is the write's, under the room lock, because a count taken out here is
    already stale by the time the caller acts on it.

    Counted from `wiki_versions`, which is the only count that survives the
    page: an agent that wrote forty pages and archived them all has still
    written forty times, and the cap is about how hard it has been hammering
    the wiki."""
    return max(0, limit - await _writes_since(session, AGENT_PREFIX + agent, now))


async def say_budget_once(session, producer, conv, limit: int, now) -> RelayMessage | None:
    """Tell `#wiki` that an agent has run out of write budget — at most once an
    hour, and None when it has already been said.

    Same shape as the router's own budget notice and Tickets', and for the same
    reason: an agent over budget is an agent retrying, so a notice per refusal
    would BE the flood the cap exists to stop. Matched on `kind == "system"`
    and not on the text alone, because a message quoting the notice is somebody
    talking about the pause, not the platform declaring it."""
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


# --- the parts every write shares --------------------------------------------


async def _apply(session, producer, page, *, actor: str, body: str, event: str,
                 title: str | None = None, tags=None, reason: str = "", run=None,
                 url_base: str = "", budget_limit: int | None = None,
                 max_body_bytes: int | None = None) -> WikiPage:
    """One write to a page that already exists: the caller has decided WHAT the
    new body is and has already taken the row lock; everything after that is
    the same for an edit, an append, a roll-back and a promotion."""
    if page.archived_at is not None:
        raise WikiRuleError(f"{page.slug} is archived; restore it first")
    reason = _check_reason(reason)
    _check_body(body, max_body_bytes)
    new_title = page.title if title is None else _check_title(title)
    new_tags = page.tags if tags is None else _check_tags(tags)
    conv = await _lock_room(session)
    await _check_budget(session, actor, budget_limit)
    # Nothing above this line has touched the row: every refusal — a cap, the
    # budget — has to leave the caller's session as clean as it found it, since
    # a caller that catches WikiRuleError and carries on would otherwise flush
    # a half-written page on its next commit.
    counts = line_counts(page.body or "", body or "")
    page.title, page.tags = new_title, new_tags
    page.body, page.summary = body or "", summary_of(body)
    page.updated_by, page.version = actor, (page.version or 0) + 1
    version = await _add_version(session, page, actor=actor, run=run, reason=reason)
    await _rewrite_links(session, page)
    return await _finish(session, producer, conv, page, version, event=event,
                         counts=counts, actor=actor, run=run, url_base=url_base)


async def _set_archived(session, producer, slug: str, *, at, actor: str,
                        reason: str, run=None, url_base: str = "",
                        event: str) -> WikiPage:
    """Archive or un-archive, which are the same write in both directions: one
    column, no new version, and a card so the room hears about it."""
    _require_actor(actor, run)
    reason = _check_reason(reason)
    page = await _lock_page(session, _check_slug(slug))
    if page is None:
        raise WikiRuleError(f"there is no page called {slug}")
    # Asked HERE, under the row lock, and not by the caller: two DELETEs that
    # both read a live page both pass any check made before this point, and the
    # loser would archive an archived page — a second card in the room and a
    # second event for a change that did not happen. The lock is what makes
    # read-check-write one decision, exactly as it is for a body.
    if at is not None and page.archived_at is not None:
        raise WikiRuleError(f"{page.slug} is already archived")
    if at is None and page.archived_at is None:
        raise WikiRuleError(f"{page.slug} is not archived")
    conv = await _lock_room(session)
    page.archived_at, page.updated_by = at, actor
    return await _finish(session, producer, conv, page,
                         _card_stand_in(page, actor, reason),
                         event=event, counts=(0, 0), actor=actor, run=run,
                         url_base=url_base)


def _card_stand_in(page, actor: str, reason: str) -> SimpleNamespace:
    """What a card is made from when no version row exists. Archiving writes no
    history (see `archive_page`), but the room still gets the same card as every
    other change, and `card_for`/`card_body` read exactly these three fields —
    so a detached stand-in says what happened without putting a version in the
    history that nobody could diff."""
    return SimpleNamespace(version=page.version, author=actor, reason=reason)


def _for_update(session, stmt):
    """`FOR UPDATE` where it means something. Postgres is where two writers
    actually race; sqlite is single-writer and its compiler drops the clause
    entirely, so it is asked for by dialect the way `api/relay.py` does."""
    return (stmt.with_for_update()
            if session.get_bind().dialect.name == "postgresql" else stmt)


async def _page_by_slug(session, slug: str) -> WikiPage | None:
    return (await session.execute(select(WikiPage).where(
        WikiPage.slug == slug))).scalars().first()


async def _lock_page(session, slug: str) -> WikiPage | None:
    """Re-read a page under a row lock before changing it, or None when there
    is none to change.

    Without it, two writers who both read v2 both compute v3 from the body they
    read, and the second one to land erases the first — the base_version check
    would pass for both, because both took it before either wrote. The lock is
    what makes read-check-write one decision; the unique (page_id, version) is
    the backstop that turns a missed lock into an error instead of a silent
    loss."""
    return (await session.execute(_for_update(
        session, select(WikiPage).where(WikiPage.slug == slug))
        .execution_options(populate_existing=True))).scalars().first()


async def _lock_room(session) -> Conversation | None:
    """`#wiki`, locked — the serialisation point for the hourly write budget,
    and None when the room is gone or archived.

    It is this row and not the page's because the budget is per AGENT and not
    per page: an agent rewriting thirty different pages at once would otherwise
    take thirty different locks and pass one count thirty times. Every write
    already contends here anyway — posting the card bumps the room's
    `updated_at` — so taking it up front costs nothing.

    A write that changes an existing page holds that page's lock by the time it
    gets here, and a create holds nothing else: no transaction ever waits for a
    page while holding the room, so the two orders cannot close a cycle.

    A missing room therefore costs a write its card AND makes its budget
    advisory rather than exact. That is the right trade: the wiki is the record
    and the room is the audience, and losing the audience must not lose the
    record."""
    conv = await channel_by_name(session, WIKI_CHANNEL)
    if conv is None:
        return None
    return (await session.execute(_for_update(
        session, select(Conversation).where(Conversation.id == conv.id))
        .execution_options(populate_existing=True))).scalars().one()


async def _version_of(session, page, version: int) -> WikiVersion | None:
    return (await session.execute(select(WikiVersion).where(
        WikiVersion.page_id == page.id,
        WikiVersion.version == version))).scalars().first()


async def _add_version(session, page, *, actor: str, run, reason: str) -> WikiVersion:
    version = WikiVersion(page_id=page.id, version=page.version, title=page.title,
                          body=page.body or "", author=actor,
                          run_id=run.id if run is not None else None,
                          reason=reason)
    session.add(version)
    await session.flush()
    return version


async def _rewrite_links(session, page) -> None:
    """The page's `[[slug]]` rows, from scratch. Deleted and re-inserted rather
    than diffed because the body is the only truth about what a page links to:
    a link that was removed has to STOP being a backlink, and a diff that
    missed one would leave a sidebar pointing at a paragraph that no longer
    exists."""
    await session.execute(delete(WikiLink).where(WikiLink.from_page_id == page.id))
    for target in find_links(page.body or ""):
        session.add(WikiLink(from_page_id=page.id, to_slug=target))
    await session.flush()


async def _writes_since(session, actor: str, now) -> int:
    return (await session.execute(select(func.count()).select_from(WikiVersion).where(
        WikiVersion.author == actor,
        WikiVersion.created_at >= now - timedelta(hours=1)))).scalar() or 0


async def _check_budget(session, actor: str, limit: int | None) -> None:
    """The hourly write cap, for agents only. A person editing forty pages in
    an hour is a working afternoon; an agent doing it is usually a loop, and
    the wiki is the one store where a loop rewrites what everybody else
    believes."""
    if limit is None or not is_agent(actor):
        return
    used = await _writes_since(session, actor, utcnow())
    if used >= limit:
        raise WikiBudgetError(
            f"{participant_label(actor)} has written {used} wiki versions in the "
            f"last hour (limit {limit}); try again later")


def _check_slug(slug) -> str:
    """The slug as given, or a refusal. Deliberately NOT normalised: the doors
    slugify (the API from a title, the tool from a memory key) and this is the
    gate they hand the answer to. A store that lower-cased would make
    `Deploying` and `deploying` one page through one door and two through
    another, and the mismatch would only show up as a link nobody can follow."""
    if not SLUG_RE.fullmatch(str(slug or "")):
        raise WikiRuleError(f"{slug!r} is not a wiki slug")
    return str(slug)


def _check_title(title) -> str:
    text = str(title or "").strip()
    if not text:
        raise WikiRuleError("a page needs a title")
    if len(text) > TITLE_LIMIT:
        raise WikiRuleError(f"a title is at most {TITLE_LIMIT} characters")
    return text


def _check_reason(reason) -> str:
    text = str(reason or "").strip()
    if len(text) > REASON_LIMIT:
        raise WikiRuleError(f"a reason is at most {REASON_LIMIT} characters")
    return text


def _check_tags(tags) -> list[str]:
    out: list[str] = []
    for tag in tags or []:
        text = str(tag or "").strip()
        if not text:
            continue
        if len(text) > TAG_LIMIT:
            raise WikiRuleError(f"a tag is at most {TAG_LIMIT} characters")
        if text not in out:
            out.append(text)
    if len(out) > TAGS_MAX:
        raise WikiRuleError(f"a page carries at most {TAGS_MAX} tags")
    return out


def _check_body(body, max_body_bytes: int | None) -> None:
    """The body cap, in BYTES and not characters: the column is text and the
    limit is about what the history costs, so a page of emoji must not be four
    times the page a cap in characters would have allowed. Falls back to the
    configured cap, which is the one every door should be using anyway — a
    caller that passes its own is a test or an app with a smaller idea of big."""
    limit = (get_settings().wiki_max_body_bytes if max_body_bytes is None
             else max_body_bytes)
    size = len(str(body or "").encode("utf-8"))
    if size > limit:
        raise WikiRuleError(f"a page body is at most {limit} bytes (this one is {size})")


def _title_from(slug: str) -> str:
    """The title a page gets when it is created by a write that named only its
    slug — an `append` to a wanted page, usually. Lossy and obviously so: the
    slug is what somebody linked to, and a human title can be set on the next
    edit, which is better than the page being born titled `deploying-the-nuc`."""
    return slug.replace("-", " ").title()[:TITLE_LIMIT]


async def _finish(session, producer, conv, page, version, *, event: str,
                  counts: tuple[int, int], actor: str, run, url_base: str) -> WikiPage:
    """The card, the single commit, then everyone is told.

    The card is posted BEFORE the commit so that the page, its version, its
    links and the room's copy of the news land in one transaction: a commit in
    between would let a body change durably while the card explaining it rolled
    back, and the room would be missing an edit nobody can now find. After the
    commit the row is the record, so a broker that is down costs a write its
    live update and never the write itself — the ordering Relay has used since
    design-19.

    A missing `#wiki` skips the card and is logged, not raised: the room is the
    audience, not the record."""
    added, removed = counts
    msg = None
    if conv is not None:
        msg = await post_relay_message(
            session, conv, author=actor, kind="event",
            body=card_body(page, version, added=added, removed=removed),
            card=card_for(page, version, url_base=url_base, added=added,
                          removed=removed),
            mentions=[], run_id=run.id if run is not None else None,
            hop=actor_hop(run))
    else:
        log.warning("no live #%s channel; %s v%s got no card", WIKI_CHANNEL,
                    page.slug, version.version)
    await session.commit()
    if msg is not None:
        await publish_relay_message(
            producer, conv, msg,
            outbound=await outbound_for_message(session, conv, msg))
    if producer is not None:
        try:
            await producer.publish(TOPIC_WIKI_EVENTS, page.id, {
                "event": event, "page": page_view(page),
                "version": version.version, "author": actor,
                "run_id": run.id if run is not None else None,
                "reason": version.reason or "", "added": added, "removed": removed,
            }, type="wiki.event")
        except Exception:
            log.warning("wiki.events publish failed for %s %s", page.slug, event,
                        exc_info=True)
    return page
