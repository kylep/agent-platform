"""Relay's REST surface (docs/design/19): channels, messages, DMs, reactions,
search, presence and stats.

Authorship is the invariant the whole messenger rests on. There is no author
field on the wire: a message is attributed from the caller's token —
`agent:<name>` for a per-run agent token, `user:<principal>` for a human — so
an agent can only ever speak as itself, and a prompt-injected one gains nothing
by asking to be someone else. Membership (`relay.is_member`) is the second
fence: it bounds where an agent may read and post, independent of role."""
import asyncio
import json
import logging
import re
from datetime import timedelta
from typing import NamedTuple

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import Text, case, cast, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased

from agentplatform.api.auth import (ANNOTATE_ROLES, INVOKE_ROLES, READ_ROLES,
                                    authenticate, require_role, role_allows)
from agentplatform.conversation import continue_conversation
from agentplatform.db import (ACTIVE_STATES, Conversation, RELAY_SEED_CHANNELS,
                              RelayInvocation, dm_key_of)
from agentplatform.db import RelayBinding as BindingRow
from agentplatform.db import RelayMessage as MessageRow
from agentplatform.db import RelayParticipant as ParticipantRow
from agentplatform.db import RelayReaction as ReactionRow
from agentplatform.db import Run, Ticket, utcnow
from agentplatform.relay import (agent_name, is_agent, is_member, is_participant,
                                 mentionable_in, parse_mentions, participant_of,
                                 room_dispatch_mode, room_home, room_reply_mode,
                                 strip_room_mentions)
# Aliased: the route below is the HTTP name for the same act, and the store
# helper is what actually writes the row.
from agentplatform.relay_feed import OVERFLOW
from agentplatform.relay_store import post_relay_message as _insert_message
from agentplatform.relay_store import (bindings_of, channel_by_ref, faces_for,
                                       message_view, outbound_for_message,
                                       relay_message_payload,
                                       publish_relay_message)
from agentplatform.tickets import KEY_RE, derive_prefix, one_line

log = logging.getLogger("relay")

from agentplatform.api import schemas as S
router = APIRouter()

# T5 mints this role for relay-only agents. Named here already so that adding
# it to `auth.ROLES` is the whole change — these routes need no edit.
RELAY_ROLE = "relay"
READ = (*READ_ROLES, RELAY_ROLE)
WRITE = (*INVOKE_ROLES, RELAY_ROLE)
# The roles a per-run AGENT token may carry INTO Relay — deliberately not the
# route's human tuple. `tools` reaches nothing but /api/whoami by design,
# `session` exists only to move a resume blob, and `reader` is a human scope:
# none of them earns a voice in a room merely by naming an agent. T5 mints
# `relay` for a participant grant and `annotator` for core API tools; the broker
# checks the exact frozen tool grant besides.
AGENT_ROLES = ("relay", "annotator", "operator", "admin")

_NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
def _is_ticket_prefix(prefix: str) -> bool:
    """Whether a channel may stamp its keys with this (docs/design/20).

    Asked by building a key out of it rather than by a second regex: the
    prefix is valid exactly when `KEY_RE` — the one definition of what a key
    looks like — accepts what it produces. A prefix nothing can match is a
    channel owning tickets no message can ever refer to by name."""
    return KEY_RE.fullmatch(f"{prefix}-1") is not None
SEED_NAMES = {name for name, _ in RELAY_SEED_CHANNELS}
# Preview length of the rail's last-message line.
PREVIEW_CHARS = 200
# How long a silent stream waits before saying something anyway. Proxies and
# browsers both close an idle connection, and a comment costs one line.
HEARTBEAT_SECONDS = 15.0
# The replay cap when a reconnecting client names a message it already has: a
# client that was away for an hour catches up by re-fetching the page, not by
# having the whole backlog pushed at it.
REPLAY_LIMIT = 200

# Why the router refused a mention, exactly as `relay_router` records it. The
# split matters because only three of them mean a mention went unanswered: a
# COALESCED wake is the guard working (three mentions became one run) and
# FACADE_OWNS_TURN is a DM being answered by the other door. Counting those as
# trouble is how a perfectly healthy room ends up reported as a problem, so the
# headline `suppressed_24h` is the refusals and the breakdown carries the rest.
REFUSED_REASONS = ("hop_limit", "budget", "not_member")
ROUTINE_REASONS = ("coalesced", "facade_owns_turn")
SUPPRESSION_REASONS = REFUSED_REASONS + ROUTINE_REASONS


class Caller(NamedTuple):
    participant: str
    agent: str | None
    principal: str


def _participant(**identity) -> str:
    try:
        return participant_of(**identity)
    except ValueError as e:
        raise HTTPException(403, f"unusable identity: {e}")


def require_relay_access(*roles: str, agents: bool = True):
    """`require_role`, plus the agent seam, resolved to a `Caller`.

    A per-run agent token is judged against `AGENT_ROLES` rather than the
    route's human tuple: the role it will carry (`relay`) does not exist until
    T5, and inside Relay it is MEMBERSHIP, not the role, that bounds where an
    agent may speak. `agents=False` marks the routes that administer a room —
    those stay human, so an agent cannot rename or archive a channel it happens
    to be in."""
    async def dep(request: Request) -> Caller:
        ident = await authenticate(request)
        if ident is None:
            raise HTTPException(401)
        name, role = ident
        agent = getattr(request.state, "api_key_agent", None)
        if agent is not None:
            if not agents or not role_allows(role, AGENT_ROLES):
                raise HTTPException(403)
            return Caller(_participant(agent=agent), agent, name)
        if not role_allows(role, roles):
            raise HTTPException(403)
        return Caller(_participant(principal=name), None, name)
    return dep


def _iso(dt) -> str | None:
    return dt.isoformat() if dt else None


def _agent_set(request: Request) -> set[str]:
    """Enabled, non-quarantined agents: the set a mention may resolve to, and
    the set that is implicitly a member of every open channel. Read off the
    store's TTL cache — a few seconds of staleness costs a mention at worst,
    where reloading would put a definitions query in front of every message in
    the room. The one path that reloads is the one that 404s on a miss."""
    return {i.name for i in request.app.state.agent_store.list()
            if i.enabled and i.error is None}


async def _explicit(s, channel_id: str) -> set[str]:
    return set((await s.execute(select(ParticipantRow.participant).where(
        ParticipantRow.channel_id == channel_id))).scalars())


async def _explicit_many(s, ids: list[str]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {i: set() for i in ids}
    if not ids:
        return out
    for cid, participant in (await s.execute(select(
            ParticipantRow.channel_id, ParticipantRow.participant).where(
            ParticipantRow.channel_id.in_(ids)))).all():
        out[cid].add(participant)
    return out


# Faces are shared with the router (docs/design/19): the roster an agent is
# handed in its prompt and the one a human sees in the pane are the same room,
# so they are rendered by one function.
_faces = faces_for


async def _hop_of(s, run_id: str | None) -> int:
    """The hop a message posted by a RUN carries.

    This is the same sum the recorder makes for an agent's final answer
    (`trigger.hop + 1`), read off the run instead of the message: `Run.depth`
    IS the hop of the mention that summoned it. Without it every message an
    agent posts through the `relay` tool would land at hop 0 — a chain that
    never counts, so the router's hop cap never fires and two agents can
    address each other forever with only the hourly budget as a brake.

    A human's message is hop 0 by definition. A run we cannot find is still a
    run, so its message is at least one hop from the person who started it."""
    if not run_id:
        return 0
    run = await s.get(Run, run_id)
    return (run.depth or 0) + 1 if run is not None else 1


def _newer_than(row):
    """Strictly after `row` in the room's total order. (created_at, id) is that
    order everywhere — the page, the SSE replay, the wake's context window — so
    two messages written in the same tick still page deterministically."""
    return or_(MessageRow.created_at > row.created_at,
               (MessageRow.created_at == row.created_at) & (MessageRow.id > row.id))


def _older_than(row):
    return or_(MessageRow.created_at < row.created_at,
               (MessageRow.created_at == row.created_at) & (MessageRow.id < row.id))


def _face_of(author: str, faces: dict[str, dict]) -> dict | None:
    return faces.get(agent_name(author) or "") if is_agent(author) else None


def _agents_among(participants) -> set[str]:
    return {agent_name(p) for p in participants if is_agent(p)}


# The message view is shared with the recorder and the conversation facade, so
# every writer renders a message the same way; the private name stays because
# it is what this module's routes read as.
_message = message_view


def _channel(conv: Conversation, *, participants: set[str], last=None,
             count: int = 0, unread: int = 0) -> dict:
    return {"id": conv.id, "kind": conv.kind, "name": conv.name,
            "home": room_home(conv), "reply_mode": room_reply_mode(conv),
            "dispatch_mode": room_dispatch_mode(conv),
            "default_agent": conv.default_agent or conv.agent,
            "title": conv.title, "topic": conv.topic,
            "open": bool(conv.open), "archived_at": _iso(conv.archived_at),
            "agent": conv.agent, "ticket_prefix": conv.ticket_prefix,
            "team_id": conv.team_id, "project_id": conv.project_id,
            "participants": sorted(participants),
            "last_message": None if last is None else {
                "id": last.id, "author": last.author,
                "body": (last.body or "")[:PREVIEW_CHARS],
                "created_at": _iso(last.created_at)},
            "message_count": count, "unread": unread}


async def _detail(s, conv: Conversation) -> dict:
    participants = await _explicit(s, conv.id)
    names = _agents_among(participants) | ({conv.agent} if conv.agent else set())
    view = _channel(conv, participants=participants)
    view["faces"] = await _faces(s, names)
    view["bindings"] = [_binding(b) for b in await bindings_of(s, conv.id)]
    # What the people from other networks are CALLED. Only they have one: an
    # agent and a principal are named by their participant string, and a client
    # that had to guess which half of `discord:415…` to show would show the
    # number.
    view["display_names"] = dict((await s.execute(select(
        ParticipantRow.participant, ParticipantRow.display_name).where(
        ParticipantRow.channel_id == conv.id,
        ParticipantRow.display_name.is_not(None)))).all())
    return view


async def _visible(s, caller: Caller, agents: set[str]) -> tuple[list, dict]:
    """The channels this caller may see — everything unarchived for a human,
    only the rooms it belongs to for an agent — and their membership rows,
    which the caller needs anyway and must not have to query twice."""
    rows = list((await s.execute(select(Conversation).where(
        Conversation.archived_at.is_(None)))).scalars())
    explicit = await _explicit_many(s, [c.id for c in rows])
    if caller.agent is not None:
        rows = [c for c in rows
                if is_member(c, caller.participant, agents, explicit[c.id])]
    return rows, explicit


async def _last_messages(s, ids: list[str]) -> dict:
    if not ids:
        return {}
    rank = func.row_number().over(
        partition_by=MessageRow.channel_id,
        order_by=(MessageRow.created_at.desc(), MessageRow.id.desc())).label("rank")
    newest = select(MessageRow, rank).where(
        MessageRow.channel_id.in_(ids), MessageRow.deleted_at.is_(None)).subquery()
    rows = (await s.execute(select(newest).where(newest.c.rank == 1))).all()
    return {r.channel_id: r for r in rows}


async def _counts(s, ids: list[str]) -> dict[str, int]:
    if not ids:
        return {}
    return dict((await s.execute(select(MessageRow.channel_id, func.count()).where(
        MessageRow.channel_id.in_(ids), MessageRow.deleted_at.is_(None))
        .group_by(MessageRow.channel_id))).all())


async def _unread(s, participant: str, ids: list[str]) -> dict[str, int]:
    """Messages newer than the caller's own last one, per channel. A caller who
    has never spoken here drops out (the correlated max is NULL, so nothing
    compares greater) rather than counting the room's whole history as unread."""
    if not ids:
        return {}
    mine = aliased(MessageRow)
    newest_mine = (select(func.max(mine.created_at)).where(
        mine.channel_id == MessageRow.channel_id, mine.author == participant,
        mine.deleted_at.is_(None)).scalar_subquery())
    return dict((await s.execute(select(MessageRow.channel_id, func.count()).where(
        MessageRow.channel_id.in_(ids), MessageRow.deleted_at.is_(None),
        MessageRow.created_at > newest_mine).group_by(MessageRow.channel_id))).all())


def _order(rows: list[Conversation], last: dict) -> list[Conversation]:
    """Named channels first, alphabetically — they are the furniture of the
    place. Private rooms follow by last activity, which is what a rail full of
    DMs actually needs."""
    def activity(c):
        row = last.get(c.id)
        return row.created_at if row is not None else c.updated_at
    named = sorted((c for c in rows if c.kind == "channel"), key=lambda c: c.name or "")
    private = sorted((c for c in rows if c.kind != "channel"), key=activity, reverse=True)
    return named + private


async def _channel_or_404(s, channel_id: str, caller: Caller,
                          agents: set[str]) -> Conversation:
    """A channel the caller may reach. A human reads any room (READ_ROLES is
    the operator's view of the platform); an agent only its own."""
    conv = await s.get(Conversation, channel_id)
    if conv is None:
        raise HTTPException(404, "unknown channel")
    if caller.agent is not None and not is_member(
            conv, caller.participant, agents, await _explicit(s, conv.id)):
        raise HTTPException(403, "not a member of this channel")
    return conv


async def _name_taken(s, name: str, *, besides: str | None = None) -> bool:
    stmt = select(Conversation.id).where(Conversation.kind == "channel",
                                         Conversation.name == name)
    if besides:
        stmt = stmt.where(Conversation.id != besides)
    return (await s.execute(stmt)).first() is not None


def _patch_conflict(body: S.RelayChannelPatch) -> str:
    """Which uniqueness a lost race hit. Only a field this patch actually set
    can have collided, and a prefix is the one the caller will not guess at
    from a message about a name."""
    if body.ticket_prefix is not None:
        return f"{body.ticket_prefix.strip().upper()} is another project's prefix"
    return f"#{body.name} already exists"


async def _taken_prefixes(s, *, besides: str | None = None) -> set[str]:
    stmt = select(Conversation.ticket_prefix).where(Conversation.ticket_prefix.isnot(None))
    if besides:
        stmt = stmt.where(Conversation.id != besides)
    return set((await s.execute(stmt)).scalars())


async def _set_ticket_prefix(s, conv: Conversation, prefix: str) -> None:
    """Make this channel a project, or rename the stem of its keys.

    Only until the first ticket: a key is the name people say and the one on
    every card, system row and cross-reference already written, so re-stemming
    a project that has issued keys would orphan all of them at once."""
    if conv.kind != "channel":
        raise HTTPException(422, "only a channel can be a project")
    prefix = prefix.strip().upper()
    if not _is_ticket_prefix(prefix):
        raise HTTPException(422, "a ticket prefix is 2-6 letters or digits, "
                                 "starting with a letter (OPS, PLAT)")
    if prefix == conv.ticket_prefix:
        return
    if (await s.execute(select(Ticket.id).where(
            Ticket.channel_id == conv.id).limit(1))).first() is not None:
        raise HTTPException(409, f"#{conv.name} has already issued keys under "
                                 f"{conv.ticket_prefix}")
    if prefix in await _taken_prefixes(s, besides=conv.id):
        raise HTTPException(409, f"{prefix} is another project's prefix")
    conv.ticket_prefix = prefix


def _slug(name: str | None) -> str:
    name = (name or "").strip()
    if not _NAME_RE.fullmatch(name):
        raise HTTPException(422, "a channel name must be a slug: lowercase letters, "
                                 "digits and dashes, starting with a letter or digit")
    return name


@router.get("/api/relay/channels", response_model=list[S.RelayChannel])
async def list_relay_channels(request: Request,
                              caller: Caller = Depends(require_relay_access(*READ))):
    agents = _agent_set(request)
    async with request.app.state.session_factory() as s:
        rows, participants = await _visible(s, caller, agents)
        ids = [c.id for c in rows]
        last = await _last_messages(s, ids)
        counts = await _counts(s, ids)
        unread = await _unread(s, caller.participant, ids)
    return [_channel(c, participants=participants[c.id], last=last.get(c.id),
                     count=counts.get(c.id, 0), unread=unread.get(c.id, 0))
            for c in _order(rows, last)]


@router.post("/api/relay/channels", status_code=201, response_model=S.RelayChannelDetail)
async def create_relay_channel(request: Request, body: S.RelayChannelIn,
                               caller: Caller = Depends(require_relay_access(*WRITE))):
    if body.kind not in ("channel", "group"):
        raise HTTPException(422, "kind must be channel or group")
    # An agent may convene a working group; the shared rooms of the place are
    # the operator's to name.
    if caller.agent is not None and body.kind != "group":
        raise HTTPException(403, "agents may only create groups")
    is_channel = body.kind == "channel"
    if body.open is not None and not is_channel:
        raise HTTPException(422, "only a channel can be open")
    name = _slug(body.name) if is_channel else None
    is_open = (body.open is None or body.open) if is_channel else False
    # An open channel carries NO membership rows — everyone is in it by
    # definition — so a participant list there would be rows nothing reads.
    if is_open and body.participants:
        raise HTTPException(422, "an open channel has no participants: "
                                 "every agent and every human is already a member")
    participants = set()
    for p in body.participants:
        if not is_participant(p):
            raise HTTPException(422, f"'{p[:64]}' is not a participant string")
        participants.add(p)
    if not is_open:
        # The creator has to be in the room, or it could not post in what it
        # just made.
        participants.add(caller.participant)
    async with request.app.state.session_factory() as s:
        if is_channel and await _name_taken(s, name):
            raise HTTPException(409, f"#{name} already exists")
        conv = Conversation(connector="web", agent=None, kind=body.kind, name=name,
                            home="relay", reply_mode="threaded",
                            dispatch_mode="mentions",
                            topic=body.topic.strip()[:256], open=is_open,
                            title=f"#{name}" if is_channel
                                  else (body.name or "group").strip()[:256])
        if is_channel:
            # A channel is a project by default (docs/design/20): the prefix is
            # derived from the name and stays editable until the first ticket,
            # so a room nobody files against simply never uses it. Groups and
            # DMs get none — they are conversations, not bodies of work.
            conv.ticket_prefix = derive_prefix(name, await _taken_prefixes(s))
        s.add(conv)
        await s.flush()
        for p in sorted(participants):
            s.add(ParticipantRow(channel_id=conv.id, participant=p,
                                 role="owner" if p == caller.participant else "member"))
        try:
            await s.commit()
        except IntegrityError:
            # The partial unique index is the real arbiter of a channel name;
            # the check above just makes the common case a clean 409.
            await s.rollback()
            raise HTTPException(409, f"#{name} already exists")
        return await _detail(s, conv)


@router.get("/api/relay/channels/{channel_id}", response_model=S.RelayChannelDetail)
async def get_relay_channel(request: Request, channel_id: str,
                            caller: Caller = Depends(require_relay_access(*READ))):
    agents = _agent_set(request)
    async with request.app.state.session_factory() as s:
        return await _detail(s, await _channel_or_404(s, channel_id, caller, agents))


@router.patch("/api/relay/channels/{channel_id}", response_model=S.RelayChannelDetail,
              dependencies=[Depends(require_relay_access(*INVOKE_ROLES, agents=False))])
async def patch_relay_channel(request: Request, channel_id: str, body: S.RelayChannelPatch):
    async with request.app.state.session_factory() as s:
        conv = await s.get(Conversation, channel_id)
        if conv is None:
            raise HTTPException(404, "unknown channel")
        if body.name is not None:
            if conv.kind != "channel":
                raise HTTPException(422, "only a channel has a name")
            name = _slug(body.name)
            if await _name_taken(s, name, besides=conv.id):
                raise HTTPException(409, f"#{name} already exists")
            conv.name, conv.title = name, f"#{name}"
        if body.topic is not None:
            conv.topic = body.topic.strip()[:256]
        if body.archived is not None:
            conv.archived_at = utcnow() if body.archived else None
        if body.reply_mode is not None:
            if conv.kind != "channel":
                raise HTTPException(422, "only a channel can change reply mode")
            conv.reply_mode = body.reply_mode
        if body.ticket_prefix is not None:
            await _set_ticket_prefix(s, conv, body.ticket_prefix)
        try:
            await s.commit()
        except IntegrityError:
            # The partial unique indexes are the real arbiters of a channel's
            # name and of its ticket prefix; the checks above only make the
            # common case a clean 409. Two patches choosing the same prefix
            # both pass those checks — the loser meets the index HERE, and an
            # uncaught one would be a 500 for what is plainly a conflict.
            await s.rollback()
            raise HTTPException(409, _patch_conflict(body))
        return await _detail(s, conv)


@router.delete("/api/relay/channels/{channel_id}", response_model=S.OkId,
               dependencies=[Depends(require_relay_access(*INVOKE_ROLES, agents=False))])
async def archive_relay_channel(request: Request, channel_id: str):
    """Archive, never delete: a channel's messages are the record of what the
    agents actually did, and a room that stops taking new messages still has to
    be readable."""
    async with request.app.state.session_factory() as s:
        conv = await s.get(Conversation, channel_id)
        if conv is None:
            raise HTTPException(404, "unknown channel")
        if conv.kind == "channel" and conv.name in SEED_NAMES:
            raise HTTPException(409, f"#{conv.name} is a platform channel")
        conv.archived_at = conv.archived_at or utcnow()
        await s.commit()
    return {"ok": True, "id": channel_id}


# --- bindings (docs/design/19) ------------------------------------------------
# A binding makes a room two-sided: the Discord channel behind it is the SAME
# room, and everything written here is mirrored there. That is a decision about
# who can read the room, so these routes are human-only — `agents=False` — even
# for the list: an agent that could bind a channel could choose its own
# audience, and one that could read the bindings would learn where the rooms it
# is in are being echoed.

CONNECTORS = ("discord", "slack", "telegram")


def _binding(row) -> dict:
    return {"id": row.id, "connector": row.connector,
            "external_ref": row.external_ref,
            "external_kind": row.external_kind or "channel",
            "parent_external_ref": row.parent_external_ref,
            "display_name": row.display_name or "",
            "external_url": row.external_url or "",
            "status": row.status or "active", "config": row.config or {}}


@router.get("/api/relay/bindings", response_model=list[S.RelayBindingRef],
            dependencies=[Depends(require_relay_access(*READ_ROLES, "connector",
                                                       agents=False))])
async def list_bindings_for_connector(request: Request,
                                      connector: str = Query(max_length=32)):
    """Every endpoint this connector owns. A bridge asks the platform which
    rooms it is responsible for rather than being told in its environment: a
    binding made in the UI has to reach it without a redeploy, and the
    connector holds no state of its own worth trusting.

    Endpoint kind is explicit: the connector hydrates channel mirrors and
    assistant threads into separate maps, so a restart can resume a known
    thread without trying to attach a channel webhook to it."""
    async with request.app.state.session_factory() as s:
        rows = list((await s.execute(
            select(BindingRow)
            .where(BindingRow.connector == connector,
                   BindingRow.status == "active")
            .order_by(BindingRow.external_ref))).scalars())
    return [{"channel_id": r.channel_id, "external_ref": r.external_ref,
             "external_kind": r.external_kind or "channel",
             "parent_external_ref": r.parent_external_ref,
             "display_name": r.display_name or "",
             "external_url": r.external_url or "",
             "status": r.status or "active", "config": r.config or {}}
            for r in rows]


@router.get("/api/relay/channels/{channel_id}/bindings",
            response_model=list[S.RelayBindingView],
            dependencies=[Depends(require_relay_access(*READ_ROLES, agents=False))])
async def list_relay_bindings(request: Request, channel_id: str):
    async with request.app.state.session_factory() as s:
        if await s.get(Conversation, channel_id) is None:
            raise HTTPException(404, "unknown channel")
        return [_binding(b) for b in await bindings_of(s, channel_id)]


@router.post("/api/relay/channels/{channel_id}/bindings", status_code=201,
             response_model=S.RelayBindingView,
             dependencies=[Depends(require_relay_access(*INVOKE_ROLES, agents=False))])
async def create_relay_binding(request: Request, channel_id: str, body: S.RelayBindingIn):
    if body.connector not in CONNECTORS:
        raise HTTPException(422, f"connector must be one of {', '.join(CONNECTORS)}")
    external_ref = body.external_ref.strip()
    if not external_ref:
        raise HTTPException(422, "external_ref must name a room on that network")
    if body.external_kind not in ("channel", "thread", "dm"):
        raise HTTPException(422, "external_kind must be channel, thread, or dm")
    async with request.app.state.session_factory() as s:
        conv = await s.get(Conversation, channel_id)
        if conv is None:
            raise HTTPException(404, "unknown channel")
        if conv.kind == "dm":
            # The connector owns its own threads (see the listing above): a DM
            # already has a bridge, made by the ingestor when the thread first
            # spoke, and a second one made here would only be a room the
            # connector then mirrors twice.
            raise HTTPException(409, "DMs are bound by the connector's own thread flow")
        row = BindingRow(channel_id=channel_id, connector=body.connector,
                         external_ref=external_ref,
                         external_kind=body.external_kind,
                         parent_external_ref=body.parent_external_ref,
                         display_name=body.display_name.strip(),
                         external_url=body.external_url.strip(),
                         config=dict(body.config))
        s.add(row)
        try:
            await s.commit()
        except IntegrityError:
            # The unique (connector, external_ref) is the real arbiter: one room
            # on the other network resolves to exactly one channel, or an
            # inbound message would have two places to land.
            await s.rollback()
            raise HTTPException(409, f"{body.connector}:{external_ref} is already "
                                     f"bound to a channel")
        return _binding(row)


@router.delete("/api/relay/channels/{channel_id}/bindings/{binding_id}",
               response_model=S.OkId,
               dependencies=[Depends(require_relay_access(*INVOKE_ROLES, agents=False))])
async def delete_relay_binding(request: Request, channel_id: str, binding_id: str):
    """Unbind: the room stays, the bridge stops. The messages on both sides are
    the record of what was said and neither is touched."""
    async with request.app.state.session_factory() as s:
        row = await s.get(BindingRow, binding_id)
        if row is None or row.channel_id != channel_id:
            raise HTTPException(404, "unknown binding")
        await s.delete(row)
        await s.commit()
    return {"ok": True, "id": binding_id}


async def _reactions(s, ids: list[str], participant: str) -> dict[str, list[dict]]:
    if not ids:
        return {}
    mine = case((ReactionRow.participant == participant, 1), else_=0)
    rows = (await s.execute(select(
        ReactionRow.message_id, ReactionRow.emoji, func.count(), func.max(mine))
        .where(ReactionRow.message_id.in_(ids))
        .group_by(ReactionRow.message_id, ReactionRow.emoji)
        .order_by(func.min(ReactionRow.created_at), ReactionRow.emoji))).all()
    out: dict[str, list[dict]] = {}
    for message_id, emoji, count, is_mine in rows:
        out.setdefault(message_id, []).append(
            {"emoji": emoji, "count": count, "mine": bool(is_mine)})
    return out


@router.get("/api/relay/channels/{channel_id}/messages", response_model=list[S.RelayMessage])
async def list_relay_messages(request: Request, channel_id: str,
                              before: str | None = None,
                              after: str | None = None,
                              limit: int = Query(50, ge=1, le=200),
                              thread: str | None = None,
                              caller: Caller = Depends(require_relay_access(*READ))):
    """A page of the room, from a cursor the client already holds.

    Two directions, because a reader and a poll want opposite ends of the room.
    `before` pages BACKWARDS, newest-first: scrolling up through history.
    `after` pages FORWARDS, oldest-first: the catch-up a client does when its
    stream was down, where keeping the OLDEST of the newer messages is what
    makes the next page continue from this one instead of leaving a hole. Both
    are message ids rather than timestamps, so a client pages by what it has;
    `thread` narrows either to one root and its replies.

    They are mutually exclusive: "newer than X and older than Y" is a range,
    which is a different endpoint with a different contract, and quietly
    honouring one of the two would hand a paging client a silent gap."""
    if before and after:
        raise HTTPException(422, "pass `before` or `after`, not both")
    agents = _agent_set(request)
    async with request.app.state.session_factory() as s:
        await _channel_or_404(s, channel_id, caller, agents)
        stmt = select(MessageRow).where(MessageRow.channel_id == channel_id,
                                        MessageRow.deleted_at.is_(None))
        if thread:
            stmt = stmt.where(or_(MessageRow.thread_root == thread, MessageRow.id == thread))
        if after:
            cursor = await s.get(MessageRow, after)
            if cursor is None or cursor.channel_id != channel_id:
                # The SSE replay's posture (`_missed`): a cursor we cannot
                # place is from another room or a pruned message, and answering
                # it with a page the client may already be showing would
                # duplicate the room rather than catch it up.
                return []
            stmt = stmt.where(_newer_than(cursor)).order_by(MessageRow.created_at,
                                                            MessageRow.id)
        else:
            if before:
                cursor = await s.get(MessageRow, before)
                if cursor is None or cursor.channel_id != channel_id:
                    raise HTTPException(422, "unknown `before` cursor")
                stmt = stmt.where(_older_than(cursor))
            stmt = stmt.order_by(MessageRow.created_at.desc(), MessageRow.id.desc())
        rows = list((await s.execute(stmt.limit(limit))).scalars())
        faces = await _faces(s, _agents_among(r.author for r in rows))
        reactions = await _reactions(s, [r.id for r in rows], caller.participant)
    return [_message(r, face=_face_of(r.author, faces), reactions=reactions.get(r.id))
            for r in rows]


async def _dm_turn(request: Request, conv: Conversation, text: str, caller: Caller,
                   agent: str) -> dict:
    """Post a human's DM message the way the conversation facade does: message,
    event, and the run that answers it.

    A DM with an agent has two doors — this route and
    `POST /api/conversations/{id}/messages` — onto one room, and in a DM every
    human message is an implicit summons, mention or not. Only one of the two
    may create the turn, or a message posted here gets answered twice: the
    router deliberately leaves DM turns alone (`relay_router._facade_owns`),
    because it sees the message before the facade's run exists and cannot tell
    a duplicate from a turn. So this door delegates rather than inserting, and
    `continue_conversation` stays the single definition of what a turn is.

    `reply_to` is dropped: a DM is linear, and the facade threads nothing."""
    info = request.app.state.agent_store.get(agent)
    if info is not None and not info.enabled:
        # The same answer the conversations endpoint gives: disabling an agent
        # stops its threads rather than queueing work nothing will pick up.
        raise HTTPException(409, "agent is disabled")
    run_id = await continue_conversation(request.app.state.session_factory,
                                         request.app.state.producer, conv.id, text,
                                         caller.principal)
    if run_id is None:
        raise HTTPException(409, "conversation is closed, missing, or has a turn in progress")
    async with request.app.state.session_factory() as s:
        run = await s.get(Run, run_id)
        row = await s.get(MessageRow, run.trigger_message_id) if run is not None else None
    if row is None:
        raise HTTPException(500, "the turn was created without its message")
    # The facade published to Kafka; this pod's own streams still want it
    # first-hand, for the same reason the generic path pushes before publishing.
    request.app.state.feed.publish(conv.id, "message", relay_message_payload(row, conv))
    return _message(row)


@router.post("/api/relay/channels/{channel_id}/messages", response_model=S.RelayMessage)
async def post_relay_message(request: Request, channel_id: str, body: S.RelayMessageIn,
                             caller: Caller = Depends(require_relay_access(*WRITE))):
    agents = _agent_set(request)
    async with request.app.state.session_factory() as s:
        conv = await s.get(Conversation, channel_id)
        if conv is None or conv.archived_at is not None:
            raise HTTPException(404, "unknown channel")
        explicit = await _explicit(s, conv.id)
        if not is_member(conv, caller.participant, agents, explicit):
            raise HTTPException(403, "not a member of this channel")
        if body.reply_to:
            parent = await s.get(MessageRow, body.reply_to)
            if (parent is None or parent.channel_id != channel_id
                    or parent.deleted_at is not None):
                raise HTTPException(404, "unknown reply_to")
        # A person's message in an agent DM is a TURN, and turns belong to the
        # facade (see _dm_turn). Everything else — every channel and group, and
        # an agent's own posts anywhere — is a plain message.
        turn_agent = (conv.agent if caller.agent is None
                      and room_dispatch_mode(conv) == "facade"
                      and conv.agent else None)
        if turn_agent is None:
            text = body.body if caller.agent is None else strip_room_mentions(body.body)
            run_id = (getattr(request.state, "api_key_run_id", None)
                      if caller.agent else None)
            row = await _insert_message(
                s, conv, author=caller.participant, body=text, reply_to=body.reply_to,
                # A closed room can only summon its own members: `@news` in a
                # private group news is not in must stay text, or the mention
                # hands it messages its absence was meant to withhold.
                mentions=parse_mentions(text, mentionable_in(conv, agents, explicit),
                                        caller.participant),
                run_id=run_id, hop=await _hop_of(s, run_id))
            await s.commit()
            faces = await _faces(s, {caller.agent} if caller.agent else set())
            face = _face_of(caller.participant, faces)
            view = _message(row, face=face)
            # A bound room is mirrored both ways (docs/design/19 T10): what a
            # person types here is what Discord shows. Resolved inside the
            # session, published outside it.
            outbound = await outbound_for_message(s, conv, row)
    if turn_agent is not None:
        # Outside the session: the facade opens its own, and the turn it
        # materializes must not be nested inside a transaction this route holds.
        return await _dm_turn(request, conv, body.body, caller, turn_agent)
    # Straight to this pod's own streams first: the room stays live when Kafka
    # is down, and when it is up the echo off `relay.messages` is deduped by id.
    request.app.state.feed.publish(conv.id, "message",
                                   relay_message_payload(row, conv, face=face))
    await publish_relay_message(request.app.state.producer, conv, row, face=face,
                                outbound=outbound)
    return view


# An app key is `app:<name>` (appprovisioner), which is already a well-formed
# participant of the connector shape — the app IS the connector for its own
# rows — so the row names the key itself, never a string the caller chose.
_APP_KEY_PREFIX = "app:"


def _notify_author(request: Request, principal: str) -> str:
    if principal.startswith(_APP_KEY_PREFIX) and is_participant(principal):
        return principal
    agent = getattr(request.state, "api_key_agent", None)
    return _participant(agent=agent) if agent else _participant(principal=principal)


async def _notify_count(s, author: str, since) -> int:
    """This route's rows by `author` in the window: the hourly ledger, kept in
    the messages themselves so it survives a restart. An event row with no
    card is this route's alone — every other event writer (tickets, the wiki,
    the workbench) attaches a card."""
    # `card=None` lands as a JSON null, not a SQL NULL (the column's
    # none_as_null default), so both spellings of "no card" are counted — by
    # text, since Postgres has no equality on its `json` type.
    return (await s.execute(select(func.count()).select_from(MessageRow).where(
        MessageRow.author == author, MessageRow.kind == "event",
        or_(MessageRow.card.is_(None), cast(MessageRow.card, Text) == "null"),
        MessageRow.created_at >= since))).scalar() or 0


@router.post("/api/relay/notify", status_code=201, response_model=S.RelayMessage)
async def relay_notify(request: Request, body: S.RelayNotifyIn,
                       principal: str = Depends(require_role(*ANNOTATE_ROLES))):
    """A system row into a room from a caller that is not a participant of it
    (docs/design/25): the tcms app's `app:tcms` key announcing a recorded run
    in `#qa`. Posting a MESSAGE is a member's act — an app is in no room and
    holds no voice there — so this is the platform's own card shape instead:
    `kind=event`, no mentions, so the text summons nobody and the router has
    nothing to route; no membership check, as none of the platform's cards
    have one; one line, so the room's most trusted voice cannot be made to say
    a second sentence by the text it relays. The author is the caller's
    principal, so the row still says who really wrote it."""
    author = _notify_author(request, principal)
    text = one_line(body.text, S.RELAY_NOTIFY_MAX)
    if not text:
        raise HTTPException(422, "text is empty once flattened")
    limit = request.app.state.settings.relay_notify_per_hour
    async with request.app.state.session_factory() as s:
        conv = await channel_by_ref(s, body.channel)
        # A channel only: `channel_by_ref` also answers to a DM's or a group's
        # id, and those are closed rooms whose walls a non-member's voice must
        # not pass through — 404, the same answer as for a room that is not
        # there, so an id cannot be probed for its kind either.
        if conv is None or conv.kind != "channel" or conv.archived_at is not None:
            raise HTTPException(404, "unknown channel")
        if await _notify_count(s, author, utcnow() - timedelta(hours=1)) >= limit:
            raise HTTPException(429, f"notify budget spent ({limit}/hour); try again later")
        row = await _insert_message(s, conv, author=author, body=text, kind="event",
                                    mentions=[])
        await s.commit()
        view = _message(row)
        outbound = await outbound_for_message(s, conv, row)
    request.app.state.feed.publish(conv.id, "message", relay_message_payload(row, conv))
    await publish_relay_message(request.app.state.producer, conv, row, outbound=outbound)
    return view


@router.post("/api/relay/messages/{message_id}/reactions", response_model=S.RelayReactionView)
async def toggle_relay_reaction(request: Request, message_id: str, body: S.RelayReactionIn,
                                caller: Caller = Depends(require_relay_access(*WRITE))):
    agents = _agent_set(request)
    async with request.app.state.session_factory() as s:
        message = await s.get(MessageRow, message_id)
        if message is None or message.deleted_at is not None:
            raise HTTPException(404, "unknown message")
        conv = await s.get(Conversation, message.channel_id)
        if conv is None:
            raise HTTPException(404, "unknown message")
        if not is_member(conv, caller.participant, agents, await _explicit(s, conv.id)):
            raise HTTPException(403, "not a member of this channel")
        key = (message_id, caller.participant, body.emoji)
        existing = await s.get(ReactionRow, key)
        if existing is None:
            s.add(ReactionRow(message_id=message_id, participant=caller.participant,
                              emoji=body.emoji))
            try:
                await s.commit()
            except IntegrityError:
                # A double-click racing itself: the reaction is already there,
                # so this toggle is the one that takes it off again.
                await s.rollback()
                existing = await s.get(ReactionRow, key)
                if existing is not None:
                    await s.delete(existing)
                    await s.commit()
        else:
            await s.delete(existing)
            await s.commit()
        count = (await s.execute(select(func.count()).select_from(ReactionRow).where(
            ReactionRow.message_id == message_id,
            ReactionRow.emoji == body.emoji))).scalar_one()
    # Reactions have no Kafka topic in this design — they are a UI affordance,
    # not a fact the router or a bridge acts on — so the feed is their only
    # live path, and a second API pod's viewers see them on their next fetch.
    request.app.state.feed.publish(message.channel_id, "reaction",
                                   {"message_id": message_id, "emoji": body.emoji,
                                    "count": count, "participant": caller.participant})
    return {"emoji": body.emoji, "count": count, "mine": existing is None}


def _frame(event: str, data: dict) -> str:
    """One SSE frame. A message carries its own id as the event id, which is
    what a reconnecting browser sends back as `Last-Event-ID` — the resume
    cursor is the message, not a sequence number we would have to keep."""
    head = [f"id: {data['id']}"] if event == "message" and data.get("id") else []
    return "\n".join(head + [f"event: {event}",
                             "data: " + json.dumps(data, separators=(",", ":")), "", ""])


async def _still_a_member(request: Request, channel_id: str, caller: Caller) -> bool:
    """Whether an agent still belongs in this room. Membership can be taken
    away while a stream is open — a participant row removed, the agent disabled
    — and a socket that keeps delivering afterwards is the one way an agent
    reads a room it was thrown out of."""
    async with request.app.state.session_factory() as s:
        conv = await s.get(Conversation, channel_id)
        return conv is not None and is_member(
            conv, caller.participant, _agent_set(request), await _explicit(s, conv.id))


async def _missed(s, channel_id: str, cursor: str) -> list:
    """Messages newer than the one the client says it already has, oldest
    first. An id we cannot place replays NOTHING: it is a cursor from another
    room or a pruned message, and answering it with the newest page would
    duplicate whatever the client is already showing."""
    row = await s.get(MessageRow, cursor)
    if row is None or row.channel_id != channel_id:
        return []
    return list((await s.execute(select(MessageRow).where(
        MessageRow.channel_id == channel_id, MessageRow.deleted_at.is_(None),
        _newer_than(row))
        .order_by(MessageRow.created_at, MessageRow.id).limit(REPLAY_LIMIT))).scalars())


@router.get("/api/relay/channels/{channel_id}/events", response_class=StreamingResponse)
async def relay_events(request: Request, channel_id: str, after: str | None = None,
                       last_event_id: str | None = Header(None, alias="Last-Event-ID"),
                       caller: Caller = Depends(require_relay_access(*READ))):
    """The room, live: `message`, `reaction` and `presence` events as they
    happen, plus a heartbeat comment so nothing between here and the browser
    decides an idle stream is a dead one.

    Membership is resolved BEFORE the response starts: a 403 has to be a 403,
    not an event stream that opens and then says nothing."""
    agents = _agent_set(request)
    feed = request.app.state.feed
    async with request.app.state.session_factory() as s:
        conv = await _channel_or_404(s, channel_id, caller, agents)
        # Subscribed before the replay is read, so a message posted between the
        # two is queued rather than lost in the gap. Nothing after this point
        # may leave without unsubscribing — a stream that never starts would
        # otherwise hold a queue nobody will ever drain.
        queue = feed.subscribe(channel_id)
        try:
            cursor = after or last_event_id
            rows = await _missed(s, channel_id, cursor) if cursor else []
            faces = await _faces(s, _agents_among(r.author for r in rows))
            replay = [relay_message_payload(r, conv, face=_face_of(r.author, faces))
                      for r in rows]
        except BaseException:
            feed.unsubscribe(channel_id, queue)
            raise

    async def stream():
        last = cursor
        try:
            for data in replay:
                last = data.get("id") or last
                yield _frame("message", data)
            while True:
                try:
                    # The interval is read per wait on purpose: it is a module
                    # global a test can turn down without patching the route.
                    event, data = await asyncio.wait_for(queue.get(), HEARTBEAT_SECONDS)
                except TimeoutError:
                    # The quiet tick is also the cheapest place to re-ask the
                    # question the connect answered: one small query per open
                    # agent stream per interval.
                    if caller.agent is not None and not await _still_a_member(
                            request, channel_id, caller):
                        yield _frame("closed", {"reason": "no longer a member "
                                                          "of this channel"})
                        return
                    yield ": heartbeat\n\n"
                    continue
                if event == OVERFLOW:
                    # This stream fell behind and lost frames. It is told where
                    # its picture stops being trustworthy; refetching from there
                    # is the client's job, and cheaper than us replaying blind.
                    yield _frame(OVERFLOW, {"after": last})
                    continue
                last = data.get("id") or last if event == "message" else last
                yield _frame(event, data)
        finally:
            # Runs on client disconnect too (the generator is closed), which is
            # the only thing that keeps the fan-out's subscriber set honest.
            feed.unsubscribe(channel_id, queue)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      # nginx buffers a proxied response by
                                      # default, which would hold every frame
                                      # until the stream ends — i.e. forever.
                                      "X-Accel-Buffering": "no"})


async def _find_dm(s, pair: list[str]) -> Conversation | None:
    """The DM between exactly these two, if it already exists. `dm_key` answers
    it with an index lookup; the participant scan behind it is for a legacy DM
    whose pair was already claimed, which the backfill leaves keyless."""
    conv = (await s.execute(select(Conversation).where(
        Conversation.kind == "dm", Conversation.dm_key == dm_key_of(pair),
        Conversation.archived_at.is_(None)))).scalars().first()
    if conv is not None:
        return conv
    ids = list((await s.execute(select(ParticipantRow.channel_id).where(
        ParticipantRow.participant.in_(pair)).group_by(ParticipantRow.channel_id)
        .having(func.count() == len(pair)))).scalars())
    if not ids:
        return None
    convs = list((await s.execute(select(Conversation).where(
        Conversation.id.in_(ids), Conversation.kind == "dm",
        Conversation.archived_at.is_(None))
        .order_by(Conversation.created_at, Conversation.id))).scalars())
    sizes = dict((await s.execute(select(ParticipantRow.channel_id, func.count()).where(
        ParticipantRow.channel_id.in_([c.id for c in convs]))
        .group_by(ParticipantRow.channel_id))).all())
    # A room that holds these two AND somebody else is a group, not their DM.
    return next((c for c in convs if sizes.get(c.id) == len(pair)), None)


@router.post("/api/relay/dm", response_model=S.RelayChannelDetail)
async def open_relay_dm(request: Request, body: S.RelayDmIn,
                        caller: Caller = Depends(require_relay_access(*WRITE))):
    """Get-or-create, never "create": a DM is an identity (these two people),
    so asking for it twice must not fork the history."""
    other = body.with_.strip()
    if not is_participant(other):
        raise HTTPException(422, f"'{other[:64]}' is not a participant string")
    if other == caller.participant:
        raise HTTPException(422, "a dm needs two participants")
    target = agent_name(other)
    if target is not None and target not in _agent_set(request):
        # A miss is the one thing worth a reload: an agent created moments ago
        # has to be reachable, and this is the path that 404s on it.
        await request.app.state.agent_store.reload()
        if target not in _agent_set(request):
            raise HTTPException(404, "unknown agent")
    pair = sorted([caller.participant, other])
    async with request.app.state.session_factory() as s:
        conv = await _find_dm(s, pair)
        if conv is None:
            # `agent` is the legacy single-agent column: set so the
            # /api/conversations facade and the design-14 resume path keep
            # working over the same row.
            conv = Conversation(connector="web", kind="dm", agent=target or caller.agent,
                                home="relay", reply_mode="linear",
                                dispatch_mode="facade", default_agent=target or caller.agent,
                                topic="", open=False, dm_key=dm_key_of(pair),
                                title="dm:" + ":".join(pair))
            s.add(conv)
            await s.flush()
            for p in pair:
                s.add(ParticipantRow(channel_id=conv.id, participant=p, role="member"))
            try:
                await s.commit()
            except IntegrityError:
                # The unique index caught a concurrent open of the same DM: the
                # winner's row IS the room, so return that rather than forking.
                await s.rollback()
                conv = await _find_dm(s, pair)
                if conv is None:
                    raise HTTPException(409, "the dm was opened and archived at once")
        return await _detail(s, conv)


@router.get("/api/relay/search", response_model=list[S.RelayMessage])
async def search_relay_messages(request: Request, q: str = Query(min_length=1, max_length=200),
                                channel: str | None = None,
                                project: str | None = None,
                                limit: int = Query(50, ge=1, le=100),
                                caller: Caller = Depends(require_relay_access(*READ))):
    agents = _agent_set(request)
    async with request.app.state.session_factory() as s:
        # The same reading of a channel reference the board uses: an agent
        # searching the room it is in holds `#ops`, not the room's id, and a
        # reference this door could not read used to come back as "nothing
        # matched" rather than as a miss.
        scope = None if channel is None else await channel_by_ref(s, channel)
        if channel is not None and scope is None:
            raise HTTPException(404, "unknown channel")
        visible, _ = await _visible(s, caller, agents)
        if project is not None:
            from agentplatform.db import Project, ProjectAgent
            project_row = (await s.execute(select(Project).where(
                Project.slug == project))).scalar_one_or_none()
            if project_row is None:
                raise HTTPException(404, "unknown project")
            if caller.agent and not (await s.execute(select(ProjectAgent).where(
                ProjectAgent.project_id == project_row.id,
                ProjectAgent.agent == caller.agent))).scalar_one_or_none():
                return []
            visible = [c for c in visible if c.project_id == project_row.id]
        # Visibility still decides the answer, not the reference: a room the
        # caller cannot see searches as empty rather than as a 403, which is
        # the same thing an unscoped search tells them about it.
        ids = [c.id for c in visible if scope is None or c.id == scope.id]
        if not ids:
            return []
        stmt = select(MessageRow).where(MessageRow.channel_id.in_(ids),
                                        MessageRow.deleted_at.is_(None))
        if s.get_bind().dialect.name == "postgresql":
            stmt = stmt.where(func.to_tsvector("english", MessageRow.body).op("@@")(
                func.plainto_tsquery("english", q)))
        else:
            # sqlite has no full-text index here (the GIN index is postgres
            # only), so the dev/test dialect gets an honest substring scan.
            stmt = stmt.where(func.lower(MessageRow.body).contains(q.lower(), autoescape=True))
        rows = list((await s.execute(stmt.order_by(
            MessageRow.created_at.desc(), MessageRow.id.desc()).limit(limit))).scalars())
        faces = await _faces(s, _agents_among(r.author for r in rows))
    return [_message(r, face=_face_of(r.author, faces)) for r in rows]


@router.get("/api/relay/presence", response_model=list[S.RelayPresence],
            dependencies=[Depends(require_role(*READ_ROLES))])
async def relay_presence(request: Request):
    """Presence is derived, never stored: an agent is `thinking` while it has a
    run in a channel, so nothing has to be cleaned up when a pod dies."""
    store = request.app.state.agent_store
    await store.reload()
    infos = sorted(store.list(), key=lambda i: i.name)
    async with request.app.state.session_factory() as s:
        active = (await s.execute(select(Run.agent, Run.conversation_id).where(
            Run.state.in_(ACTIVE_STATES), Run.conversation_id.isnot(None)))).all()
        faces = await _faces(s, {i.name for i in infos})
    thinking: dict[str, list[str]] = {}
    for agent, channel_id in active:
        where = thinking.setdefault(agent, [])
        if channel_id not in where:
            where.append(channel_id)
    out = []
    for info in infos:
        where = thinking.get(info.name, [])
        state = ("quarantined" if info.error is not None else
                 "disabled" if not info.enabled else
                 "thinking" if where else "idle")
        out.append({"agent": info.name, "state": state, "thinking_in": where,
                    "face": faces[info.name]})
    return out


@router.get("/api/relay/stats", response_model=S.RelayStats,
            dependencies=[Depends(require_role(*READ_ROLES))])
async def relay_stats(request: Request):
    settings = request.app.state.settings
    day, hour = utcnow() - timedelta(hours=24), utcnow() - timedelta(hours=1)
    async with request.app.state.session_factory() as s:
        async def count(model, *where):
            return (await s.execute(select(func.count()).select_from(model)
                                    .where(*where))).scalar_one()
        messages = await count(MessageRow, MessageRow.created_at >= day,
                               MessageRow.deleted_at.is_(None))
        by_agents = await count(MessageRow, MessageRow.created_at >= day,
                                MessageRow.deleted_at.is_(None),
                                MessageRow.author.like("agent:%"))
        # Invoked only: the tile reads "agent invocations today", and a number
        # that grew every time the router REFUSED to invoke somebody would be
        # counting the opposite of what it says.
        invocations = await count(RelayInvocation, RelayInvocation.created_at >= day,
                                  RelayInvocation.decision == "invoked")
        by_reason = (await s.execute(
            select(RelayInvocation.reason, func.count())
            .where(RelayInvocation.created_at >= day,
                   RelayInvocation.decision == "suppressed")
            .group_by(RelayInvocation.reason))).all()
        # The global budget the router spends against: invocations that became
        # runs, in the trailing hour.
        used = await count(RelayInvocation, RelayInvocation.created_at >= hour,
                           RelayInvocation.decision == "invoked")
    # Zero-filled: "nothing was refused for that reason" and "that reason is
    # gone" are different answers, and a missing key gives the reader the wrong
    # one. A reason the router grows later still appears — it simply has no
    # zero-filled floor until it is named above.
    breakdown = {reason: 0 for reason in SUPPRESSION_REASONS}
    for reason, n in by_reason:
        breakdown[reason or "unknown"] = breakdown.get(reason or "unknown", 0) + n
    suppressed = sum(breakdown[reason] for reason in REFUSED_REASONS)
    return {"messages_24h": messages, "agent_messages_24h": by_agents,
            "invocations_24h": invocations, "suppressed_24h": suppressed,
            "suppressed_by_reason": breakdown,
            "budget": {"channel_per_hour": settings.relay_channel_invocations_per_hour,
                       "global_per_hour": settings.relay_global_invocations_per_hour,
                       "global_used_last_hour": used},
            # The guards themselves, so the numbers above can be read against
            # what produced them. Environment settings, hence read-only here.
            "settings": {"default_grant": settings.relay_default_grant,
                         "max_hops": settings.relay_max_hops,
                         "channel_per_hour": settings.relay_channel_invocations_per_hour,
                         "global_per_hour": settings.relay_global_invocations_per_hour,
                         "cooldown_seconds": settings.relay_agent_cooldown_seconds,
                         "context_messages": settings.relay_context_messages}}
