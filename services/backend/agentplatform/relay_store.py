"""Writing a Relay message: the row, its view, and the events it becomes.

Four callers post into channels — the REST API, the recorder (an agent's
reply), the router (the platform's own notices) and the conversation facade (a
human's turn, whether typed here or bridged in) — and every one of them must
produce the same row and the same events, because the router, the SSE fan-out
and the bridges downstream read only what was written here. A second
implementation would be a second definition of what a message is.

A message in a bound room becomes more than one event: `relay.messages` for the
platform, plus a `conversation.outbound` for each network the room is bridged
to. They are published together, here, so a room cannot be mirrored one way by
the API and another by the recorder."""
import logging

from sqlalchemy import or_, select

from agentplatform.db import (AgentDef, Conversation, RelayBinding, RelayMessage,
                              RelayParticipant, Run, utcnow)
from agentplatform.events import (TOPIC_CONVERSATION_OUTBOUND,
                                  TOPIC_RELAY_MESSAGES)
from agentplatform.relay import face_for, mentionable_in, parse_mentions

log = logging.getLogger("relay_store")


def message_view(row, *, face=None, reactions=None) -> dict:
    return {"id": row.id, "channel_id": row.channel_id, "author": row.author,
            "kind": row.kind, "body": row.body, "card": row.card,
            "reply_to": row.reply_to, "thread_root": row.thread_root,
            "run_id": row.run_id, "hop": row.hop, "mentions": row.mentions or [],
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "edited_at": row.edited_at.isoformat() if row.edited_at else None,
            "face": face, "reactions": reactions or []}


def relay_message_payload(msg, conv, *, face=None) -> dict:
    """The `relay.messages` body. `channel_kind` rides along so a consumer can
    apply the room's rules (a dm is not a channel) without a lookup."""
    return {**message_view(msg, face=face), "channel_kind": conv.kind}


async def post_relay_message(session, conv, *, author: str, body: str,
                             kind: str = "text", run_id: str | None = None,
                             hop: int = 0, trigger_message_id: str | None = None,
                             reply_to: str | None = None,
                             mentions=None, card: dict | None = None) -> RelayMessage:
    """Insert a message into `conv`. Flushed but NOT committed: the caller owns
    the transaction, so a turn's message and the run that answers it land
    together or not at all."""
    thread_root = None
    if reply_to:
        parent = await session.get(RelayMessage, reply_to)
        # A thread is flat: a reply to a reply joins the same root, so the pane
        # never has to render a tree.
        thread_root = (parent.thread_root or parent.id) if parent is not None else None
    row = RelayMessage(channel_id=conv.id, author=author, kind=kind, body=body,
                       card=card, reply_to=reply_to, thread_root=thread_root,
                       run_id=run_id, trigger_message_id=trigger_message_id,
                       hop=hop, mentions=list(mentions or []))
    session.add(row)
    # Posting is the activity the rail sorts private rooms by, and a room whose
    # only message was just written has nothing else to sort on.
    conv.updated_at = utcnow()
    await session.flush()
    return row


async def edit_message_card(session, msg, *, card: dict, body: str) -> RelayMessage:
    """Rewrite a `kind=event` message's card and its plain-text fallback.

    The only editable thing about a Relay message, and it exists for one reason
    (docs/design/20): a ticket's card sits in the room showing its state, so a
    move that posted a new card would leave the channel holding a stack of
    contradictory ones. Author, hop, reply_to, thread_root and created_at are
    deliberately untouched — an edit changes what a message SAYS, never who
    said it or where it sits in the thread, which is also what lets the SSE
    consumer upsert it by id.

    Flushed but NOT committed and NOT published, exactly like
    `post_relay_message`: the caller owns the transaction, and an edit that
    committed on its own would split a ticket change into two — the row durably
    moved, the row that explains the move still uncommitted. Hand the returned
    message to `publish_relay_message` after the commit and the SSE feed
    re-sends it.

    Publish it WITHOUT `outbound`: a bridge has no concept of an edit, so
    mirroring one would post the card into Discord again on every transition.
    The system row in the thread is what the bridge carries."""
    msg.card, msg.body, msg.edited_at = card, body, utcnow()
    await session.flush()
    return msg


# What a message's `state` says when no run wrote it. The field is a run's
# outcome, and a human typing in a channel has none — but connectors have read
# it since design-07, so it stays present and honest rather than absent.
POSTED = "posted"


async def outbound_for_message(session, conv, msg, *,
                               state: str | None = None) -> list[dict]:
    """The `conversation.outbound` payloads that mirror `msg` to the networks on
    the other side of its room — one per binding, empty when nobody is
    listening.

    A LIST because a room may be bridged more than once: #ops in Discord and in
    Slack is still one room, and mirroring to whichever binding a query happened
    to return first would make the other network silently miss half a
    conversation.

    Everything written into a bound room is mirrored — an agent's reply, a
    human's post, the platform's own notices — because the channel behind a
    binding IS the room, not a notification sink: a bridge that carried only
    agent replies would show Discord half a conversation, with every question
    missing.

    Two rules decide the set. A binding is the bridge (docs/design/19); the
    legacy connector columns still answer for a thread the backfill has not
    reached, and a web room with neither has no other side at all. And a message
    that ARRIVED over one of these bridges is never sent back down THAT one: the
    author's namespace is the network it came from (`discord:<id>`), so
    comparing it to each binding's connector is the whole loop guard — without
    it every human line in a bound channel is echoed back at the person who
    typed it. The other bridges still get it: a Discord message belongs on the
    Slack side of the same room.

    `state` is the run outcome the recorder holds before the Run row does; left
    out, it is read off the run that authored the message."""
    bridges = [(b.connector, b.external_ref) for b in await bindings_of(session, conv.id)]
    if not bridges:
        if conv.connector == "web" or not conv.external_ref:
            return []
        bridges = [(conv.connector, conv.external_ref)]
    origin = (msg.author or "").partition(":")[0]
    bridges = [(connector, ref) for connector, ref in bridges if connector != origin]
    if not bridges:
        return []
    if state is None:
        run = await session.get(Run, msg.run_id) if msg.run_id else None
        state = run.state if run is not None else POSTED
    return [{"channel_id": conv.id,
             # The same id under its design-07 name: connectors and the DLQ UI
             # still read `conversation_id`, and a bridge is not the place to
             # break a wire format over a rename.
             "conversation_id": conv.id,
             "connector": connector, "external_ref": ref,
             "author": msg.author, "kind": msg.kind, "message_id": msg.id,
             "run_id": msg.run_id, "text": msg.body or "", "state": state}
            for connector, ref in bridges]


async def publish_relay_message(producer, conv, msg, *, face=None,
                                outbound=()) -> None:
    """`relay.messages` is what the router, the SSE fan-out and the bridges all
    read. The row is committed and is the source of truth, so a broker blip must
    not fail a post that demonstrably landed — it costs the message its routing,
    which the invocation log shows as the mention that did nothing.

    `outbound` is the bridges' copies of the same message (one per binding),
    resolved by `outbound_for_message` while the caller still held its session:
    a binding lookup here would put a database round trip inside the publish,
    and every caller has already let its transaction go by this point. All the
    publishes happen in one call so that the API, the recorder and the router
    cannot mirror a room differently — there is one definition of "the message
    went out", and this is it."""
    if producer is None:
        return
    try:
        await producer.publish(TOPIC_RELAY_MESSAGES, conv.id,
                               relay_message_payload(msg, conv, face=face),
                               type="relay.message")
    except Exception:
        log.warning("relay.messages publish failed for message %s", msg.id, exc_info=True)
    for payload in outbound or ():
        try:
            # `conversation.reply` since design-07, for messages that are no
            # longer only replies: the connectors match on the type, so
            # renaming it would silence every bridge the moment this deploys.
            await producer.publish(TOPIC_CONVERSATION_OUTBOUND, conv.id, payload,
                                   type="conversation.reply")
        except Exception:
            # Per bridge: one unreachable network must not cost the others
            # their copy of the message.
            log.warning("conversation.outbound publish failed for message %s to %s",
                        msg.id, payload.get("connector"), exc_info=True)


async def channel_by_name(session, name: str) -> Conversation | None:
    """The live channel called `name`, or None. Archived rooms do not answer to
    their name: a job pointed at one has nowhere to post, and reviving the room
    by writing into it is not the scheduler's call."""
    return (await session.execute(select(Conversation).where(
        Conversation.kind == "channel", Conversation.name == name,
        Conversation.archived_at.is_(None)))).scalars().first()


async def channel_by_ref(session, ref: str) -> Conversation | None:
    """A channel by id, by `#name`, or by the bare name — the one reading of a
    channel reference every door uses (docs/design/20). An agent knows the room
    it is talking in by name and a model drops the sigil, so a door that took
    only an id answered "nothing matched" to a reference that named a real room.

    A bare ref is a NAME first and an id second. Shape cannot decide it: a
    channel slug may be 32 hex characters, so "that looks like an id" would make
    the room called `ab…ab` unreachable by the only word anyone calls it. A
    `#name` stays a name outright — the sigil is the caller saying so."""
    if ref.startswith("#"):
        return await channel_by_name(session, ref[1:])
    return await channel_by_name(session, ref) or await session.get(Conversation, ref)


async def summon_channel(session_factory, producer, name: str, *,
                         author: str, body: str) -> RelayMessage | None:
    """Post `body` into the channel called `name` as a NON-AGENT author, and
    publish it. Returns the message, or None when there is no such room.

    This is how the platform itself speaks into a room on a schedule
    (docs/design/19: the #standup job). It is one function because two doors
    lead here — the scheduler's tick and Run Now — and a summons that parsed its
    mentions differently depending on which one fired it would be two features.

    The author must not be an agent, and that is the whole point of the seam:
    `parse_mentions` refuses an agent's `@all`, so only a human or the platform
    can put `*` on a message and wake the room.

    No Run is created and nothing is recorded beyond the message: the router
    reads it off `relay.messages` like any other, and what it decides to summon
    is its business, not the caller's."""
    async with session_factory() as s:
        conv = await channel_by_name(s, name)
        if conv is None:
            log.warning("no live channel #%s to post into", name)
            return None
        agents = await enabled_agents(s)
        explicit = await explicit_members(s, conv.id)
        # Hop 0, like every message nobody's run wrote: a summons starts a fresh
        # chain, which is what gives the agents it wakes their full hop budget.
        msg = await post_relay_message(
            s, conv, author=author, body=body,
            mentions=parse_mentions(body, mentionable_in(conv, agents, explicit),
                                    author))
        await s.commit()
        outbound = await outbound_for_message(s, conv, msg)
    await publish_relay_message(producer, conv, msg, outbound=outbound)
    return msg


async def enabled_agents(session) -> set[str]:
    """The agents a mention may resolve to, read off the rows rather than the
    AgentStore's cache: the recorder and the conversation facade hold a session,
    not a store. A quarantined definition still answers to its name here, which
    the router re-checks before it invokes anything."""
    return set((await session.execute(
        select(AgentDef.name).where(AgentDef.enabled))).scalars())


async def faces_for(session, names: set[str]) -> dict[str, dict]:
    """Faces for a set of agent names. The row's own `icon` wins, but the hue
    stays derived either way, so a custom emoji still gets its stable colour.
    Shared by the API (the message view) and the router (the run prompt's
    roster): an agent that looks one way in the UI and another in another
    agent's prompt is two agents as far as a reader is concerned."""
    if not names:
        return {}
    rows = {name: (icon, image) for name, icon, image in (await session.execute(
        select(AgentDef.name, AgentDef.icon, AgentDef.image_artifact_id)
        .where(AgentDef.name.in_(names)))).all()}
    out = {}
    for name in names:
        face = face_for(name)
        icon, image = rows.get(name, (None, None))
        # The picture (docs/design/23) rides beside the emoji rather than
        # replacing it: a client that cannot show the image still has a face.
        out[name] = {"emoji": icon or face["emoji"], "hue": face["hue"],
                     "image_url": f"/api/artifacts/{image}/thumb" if image else None}
    return out


async def explicit_members(session, channel_id: str) -> set[str]:
    return set((await session.execute(select(RelayParticipant.participant).where(
        RelayParticipant.channel_id == channel_id))).scalars())


async def bindings_of(session, channel_id: str) -> list[RelayBinding]:
    """Every bridge this room is mirrored to, in a stable order — the order is
    the reason this is not a `.first()`: two bindings must produce the same two
    payloads every time, not whichever one the planner returned."""
    return list((await session.execute(select(RelayBinding).where(
        RelayBinding.channel_id == channel_id).order_by(
        RelayBinding.connector, RelayBinding.external_ref))).scalars())


async def _thread_window(session, base, thread_root: str, limit: int):
    """One thread, oldest first: the root ALWAYS, then the newest replies under
    it.

    The root is force-kept rather than left to the ordinary newest-n cut,
    because in a ticket's thread the root is the CARD — the key, the title, the
    state the whole conversation is about (docs/design/20). A thread longer
    than the window would otherwise hand every later summons a pile of replies
    about a ticket it can no longer name, and the agent's first move would be
    to ask which one. Deleted rows stay out on both halves (`base` carries that
    filter), and a root that is gone simply gives its slot back to the replies."""
    root = (await session.execute(
        base.where(RelayMessage.id == thread_root))).scalars().first()
    replies = list((await session.execute(
        base.where(RelayMessage.thread_root == thread_root)
        .order_by(RelayMessage.created_at.desc(), RelayMessage.id.desc())
        .limit(max(0, limit - 1) if root is not None else limit))).scalars())
    return ([root] if root is not None else []) + list(reversed(replies))


async def context_window(session, channel_id: str, *, limit: int,
                         since_message_id: str | None = None,
                         thread_root: str | None = None):
    """The messages a summoned agent is shown, oldest first.

    Three shapes, one window. Normally it is the last `limit` messages — the room
    as a human would scroll it. When the agent was busy and its wake was
    coalesced, `since_message_id` says where it stopped reading, and it gets
    everything that has happened since instead, so a run answering three
    mentions at once sees all three rather than a page that might not even
    contain the first. That tail is capped at 3x limit: a room that ran away
    while an agent was thinking must not hand it an unbounded prompt, and if the
    tail has to be truncated it is the NEWEST part that is kept — the mention
    that woke it is at the end, and a prompt without that is no use at all.

    An unknown cursor falls back to the plain page rather than returning
    nothing: a message may have been pruned out from under the wake.

    `thread_root` is the third shape (docs/design/20): a summons inside a thread
    is answered from the thread — the root and its replies, and `limit` is then
    the caller's thread limit rather than its room one. A ticket's thread IS the
    ticket's history, and a hand-off shown the room's last page instead would
    start halfway through work somebody already did. A coalesced wake still
    wins, deliberately: the backlog an agent was woken for is the ROOM's, and
    narrowing it to one thread would drop the messages it is answering."""
    base = select(RelayMessage).where(RelayMessage.channel_id == channel_id,
                                      RelayMessage.deleted_at.is_(None))
    cursor = await session.get(RelayMessage, since_message_id) if since_message_id else None
    resumed = cursor is not None and cursor.channel_id == channel_id
    if thread_root and not resumed:
        return await _thread_window(session, base, thread_root, limit)
    if resumed:
        # (created_at, id) is the same total order the message list pages by,
        # so "after" means the same thing to the reader and to the wake.
        base = base.where(or_(RelayMessage.created_at > cursor.created_at,
                              (RelayMessage.created_at == cursor.created_at)
                              & (RelayMessage.id > cursor.id)))
        limit = limit * 3
    rows = list((await session.execute(base.order_by(
        RelayMessage.created_at.desc(), RelayMessage.id.desc()).limit(limit))).scalars())
    return list(reversed(rows))
