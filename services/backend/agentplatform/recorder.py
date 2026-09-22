import logging
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from agentplatform.apikeys import revoke_run_keys
from agentplatform.db import (ACTIVE_STATES, Conversation, RelayMessage, Run,
                              RunModelUsage, RunState, SecretMeta, TranscriptEvent,
                              utcnow)
from agentplatform.events import (TOPIC_RUN_DLQ, TOPIC_RUN_EVENTS,
                                  TOPIC_RUN_TRANSCRIPT)
from agentplatform.relay import (SYSTEM_AUTHOR, mentionable_in, parse_mentions,
                                 participant_of, room_reply_mode)
from agentplatform.relay_store import (enabled_agents, explicit_members,
                                       outbound_for_message, post_relay_message,
                                       publish_relay_message)
from agentplatform.secrets import CLAUDE_CREDENTIAL, CODEX_CREDENTIAL

# How much of a run's error a reader needs to know what went wrong. The run
# page holds the rest.
ERROR_CHARS = 200

log = logging.getLogger("recorder")


class Recorder:
    def __init__(self, session_factory, producer=None, *, agent_store=None):
        self.sf = session_factory
        # Optional: publishes conversation.outbound when a conversation run ends,
        # and per-manifest result_topic events (agent output → app, design/11).
        self.producer = producer
        self.agent_store = agent_store

    async def _probe_credential(self, s, status: str, runtime: str = "claude") -> None:
        """Record the observed validity of the Claude credential. This is the
        token "probe": a run cannot reach `succeeded` without authenticating,
        and the CLI reports auth failures with error=authentication_failed, so
        real run outcomes tell us whether the stored token works."""
        name = CODEX_CREDENTIAL if runtime == "codex" else CLAUDE_CREDENTIAL
        meta = await s.get(SecretMeta, name) or SecretMeta(name=name)
        if meta.status != status:
            meta.status = status
            s.add(meta)

    async def handle(self, topic: str, key: str, value: dict) -> None:
        if topic == TOPIC_RUN_TRANSCRIPT:
            await self._handle_transcript(key, value)
        elif topic == TOPIC_RUN_EVENTS:
            await self._handle_state(key, value)
        elif topic == TOPIC_RUN_DLQ:
            await self._handle_dlq(key, value)

    async def _handle_transcript(self, run_id: str, value: dict) -> None:
        async with self.sf() as s:
            event = TranscriptEvent(run_id=run_id, seq=value["seq"], payload=value)
            s.add(event)
            try:
                await s.commit()
            except IntegrityError:
                await s.rollback()
                return

            run = await s.get(Run, run_id)
            if run is None:
                return
            # Tool calls surface as `tool_use` content blocks inside an
            # `assistant` stream-json frame (one block per invocation); the
            # top-level frame type is never `tool_use`, so count the blocks.
            if value.get("type") == "assistant":
                content = value.get("message", {}).get("content") or []
                run.tool_calls += sum(
                    1 for b in content
                    if isinstance(b, dict) and b.get("type") == "tool_use"
                )
            # The terminal `result` frame carries the final assistant reply and
            # the per-model token breakdown.
            result_event = None
            posted = None
            if value.get("type") == "result":
                if value.get("result"):
                    run.result = value.get("result")
                # The reply belongs to whichever consumer is holding the text,
                # and that is this one: `result` is in hand right here. Waiting
                # for the terminal state to publish it would be a cross-topic
                # read with no ordering guarantee (see `_claim_reply`).
                failed = value.get("is_error") is True
                if run.conversation_id and (run.result or failed):
                    state = (run.state if run.state not in ACTIVE_STATES else
                             (RunState.FAILED if failed else RunState.SUCCEEDED))
                    if await self._claim_reply(s, run_id):
                        posted = await self._post_reply(s, run, state, run.result or "",
                                                        failed=failed)
                for model, u in (value.get("modelUsage") or {}).items():
                    # merge = idempotent upsert on (run_id, model) for redelivery.
                    await s.merge(RunModelUsage(
                        run_id=run_id, model=model, agent=run.agent,
                        tokens_in=u.get("inputTokens", 0), tokens_out=u.get("outputTokens", 0),
                        tokens_cache_read=u.get("cacheReadInputTokens", 0),
                        tokens_cache_creation=u.get("cacheCreationInputTokens", 0)))
                # Blocked tool calls: a signal a least-privilege agent tried
                # something outside its allow-list (e.g. an injected agent).
                denials = value.get("permission_denials") or []
                if denials:
                    run.permission_denials = denials
                    tools = [d.get("tool_name") or d.get("tool") for d in denials
                             if isinstance(d, dict)]
                    log.warning("run %s (agent %s): %d permission denial(s): %s",
                                run_id, run.agent, len(denials), tools)
                # Manifest-declared result feed: a successful result is
                # published to the agent's `result_topic` so the consuming app
                # can act on it (docs/design/11 — e.g. the news app ingests
                # the gatherer's digest). Done here, on the result frame, so
                # the text is in hand with no cross-topic ordering dependency.
                if value.get("is_error") is not True and value.get("result"):
                    # Routing is part of the frozen invocation contract. An
                    # edit made while a run is active must not redirect that
                    # run's result. Old rows without a snapshot retain the
                    # pre-snapshot lookup as a compatibility fallback.
                    snapshot = run.definition_snapshot or {}
                    topic = snapshot.get("result_topic", "")
                    if not snapshot and self.agent_store is not None:
                        info = self.agent_store.get(run.agent)
                        topic = info.manifest.result_topic if info and info.manifest else ""
                    if topic:
                        result_event = (topic, {"run_id": run_id, "agent": run.agent,
                                                "result": value.get("result")})
            usage = value.get("usage", {})
            run.tokens_in += usage.get("input_tokens", 0)
            run.tokens_out += usage.get("output_tokens", 0)
            # `or 0`: pre-migration rows carry NULL until first written.
            run.tokens_cache_read = ((run.tokens_cache_read or 0)
                                     + usage.get("cache_read_input_tokens", 0))
            run.tokens_cache_creation = ((run.tokens_cache_creation or 0)
                                         + usage.get("cache_creation_input_tokens", 0))
            # A 401 / authentication_failed frame proves the stored token is
            # bad, regardless of how the run ultimately terminates.
            if value.get("error") == "authentication_failed" or value.get("error_status") == 401:
                await self._probe_credential(s, "invalid", value.get("runtime", "claude"))
            await s.commit()
        # Publish outside the DB session (see _handle_state's note).
        await self._publish_reply(posted)
        if result_event is not None and self.producer is not None:
            topic, payload = result_event
            await self.producer.publish(topic, payload["run_id"], payload,
                                        type="agent.result")

    async def _claim_reply(self, s, run_id: str) -> bool:
        """Claim the right to publish this run's conversation reply, returning
        True to the single winner.

        The reply text (`run.result`) rides the `run.transcript` topic while the
        terminal state rides `run.events`, and there is no ordering guarantee
        between two topics. So whichever consumer is holding what it needs tries
        to publish, and this conditional UPDATE — atomic in the database — makes
        sure the thread gets exactly one message."""
        res = await s.execute(
            update(Run)
            .where(Run.id == run_id, Run.reply_published_at.is_(None))
            .values(reply_published_at=utcnow())
            .returning(Run.id))
        return res.first() is not None

    async def _post_reply(self, s, run: Run, state: str, text: str, *,
                          failed: bool) -> tuple | None:
        """Write the run's answer into its channel (docs/design/19) and build
        the bridge payload that goes with it. Called by the single winner of
        `_claim_reply`, so the room gets exactly one of each.

        A failed run says so in the room rather than going quiet: silence is
        indistinguishable from an agent that is still thinking."""
        conv = await s.get(Conversation, run.conversation_id)
        if conv is None:
            return None
        trigger = (await s.get(RelayMessage, run.trigger_message_id)
                   if run.trigger_message_id else None)
        # The hop is what bounds agent-to-agent chatter, and only a mention is
        # part of such a chain — a human's turn restarts the count at 0.
        hop = trigger.hop + 1 if trigger is not None and run.trigger == "mention" else 0
        # Answer inside the triggering message's thread, so a room with several
        # conversations running keeps them apart. A DM IS one conversation, so
        # its answer goes top-level: threaded there it would sit behind a
        # "replies" chip the DM pane never shows (QA-16).
        reply_to = None
        if trigger is not None and room_reply_mode(conv) != "linear":
            reply_to = trigger.thread_root or trigger.id
        if failed:
            author, kind = SYSTEM_AUTHOR, "system"
            if state == RunState.SUCCEEDED:
                # The sweep, on a run that finished fine and whose `result`
                # frame never landed. It did the work, so the room must not be
                # told it failed — only that the words are gone.
                body = (f"😵 {run.agent} answered, but the reply was lost "
                        f"(run {run.id[:8]})")
            else:
                body = (f"😵 {run.agent} couldn't answer: "
                        f"{(run.error or state)[:ERROR_CHARS]}")
            # A notice about a failure must not summon anyone: the room is
            # already one broken run deep.
            mentions = []
        else:
            author, kind, body = participant_of(agent=run.agent), "text", text
            mentions = parse_mentions(
                body, mentionable_in(conv, await enabled_agents(s),
                                     await explicit_members(s, conv.id)), author)
        msg = await post_relay_message(
            s, conv, author=author, body=body, kind=kind, run_id=run.id, hop=hop,
            trigger_message_id=run.trigger_message_id, reply_to=reply_to,
            mentions=mentions)
        # A bridged room is shown the message, whatever it turned out to be: a
        # failed run's notice is what the room says, so it is what the bridge
        # relays — an empty message is worse than the reason it is empty.
        # `state` is passed because the Run row does not carry the terminal
        # state yet on the path that holds the result frame.
        return conv, msg, await outbound_for_message(s, conv, msg, state=state)

    async def _publish_reply(self, posted) -> None:
        """Announce a claimed reply: the message to Relay, the bridge payload to
        the connectors. Outside the DB session, since neither is worth holding a
        transaction open for."""
        if posted is None or self.producer is None:
            return
        conv, msg, outbound = posted
        await publish_relay_message(self.producer, conv, msg, outbound=outbound)

    async def reconcile_replies(self, grace_seconds: int) -> int:
        """Publish replies for finished conversation turns that never got one,
        and return how many were sent.

        The two publish sites each fire when they hold what they need, which
        covers every ordering — but not a `result` frame that never arrives at
        all. Without this, one lost frame leaves a Discord thread waiting
        forever. Runs are only considered once they've been finished for
        `grace_seconds`, so this never races the normal path."""
        cutoff = utcnow() - timedelta(seconds=grace_seconds)
        sent = 0
        async with self.sf() as s:
            rows = (await s.execute(
                select(Run).where(Run.conversation_id.is_not(None),
                                  Run.reply_published_at.is_(None),
                                  Run.state.not_in(ACTIVE_STATES),
                                  Run.finished_at.is_not(None),
                                  Run.finished_at < cutoff))).scalars().all()
            late = []
            for run in rows:
                if not await self._claim_reply(s, run.id):
                    continue
                posted = await self._post_reply(
                    s, run, run.state, run.result or f"(the run {run.state} without a reply)",
                    failed=run.result is None)
                if posted is not None:
                    late.append((run.id, posted))
            await s.commit()
        for run_id, posted in late:
            log.warning("run %s: publishing conversation reply late — no result "
                        "frame arrived within %ds", run_id, grace_seconds)
            await self._publish_reply(posted)
            sent += 1
        return sent

    async def _handle_state(self, run_id: str, value: dict) -> None:
        async with self.sf() as s:
            run = await s.get(Run, run_id)
            if run is None:
                return
            terminal = run.state not in ACTIVE_STATES
            if terminal:
                return
            new_state = value.get("state")
            run.state = new_state
            new_terminal = new_state not in ACTIVE_STATES
            if new_terminal:
                if run.finished_at is None:
                    run.finished_at = utcnow()
                await revoke_run_keys(s, run_id)
                run.exit_code = value.get("exit_code")
                detail = value.get("detail")
                if detail:
                    run.error = detail
                # A run that reached `succeeded` necessarily authenticated, so
                # the stored Claude token is known-good.
                if new_state == RunState.SUCCEEDED:
                    await self._probe_credential(s, "valid", value.get("runtime", "claude"))
            posted = None
            if new_terminal and run.conversation_id:
                # Only publish from here when this consumer actually holds the
                # reply, or when no reply is ever coming. A run that succeeded
                # but whose `result` frame hasn't landed yet is left alone — the
                # transcript consumer publishes it, with the real text. Claiming
                # it here would post a placeholder and permanently suppress the
                # answer, which is the bug this guard exists to prevent.
                text = run.result
                if text is None and new_state != RunState.SUCCEEDED:
                    text = f"(the run {new_state} without a reply)"
                if text is not None and await self._claim_reply(s, run_id):
                    posted = await self._post_reply(s, run, new_state, text,
                                                    failed=new_state != RunState.SUCCEEDED)
            await s.commit()
        # Publish the reply outside the DB session (Relay renders the message;
        # the connectors deliver the outbound to their bridged rooms).
        await self._publish_reply(posted)

    async def _handle_dlq(self, run_id: str, value: dict) -> None:
        async with self.sf() as s:
            run = await s.get(Run, run_id)
            if run is None:
                return
            if run.state not in ACTIVE_STATES:
                return
            run.state = RunState.DLQ
            if run.finished_at is None:
                run.finished_at = utcnow()
            await revoke_run_keys(s, run_id)
            error = value.get("error")
            if error:
                run.error = error
            await s.commit()
