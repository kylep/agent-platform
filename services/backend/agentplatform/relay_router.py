"""The router (docs/design/19): the half of Relay that decides who a message
summons, and the only thing standing between a room full of agents and a loop.

Every reply an agent posts is another message, and every message can address
someone — so this module is mostly guards. A hop counter bounds a chain, two
hourly budgets bound the spend, a cooldown folds a flurry of mentions into one
follow-up, and a membership re-check keeps a mention from being a way into a
room. They are all here, in one pass over one message, because they only make
sense together: a busy agent at the hop limit and an over-budget hour that
arrives while a wake is pending have to resolve to exactly one answer.

Every decision, including every refusal, becomes a `relay_invocations` row and
a `relay.invocations` event. "Why did nothing happen when I mentioned it?" is
the question this block will be asked most often, and the answer must not be
"read the dispatcher's logs".

The decision and the RUN are not one transaction. The invocation rows, the
wakes and the notices commit together in this module's session; each run is
then created by `materialize_run` in a session of its own, because that is the
single place a Run is created platform-wide and it owns its own commit. So the
failure window is: an invocation row that says `invoked` with a run_id, and no
Run behind it (the handler raised, and `consume_forever` dead-lettered the
message). That is deliberately the visible direction — the row names the run
that is missing — rather than a run nothing explains."""
import logging
import uuid
from datetime import timedelta

from aiokafka import AIOKafkaConsumer
from sqlalchemy import func, or_, select

from agentplatform.db import (ACTIVE_STATES, Conversation, RelayInvocation,
                              RelayMessage, RelayWake, Run, Ticket, TicketEvent,
                              utcnow)
from agentplatform.events import (TOPIC_RELAY_INVOCATIONS, TOPIC_RELAY_MESSAGES,
                                  TOPIC_RUN_EVENTS, consume_forever)
from agentplatform.materialize import materialize_run
from agentplatform.relay import (AGENT_PREFIX, ALL, SYSTEM_AUTHOR, USER_PREFIX,
                                 agent_name, build_mention_prompt, is_agent,
                                 is_member, is_open_channel, mentionable_in,
                                 parse_mentions)
from agentplatform.relay_store import (context_window, explicit_members, faces_for,
                                       outbound_for_message, post_relay_message,
                                       publish_relay_message)
from agentplatform.tickets import CLOSED_STATES
from agentplatform.wiki_store import search_for_prompt

log = logging.getLogger("relay_router")

CONSUMER_GROUP = "relay-router"

# Two topics, one loop. `relay.messages` is the router's work; `run.events` is
# its backstop — the terminal state of a run is the one signal that an agent is
# free that always arrives, even when the reply that usually carries the news
# is lost, deleted, or never written (see `on_run_terminal`).
TOPICS = (TOPIC_RELAY_MESSAGES, TOPIC_RUN_EVENTS)

# The two things the platform says in a room on its own behalf. Constants
# because they are also how the router recognises that it has already said it:
# a pause notice repeated once per suppressed mention is the noise the guard
# was supposed to prevent.
HOP_LIMIT_BODY = "🛑 paused: hop limit reached — a human can @mention to continue"
BUDGET_PREFIX = "⏸️ paused: this room has used its hourly agent budget"


def budget_body(limit: int) -> str:
    return f"{BUDGET_PREFIX} ({limit}/hour); try again later"


# How much of a ticket travels with the summons (docs/design/20). Five events
# is the hand-off — who opened it, who moved it, what they said — without
# replaying a month of an old ticket's history; ten queue lines is a glance at
# the board, which is all `<your-tickets>` is for.
TICKET_EVENTS = 5
YOUR_TICKETS = 10

# How much of the room's talk the wiki search reads (docs/design/21). The
# summons plus its thread, truncated: a thread can be a day long, and the query
# only needs to know what the conversation is ABOUT.
WIKI_MATCH_CHARS = 2000


class RelayRouter:
    """Consumes `relay.messages` and turns mentions into runs — plus
    `run.events`, where a run ending is the backstop that releases a wake its
    agent's reply did not. One instance per dispatcher process, next to
    `ConversationIngestor`."""

    def __init__(self, settings, session_factory, producer, agent_store):
        self.settings = settings
        self.sf = session_factory
        self.producer = producer
        self.agents = agent_store

    async def run_forever(self) -> None:
        consumer = AIOKafkaConsumer(
            *TOPICS, bootstrap_servers=self.settings.kafka_bootstrap,
            group_id=CONSUMER_GROUP, enable_auto_commit=False,
            # `latest`, emphatically NOT `earliest`: the router's backlog is a
            # day of mentions that were already answered. Replaying it after a
            # restart would summon everyone a second time, for messages whose
            # conversations are long over.
            auto_offset_reset="latest")
        await consumer.start()
        try:
            await consume_forever(consumer, self.producer, self._on_message)
        finally:
            await consumer.stop()

    async def _on_message(self, msg, data: dict) -> None:
        if msg.topic == TOPIC_RELAY_MESSAGES:
            await self.handle(data)
            return
        # `run.events` carries every state of every run on the platform, and
        # only the last one frees an agent. The state is taken from the EVENT
        # rather than re-read from the row: the recorder is the one that writes
        # that row and it reads the same topic, so the row may not have caught
        # up yet — and a wake dropped on that race is a room gone quiet.
        state = (data or {}).get("state")
        if state and state not in ACTIVE_STATES and data.get("run_id"):
            await self.on_run_terminal(data["run_id"])

    async def handle(self, data: dict) -> None:
        """Route one message. The whole decision is made inside a single
        session and committed before anything leaves the process, so a room
        can never show a run that no invocation row explains."""
        # Only a person or an agent can address someone, so `text` is the only
        # kind that summons — and since the notices below are themselves
        # messages, anything else would be a loop of the router's own making.
        # A `system` message is still routed, for one reason: the recorder's
        # "😵 couldn't answer" notice is what a FAILED run posts instead of a
        # reply, and the agent behind it may be holding a wake. Without this a
        # run that died would keep a backlog hostage until someone noticed.
        if (data or {}).get("kind") not in ("text", "system"):
            return
        async with self.sf() as s:
            conv = await s.get(Conversation, (data.get("channel_id") or ""))
            # The row, not the payload: the event is a view of a message that
            # may since have been edited or deleted, and what the router acts
            # on has to be what the room currently holds.
            msg = await s.get(RelayMessage, (data.get("id") or ""))
            if conv is None or msg is None or msg.deleted_at is not None:
                return
            enabled = self._live_agents()
            explicit = await explicit_members(s, conv.id)
            summons = await self._summons(s, conv, msg, enabled, explicit)
            if not summons:
                return
            decided, specs, mirrored = await self._decide(
                s, conv, msg, summons, enabled, explicit)
        await self._emit(conv, decided, specs, mirrored)

    async def _decide(self, s, conv, msg, summons, enabled, explicit):
        """Run every summons through the guards, commit the decisions, and hand
        back what still has to leave the process.

        Split out of `handle` because `on_run_terminal` summons an agent too,
        with no message of its own to route: one decision loop means the wake a
        reply fires and the wake a terminal state fires are the same wake,
        recorded the same way, rather than two implementations that drift."""
        decided: list[dict] = []
        notices: list[RelayMessage] = []
        specs: list[dict] = []
        channel_used, global_used = await self._spend(s, conv.id)
        paused_limit, said_hop = None, False
        for agent, mention, wake, kind in summons:
            hop = (mention.hop or 0) if is_agent(mention.author) else 0
            run_id, limit = None, None
            if not is_member(conv, AGENT_PREFIX + agent, enabled, explicit):
                decision, reason = "suppressed", "not_member"
            elif kind == "mention" and self._facade_owns(conv, msg, agent):
                # Recorded BEFORE the "already answered" skip below: it is
                # true of the room rather than of a particular run, so it
                # is the same answer whether the facade's run exists yet or
                # not — and it is the answer to "why did the router ignore
                # my DM?", which silence would not be.
                decision, reason = "suppressed", "facade_owns_turn"
            # A mention that already has its run is answered, and a second
            # decision about it would be noise: Kafka is at-least-once.
            elif await self._answered(s, conv.id, agent, mention.id):
                continue
            elif self._out_of_hops(mention, hop):
                decision, reason = "suppressed", "hop_limit"
                said_hop = True
            elif (limit := self._exhausted(channel_used, global_used)) is not None:
                decision, reason = "suppressed", "budget"
                paused_limit = limit
            elif kind == "mention" and await self._occupied(s, conv, msg, agent):
                decision, reason = "suppressed", "coalesced"
                await self._coalesce(s, conv.id, agent, wake, msg)
            else:
                decision, reason = "invoked", kind
                run_id = uuid.uuid4().hex
                channel_used, global_used = channel_used + 1, global_used + 1
                specs.append(await self._spec(s, conv, mention, agent, hop, run_id,
                                              wake=wake, enabled=enabled,
                                              explicit=explicit))
            # A wake is consumed by being acted on. Fired or refused, it
            # must not survive: an agent that reports for duty on every
            # subsequent reply is the coalescing bug in reverse. The
            # exception is `budget`, which is an answer about the HOUR and
            # not about this backlog — deleting the wake there would
            # silently discard messages nobody has read, and the hour is
            # over in minutes.
            #
            # This delete is also what makes a wake fire exactly ONCE when two
            # things race for it: an agent's reply and its run's terminal state
            # both free that agent, they ride different topics, and either can
            # arrive first. The guarantee is sequential, not concurrent — one
            # `consume_forever` loop over both topics, one router per
            # dispatcher, one dispatcher replica (charts/dispatcher.yaml) — so
            # the second arrival reads the row after the first deleted it and
            # summons nobody. It does NOT survive a second router: two sessions
            # holding the same wake would both commit and both materialize a
            # run, because a zero-row ORM DELETE on a table without a
            # `version_id_col` only warns (SQLAlchemy's `only_warn` path in
            # orm/persistence.py — verified, it is the UPDATE that raises
            # StaleDataError, not the DELETE). Scaling the dispatcher out means
            # claiming the wake with a conditional DELETE ... RETURNING first,
            # the way `recorder._claim_reply` claims the right to post a reply.
            if (wake is not None and reason != "budget"
                    and (kind == "wake" or decision == "invoked")):
                await s.delete(wake)
            decided.append(await self._record(
                s, channel_id=conv.id, message_id=mention.id, agent=agent,
                decision=decision, reason=reason, run_id=run_id, hop=hop))
        if said_hop:
            notices.append(await self._say_hop_limit(s, conv, msg))
        if paused_limit is not None:
            notices.append(await self._say_budget(s, conv, paused_limit))
        await s.commit()
        # The bridge's copy of each notice, resolved while the session is
        # still open (docs/design/19 T10): a room mirrored into Discord is
        # owed the reason it went quiet just as much as the web pane is.
        mirrored = [(row, await outbound_for_message(s, conv, row))
                    for row in notices if row is not None]
        return decided, specs, mirrored

    async def _emit(self, conv, decided, specs, mirrored) -> None:
        """Committed first, published after: the row is the record, and a broker
        blip must cost the room its notice, never its decision."""
        for row, outbound in mirrored:
            await publish_relay_message(self.producer, conv, row, outbound=outbound)
        for spec in specs:
            await materialize_run(self.sf, self.producer, spec)
        for payload in decided:
            await self._publish(payload)

    async def on_run_terminal(self, run_id: str) -> None:
        """Fire the wake this run's agent was carrying, now that the run is over.

        The reply path in `_summons` is the usual one — an agent's answer is how
        the room learns its run ended. This is the backstop for the answer the
        router never sees: a lost `result` frame, a deleted message, a reply
        published by a process that crashed before the router read it. The
        terminal state always lands, so it is the one signal that cannot go
        missing, and firing from both is safe because a wake is deleted by
        being acted on — whichever arrives first consumes it.

        The caller has SEEN the terminal state; `run.state` is deliberately not
        re-read, because the recorder writes that row from the same topic and
        may not have got there yet. What matters instead is that this run does
        not count as its own agent's "busy" (`ignore_run_id`) — which is the
        whole bug this repairs."""
        async with self.sf() as s:
            run = await s.get(Run, run_id)
            if run is None or not run.conversation_id:
                return
            wake = await s.get(RelayWake, (run.conversation_id, run.agent))
            if wake is None:
                return
            conv = await s.get(Conversation, run.conversation_id)
            # The anchor is the oldest message the agent has not read, and the
            # follow-up is built from it. Gone (pruned, deleted), the wake is
            # left where it is: the next message in the room fires it with a
            # window of its own rather than the router inventing a turn.
            anchor = await s.get(RelayMessage, wake.since_message_id)
            if conv is None or anchor is None or anchor.deleted_at is not None:
                return
            if await self._busy(s, conv.id, run.agent, ignore_run_id=run_id):
                return
            decided, specs, mirrored = await self._decide(
                s, conv, anchor, [(run.agent, anchor, wake, "wake")],
                self._live_agents(), await explicit_members(s, conv.id))
        await self._emit(conv, decided, specs, mirrored)

    # --- who is being addressed ---------------------------------------------

    def _live_agents(self) -> set[str]:
        """The agents that can be summoned at all. A disabled agent is switched
        off and a quarantined one (`error` set — a row that no longer
        validates) is not understood well enough to run, and neither is a
        member of any room: `is_member` checks this set in both branches."""
        return {i.name for i in self.agents.list() if i.enabled and i.error is None}

    async def _summons(self, s, conv, msg, enabled, explicit):
        """Everyone this message addresses, as (agent, summoning message, wake,
        kind) — the mentions it carries, plus the pending wake its author's own
        reply releases."""
        out = []
        # A run ending is what frees its agent, and the room learns that from
        # the message the run left behind. If the agent was carrying a wake,
        # this is the moment it fires, answering from where it stopped reading.
        freed = await self._freed_by(s, msg)
        if freed is not None:
            wake = await s.get(RelayWake, (conv.id, freed))
            # `msg.run_id` is the run this message ENDS — an agent's own answer,
            # or the platform's notice for a run that died. Either way it is
            # that run's last word, so it does not make its own agent busy.
            if wake is not None and not await self._busy(s, conv.id, freed,
                                                         ignore_run_id=msg.run_id):
                anchor = await s.get(RelayMessage, wake.since_message_id)
                out.append((freed, anchor if anchor is not None else msg, wake, "wake"))
        if msg.kind != "text":
            return out
        for target in self._targets(conv, msg, enabled, explicit):
            # A target that is still carrying a wake and is free now gets its
            # backlog with this mention: the person asking again should not
            # have to wait for the agent's own next reply to unstick the room.
            out.append((target, msg, await s.get(RelayWake, (conv.id, target)), "mention"))
        return out

    async def _freed_by(self, s, msg) -> str | None:
        """The agent whose run this message marks the end of, if any.

        Usually that is simply its author: an agent's reply is posted as its
        run finishes. The other case is a run that FAILED — it leaves a system
        notice in the platform's own name instead of an answer — and the agent
        behind that notice is owed its pending wake just the same.

        A run's `run_id` is NOT enough on its own, which is the whole of this
        check. An agent posts mid-run too — through the `relay` tool, and
        through every ticket it touches (docs/design/20: the card, the
        assignment mention, a comment) — and those messages carry `run_id` for
        attribution. Read as "the run is over" they free an agent that is still
        executing, and a pending wake then starts a SECOND run of it alongside
        the first. So the test is `trigger_message_id`: the recorder stamps its
        reply with the run's own trigger (`recorder._post_reply`), and neither
        `api/relay.py` nor `ticket_store` ever sets it. A conversation turn is
        covered too — the facade materializes those runs WITH a trigger message
        (`conversation.continue_conversation`), so a DM reply still frees.
        Anything left over — a scheduled run with no triggering message, a
        reply that was lost — is freed by `run.events`, the backstop that
        always lands."""
        name = agent_name(msg.author)
        if name is not None:
            return name if (msg.run_id and msg.trigger_message_id) else None
        if msg.kind == "system" and msg.run_id:
            run = await s.get(Run, msg.run_id)
            return run.agent if run is not None else None
        return None

    def _facade_owns(self, conv, msg, agent: str) -> bool:
        """Whether the conversation facade is already answering this turn.

        A DM is one room seen from two sides (docs/design/19 T4): the
        `/api/conversations` path posts the human's message and materializes
        the reply run itself, and it PUBLISHES the message first — so the
        router can reach the mention before the run it would have deduplicated
        against exists. Left alone, "@news hi" in a DM with news gets two runs
        for one turn. Only a human's mention of the DM's own agent is the
        facade's; a wake's backlog is not a turn, and an agent-authored message
        never went through the facade at all."""
        return (conv.kind == "dm" and agent == conv.agent
                and not is_agent(msg.author))

    def _targets(self, conv, msg, enabled, explicit) -> list[str]:
        room = mentionable_in(conv, enabled, explicit)
        author = agent_name(msg.author)
        out: list[str] = []
        for token in parse_mentions(msg.body or "", room, msg.author):
            # ALL only ever comes from a human — `parse_mentions` drops an
            # agent's room mention, because an agent that can page everyone is
            # a storm. It expands to the room's agent roster: every enabled
            # agent in an open channel, the agent members of a closed one.
            for name in (self._room_roster(room) if token == ALL else [token]):
                if name != author and name not in out:
                    out.append(name)
        return out

    def _room_roster(self, room: set[str]) -> list[str]:
        """Who `@all` actually wakes: everyone in the room EXCEPT the platform's
        own agents.

        A system agent (the run summarizer, the health monitor) is
        infrastructure. It answers to its NAME, not to the room: `@health-monitor
        why?` still summons it, and every guard applies to that mention as
        usual. What it must not do is answer a question addressed to everybody —
        the 09:00 #standup would otherwise hand each of them a Claude run every
        morning to report work nobody asked them about, and a human's `@all` in
        any open channel would page them too.

        Filtered HERE and not in `_live_agents`, deliberately: that set is
        MEMBERSHIP (`is_member` reads it in both branches), and an agent removed
        from it stops being in the room at all — which would break the direct
        mention this is careful to keep."""
        return sorted(n for n in room if not self._is_system(n))

    def _is_system(self, name: str) -> bool:
        """Whether the agent's definition declares it platform-internal. A
        quarantined row has no manifest to ask, and it is not summonable anyway
        (`_live_agents` drops it), so the missing answer is simply `False`."""
        info = self.agents.get(name)
        return bool(info is not None and info.manifest is not None
                    and info.manifest.system)

    # --- the guards ----------------------------------------------------------

    def _out_of_hops(self, mention, hop: int) -> bool:
        """A run triggered by a message at hop h posts its reply at h+1, so a
        chain of agents answering each other counts up to the cap and stops. A
        human's message is hop 0 by construction, which is what lets a person
        restart a paused thread — and why only an agent-authored mention can be
        at the cap. `max_run_chain_depth` is the platform-wide fence the run
        chain already had; Relay must not be a way around it."""
        return ((is_agent(mention.author) and hop >= self.settings.relay_max_hops)
                or hop > self.settings.max_run_chain_depth)

    async def _spend(self, s, channel_id: str) -> tuple[int, int]:
        """Invocations in the last hour, per channel and platform-wide. Counted
        from the rows rather than an in-memory tally so a restart does not hand
        the room a fresh budget."""
        cutoff = utcnow() - timedelta(hours=1)
        counted = select(func.count()).select_from(RelayInvocation).where(
            RelayInvocation.decision == "invoked", RelayInvocation.created_at >= cutoff)
        return ((await s.scalar(counted.where(
                    RelayInvocation.channel_id == channel_id))) or 0,
                (await s.scalar(counted)) or 0)

    def _exhausted(self, channel_used: int, global_used: int) -> int | None:
        """The budget that is spent, or None. A human mention is subject to
        BOTH caps, not just the global one the design names: the channel cap is
        what stops a single runaway room, and "a person asked for it" is not a
        reason to let one room spend the whole platform's hour."""
        if channel_used >= self.settings.relay_channel_invocations_per_hour:
            return self.settings.relay_channel_invocations_per_hour
        if global_used >= self.settings.relay_global_invocations_per_hour:
            return self.settings.relay_global_invocations_per_hour
        return None

    async def _busy(self, s, channel_id: str, agent: str, *,
                    ignore_run_id: str | None = None) -> bool:
        """Whether the agent has a run in flight in this room.

        `ignore_run_id` is the run whose ENDING is being processed. Its state
        row lags: the reply rides `run.transcript` and the terminal state rides
        `run.events`, so at the moment a run's own answer reaches the router
        the row still says RUNNING. Counting it would mean an agent is
        permanently busy at exactly the moment it becomes free, which is how
        two agents introduced to each other both sat on a wake that never
        fired."""
        q = select(Run.id).where(
            Run.conversation_id == channel_id, Run.agent == agent,
            Run.state.in_(ACTIVE_STATES))
        if ignore_run_id:
            q = q.where(Run.id != ignore_run_id)
        return (await s.execute(q.limit(1))).first() is not None

    async def _occupied(self, s, conv, msg, agent: str) -> bool:
        """Whether this mention should become a wake rather than a run: the
        agent is already working in this room, or it spoke here moments ago and
        it is another AGENT pulling at it. The cooldown deliberately does not
        apply to people — a human's mention of a free agent always gets a run,
        because the human is the one who can tell it is repeating itself."""
        if await self._busy(s, conv.id, agent):
            return True
        if not is_agent(msg.author):
            return False
        cutoff = utcnow() - timedelta(seconds=self.settings.relay_agent_cooldown_seconds)
        return (await s.execute(select(RelayMessage.id).where(
            RelayMessage.channel_id == conv.id,
            RelayMessage.author == AGENT_PREFIX + agent,
            RelayMessage.deleted_at.is_(None),
            RelayMessage.created_at > cutoff).limit(1))).first() is not None

    async def _answered(self, s, channel_id: str, agent: str, message_id: str) -> bool:
        return (await s.execute(select(Run.id).where(
            Run.conversation_id == channel_id, Run.agent == agent,
            Run.trigger_message_id == message_id).limit(1))).first() is not None

    async def _coalesce(self, s, channel_id: str, agent: str, wake, msg) -> None:
        """One wake per (channel, agent), anchored at the FIRST message it
        missed: three mentions arriving during one run become one follow-up
        that answers all three, never three runs answering one each."""
        if wake is None:
            s.add(RelayWake(channel_id=channel_id, agent=agent, since_message_id=msg.id))
        else:
            wake.created_at = utcnow()
        await s.flush()

    # --- the run -------------------------------------------------------------

    async def _spec(self, s, conv, mention, agent: str, hop: int, run_id: str, *,
                    wake, enabled, explicit) -> dict:
        # A summons inside a thread is answered from the thread (docs/design/20):
        # "in a thread" is `thread_root` set and nothing cleverer, because the
        # one message that opens a ticket's thread is its card, and a card
        # mentions nobody — so a root message is never itself a summons into a
        # ticket, and a root that is not a ticket's is just the room talking.
        thread_root = mention.thread_root
        ticket = await self._ticket_of(s, thread_root)
        window = await context_window(
            s, conv.id, thread_root=thread_root,
            limit=(self.settings.tickets_thread_context_messages if thread_root
                   else self.settings.relay_context_messages),
            since_message_id=await self._resume_from(s, conv.id, wake))
        participants = self._roster(conv, explicit, enabled, window)
        names = {n for n in (agent_name(p) for p in participants) if n}
        names |= {n for n in (agent_name(m.author) for m in window) if n}
        prompt = build_mention_prompt(
            channel=conv, messages=window, mention=mention, agent=agent,
            hops_left=max(0, self.settings.relay_max_hops - hop - 1),
            participants=participants, faces=await faces_for(s, names),
            ticket=ticket,
            ticket_events=(await self._ticket_events(s, ticket)
                           if ticket is not None else ()),
            # Only when there is no ticket in hand: an agent summoned INTO a
            # ticket has been told which work this is, and appending its whole
            # queue underneath invites it to answer about a different one.
            your_tickets=(() if ticket is not None
                          else await self._your_tickets(s, agent)),
            wiki_pages=await self._wiki_pages(s, mention, window, thread_root))
        # prompt and user_message are the same text on purpose: for a relay run
        # the built context IS the turn, and the run page shows it as what the
        # agent was asked.
        return {"run_id": run_id, "agent": agent, "prompt": prompt,
                "user_message": prompt, "trigger": "mention",
                "requested_by": mention.author,
                "initiated_by": await self._initiated_by(s, mention),
                "parent_run_id": mention.run_id, "depth": hop,
                "conversation_id": conv.id, "trigger_message_id": mention.id,
                # What this run is WORK on, as opposed to what it is a reply to:
                # the ticket page lists it, and the board shows the agent
                # thinking on the card.
                "ticket_id": ticket.id if ticket is not None else None}

    async def _ticket_of(self, s, thread_root: str | None):
        """The ticket whose thread this summons sits in, or None.

        The card IS the thread root (docs/design/20), so the lookup is that one
        column — which is also why an assignment mention finds its ticket
        without carrying a key: it is a reply, and its root is the card."""
        if not thread_root:
            return None
        return (await s.execute(select(Ticket).where(
            Ticket.root_message_id == thread_root).limit(1))).scalars().first()

    async def _ticket_events(self, s, ticket) -> list:
        """The tail of the ticket's history, oldest first — read newest-first
        and reversed, so a long-running ticket hands the agent where the work
        got to rather than where it started."""
        rows = (await s.execute(select(TicketEvent)
                .where(TicketEvent.ticket_id == ticket.id)
                .order_by(TicketEvent.created_at.desc(), TicketEvent.id.desc())
                .limit(TICKET_EVENTS))).scalars().all()
        return list(reversed(rows))

    async def _your_tickets(self, s, agent: str) -> list:
        """The agent's own open work, most recently active first. Closed
        tickets are left out for the same reason the board's columns end: a
        queue is what is still owed, and a done ticket in it is an invitation
        to report work that was finished last week."""
        return list((await s.execute(select(Ticket)
                     .where(Ticket.assignee == AGENT_PREFIX + agent,
                            Ticket.state.not_in(CLOSED_STATES))
                     .order_by(Ticket.last_activity_at.desc(), Ticket.id.desc())
                     .limit(YOUR_TICKETS))).scalars())

    async def _wiki_pages(self, s, mention, window, thread_root) -> list[dict]:
        """The pages the room is talking about (docs/design/21), as the block
        renders them.

        The text matched is the summons and — only in a thread, where the
        window IS the thread — what it is a reply to: "any thoughts?" under a
        page's worth of discussion is about the discussion, and matching the
        four words of the mention alone would find nothing. In the room, the
        window is the last page of whatever everyone has been saying, which is
        not what this agent was asked."""
        text = mention.body or ""
        if thread_root:
            text = "\n".join([*(m.body or "" for m in window), text])
        pages = await search_for_prompt(s, text[-WIKI_MATCH_CHARS:],
                                        limit=self.settings.wiki_prompt_pages)
        return [{"slug": p.slug, "title": p.title, "summary": p.summary}
                for p in pages]

    async def _resume_from(self, s, channel_id: str, wake) -> str | None:
        """Where a woken agent starts reading again.

        `context_window` resumes AFTER its cursor, and a wake's anchor is the
        first message the agent has NOT seen — so the cursor is the message
        before it, or the window would open by skipping the very mention it is
        answering. None (no wake, the anchor opened the room, or it has since
        been pruned) falls back to the plain page, which contains the anchor
        anyway unless the room has moved a long way on."""
        if wake is None:
            return None
        anchor = await s.get(RelayMessage, wake.since_message_id)
        if anchor is None or anchor.channel_id != channel_id:
            return None
        return (await s.execute(select(RelayMessage.id).where(
            RelayMessage.channel_id == channel_id, RelayMessage.deleted_at.is_(None),
            or_(RelayMessage.created_at < anchor.created_at,
                (RelayMessage.created_at == anchor.created_at)
                & (RelayMessage.id < anchor.id)))
            .order_by(RelayMessage.created_at.desc(), RelayMessage.id.desc())
            .limit(1))).scalars().first()

    def _roster(self, conv, explicit, enabled, window) -> list[str]:
        """Who the summoned agent is told is in the room. An open channel has
        no participant rows — every enabled agent is in it by definition — so
        its roster is derived: the agents, plus the people who have actually
        spoken in the window, who are the only humans anyone can name."""
        people = set(explicit)
        if is_open_channel(conv):
            people |= {AGENT_PREFIX + name for name in enabled}
            people |= {m.author for m in window
                       if not is_agent(m.author) and not m.author.startswith("system:")}
        return sorted(people)

    async def _initiated_by(self, s, mention) -> str:
        """The principal at the root of the chain (docs/design/13). A person's
        own mention roots it in them; an agent's inherits whatever its own run
        was doing, so a Discord user's request stays theirs however many agents
        it passes through."""
        if not is_agent(mention.author):
            return (mention.author[len(USER_PREFIX):]
                    if mention.author.startswith(USER_PREFIX) else mention.author)
        run = await s.get(Run, mention.run_id) if mention.run_id else None
        # The triggering run is gone (pruned, or a message written by hand):
        # the chain still has to name someone, and the single-operator stub is
        # the honest answer rather than attributing it to the agent.
        return (run.initiated_by if run is not None and run.initiated_by else "admin")

    # --- what the room and the log are told ----------------------------------

    async def _record(self, s, **fields) -> dict:
        """One decision: the row and the event payload, from one place, so the
        dashboard's count and the topic's count can never disagree."""
        row = RelayInvocation(**fields)
        s.add(row)
        await s.flush()
        return {"id": row.id, "channel_id": row.channel_id,
                "message_id": row.message_id, "agent": row.agent,
                "decision": row.decision, "reason": row.reason, "run_id": row.run_id,
                "hop": row.hop,
                "created_at": row.created_at.isoformat() if row.created_at else None}

    async def _publish(self, payload: dict) -> None:
        if self.producer is None:
            return
        try:
            await self.producer.publish(TOPIC_RELAY_INVOCATIONS, payload["channel_id"],
                                        payload, type="relay.invocation")
        except Exception:
            log.warning("relay.invocations publish failed for %s/%s",
                        payload["message_id"], payload["agent"], exc_info=True)

    async def _say_hop_limit(self, s, conv, msg):
        """Say once, in the thread that stopped, that it stopped. Said again
        only after a human has spoken there: the notice is "paused", and a
        person speaking is what un-pauses it."""
        root = msg.thread_root or msg.id
        rows = (await s.execute(select(RelayMessage).where(
            RelayMessage.channel_id == conv.id, RelayMessage.deleted_at.is_(None),
            or_(RelayMessage.thread_root == root, RelayMessage.id == root))
            .order_by(RelayMessage.created_at.desc(),
                      RelayMessage.id.desc()))).scalars().all()
        for row in rows:
            if row.kind == "system" and row.body == HOP_LIMIT_BODY:
                return None
            if row.kind == "text" and not is_agent(row.author):
                break
        return await post_relay_message(s, conv, author=SYSTEM_AUTHOR,
                                        body=HOP_LIMIT_BODY, kind="system",
                                        reply_to=root)

    async def _say_budget(self, s, conv, limit: int):
        """Once per channel per hour, not once per suppressed mention: over
        budget, the room is being mentioned in a lot, and a notice per mention
        would be the flood the cap is there to stop."""
        cutoff = utcnow() - timedelta(hours=1)
        said = (await s.execute(select(RelayMessage.id).where(
            RelayMessage.channel_id == conv.id, RelayMessage.kind == "system",
            RelayMessage.body.like(BUDGET_PREFIX + "%"),
            RelayMessage.created_at >= cutoff).limit(1))).first()
        if said is not None:
            return None
        return await post_relay_message(s, conv, author=SYSTEM_AUTHOR,
                                        body=budget_body(limit), kind="system")
