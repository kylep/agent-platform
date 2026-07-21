"""Integration health for the Reporting page. For each external integration we
report whether it's **configured** (its secret is set) and whether it's actually
**working** (recent successful activity), so "is Discord set up right?" has a
straight answer."""
from datetime import timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select

from agentplatform.api.auth import require_admin
from agentplatform.db import Conversation, Run, RunState, utcnow

router = APIRouter()


def _row(name, kind, secrets, configured, active, *, working, configured_msg, missing):
    status = "working" if (configured and active) else ("configured" if configured else "missing")
    detail = working if status == "working" else (configured_msg if status == "configured" else missing)
    return {"name": name, "kind": kind, "secrets": secrets,
            "configured": configured, "status": status, "detail": detail}


@router.get("/api/integrations", dependencies=[Depends(require_admin)])
async def integrations(request: Request):
    store = request.app.state.secret_store
    sf = request.app.state.session_factory
    now = utcnow()

    async def present(*names) -> bool:
        for n in names:
            if not await store.exists(n):
                return False
        return True

    async def count(stmt) -> int:
        async with sf() as s:
            return (await s.execute(stmt)).scalar_one()

    out = []

    # Discord connector — working if a Discord conversation turn succeeded lately.
    disc_active = await count(
        select(func.count()).select_from(Run)
        .join(Conversation, Run.conversation_id == Conversation.id)
        .where(Conversation.connector == "discord", Run.state == RunState.SUCCEEDED,
               Run.created_at >= now - timedelta(days=1))) > 0
    out.append(_row("Discord", "connector", ["discord-bot"], await present("discord-bot"), disc_active,
                    working="Replied in a Discord conversation within the last 24h.",
                    configured_msg="Token set. Enable the connector (helm) and mention the bot to confirm.",
                    missing="Set the discord-bot secret, then enable the connector."))

    # GitHub App (self-edit / PRs) — working if a self-edit run succeeded lately.
    gha_active = await count(
        select(func.count()).select_from(Run)
        .where(Run.trigger == "self-edit", Run.state == RunState.SUCCEEDED,
               Run.created_at >= now - timedelta(days=7))) > 0
    out.append(_row("GitHub App", "git", ["github-app"], await present("github-app"), gha_active,
                    working="Opened a self-edit PR within the last 7d.",
                    configured_msg="Configured — no recent self-edit runs to confirm it.",
                    missing="Set the github-app secret to enable PR-based self-edits."))

    return out
