"""Notify: publish a message to a Discord channel via the connector. Lets a
brokered system agent (health-monitor) post an alert without holding the bot
token or a shell — it calls this over MCP; the connector delivers it."""
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from agentplatform.api.auth import ANNOTATE_ROLES, require_role
from agentplatform.events import TOPIC_CHANNEL_POST

from agentplatform.api import schemas as S
router = APIRouter()


class NotifyIn(BaseModel):
    channel: str
    text: str


@router.post("/api/notify", response_model=S.Ok, dependencies=[Depends(require_role(*ANNOTATE_ROLES))])
async def notify(request: Request, body: NotifyIn):
    # Defang mass-pings even though the caller is a trusted system agent.
    text = body.text[:6000].replace("@everyone", "@​everyone").replace("@here", "@​here")
    await request.app.state.producer.publish(
        TOPIC_CHANNEL_POST, body.channel,
        {"channel": body.channel, "text": text}, type="channel.post")
    return {"ok": True}
