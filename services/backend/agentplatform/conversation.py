"""Conversation turn logic, shared by the web API (`POST /conversations/{id}/
messages`) and the connector-ingest consumer. The platform owns conversation
history: each turn is a Run, and the next turn's prompt is built from prior
(user_message, result) pairs so the agent has context without the connector
carrying any state."""
import uuid

from sqlalchemy import select

from agentplatform.db import ACTIVE_STATES, Conversation, Run, utcnow
from agentplatform.materialize import materialize_run

_HISTORY_TURNS = 20


async def _history(session, conversation_id: str) -> list[tuple[str, str]]:
    rows = (await session.execute(
        select(Run).where(Run.conversation_id == conversation_id)
        .order_by(Run.created_at))).scalars().all()
    out = []
    for r in rows:
        user = r.user_message or ""
        if user or r.result:
            out.append((user, r.result or ""))
    return out[-_HISTORY_TURNS:]


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


async def continue_conversation(session_factory, producer, conversation_id: str,
                                message: str, requested_by: str) -> str | None:
    """Add a turn: build the prompt from history + `message` and materialize a
    run tagged with the conversation. Returns the run id, or None if the
    conversation is missing/closed or already has a turn in flight."""
    async with session_factory() as s:
        conv = await s.get(Conversation, conversation_id)
        if conv is None or conv.status != "active":
            return None
        # Serialize turns: don't start a new one while a run is still active.
        active = (await s.execute(select(Run).where(
            Run.conversation_id == conversation_id,
            Run.state.in_(ACTIVE_STATES)))).first()
        if active is not None:
            return None
        history = await _history(s, conversation_id)
        agent = conv.agent
        conv.updated_at = utcnow()
        await s.commit()

    run_id = uuid.uuid4().hex
    await materialize_run(session_factory, producer, {
        "run_id": run_id, "agent": agent, "prompt": build_prompt(history, message),
        "trigger": "conversation", "requested_by": requested_by,
        "conversation_id": conversation_id, "user_message": message,
    })
    return run_id
