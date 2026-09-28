"""Retired broadcast route retained with an explicit migration error."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, model_validator

from agentplatform.api.auth import authenticate


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
    raise HTTPException(410, "Legacy Discord broadcasts are retired; a persona uses its owned connector account.")
