"""Notify: publish a message to a Discord channel via the connector. Lets a
brokered system agent (health-monitor) post an alert without holding the bot
token or a shell — it calls this over MCP; the connector delivers it."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select

from agentplatform.api.auth import ANNOTATE_ROLES, authenticate, role_allows
from agentplatform.api.chat_identities import _agent_can_send, _configured
from agentplatform.events import TOPIC_CHANNEL_POST
from agentplatform.db import (ChatIdentity, Conversation, DEFAULT_DISCORD_IDENTITY,
                              RelayBinding, RelayParticipant)
from agentplatform.relay import is_member, participant_of

from agentplatform.api import schemas as S
router = APIRouter()


class NotifyIn(BaseModel):
    identity_id: str | None = Field(default=None,
                                    pattern=r"^discord-[a-z][a-z0-9-]{0,31}$")
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


@router.post("/api/notify", response_model=S.Ok)
async def notify(request: Request, body: NotifyIn):
    caller = await authenticate(request)
    if caller is None:
        raise HTTPException(401)
    identity_id = body.identity_id or DEFAULT_DISCORD_IDENTITY
    agent = getattr(request.state, "api_key_agent", None)
    if agent is not None and not await _agent_can_send(request, identity_id):
        raise HTTPException(403, "agent has not been granted this Discord identity")
    if identity_id == DEFAULT_DISCORD_IDENTITY:
        if not role_allows(caller[1], ANNOTATE_ROLES):
            raise HTTPException(403)
    else:
        if body.channel_id is None:
            raise HTTPException(422, "another chat identity needs an exact channel_id")
        if caller[1] != "admin":
            if agent is None:
                raise HTTPException(403, "another chat identity needs an agent or admin")
            await request.app.state.agent_store.reload()
            info = request.app.state.agent_store.get(agent)
            frozen = getattr(request.state, "frozen_tools", None)
            grants = frozen if frozen is not None else (info.platform_tools if info else [])
            if (info is None or not info.enabled
                    or "mcp__platform__discord_chat" not in grants):
                raise HTTPException(403, "agent has no Discord send grant")
    async with request.app.state.session_factory() as session:
        identity = await session.get(ChatIdentity, identity_id)
        if identity is None or identity.status != "active" or (
                identity_id != DEFAULT_DISCORD_IDENTITY
                and not await _configured(request, identity)):
            raise HTTPException(409, "Discord chat identity is disabled")
        if identity_id != DEFAULT_DISCORD_IDENTITY:
            binding = (await session.execute(select(RelayBinding).where(
                RelayBinding.connector == "discord",
                RelayBinding.identity_id == identity_id,
                RelayBinding.external_ref == body.channel_id,
                RelayBinding.external_kind == "channel",
                RelayBinding.status == "active"))).scalar_one_or_none()
            if binding is None:
                raise HTTPException(404, "channel is not bound to this chat identity")
            if caller[1] != "admin":
                room = await session.get(Conversation, binding.channel_id)
                if room is None or room.archived_at is not None or room.kind == "dm":
                    raise HTTPException(404, "channel is not available")
                explicit = set((await session.execute(select(
                    RelayParticipant.participant).where(
                    RelayParticipant.channel_id == room.id))).scalars())
                if not is_member(room, participant_of(agent=agent), {agent}, explicit):
                    raise HTTPException(403, "agent is not a member of this channel")
    # Defang mass-pings even though the caller is a trusted system agent.
    text = body.text[:6000].replace("@everyone", "@​everyone").replace("@here", "@​here")
    target = body.channel_id or body.channel
    payload = {"text": text, "identity_id": identity_id}
    payload["channel_id" if body.channel_id else "channel"] = target
    if identity_id != DEFAULT_DISCORD_IDENTITY:
        payload["requested_by"] = (f"agent:{agent}" if agent is not None
                                   else f"user:{caller[0]}")
    await request.app.state.producer.publish(
        TOPIC_CHANNEL_POST, target, payload, type="channel.post")
    return {"ok": True}
