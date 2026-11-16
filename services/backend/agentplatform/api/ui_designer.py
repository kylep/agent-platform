"""The UI Designer's REST surface (docs/design/46 §6, §9).

T5's authorization seam lives here; the remaining routes (list, create, get,
outline, the real `edit`, render/svg/resolved, assets, history, restore,
delete, imports) are added beside it and every one of them takes
`require_ui_designer_access` — that is the whole rule for who reaches a
document. What this module owns:

- WHO is acting: `caller.participant`, resolved from the token by
  `require_relay_access`; there is no author on the wire. An agent writes only
  from its own Run (`write=True` on the dependency), so a run-less agent key
  reads and writes nothing.
- WHO may reach a document at all: Kyle's session and human keys by role, an
  agent only when its frozen grants hold `ui_designer` — for reads too, so the
  messenger grant does not open the designer (the wiki's fence, for the wiki's
  reason).
- HOW an image gets in: `place`, the one door (§9). Copy-on-place: the bytes
  are authorized once here and from then on the document is self-contained.
- WHERE pictures go: `snapshot` on `edit` is refused for any caller without a
  run (`UD-NO-IMAGES-HERE`). The facade's admin bearer and API keys get text;
  images go to agent runs, people use the web UI.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from agentplatform import artifact_store
from agentplatform.agentspec import TOOL_ARTIFACTS, TOOL_UI_DESIGNER
from agentplatform.api import agents as agents_api
from agentplatform.api import relay as relay_api
from agentplatform.api.artifacts import _authorize_read
from agentplatform.api.relay import READ, WRITE, Caller, require_relay_access
from agentplatform.api.tickets import _run_of
from agentplatform.appdata import artifacts as app_artifacts
from agentplatform.db import Conversation, Run
from agentplatform.relay import is_open_channel, room_home
from agentplatform.ui_designer import errors as ud_errors
from agentplatform.ui_designer import store
from agentplatform.ui_designer.errors import UiDesignerError

router = APIRouter()

PREFIX = "/api/ui-designer"
NOT_GRANTED = "this agent is not granted the ui_designer tool"

UD_NO_IMAGES_HERE = "UD-NO-IMAGES-HERE"
ud_errors.CODES.setdefault(UD_NO_IMAGES_HERE,
                           "Images go to agent runs; use the web UI, or call without `snapshot`.")

# UiDesignerError code -> HTTP status. Anything unlisted is a 422: the caller
# sent something the document rules refuse.
_STATUS = {
    store.UD_NOT_FOUND: 404,
    store.UD_SLUG_TAKEN: 409,
    store.UD_REQUEST_REUSED: 409,
    store.UD_BUDGET_STORAGE: 507,
    ud_errors.UD_VERSION_CONFLICT: 409,
    ud_errors.UD_RENDER_FAILED: 502,
    UD_NO_IMAGES_HERE: 403,
}


def ud_http(e: UiDesignerError) -> HTTPException:
    """A store refusal as the HTTP answer: the stable code, message and fix in
    the body, which is what an agent acts on."""
    return HTTPException(_STATUS.get(e.code, 422), e.as_dict())


# --- the seam ------------------------------------------------------------------

def require_ui_designer_access(*roles: str, write: bool = False):
    """`require_relay_access`, plus the grant this door is behind and, on a
    write, the Run an agent must be speaking from.

    Humans are judged by role (Kyle's session, the admin bearer, human keys).
    An agent is judged by its grant: `_caller_platform_tools` and not the row,
    because a run JWT froze its grants at launch and a grant added mid-run must
    not widen the run already using its token. The `relay` role alone opens
    nothing here. Disabled and quarantined agents are refused. A run-less agent
    key (or one whose run has gone) may read and may not write: every version
    is stamped with the run that wrote it."""
    inner = require_relay_access(*roles)

    async def dep(request: Request) -> Caller:
        caller = await inner(request)
        if caller.agent is None:
            return caller
        granted = await agents_api._caller_platform_tools(request, caller.agent)
        if caller.agent not in relay_api._agent_set(request):
            raise HTTPException(403, "unknown or disabled agent")
        if TOOL_UI_DESIGNER not in granted:
            raise HTTPException(403, NOT_GRANTED)
        if write:
            async with request.app.state.session_factory() as s:
                await _run_of(s, request, caller, writing=True)
        return caller

    return dep


async def caller_run_id(request: Request, caller: Caller) -> str | None:
    """The run an agent caller acts from (None for a human, or a run-less
    agent key on a read). Routes stamp it on versions, receipts, placements."""
    if caller.agent is None:
        return None
    async with request.app.state.session_factory() as s:
        run = await _run_of(s, request, caller, writing=False)
        return run.id if run is not None else None


async def refuse_runless_snapshot(request: Request, caller: Caller, snapshot: Any) -> None:
    """§6: `snapshot` cannot be excluded from the facade by path, so the API
    refuses it for any caller without a run — Kyle's session, the facade's
    admin bearer, any API key. The `edit` route calls this before anything
    else touches the document."""
    if not snapshot:
        return
    if await caller_run_id(request, caller) is None:
        raise ud_http(UiDesignerError(UD_NO_IMAGES_HERE,
                                      "images go to agent runs; use the web UI"))


# --- place (copy on place, §9) -------------------------------------------------

class PlaceIn(BaseModel):
    artifact_id: str = Field(pattern=r"^[0-9a-f]{32}$")


class PlaceOut(BaseModel):
    sha256: str
    bytes: int
    mime: str


async def _run_is_restricted(s, run: Run) -> bool:
    """Whether reading this run is restricted for ANYONE: the cases
    `authority.assert_readable_run` checks — a startup-pilot run, a local
    persona session, a run in a room that is not an open Relay channel. A
    document is readable by every holder of the tool, so an artifact from such
    a run would widen its readership; the caller's own access does not matter.
    A room that has gone counts as restricted (we cannot say who it was for)."""
    if getattr(run, "startup_memory_enabled", False):
        return True
    if getattr(run, "local_persona_session_id", None):
        return True
    if run.conversation_id:
        room = await s.get(Conversation, run.conversation_id)
        if room is None or room_home(room) != "relay" or not is_open_channel(room):
            return True
    return False


@router.post(PREFIX + "/docs/{slug}/place", response_model=PlaceOut)
async def place(slug: str, body: PlaceIn, request: Request,
                caller: Caller = Depends(require_ui_designer_access(*WRITE, write=True))):
    """Copy an image artifact into the document's assets and record the
    placement. The node then names the sha256; `insert`/`replace`/`set_doc`
    accept only placed assets (UD-ASSET-UNKNOWN)."""
    if caller.agent is not None:
        # `_authorize_read` alone is not enough: `artifacts_granted` defaults to
        # True when `require_artifacts_access` did not run, which it did not.
        granted = await agents_api._caller_platform_tools(request, caller.agent)
        if TOOL_ARTIFACTS not in granted:
            raise HTTPException(403, "placing an image needs the artifacts tool")
    request.state.artifacts_granted = True
    async with request.app.state.session_factory() as s:
        art = await artifact_store.get(s, body.artifact_id)
        # App-owned: refused outright and indistinguishable from missing (the
        # avatar rule) — its readership is its App's, never a document's.
        if art is None or await app_artifacts.ownership(s, art.id) is not None:
            raise HTTPException(404, "unknown artifact")
        await _authorize_read(s, request, art)
        if art.kind != "image" or art.mime not in artifact_store.RASTERS:
            raise HTTPException(422, "only a raster image artifact can be placed")
        if art.run_id:
            run = await s.get(Run, art.run_id)
            if run is None or await _run_is_restricted(s, run):
                raise HTTPException(403, "this artifact comes from a run whose history is "
                                         "restricted; it cannot be placed in a shared document")
        elif caller.agent is not None or art.owner != caller.participant:
            # A run-less artifact is a human's upload: only its uploader places
            # it. This is what bounds the admin — an admin reads everything, and
            # a document must not become a way to show the rest to every holder.
            raise HTTPException(403, "an artifact without a run can be placed only by "
                                     "the person who uploaded it")
        data = await artifact_store.content(s, art.id)
        if data is None:
            raise HTTPException(404, "unknown artifact")
        try:
            out = await store.place_asset(s, slug, data=data, mime=art.mime,
                                          placed_by=caller.participant,
                                          placed_from=f"artifact:{art.id}",
                                          width=art.width, height=art.height)
        except UiDesignerError as e:
            raise ud_http(e)
        await s.commit()
    # TODO(T5 routes): budgets from settings (`Caps`) and the
    # `ui_designer.events` publish.
    return out


# --- edit (stub; the real batch lands with the remaining T5 routes) ------------

class EditIn(BaseModel):
    ops: list[dict[str, Any]]
    expected_version: int
    reason: str = ""
    request_id: str | None = None
    snapshot: Any = None


@router.post(PREFIX + "/docs/{slug}/edit")
async def edit(slug: str, body: EditIn, request: Request,
               caller: Caller = Depends(require_ui_designer_access(*WRITE, write=True))):
    await refuse_runless_snapshot(request, caller, body.snapshot)
    # Seam: store.edit_document(author=caller.participant,
    # run_id=await caller_run_id(...), wants_snapshot=bool(body.snapshot), ...),
    # then render the snapshot for a run caller.
    raise HTTPException(501, "edit is not implemented yet")
