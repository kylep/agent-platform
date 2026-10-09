"""Room watchers (docs/design/41).

A room in `watchers` dispatch mode has an ordered list of agents. An
unaddressed human post opens a watch round (or joins the open one); each
watcher then takes one turn, in order, and either answers publicly through its
own identity or declines with `NO_REPLY`.

The round/turn rows are the state machine AND the record of why a watcher did
or did not answer. Events (a post, a run ending, a delivery receipt) only make
the reconciler run sooner; `advance_open_rounds` is the source of truth, so a
lost event or a restart can delay a round but never strand it.
"""
from __future__ import annotations

import logging
import uuid
from datetime import timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from agentplatform.db import (
    ACTIVE_STATES,
    ChatIdentity,
    Conversation,
    RelayMessage,
    RoomWatcher,
    Run,
    WatchRound,
    WatchTurn,
    utcnow,
)
from agentplatform.relay import WATCH_DECLINE
from agentplatform.scheduler import as_utc

log = logging.getLogger(__name__)

MAX_WATCHERS = 5
WATCHERS_MODE = "watchers"
# A delivery that is still in one of these has not resolved yet.
OPEN_DELIVERY = ("pending", "claimed")
# A succeeded run's final rides a different topic than its terminal state; give
# the recorder this long to queue the delivery before calling it undelivered.
FINAL_GRACE = timedelta(seconds=30)


class WatcherConfigError(ValueError):
    pass


def is_decline(text: str | None) -> bool:
    """`NO_REPLY` as models actually write it: in backticks, with a full stop,
    any case, surrounded by whitespace. Nothing else counts."""
    core = (text or "").strip().strip("`*_\"'.! \t\n")
    return core.upper() == WATCH_DECLINE


async def room_endpoint(s, conv):
    """The external channel endpoint mirrored into this room, or None."""
    from agentplatform.external_chat import ExternalEndpoint
    return (await s.execute(select(ExternalEndpoint).where(
        ExternalEndpoint.channel_id == conv.id).limit(1))).scalar_one_or_none()


async def watch_identity(s, agent: str, ep, *, send: bool = False):
    """The agent's own identity that can read (and optionally send in) the
    room, or ExternalChatError."""
    from agentplatform.external_chat import (
        ExternalChatError,
        endpoint_access,
        owned_identity,
    )
    rows = (await s.execute(select(ChatIdentity).where(
        ChatIdentity.owner_agent == agent, ChatIdentity.connector == ep.provider,
        ChatIdentity.status == "active"))).scalars().all()
    for row in rows:
        try:
            account = await owned_identity(s, row.id, agent)
            await endpoint_access(s, account, ep.external_ref, send=send)
            return account
        except ExternalChatError:
            continue
    raise ExternalChatError(f"{agent} has no identity that can "
                            f"{'send in' if send else 'read'} this room")


async def list_watchers(s, channel_id: str) -> list[str]:
    return list((await s.execute(select(RoomWatcher.agent).where(
        RoomWatcher.channel_id == channel_id).order_by(RoomWatcher.position))).scalars())


async def set_watchers(s, conv, agents: list[str], *, by: str) -> list[str]:
    """Replace the room's watchers atomically. Returns warnings (soft checks).

    Hard checks: a channel (not a DM or thread), an external room with a
    mirrored endpoint, at most MAX_WATCHERS distinct agents, each able to read
    the room through an identity it owns. `can_send` is only warned about:
    leases refresh, and it is re-checked when the turn starts."""
    from agentplatform.external_chat import ExternalChatError
    agents = [a.strip() for a in agents if a and a.strip()]
    if len(set(agents)) != len(agents):
        raise WatcherConfigError("an agent may watch a room only once")
    if len(agents) > MAX_WATCHERS:
        raise WatcherConfigError(f"at most {MAX_WATCHERS} watchers per room")
    warnings: list[str] = []
    if agents:
        if conv.home != "external" or conv.kind != "channel":
            raise WatcherConfigError("only external channels (not DMs or threads) can be watched")
        ep = await room_endpoint(s, conv)
        if ep is None or ep.kind != "channel":
            raise WatcherConfigError("this room has no mirrored channel endpoint")
        for agent in agents:
            try:
                await watch_identity(s, agent, ep)
            except ExternalChatError as exc:
                raise WatcherConfigError(str(exc)) from exc
            try:
                await watch_identity(s, agent, ep, send=True)
            except ExternalChatError:
                warnings.append(f"{agent} cannot currently send in this room")
    await s.execute(delete(RoomWatcher).where(RoomWatcher.channel_id == conv.id))
    await s.flush()
    for position, agent in enumerate(agents):
        s.add(RoomWatcher(channel_id=conv.id, agent=agent, position=position, created_by=by))
    conv.dispatch_mode = WATCHERS_MODE if agents else "mentions"
    await s.flush()
    return warnings


def is_candidate(conv, observation) -> bool:
    """Message-level facts only, so every bot's observation agrees: a human post
    in a watched channel that addresses no bot at all (no @mention, no reply-ping
    — the connector reports both in `mentioned_bot_ids`)."""
    return (getattr(conv, "dispatch_mode", None) == WATCHERS_MODE and conv.kind == "channel"
            and observation is not None and not observation.addressed
            and not observation.author_bot and not (observation.mentioned_bot_ids or []))


async def observe(s, conv, msg) -> str | None:
    """Open a round for this post, or fold it into the room's open round.
    Idempotent: three connectors reporting one post open exactly one round."""
    open_round = (await s.execute(select(WatchRound).where(
        WatchRound.channel_id == conv.id, WatchRound.state == "open").limit(1))).scalar_one_or_none()
    if open_round is not None:
        if open_round.last_message_id != msg.id:
            last = await s.get(RelayMessage, open_round.last_message_id)
            if last is None or (as_utc(msg.created_at), msg.id) > (as_utc(last.created_at), last.id):
                open_round.last_message_id = msg.id
        return open_round.id
    watchers = await list_watchers(s, conv.id)
    if not watchers:
        return None
    ep = await room_endpoint(s, conv)
    round_ = WatchRound(channel_id=conv.id, anchor_message_id=msg.id, last_message_id=msg.id)
    try:
        async with s.begin_nested():
            s.add(round_)
            await s.flush()
    except IntegrityError:
        existing = (await s.execute(select(WatchRound).where(
            WatchRound.anchor_message_id == msg.id))).scalar_one_or_none()
        return existing.id if existing else None
    from agentplatform.external_chat import ExternalChatError
    for position, agent in enumerate(watchers):
        turn = WatchTurn(round_id=round_.id, position=position, agent=agent, identity_id="")
        try:
            turn.identity_id = (await watch_identity(s, agent, ep)).id if ep else ""
            if not ep:
                raise ExternalChatError("room has no endpoint")
        except ExternalChatError as exc:
            turn.state, turn.outcome, turn.reason = "done", "skipped_access", str(exc)[:256]
            turn.finished_at = utcnow()
        s.add(turn)
    await s.flush()
    return round_.id


async def running_turn_for_run(s, run_id: str | None):
    if not run_id:
        return None
    return (await s.execute(select(WatchTurn).where(
        WatchTurn.run_id == run_id, WatchTurn.state == "running").limit(1))).scalar_one_or_none()


async def record_decline(s, run, text: str | None) -> bool:
    """Called by the recorder before delivering a final. True means the final
    was a decline on a watch turn: deliver nothing. Bound to this run's own
    turn, so nothing a room says can suppress a different watcher."""
    turn = await running_turn_for_run(s, run.id)
    if turn is None or not is_decline(text):
        return False
    turn.outcome, turn.reason = "declined", "watcher answered NO_REPLY"
    return True


def _finish(turn, outcome: str, reason: str = "") -> None:
    turn.state, turn.outcome, turn.reason = "done", outcome, reason[:256]
    turn.finished_at = utcnow()


async def _observation_for(s, turn, round_):
    """This watcher's own observation of the round's newest post it has seen."""
    from agentplatform.external_chat import ExternalObservation
    rows = (await s.execute(select(ExternalObservation, RelayMessage).join(
        RelayMessage, RelayMessage.id == ExternalObservation.message_id).where(
        ExternalObservation.identity_id == turn.identity_id,
        RelayMessage.channel_id == round_.channel_id,
        RelayMessage.created_at >= (await s.get(RelayMessage, round_.anchor_message_id)).created_at,
        ExternalObservation.addressed.is_(False), ExternalObservation.author_bot.is_(False))
        .order_by(RelayMessage.created_at.desc(), RelayMessage.id.desc()).limit(1))).first()
    return (rows[0], rows[1]) if rows else (None, None)


async def _settle_running(s, turn, settings) -> bool:
    """Close a running turn if it is over. True if it is now done."""
    from agentplatform.external_chat import ExternalDelivery
    run = await s.get(Run, turn.run_id) if turn.run_id else None
    if run is None:
        _finish(turn, "failed", "run row missing")
        return True
    if run.state in ACTIVE_STATES:
        return False
    if turn.outcome == "declined":
        _finish(turn, "declined", turn.reason or "watcher answered NO_REPLY")
        return True
    deliveries = list((await s.execute(select(ExternalDelivery).where(
        ExternalDelivery.run_id == run.id).order_by(ExternalDelivery.created_at))).scalars())
    now = utcnow()
    if not deliveries:
        ended = as_utc(run.finished_at) or now
        if run.state == "succeeded" and run.reply_published_at is None and now - ended < FINAL_GRACE:
            return False
        _finish(turn, "failed" if run.state != "succeeded" else "delivery_failed",
                f"run {run.state}; nothing was delivered")
        return True
    last = deliveries[-1]
    turn.delivery_id = last.id
    pending = [d for d in deliveries if d.state in OPEN_DELIVERY]
    if pending:
        if now - as_utc(pending[0].created_at) < timedelta(seconds=settings.relay_watch_delivery_seconds):
            return False
        _finish(turn, "delivery_failed", f"delivery still {pending[0].state} after "
                f"{settings.relay_watch_delivery_seconds}s")
        return True
    if any(d.state == "accepted" for d in deliveries):
        _finish(turn, "answered")
    else:
        _finish(turn, "delivery_failed", f"delivery {last.state}: {(last.error or '')[:200]}")
    return True


async def _budget_left(s, channel_id: str, settings) -> bool:
    cutoff = utcnow() - timedelta(hours=1)
    used = await s.scalar(select(func.count()).select_from(WatchTurn).join(
        WatchRound, WatchRound.id == WatchTurn.round_id).where(
        WatchRound.channel_id == channel_id, WatchTurn.run_id.is_not(None),
        WatchTurn.started_at >= cutoff))
    return (used or 0) < settings.relay_watch_turns_per_room_hour


async def advance_round(router, round_id: str) -> None:
    """Move one round forward as far as it can go right now."""
    from agentplatform.external_chat import ExternalChatError
    from agentplatform.relay_store import enabled_agents, explicit_members
    settings = router.settings
    spec = invocation = None
    async with router.sf() as s:
        round_ = await s.get(WatchRound, round_id)
        if round_ is None or round_.state != "open":
            return
        conv = await s.get(Conversation, round_.channel_id)
        turns = list((await s.execute(select(WatchTurn).where(
            WatchTurn.round_id == round_id).order_by(WatchTurn.position))).scalars())
        for turn in turns:
            if turn.state == "done":
                continue
            if turn.state == "running":
                if not await _settle_running(s, turn, settings):
                    break
                continue
            # pending, and every earlier turn is done
            configured = await s.get(RoomWatcher, (round_.channel_id, turn.agent))
            if conv is None or conv.dispatch_mode != WATCHERS_MODE or configured is None:
                _finish(turn, "skipped_removed", "no longer a watcher of this room")
                continue
            ep = await room_endpoint(s, conv)
            try:
                account = await watch_identity(s, turn.agent, ep, send=True)
            except ExternalChatError as exc:
                _finish(turn, "skipped_access", str(exc))
                continue
            observation, trigger = await _observation_for(s, turn, round_)
            if observation is None:
                last = await s.get(RelayMessage, round_.last_message_id)
                waited = utcnow() - as_utc(last.created_at if last else round_.created_at)
                if waited < timedelta(seconds=settings.relay_watch_observation_seconds):
                    break
                _finish(turn, "skipped_no_observation",
                        f"{turn.agent}'s connector did not report the post within "
                        f"{settings.relay_watch_observation_seconds}s")
                continue
            if not await _budget_left(s, round_.channel_id, settings):
                _finish(turn, "skipped_budget",
                        f"room watch budget ({settings.relay_watch_turns_per_room_hour}/hour) spent")
                continue
            run_id = uuid.uuid4().hex
            turn.identity_id, turn.observation_id = account.id, observation.id
            turn.state, turn.run_id, turn.started_at = "running", run_id, utcnow()
            enabled = await enabled_agents(s)
            enabled.intersection_update(router._live_agents())
            spec = await router._spec(s, conv, trigger, turn.agent, 0, run_id, wake=None,
                                      enabled=enabled, explicit=await explicit_members(s, conv.id),
                                      observation=observation,
                                      watch=(turn.position + 1, len(turns)))
            invocation = await router._record(s, channel_id=conv.id, message_id=trigger.id,
                                              agent=turn.agent, decision="invoked",
                                              reason="watch", run_id=run_id, hop=0)
            break
        else:
            round_.state, round_.closed_at = "done", utcnow()
        await s.commit()
    if spec is not None:
        from agentplatform.materialize import materialize_run
        await materialize_run(router.sf, router.producer, spec)
        await router._publish(invocation)


async def advance_open_rounds(router) -> None:
    async with router.sf() as s:
        ids = list((await s.execute(select(WatchRound.id).where(
            WatchRound.state == "open").order_by(WatchRound.created_at))).scalars())
    for round_id in ids:
        try:
            await advance_round(router, round_id)
        except Exception:
            log.exception("watch round %s failed to advance", round_id)


async def round_for_run(s, run_id: str) -> str | None:
    return (await s.execute(select(WatchTurn.round_id).where(
        WatchTurn.run_id == run_id).limit(1))).scalar_one_or_none()


async def recent_turns(s, channel_id: str, limit: int = 20) -> list[dict]:
    rounds = list((await s.execute(select(WatchRound).where(
        WatchRound.channel_id == channel_id).order_by(WatchRound.created_at.desc())
        .limit(limit))).scalars())
    out = []
    for r in rounds:
        turns = (await s.execute(select(WatchTurn).where(WatchTurn.round_id == r.id)
                 .order_by(WatchTurn.position))).scalars()
        out.append({"round_id": r.id, "state": r.state, "anchor_message_id": r.anchor_message_id,
                    "last_message_id": r.last_message_id, "created_at": r.created_at.isoformat(),
                    "closed_at": r.closed_at.isoformat() if r.closed_at else None,
                    "turns": [{"position": t.position, "agent": t.agent, "state": t.state,
                               "outcome": t.outcome, "reason": t.reason, "run_id": t.run_id,
                               "delivery_id": t.delivery_id,
                               "started_at": t.started_at.isoformat() if t.started_at else None,
                               "finished_at": t.finished_at.isoformat() if t.finished_at else None}
                              for t in turns]})
    return out
