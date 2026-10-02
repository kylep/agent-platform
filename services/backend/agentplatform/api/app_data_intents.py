"""Kyle-session confirmation and dispatch for state App page templates."""
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from agentplatform.api.app_data import _for_web, kyle_session
from agentplatform.appdata import intents
from agentplatform.appdata.access import RecordError
from agentplatform.appdata.lifecycle import Actor

router = APIRouter()


class PageActionIntentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template: str = Field(min_length=1, max_length=40)
    record_id: str | None = Field(default=None, max_length=128)
    values: dict = Field(default_factory=dict)


class PageActionDispatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    digest: str = Field(min_length=64, max_length=64)


@router.post("/api/app-data/apps/{app_id}/pages/{page}/intents", status_code=201)
async def create_page_intent(request: Request, app_id: str, page: str,
                             body: PageActionIntentIn,
                             actor: Annotated[Actor, Depends(kyle_session)]):
    async with request.app.state.session_factory() as s:
        try:
            return await intents.create(s, app_id, page=page, template=body.template,
                                        record_id=body.record_id, values=body.values)
        except RecordError as exc:
            raise _for_web(exc) from None


@router.post("/api/app-data/page-intents/{intent_id}/dispatch")
async def dispatch_page_intent(request: Request, intent_id: str, body: PageActionDispatchIn,
                               actor: Annotated[Actor, Depends(kyle_session)]):
    async with request.app.state.session_factory() as s:
        try:
            return await intents.dispatch(s, intent_id, body.digest)
        except RecordError as exc:
            raise _for_web(exc) from None
