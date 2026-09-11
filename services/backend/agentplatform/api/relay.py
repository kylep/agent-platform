"""Relay's REST surface (docs/design/19): channels, messages, DMs, reactions,
search, presence and stats.

Authorship is the invariant the whole messenger rests on. There is no author
field on the wire: a message is attributed from the caller's token —
`agent:<name>` for a per-run agent token, `user:<principal>` for a human — so
an agent can only ever speak as itself, and a prompt-injected one gains nothing
by asking to be someone else. Membership (`relay.is_member`) is the second
fence: it bounds where an agent may read and post, independent of role."""
import logging
import re
from datetime import timedelta
from typing import NamedTuple

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import case, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased

from agentplatform.api.auth import (INVOKE_ROLES, READ_ROLES, authenticate,
                                    require_role, role_allows)
from agentplatform.db import (ACTIVE_STATES, AgentDef, Conversation,
                              RELAY_SEED_CHANNELS, RelayInvocation, dm_key_of)
from agentplatform.db import RelayMessage as MessageRow
from agentplatform.db import RelayParticipant as ParticipantRow
from agentplatform.db import RelayReaction as ReactionRow
from agentplatform.db import Run, utcnow
from agentplatform.relay import (agent_name, face_for, is_agent, is_member,
                                 mentionable_in, parse_mentions, participant_of,
                                 strip_room_mentions)
# Aliased: the route below is the HTTP name for the same act, and the store
# helper is what actually writes the row.
from agentplatform.relay_store import post_relay_message as _insert_message
from agentplatform.relay_store import message_view, publish_relay_message

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
# `relay` for an agent that holds the grant, `annotator` is what a system agent
# already carries, and the broker checks the grant besides.
AGENT_ROLES = ("relay", "annotator", "operator", "coder", "admin")

_NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
# A participant is `<namespace>:<id>` — loose on the id (a Discord snowflake, a
# principal) and strict on the namespace, which is what keeps `agent:`/`user:`
# unforgeable by a connector.
_PARTICIPANT_RE = re.compile(r"[a-z][a-z0-9_-]*:\S{1,96}")
SEED_NAMES = {name for name, _ in RELAY_SEED_CHANNELS}
# Preview length of the rail's last-message line.
PREVIEW_CHARS = 200


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


async def _faces(s, names: set[str]) -> dict[str, dict]:
    """Faces for a set of agent names. The row's own `icon` wins, but the hue
    stays derived either way, so a custom emoji still gets its stable colour."""
    if not names:
        return {}
    icons = dict((await s.execute(select(AgentDef.name, AgentDef.icon)
                                  .where(AgentDef.name.in_(names)))).all())
    faces = {}
    for name in names:
        face = face_for(name)
        faces[name] = {"emoji": icons[name], "hue": face["hue"]} if icons.get(name) else face
    return faces


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
    return {"id": conv.id, "kind": conv.kind, "name": conv.name, "topic": conv.topic,
            "open": bool(conv.open), "archived_at": _iso(conv.archived_at),
            "agent": conv.agent, "participants": sorted(participants),
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
        if len(p) > 128 or not _PARTICIPANT_RE.fullmatch(p):
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
                            topic=body.topic.strip()[:256], open=is_open,
                            title=f"#{name}" if is_channel
                                  else (body.name or "group").strip()[:256])
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
        await s.commit()
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
                              limit: int = Query(50, ge=1, le=200),
                              thread: str | None = None,
                              caller: Caller = Depends(require_relay_access(*READ))):
    """A newest-first page. `before` is a message id rather than a timestamp so
    a client pages by what it already holds; `thread` narrows to one root and
    its replies."""
    agents = _agent_set(request)
    async with request.app.state.session_factory() as s:
        await _channel_or_404(s, channel_id, caller, agents)
        stmt = select(MessageRow).where(MessageRow.channel_id == channel_id,
                                        MessageRow.deleted_at.is_(None))
        if thread:
            stmt = stmt.where(or_(MessageRow.thread_root == thread, MessageRow.id == thread))
        if before:
            cursor = await s.get(MessageRow, before)
            if cursor is None or cursor.channel_id != channel_id:
                raise HTTPException(422, "unknown `before` cursor")
            stmt = stmt.where(or_(MessageRow.created_at < cursor.created_at,
                                  (MessageRow.created_at == cursor.created_at)
                                  & (MessageRow.id < cursor.id)))
        rows = list((await s.execute(stmt.order_by(
            MessageRow.created_at.desc(), MessageRow.id.desc()).limit(limit))).scalars())
        faces = await _faces(s, _agents_among(r.author for r in rows))
        reactions = await _reactions(s, [r.id for r in rows], caller.participant)
    return [_message(r, face=_face_of(r.author, faces), reactions=reactions.get(r.id))
            for r in rows]


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
        text = body.body if caller.agent is None else strip_room_mentions(body.body)
        row = await _insert_message(
            s, conv, author=caller.participant, body=text, reply_to=body.reply_to,
            # A closed room can only summon its own members: `@news` in a
            # private group news is not in must stay text, or the mention hands
            # it messages its absence was meant to withhold.
            mentions=parse_mentions(text, mentionable_in(conv, agents, explicit),
                                    caller.participant),
            run_id=getattr(request.state, "api_key_run_id", None) if caller.agent else None)
        await s.commit()
        faces = await _faces(s, {caller.agent} if caller.agent else set())
        face = _face_of(caller.participant, faces)
        view = _message(row, face=face)
    await publish_relay_message(request.app.state.producer, conv, row, face=face)
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
    return {"emoji": body.emoji, "count": count, "mine": existing is None}


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
    if len(other) > 128 or not _PARTICIPANT_RE.fullmatch(other):
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
                                limit: int = Query(50, ge=1, le=100),
                                caller: Caller = Depends(require_relay_access(*READ))):
    agents = _agent_set(request)
    async with request.app.state.session_factory() as s:
        visible, _ = await _visible(s, caller, agents)
        ids = [c.id for c in visible if channel is None or c.id == channel]
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
        invocations = await count(RelayInvocation, RelayInvocation.created_at >= day)
        suppressed = await count(RelayInvocation, RelayInvocation.created_at >= day,
                                 RelayInvocation.decision == "suppressed")
        # The global budget the router spends against: invocations that became
        # runs, in the trailing hour.
        used = await count(RelayInvocation, RelayInvocation.created_at >= hour,
                           RelayInvocation.decision == "invoked")
    return {"messages_24h": messages, "agent_messages_24h": by_agents,
            "invocations_24h": invocations, "suppressed_24h": suppressed,
            "budget": {"channel_per_hour": settings.relay_channel_invocations_per_hour,
                       "global_per_hour": settings.relay_global_invocations_per_hour,
                       "global_used_last_hour": used},
            # The guards themselves, so the numbers above can be read against
            # what produced them. Environment settings, hence read-only here.
            "settings": {"default_grant": settings.relay_default_grant,
                         "max_hops": settings.relay_max_hops,
                         "channel_per_hour": settings.relay_channel_invocations_per_hour,
                         "global_per_hour": settings.relay_global_invocations_per_hour,
                         "cooldown_seconds": settings.relay_agent_cooldown_seconds}}
