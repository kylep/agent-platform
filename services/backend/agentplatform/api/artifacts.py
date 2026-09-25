"""The artifacts REST surface (docs/design/23): the metadata, the two byte
routes, and the writes — every one a thin skin over `artifact_store`, which
is the ONE place an artifact changes.

What this module owns is the seam between a caller and that store, the
Tickets/Wiki seam once more:

- WHO is acting. The owner is `caller.participant`, resolved from the token
  by `require_relay_access`; the body may say `owner` and it is ignored. An
  agent additionally has to be speaking from its own Run (`_run_of`), so a
  per-run token whose run has gone may read artifacts and save none.
- WHAT is served. The content route is the platform handing a browser bytes
  somebody else chose, so it is the trust boundary in `docs/design/23`
  verbatim: `nosniff` always, an immutable private cache (an id's bytes never
  change), `inline` for the four rasters the store sniffed and `attachment`
  for everything else — an SVG, an HTML file that called itself a PNG, a CSV.
  No route reflects a mime a client sent; the row's is the bytes'.
- WHO may delete: the owner, an `agents_edit` holder, or the admin.

The `relay` role — the per-run participant role — reaches `/api/artifacts/*`
as it reaches Relay, Tickets and the Wiki, always as the agent the token
names — and, as with the Wiki, behind the grant itself (`require_artifacts_access`):
there is no room to be a member of here, so the role alone would bound nothing.
"""
import base64
import binascii
import json
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ValidationError, model_validator
from starlette.datastructures import UploadFile

from agentplatform import artifact_store as store
from agentplatform import image_gen_service as image_gen
from agentplatform.agentspec import TOOL_ARTIFACTS, TOOL_IMAGE_GEN
from agentplatform.api import agents as agents_api
from agentplatform.api import relay as relay_api
from agentplatform.api.agents import TOOL_AGENTS_EDIT
from agentplatform.api.auth import authenticate
from agentplatform.api.relay import READ, WRITE, Caller, require_relay_access
from agentplatform.api.tickets import _run_of
from agentplatform.artifact_store import ArtifactRuleError
from agentplatform.image_gen_service import ImageGenError

router = APIRouter()


def require_artifacts_access(*roles: str):
    """`require_relay_access`, plus the grant this door is behind — the wiki's
    fence (`require_wiki_access`) for the wiki's reason: the participant grants
    share ONE role, an artifact is not in a room, and without this an agent
    granted only `mcp__platform__relay` would read and write every artifact on
    the platform. Either artifact grant opens it: the artist holds `image_gen`
    and needs the store its pictures land in. Humans are unaffected — their
    authority is the role, as everywhere else."""
    inner = require_relay_access(*roles)

    async def dep(request: Request) -> Caller:
        caller = await inner(request)
        if caller.agent is None:
            return caller
        # Frozen at launch with the run's token, the same answer /api/whoami
        # gives — a grant added mid-run does not widen a run already using it.
        granted = await agents_api._caller_platform_tools(request, caller.agent)
        if caller.agent not in relay_api._agent_set(request):
            raise HTTPException(403, "unknown or disabled agent")
        if TOOL_ARTIFACTS not in granted and TOOL_IMAGE_GEN not in granted:
            raise HTTPException(403, "this agent is not granted the artifacts tool")
        return caller

    return dep

# Bytes never change under an id, so a browser may keep them for as long as
# it likes; `private` because they are behind a session and a shared cache
# has no business holding them.
CACHE = "private, max-age=31536000, immutable"


# --- shapes ---------------------------------------------------------------------

class ArtifactView(BaseModel):
    id: str
    name: str
    mime: str                    # what the bytes are, never what was claimed
    size: int
    sha256: str
    kind: str                    # image | file
    width: int | None
    height: int | None
    owner: str                   # participant: agent:<name> | user:<principal>
    run_id: str | None
    source: str                  # upload | generated | derived | tool
    meta: dict[str, Any]
    tags: list[str]
    created_at: str | None
    deleted_at: str | None
    # Where the bytes are; the thumb only for an image.
    thumb_url: str | None
    content_url: str
    resource_uri: str


class ArtifactIn(BaseModel):
    """The JSON create: text, or bytes as base64, exactly one of them. `mime`
    is a CLAIM the store honours only for a few text types over bytes that
    decode. `source` may not say `generated` — that is the generate route's
    word for something it paid for."""
    name: str = ""
    mime: str | None = None
    text: str | None = None
    content_b64: str | None = None
    meta: dict[str, Any] | None = None
    tags: list[str] | None = None
    source: Literal["upload", "tool", "derived"] = "upload"

    @model_validator(mode="after")
    def _one_body(self):
        if (self.text is None) == (self.content_b64 is None):
            raise ValueError("pass exactly one of text or content_b64")
        return self


class ArtifactPatch(BaseModel):
    name: str | None = None
    tags: list[str] | None = None


class ArtifactStats(BaseModel):
    count: int
    bytes: int
    total_cap: int               # artifacts_total_max_bytes
    # Generation spend (docs/design/23): from `meta.cost_usd` on the generated
    # rows, deleted ones included, since local midnight / the first of the month.
    generated_this_month: int
    spend_this_month_usd: float
    spend_today_usd: float
    daily_cap_usd: float         # image_gen_daily_usd


class ImageModel(BaseModel):
    """A registry entry × whether its provider's key is set: what the Studio's
    model select shows greyed or live. `sizes` or `aspects`, never both — which
    one says what geometry the model takes."""
    id: str
    provider: str
    label: str
    price_usd: float
    sizes: list[str] | None
    aspects: list[str] | None
    custom_size: bool
    qualities: list[str] | None
    edits: bool
    configured: bool
    default: bool
    billing: Literal["api", "codex"] = "api"
    seeded: bool = True


class GenerateIn(BaseModel):
    """`model` defaults to the registry's default; `size` or `aspect` is
    bridged by the tool when the model takes the other; `reference_ids` are
    artifacts the caller can read, images only, at most four."""
    prompt: str
    model: str | None = None
    size: str | None = None
    aspect: str | None = None
    quality: str | None = None
    seed: int | None = None
    reference_ids: list[str] | None = None
    name: str | None = None
    tags: list[str] | None = None


# --- the seam ---------------------------------------------------------------------

def _rule(e: ArtifactRuleError | ImageGenError) -> HTTPException:
    return HTTPException(e.status, str(e))


async def _row_or_404(s, artifact_id: str):
    row = await store.get(s, artifact_id)
    if row is None:
        raise HTTPException(404, "unknown artifact")
    return row


async def _may_modify(request: Request, caller: Caller, owner: str) -> None:
    """Owner, `agents_edit` holder, or admin — else 403. One rule for a rename,
    a retag and a delete: an artifact is its owner's, and a name is what a card
    shows, so a stranger renaming it is as much a lie as removing it. The admin
    check resolves the token a second time because `Caller` carries no role, on
    purpose: nothing else in the participant blocks decides by role."""
    if caller.participant == owner:
        return
    if caller.agent is not None:
        granted = await agents_api._caller_platform_tools(request, caller.agent)
        if TOOL_AGENTS_EDIT in granted:
            return
    else:
        ident = await authenticate(request)
        if ident is not None and ident[1] == "admin":
            return
    raise HTTPException(403, "only the owner, an agents_edit holder or the admin may change this")


def _body_bound(max_bytes: int) -> int:
    """The most a create's body may be, in wire bytes: the cap as base64 (the
    JSON shape's worst case) plus room for multipart framing and the fields."""
    return max_bytes * 4 // 3 + 65536


async def _bounded_body(request: Request, bound: int) -> Request:
    """The request with its body read into memory, refused at the bound
    BEFORE any parser sees it. `request.form()` and `request.json()` both
    buffer the whole body first, so the store's cap alone would arrive only
    after a caller had already made the API hold whatever it sent — and
    nginx's `client_max_body_size` guards the ingress, not the in-cluster
    callers that reach the API service directly. A declared length past the
    bound is refused unread; an undeclared (chunked) one is read until it
    passes the bound and refused there. The result is a Request over a
    replayed receive, so the parsers below are unchanged."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > bound:
        raise HTTPException(413, f"an upload is at most {bound} bytes on the wire")
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > bound:
            raise HTTPException(413, f"an upload is at most {bound} bytes on the wire")
        chunks.append(chunk)
    body = b"".join(chunks)
    replayed = iter([{"type": "http.request", "body": body, "more_body": False},
                     {"type": "http.disconnect"}])

    async def receive():
        return next(replayed)
    return Request(request.scope, receive)


async def _parse_create(request: Request, max_bytes: int) -> tuple[bytes, dict]:
    """Either body shape, reduced to (bytes, fields). Multipart is what the
    Studio and a browser send, JSON what a tool sends; both reach the store
    as the same call. The multipart read is bounded at one byte past the cap
    so an oversized upload costs the cap and not the upload."""
    ctype = request.headers.get("content-type", "")
    if ctype.startswith("multipart/form-data"):
        form = await request.form()
        file = form.get("file")
        if not isinstance(file, UploadFile):
            raise HTTPException(422, "multipart needs a `file` part")
        data = await file.read(max_bytes + 1)
        fields = {"name": form.get("name") or file.filename or "",
                  "claimed_mime": file.content_type, "source": "upload",
                  "meta": _form_json(form.get("meta"), "meta"),
                  "tags": _form_tags(form.get("tags"))}
        return data, fields
    try:
        body = ArtifactIn.model_validate(await request.json())
    except ValidationError as e:
        raise HTTPException(422, [{"loc": list(err["loc"]), "msg": err["msg"]}
                                  for err in e.errors()])
    except ValueError as e:
        raise HTTPException(422, [{"loc": ["body"], "msg": str(e)}])
    if body.text is not None:
        data, claimed = body.text.encode("utf-8"), body.mime or "text/plain"
    else:
        try:
            data = base64.b64decode(body.content_b64, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(400, "content_b64 is not base64")
        claimed = body.mime
    return data, {"name": body.name, "claimed_mime": claimed, "source": body.source,
                  "meta": body.meta, "tags": body.tags}


def _form_json(raw, field: str):
    if raw in (None, ""):
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        raise HTTPException(422, f"{field} must be JSON")


def _form_tags(raw) -> list[str] | None:
    """`["a","b"]` or `a, b` — a form field is a string either way."""
    if raw in (None, ""):
        return None
    text = str(raw).strip()
    if text.startswith("["):
        tags = _form_json(text, "tags")
        if not isinstance(tags, list):
            raise HTTPException(422, "tags must be a list")
        return [str(t) for t in tags]
    return [t for t in text.split(",")]


def _bytes_response(data: bytes, *, mime: str, filename: str, inline: bool) -> Response:
    """Every byte-serving answer: the row's mime, the flattened name, and the
    three headers that make the boundary — see the module docstring."""
    disposition = "inline" if inline else "attachment"
    return Response(content=data, media_type=mime, headers={
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": CACHE,
        "Content-Disposition": f'{disposition}; filename="{filename}"'})


# --- reads ----------------------------------------------------------------------

@router.get("/api/artifacts", response_model=list[ArtifactView])
async def list_artifacts(request: Request,
                         kind: Literal["image", "file"] | None = None,
                         owner: str | None = None,
                         source: Literal["upload", "generated", "derived", "tool"] | None = None,
                         q: str | None = Query(None, max_length=120),
                         tag: str | None = Query(None, max_length=store.TAG_LIMIT),
                         limit: int = Query(store.LIST_LIMIT, ge=1, le=store.LIST_MAX),
                         before: str | None = None,
                         caller: Caller = Depends(require_artifacts_access(*READ))):
    """Metadata only, newest first, keyset-paged from `before` (an artifact
    id). Never the bytes and never the thumb: those are the two routes below."""
    async with request.app.state.session_factory() as s:
        try:
            rows = await store.list_artifacts(s, kind=kind, owner=owner, source=source,
                                              q=q, tag=tag, limit=limit, before=before)
        except ArtifactRuleError as e:
            raise _rule(e)
        return [store.artifact_view(r) for r in rows]


@router.get("/api/artifacts/stats", response_model=ArtifactStats)
async def artifact_stats(request: Request,
                         caller: Caller = Depends(require_artifacts_access(*READ))):
    st = request.app.state
    async with st.session_factory() as s:
        count, used = await store.usage(s)
        spend = await image_gen.spend_stats(s, st.settings)
    return {"count": count, "bytes": used, "total_cap": st.settings.artifacts_total_max_bytes,
            **spend}


@router.get("/api/artifacts/models", response_model=list[ImageModel])
async def image_models(request: Request,
                       caller: Caller = Depends(require_artifacts_access(*READ))):
    """The image registry (`tools/image_gen/models.json`) with each model's
    `configured` read off its provider's secret block. Literal path, so it
    sits above `/{artifact_id}`."""
    st = request.app.state
    async with st.session_factory() as s:
        try:
            codex = await image_gen.codex_available(s, st.secret_store, st.agent_store)
            return await image_gen.models(s, st.tool_registry, st.secret_store,
                                          codex_configured=codex)
        except ImageGenError as e:
            raise _rule(e)


@router.get("/api/artifacts/{artifact_id}", response_model=ArtifactView)
async def get_artifact(request: Request, artifact_id: str,
                       caller: Caller = Depends(require_artifacts_access(*READ))):
    async with request.app.state.session_factory() as s:
        return store.artifact_view(await _row_or_404(s, artifact_id))


@router.get("/api/artifacts/{artifact_id}/content", response_class=Response)
async def artifact_content(request: Request, artifact_id: str,
                           caller: Caller = Depends(require_artifacts_access(*READ))):
    """The bytes. Inline for the four rasters — the browser renders exactly
    what Pillow already decoded on the way in — and a download for anything
    else, whatever it called itself."""
    async with request.app.state.session_factory() as s:
        row = await _row_or_404(s, artifact_id)
        data = await store.content(s, artifact_id)
    if data is None:
        raise HTTPException(404, "unknown artifact")
    return _bytes_response(data, mime=row.mime, filename=row.name,
                           inline=row.mime in store.RASTERS)


@router.get("/api/artifacts/{artifact_id}/resource", response_class=Response)
async def artifact_resource(request: Request, artifact_id: str,
                            caller: Caller = Depends(require_artifacts_access(*READ))):
    """Private MCP Resource bytes; ownership is rechecked on every read."""
    async with request.app.state.session_factory() as s:
        row = await _row_or_404(s, artifact_id)
        ident = await authenticate(request)
        if caller.participant != row.owner and (caller.agent is not None or
                                                 ident is None or ident[1] != "admin"):
            raise HTTPException(404, "unknown artifact")
        data = await store.content(s, artifact_id)
    if data is None:
        raise HTTPException(404, "unknown artifact")
    return Response(content=data, media_type=row.mime, headers={
        "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"})


@router.get("/api/artifacts/{artifact_id}/thumb", response_class=Response)
async def artifact_thumb(request: Request, artifact_id: str,
                         caller: Caller = Depends(require_artifacts_access(*READ))):
    """The raster thumb the store made — an image's only; a file has none and
    says 404 rather than serving its bytes small."""
    async with request.app.state.session_factory() as s:
        row = await _row_or_404(s, artifact_id)
        thumb = row.thumb
    if row.kind != "image" or not thumb:
        raise HTTPException(404, "no thumb for this artifact")
    png = thumb.startswith(b"\x89PNG")
    return _bytes_response(thumb, mime="image/png" if png else "image/jpeg",
                           filename="thumb.png" if png else "thumb.jpg", inline=True)


# --- writes -----------------------------------------------------------------------

@router.post("/api/artifacts", status_code=201, response_model=ArtifactView, openapi_extra={
    "requestBody": {"required": True, "content": {
        "application/json": {"schema": ArtifactIn.model_json_schema()},
        "multipart/form-data": {"schema": {
            "type": "object", "required": ["file"],
            "properties": {"file": {"type": "string", "format": "binary"},
                           "name": {"type": "string"},
                           "tags": {"type": "string", "description": "JSON list or comma-separated"},
                           "meta": {"type": "string", "description": "JSON object"}}}}}}})
async def create_artifact(request: Request,
                          caller: Caller = Depends(require_artifacts_access(*WRITE))):
    """Save bytes as a new artifact, as multipart (`file`, `name?`, `tags?`,
    `meta?`) or JSON (`ArtifactIn`). The owner is the caller; an agent's
    write carries its run. A parent named in `meta.parent_id` that is an
    existing image makes the new artifact `derived`."""
    st = request.app.state
    max_bytes = st.settings.artifacts_max_bytes
    request = await _bounded_body(request, _body_bound(max_bytes))
    data, fields = await _parse_create(request, max_bytes)
    async with st.session_factory() as s:
        run = await _run_of(s, request, caller, writing=True)
        try:
            row = await store.create(s, data=data, owner=caller.participant,
                                     run_id=run.id if run is not None else None,
                                     producer=st.producer, settings=st.settings, **fields)
        except ArtifactRuleError as e:
            await s.rollback()
            raise _rule(e)
        return store.artifact_view(row)


@router.post("/api/artifacts/generate", status_code=201, response_model=ArtifactView)
async def generate_artifact(request: Request, body: GenerateIn,
                            caller: Caller = Depends(require_artifacts_access(*WRITE))):
    """Generate one image and keep it (docs/design/23): the ONE place a
    generation happens is `image_gen_service.generate`; this is its door.
    Synchronous — the longest provider timeout plus a grace, so a caller waits
    up to 390 s — and the response is the artifact. The owner is the caller;
    an agent's generation carries its run, and a token without one is
    refused before anything is spent.

    Behind `image_gen` itself for an agent, not the either-grant fence the
    store's routes share: `artifacts` is default-granted to every agent so
    that any of them can keep a file, and this is the one door that spends
    money. Humans are bounded by their role, as everywhere else."""
    st = request.app.state
    if caller.agent is not None:
        granted = await agents_api._caller_platform_tools(request, caller.agent)
        if TOOL_IMAGE_GEN not in granted:
            raise HTTPException(403, "image_gen is not granted to this agent")

    # Subscription-backed generation is a normal Codex agent run. It has no
    # provider key and no API-dollar reservation; the runner ingests the
    # built-in ImageGen result through the run-scoped upload route.
    if body.model == image_gen.CODEX_MODEL_ID:
        async with st.session_factory() as s:
            run = await _run_of(s, request, caller, writing=True)
            if not await image_gen.codex_available(s, st.secret_store, st.agent_store):
                raise HTTPException(503, "Codex image generation is not configured")
        try:
            row = await image_gen.generate_codex(
                session_factory=st.session_factory, producer=st.producer,
                settings=st.settings, agent_store=st.agent_store,
                owner=caller.participant, principal=caller.principal,
                parent_run=run, **body.model_dump(exclude={"model", "size", "quality", "seed"}))
        except (ImageGenError, ArtifactRuleError) as e:
            raise _rule(e)
        return store.artifact_view(row)

    async with st.session_factory() as s:
        run = await _run_of(s, request, caller, writing=True)
        try:
            row = await image_gen.generate(
                s, settings=st.settings, tool_registry=st.tool_registry, producer=st.producer,
                owner=caller.participant, agent=caller.agent,
                run_id=run.id if run is not None else None,
                **body.model_dump())
        except (ImageGenError, ArtifactRuleError) as e:
            await s.rollback()
            raise _rule(e)
        return store.artifact_view(row)


@router.patch("/api/artifacts/{artifact_id}", response_model=ArtifactView)
async def patch_artifact(request: Request, artifact_id: str, body: ArtifactPatch,
                         caller: Caller = Depends(require_artifacts_access(*WRITE))):
    async with request.app.state.session_factory() as s:
        row = await _row_or_404(s, artifact_id)
        await _may_modify(request, caller, row.owner)
        try:
            row = await store.patch(s, artifact_id, name=body.name, tags=body.tags)
        except ArtifactRuleError as e:
            await s.rollback()
            raise _rule(e)
        return store.artifact_view(row)


@router.delete("/api/artifacts/{artifact_id}", response_model=ArtifactView)
async def delete_artifact(request: Request, artifact_id: str,
                          caller: Caller = Depends(require_artifacts_access(*WRITE))):
    """Soft delete: the artifact drops out of every read and its bytes stop
    being served; the row waits for the pruner."""
    st = request.app.state
    async with st.session_factory() as s:
        row = await _row_or_404(s, artifact_id)
        await _may_modify(request, caller, row.owner)
        row = await store.soft_delete(s, artifact_id, producer=st.producer, agent=caller.agent)
        return store.artifact_view(row)
