"""News approval gate. A projected digest is held as PendingNews (see the
recorder) instead of posting straight to the channel; an operator reviews it
here and approves (post + record dedup) or rejects (drop; stories may resurface).

Mirrors the Pending Changes review flow for code edits — human-in-the-loop in
the authenticated UI, not a Discord-reaction side channel."""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select

from agentplatform.api.auth import ANNOTATE_ROLES, READ_ROLES, require_role
from agentplatform.db import PendingNews, utcnow
from agentplatform.events import TOPIC_CHANNEL_POST
from agentplatform.newsprojector import record_shared

router = APIRouter()


def _view(p: PendingNews) -> dict:
    return {"id": p.id, "created_at": p.created_at.isoformat() if p.created_at else None,
            "run_id": p.run_id, "channel": p.channel, "date": p.date,
            "post_text": p.post_text, "item_count": len(p.items or []),
            "status": p.status}


@router.get("/api/news/pending", dependencies=[Depends(require_role(*READ_ROLES))])
async def list_pending(request: Request):
    """Digests awaiting a decision, newest first."""
    async with request.app.state.session_factory() as s:
        rows = (await s.execute(select(PendingNews)
                                .where(PendingNews.status == "pending")
                                .order_by(PendingNews.created_at.desc()))).scalars().all()
        return [_view(p) for p in rows]


@router.post("/api/news/pending/{news_id}/approve")
async def approve(request: Request, news_id: str,
                  principal: str = Depends(require_role(*ANNOTATE_ROLES))):
    """Post the held digest to its channel and record its stories as shared."""
    days = request.app.state.settings.news_retention_days
    async with request.app.state.session_factory() as s:
        p = await s.get(PendingNews, news_id)
        if p is None:
            raise HTTPException(404, "unknown pending news")
        if p.status != "pending":
            raise HTTPException(409, f"already {p.status}")
        # Record dedup now (deferred until the digest actually goes out) and
        # commit the decision in the same transaction.
        await record_shared(s, p.items or [], days=days)
        p.status, p.decided_at, p.decided_by = "approved", utcnow(), principal
        await s.commit()
        channel, text = p.channel, p.post_text
    await request.app.state.producer.publish(
        TOPIC_CHANNEL_POST, channel, {"channel": channel, "text": text},
        type="channel.post")
    return {"ok": True}


@router.post("/api/news/pending/{news_id}/reject")
async def reject(request: Request, news_id: str,
                 principal: str = Depends(require_role(*ANNOTATE_ROLES))):
    """Drop the held digest. Its stories are NOT recorded as shared, so a later
    run may surface them again (a reject is 'not this', not 'never')."""
    async with request.app.state.session_factory() as s:
        p = await s.get(PendingNews, news_id)
        if p is None:
            raise HTTPException(404, "unknown pending news")
        if p.status != "pending":
            raise HTTPException(409, f"already {p.status}")
        p.status, p.decided_at, p.decided_by = "rejected", utcnow(), principal
        await s.commit()
    return {"ok": True}
