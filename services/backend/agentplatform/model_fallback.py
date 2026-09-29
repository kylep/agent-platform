"""A single, pre-work provider fallback under the frozen invocation contract."""
from fastapi import HTTPException
from agentplatform.agentdefs import AgentDefModel
from agentplatform.db import AuthorizedRelaySession
from sqlalchemy import update

REASONS = {"capacity", "rate_limit", "authentication", "provider_unavailable", "model_unavailable"}


async def activate(session, run, reason):
    if reason not in REASONS:
        raise HTTPException(422, "not a provider fallback reason")
    if run.fallback_used:
        raise HTTPException(409, "backup already attempted")
    frozen = AgentDefModel(**(run.definition_snapshot or {}))
    if not frozen.backup_runtime or not frozen.backup_model:
        raise HTTPException(409, "no backup model configured")
    run.runtime, run.model = frozen.backup_runtime, frozen.backup_model
    run.fallback_used, run.fallback_reason = True, reason
    # The next primary turn must replay the backup's answer, rather than
    # resume a stale primary thread that never saw this turn.
    if run.conversation_id:
        await session.execute(update(AuthorizedRelaySession).where(
            AuthorizedRelaySession.channel_id == run.conversation_id,
            AuthorizedRelaySession.agent == run.agent,
            AuthorizedRelaySession.authorization_generation == (run.authorization_generation or 0)
        ).values(claude_session_id="", session_blob=None, codex_thread_id=""))
    return {"runtime": run.runtime, "model": run.model, "notice": notice(run)}


def notice(run):
    if not run.fallback_used:
        return ""
    primary = run.definition_snapshot or {}
    return (f"Platform model fallback: the primary {primary.get('runtime', '')} "
            f"model {primary.get('model') or '(default)'} could not start because of "
            f"{run.fallback_reason}. You are the same agent, using backup {run.model}. "
            "No answer or tool work began in that attempt. Continue the original "
            "request with your same persona, memories and permissions. This is a "
            "fresh harness session with replayed conversation context.")
