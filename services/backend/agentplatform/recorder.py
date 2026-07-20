import logging

from sqlalchemy.exc import IntegrityError

from agentplatform.db import ACTIVE_STATES, Run, RunState, SecretMeta, TranscriptEvent, utcnow
from agentplatform.events import TOPIC_RUN_DLQ, TOPIC_RUN_EVENTS, TOPIC_RUN_TRANSCRIPT
from agentplatform.secrets import CLAUDE_CREDENTIAL

log = logging.getLogger("recorder")


class Recorder:
    def __init__(self, session_factory):
        self.sf = session_factory

    async def _probe_credential(self, s, status: str) -> None:
        """Record the observed validity of the Claude credential. This is the
        token "probe": a run cannot reach `succeeded` without authenticating,
        and the CLI reports auth failures with error=authentication_failed, so
        real run outcomes tell us whether the stored token works."""
        meta = await s.get(SecretMeta, CLAUDE_CREDENTIAL) or SecretMeta(name=CLAUDE_CREDENTIAL)
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
            usage = value.get("usage", {})
            run.tokens_in += usage.get("input_tokens", 0)
            run.tokens_out += usage.get("output_tokens", 0)
            # A 401 / authentication_failed frame proves the stored token is
            # bad, regardless of how the run ultimately terminates.
            if value.get("error") == "authentication_failed" or value.get("error_status") == 401:
                await self._probe_credential(s, "invalid")
            await s.commit()

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
                run.exit_code = value.get("exit_code")
                detail = value.get("detail")
                if detail:
                    run.error = detail
                # A run that reached `succeeded` necessarily authenticated, so
                # the stored Claude token is known-good.
                if new_state == RunState.SUCCEEDED:
                    await self._probe_credential(s, "valid")
            await s.commit()

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
            error = value.get("error")
            if error:
                run.error = error
            await s.commit()
