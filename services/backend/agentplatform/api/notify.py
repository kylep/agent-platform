"""Notify: publish a message to a Discord channel via the connector. Lets a
brokered system agent (health-monitor) post an alert without holding the bot
token or a shell — it calls this over MCP; the connector delivers it."""
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, model_validator

from agentplatform.api.auth import ANNOTATE_ROLES, require_role
from agentplatform.events import TOPIC_CHANNEL_POST

from agentplatform.api import schemas as S
router = APIRouter()


class NotifyIn(BaseModel):
    channel: str | None = None
    channel_id: str | None = None
    text: str

    @model_validator(mode="after")
    def exactly_one_target(self):
        if bool(self.channel) == bool(self.channel_id):
            raise ValueError("provide exactly one channel or channel_id")
        if self.channel_id and (not self.channel_id.isdigit()
                                or not 15 <= len(self.channel_id) <= 22):
            raise ValueError("channel_id must be a Discord channel ID")
        return self


@router.post("/api/notify", response_model=S.Ok, dependencies=[Depends(require_role(*ANNOTATE_ROLES))])
async def notify(request: Request, body: NotifyIn):
    # Defang mass-pings even though the caller is a trusted system agent.
    text = body.text[:6000].replace("@everyone", "@​everyone").replace("@here", "@​here")
    target = body.channel_id or body.channel
    payload = {"text": text, "identity_id": "discord-default"}
    payload["channel_id" if body.channel_id else "channel"] = target
    await request.app.state.producer.publish(
        TOPIC_CHANNEL_POST, target, payload, type="channel.post")
    return {"ok": True}
