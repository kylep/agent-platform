"""Conversation turn logic, shared by the web API (`POST /conversations/{id}/
messages`) and the connector-ingest consumer. The platform owns conversation
history: each turn is a Run, and the next turn's prompt is built from prior
turns so the agent has context without the connector carrying any state.

Since Relay (docs/design/19) the turns themselves are `relay_messages` in the
DM channel — this module is the single-agent view of that room, and the pair
shape it hands `build_prompt` is a fold of those messages, not a second store."""
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from agentplatform.db import (ACTIVE_STATES, Conversation, RelayMessage,
                              RelayParticipant, Run, dm_key_of)
from agentplatform.materialize import materialize_run
from agentplatform.relay import (is_agent, mentionable_in, parse_mentions,
                                 participant_of, room_dispatch_mode)
from agentplatform.relay_store import (enabled_agents, explicit_members,
                                       outbound_for_message, post_relay_message,
                                       publish_relay_message)

log = logging.getLogger("conversation")

# Fallback text-replay budget (docs/design/14): the flattened history is bounded
# by estimated tokens, not an arbitrary turn count. Only used when session
# resume is unavailable; resume itself carries the full session. ~4 chars/token
# is close enough for a guardrail. (Session resume is the primary path — this
# keeps a degraded turn from replaying an unbounded transcript.)
_HISTORY_TOKEN_BUDGET = 30_000


def _est_tokens(user: str, reply: str) -> int:
    return (len(user) + len(reply)) // 4 + 1


def _fold(rows) -> list[tuple[str, str]]:
    """Messages → the (asked, answered) pairs `build_prompt` renders. A human
    or connector message opens a pair and the agent message after it closes
    one, so a room where two people spoke before the agent answered replays as
    two turns rather than collapsing into one."""
    out: list[tuple[str, str]] = []
    for row in rows:
        body = row.body or ""
        if is_agent(row.author):
            if out and not out[-1][1]:
                out[-1] = (out[-1][0], body)
            else:
                out.append(("", body))
        else:
            out.append((body, ""))
    return out


async def _run_history(session, conversation_id: str) -> list[tuple[str, str]]:
    """The pre-relay history: one pair per Run. Only reachable for a channel
    with no messages at all — a conversation that predates the backfill."""
    rows = (await session.execute(
        select(Run).where(Run.conversation_id == conversation_id)
        .order_by(Run.created_at))).scalars().all()
    return [(r.user_message or "", r.result or "") for r in rows
            if r.user_message or r.result]


async def _history(session, conversation_id: str) -> list[tuple[str, str]]:
    rows = (await session.execute(
        select(RelayMessage).where(RelayMessage.channel_id == conversation_id,
                                   RelayMessage.deleted_at.is_(None),
                                   RelayMessage.kind == "text")
        .order_by(RelayMessage.created_at, RelayMessage.id))).scalars().all()
    out = _fold(rows) if rows else await _run_history(session, conversation_id)
    # Keep the newest turns that fit the budget (oldest dropped first); always
    # keep at least the newest turn even if it alone exceeds the budget.
    kept, budget = [], _HISTORY_TOKEN_BUDGET
    for user, reply in reversed(out):
        cost = _est_tokens(user, reply)
        if kept and cost > budget:
            break
        kept.append((user, reply))
        budget -= cost
    kept.reverse()
    return kept


def build_prompt(history: list[tuple[str, str]], message: str) -> str:
    """Render the conversation into a single prompt for a fresh (stateless) run."""
    if not history:
        return message
    lines = ["You are continuing an ongoing conversation. Here is the history "
             "so far (oldest first):", ""]
    for user, reply in history:
        if user:
            lines.append(f"User: {user}")
        if reply:
            lines.append(f"Assistant: {reply}")
    lines += ["", f"User: {message}", "",
              "Respond to the latest user message, using the history for context."]
    return "\n".join(lines)


def author_of(requested_by: str) -> str:
    """The participant behind a turn. A connector turn records
    `connector:<name>:<user>`, and that external user IS the identity in the
    room — there is no platform principal behind it. Everything else is a
    principal (an admin, an operator key)."""
    if requested_by.startswith("connector:"):
        network, _, user = requested_by[len("connector:"):].partition(":")
        if network and user:
            try:
                return participant_of(connector=network, external_user=user)
            except ValueError:
                pass
    return participant_of(principal=requested_by)


async def _ensure_dm(session, conv: Conversation, author: str) -> set[str]:
    """Give the DM the participant rows and the key Relay identifies it by, and
    return its membership. A conversation created through the legacy endpoint
    (or before design/19) has neither, and without them the room is invisible to
    every membership and mention rule the messenger applies."""
    explicit = await explicit_members(session, conv.id)
    pair = [author] + ([f"agent:{conv.agent}"] if conv.agent else [])
    for participant in pair:
        if participant not in explicit:
            session.add(RelayParticipant(channel_id=conv.id, participant=participant))
            explicit.add(participant)
    if conv.dm_key is None and len(pair) == 2:
        key = dm_key_of(pair)
        # A legacy row whose pair another DM already claimed stays keyless,
        # exactly as the backfill leaves it: the unique index is the arbiter,
        # and a turn must not die on data that is merely untidy.
        taken = (await session.execute(select(Conversation.id).where(
            Conversation.dm_key == key, Conversation.id != conv.id))).first()
        if taken is None:
            conv.dm_key = key
    return explicit


async def _post_turn(session, conv: Conversation, author: str, message: str):
    """The human's half of a turn: the room's membership brought up to date, and
    the message itself. One function because a lost race has to re-run both — the
    membership read is what went stale."""
    explicit = await _ensure_dm(session, conv, author)
    return await post_relay_message(
        session, conv, author=author, body=message,
        mentions=parse_mentions(message,
                                mentionable_in(conv, await enabled_agents(session), explicit),
                                author))


async def continue_conversation(session_factory, producer, conversation_id: str,
                                message: str, requested_by: str) -> str | None:
    """Add a turn: post `message` to the DM channel, build the prompt from its
    history and materialize a run tagged with the conversation. Returns the run
    id, or None if the conversation is missing/closed/not a dm, or already has a
    turn in flight."""
    async with session_factory() as s:
        conv = await s.get(Conversation, conversation_id)
        if conv is None or conv.status != "active":
            return None
        # A channel or group is the router's to answer (docs/design/19): there
        # is no single agent to hand the turn to, and mentions decide who
        # speaks. This path stays the DM path.
        if conv.kind != "dm" or room_dispatch_mode(conv) != "facade":
            return None
        # Serialize turns: don't start a new one while a run is still active.
        active = (await s.execute(select(Run).where(
            Run.conversation_id == conversation_id,
            Run.state.in_(ACTIVE_STATES)))).first()
        if active is not None:
            return None
        history = await _history(s, conversation_id)
        agent = conv.agent
        author = author_of(requested_by)
        try:
            msg = await _post_turn(s, conv, author, message)
            await s.commit()
        except IntegrityError:
            # Two first turns raced into a DM that had no participant rows yet:
            # both staged the same (channel, participant) key and one lost. The
            # winner's rows are committed, so re-reading is all it takes — the
            # message itself is the caller's, and dropping it would be a turn
            # the user typed and never saw.
            await s.rollback()
            conv = await s.get(Conversation, conversation_id)
            if conv is None:
                return None
            try:
                msg = await _post_turn(s, conv, author, message)
                await s.commit()
            except IntegrityError:
                await s.rollback()
                log.warning("conversation %s: turn lost a second race; the caller "
                            "is told to retry", conversation_id)
                return None
        # The bridge's copy of the human's turn, resolved before the session
        # closes: a DM bound to Discord shows both halves of the conversation
        # there, and the loop guard drops the copy when the message ARRIVED
        # from that bridge in the first place (docs/design/19 T10).
        outbound = await outbound_for_message(s, conv, msg)
        message_id = msg.id
    await publish_relay_message(producer, conv, msg, outbound=outbound)

    run_id = uuid.uuid4().hex
    await materialize_run(session_factory, producer, {
        "run_id": run_id, "agent": agent, "prompt": build_prompt(history, message),
        "trigger": "conversation", "requested_by": requested_by,
        # The external user who spoke IS the root principal of this chain.
        "initiated_by": requested_by,
        "conversation_id": conversation_id, "user_message": message,
        "trigger_message_id": message_id,
    })
    return run_id
