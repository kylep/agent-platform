"""Artifacts in records (design 39, "Collections" → "Artifact fields", and
"Trust boundaries" → App-owned artifacts).

An `artifact` field's value is an artifact id. Writing one into a record makes
the artifact **App-owned**: an `app_data_artifacts` row names the App and the
**owning field**, and an `app_data_artifact_refs` row names each record field
holding it. From then on:

- **One owning field.** The first field to reference it owns it (or the field
  an `upload` named). Another field of the same App may reference it only if
  its readers are a subset of the owning field's, compared as written: `owner`
  and the owner's own name are different readers, because an ownership
  transfer moves one and not the other. So a second reference never widens
  who can read the bytes.
- **Claiming.** An ordinary artifact can be claimed only by its own agent, or
  by Kyle (who may already rename or delete anyone's), and never while an
  agent wears it as its face. One already App-owned can't move to another
  App. Every refusal of an id the caller can't use reads the same, so a write
  is no oracle for which ids exist.
- **Reads.** Every artifact route, the list, the stats and the event feed
  authorize an App-owned artifact through the owning field's read access for
  the caller (`may_read`), never through the `artifacts` grant or the run it
  came from. A definition that no longer has the owning field fails closed.
- **Bytes.** Up to `app_artifacts_max_bytes` (64 MiB) each, counted against
  the App (`app_bytes`) and not the platform's artifact total.
  `check_app_bytes` is the quota hook A11 fills in.
- **Deletion.** When the last referencing record field goes (a delete plan,
  retention, or an update that clears or replaces the value) the artifact is
  hard-deleted in the same transaction: its bytes are App data, so they don't
  wait out the soft-delete window. An upload no record ever referenced is
  swept after a day. Earlier record versions may still name a deleted id.

Like `records`, nothing here commits except `upload` and the sweep; the
helpers flush into the record write's transaction.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import delete, func, select

from agentplatform import artifact_store as store
from agentplatform.appdata.access import Access, Caller, RecordError
from agentplatform.appdata.definitions import ArtifactField, CollectionDef
from agentplatform.appdata.models import AppDataArtifact, AppDataArtifactRef
from agentplatform.config import get_settings
from agentplatform.db import AgentDef, Artifact, ArtifactBlob, utcnow

# An upload no record has referenced by then is abandoned.
ORPHAN_AFTER = timedelta(days=1)

_UNAVAILABLE = "no artifact {id} this caller can put in an App field"


def artifact_fields(c: CollectionDef) -> list[str]:
    return [name for name, spec in c.fields.items() if isinstance(spec, ArtifactField)]


def field_readers(c: CollectionDef, field: str) -> set[str]:
    """The field's readers as the definition writes them: its own override,
    else the collection's default."""
    spec = c.fields[field]
    override = spec.access.read if spec.access is not None else None
    return set(override if override is not None else c.access.read)


async def check_app_bytes(session, ctx, adding: int) -> None:
    """The quota hook: A11 refuses here, failing closed, when `adding` more
    bytes would take the App past its quota. `app_bytes` is the usage."""
    return None


async def app_bytes(session, app_id: str) -> int:
    """The bytes the App's artifacts are charged."""
    total = (await session.execute(select(func.sum(AppDataArtifact.size)).where(
        AppDataArtifact.app_id == app_id))).scalar()
    return int(total or 0)


async def ownership(session, artifact_id: str, *, lock: bool = False) -> AppDataArtifact | None:
    stmt = select(AppDataArtifact).where(AppDataArtifact.artifact_id == artifact_id)
    if lock:
        # Serializes a claim or a new reference against the delete of the
        # last one, so neither lands on an artifact the other just removed.
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalar_one_or_none()


# --- reads ------------------------------------------------------------------------------

async def may_read(session, own: AppDataArtifact, principal: str | None) -> bool:
    """Can this principal read the owning field? Fails closed on everything
    else: no principal, an App whose definitions no longer validate, an owning
    collection or field that's gone or is no longer an artifact field."""
    if principal is None:
        return False
    from agentplatform.appdata.records import load_app
    try:
        ctx = await load_app(session, own.app_id)
    except RecordError:
        return False
    c = ctx.bundle.collections.get(own.collection)
    if c is None or not isinstance(c.fields.get(own.field), ArtifactField):
        return False
    return Access(c, Caller(principal), ctx.owner).can_read(own.field)


async def request_principal(request, agent: str | None) -> str | None:
    """The App principal an API caller is: `agent:<name>` for an agent's
    token, `kyle` for a browser session with role admin, `login:qa` for the
    QA's login. Anything else (an API key, even an admin one) is no App
    principal, so it reads no App-owned artifact."""
    if agent is not None:
        return f"agent:{agent}"
    from agentplatform.api.auth import authenticate
    from agentplatform.qaprincipal import QA_PRINCIPAL
    ident = await authenticate(request)
    if ident is None or getattr(request.state, "auth_kind", None) != "session":
        return None
    name, role = ident
    if role == "admin":
        return "kyle"
    return "login:qa" if name == QA_PRINCIPAL else None


# --- writes -----------------------------------------------------------------------------

async def attach(session, ctx, caller: Caller, c: CollectionDef, record_id: str,
                 values: dict) -> None:
    """Record `record_id`'s artifact fields among `values` as references,
    claiming or checking each artifact first."""
    for name in artifact_fields(c):
        artifact_id = values.get(name)
        if artifact_id is None:
            continue
        await _admit(session, ctx, caller, c, name, artifact_id)
        session.add(AppDataArtifactRef(artifact_id=artifact_id, app_id=ctx.app_id,
                                       collection=c.collection, record_id=record_id,
                                       field=name))
    await session.flush()


async def _admit(session, ctx, caller: Caller, c: CollectionDef, field: str,
                 artifact_id: str) -> None:
    unavailable = RecordError("AD-ARTIFACT-UNAVAILABLE", _UNAVAILABLE.format(id=artifact_id),
                              422, {"field": field})
    row = await store.get(session, artifact_id)
    if row is None:
        raise unavailable
    own = await ownership(session, artifact_id, lock=True)
    if own is None:
        if not (caller.principal == "kyle" or row.owner == caller.principal):
            raise unavailable
        worn = (await session.execute(select(AgentDef.name).where(
            AgentDef.image_artifact_id == artifact_id).limit(1))).first()
        if worn is not None:
            raise RecordError("AD-ARTIFACT-IN-USE", f"artifact {artifact_id} is an agent's "
                              "picture, which everyone sees; it can't go in an App field",
                              409, {"field": field})
        await check_app_bytes(session, ctx, row.size)
        session.add(AppDataArtifact(artifact_id=artifact_id, app_id=ctx.app_id,
                                    collection=c.collection, field=field, size=row.size))
        return
    if own.app_id != ctx.app_id:
        raise unavailable
    if (own.collection, own.field) == (c.collection, field):
        return
    if not await may_read(session, own, caller.principal):
        raise unavailable
    owning = ctx.collection(own.collection)
    if not field_readers(c, field) <= field_readers(owning, own.field):
        raise RecordError("AD-ARTIFACT-WIDENS", f"{c.collection}.{field} has readers "
                          f"{own.collection}.{own.field} doesn't, and that field owns "
                          f"artifact {artifact_id}", 403,
                          {"field": field, "owner_field": f"{own.collection}.{own.field}"})


async def detach_fields(session, ctx, collection: str, record_id: str,
                        old: dict[str, str]) -> None:
    """A record's field no longer holds the artifact it did: `old` maps the
    field to the artifact id it held."""
    if not old:
        return
    for field, artifact_id in old.items():
        await session.execute(delete(AppDataArtifactRef).where(
            AppDataArtifactRef.artifact_id == artifact_id,
            AppDataArtifactRef.app_id == ctx.app_id,
            AppDataArtifactRef.collection == collection,
            AppDataArtifactRef.record_id == record_id, AppDataArtifactRef.field == field))
    await _collect(session, set(old.values()))


async def detach_records(session, ctx, collection: str, record_ids: list[str]) -> None:
    """The records are being deleted: drop their references."""
    if not artifact_fields(ctx.collection(collection)):
        return
    where = (AppDataArtifactRef.app_id == ctx.app_id,
             AppDataArtifactRef.collection == collection,
             AppDataArtifactRef.record_id.in_(record_ids))
    ids = set((await session.execute(
        select(AppDataArtifactRef.artifact_id).where(*where))).scalars())
    if not ids:
        return
    await session.execute(delete(AppDataArtifactRef).where(*where))
    await _collect(session, ids)


async def _collect(session, artifact_ids: set[str]) -> None:
    """Delete each of these artifacts that no record field holds any more."""
    doomed = []
    for artifact_id in sorted(artifact_ids):
        if await ownership(session, artifact_id, lock=True) is None:
            continue
        held = (await session.execute(select(AppDataArtifactRef.artifact_id).where(
            AppDataArtifactRef.artifact_id == artifact_id).limit(1))).first()
        if held is None:
            doomed.append(artifact_id)
    await _delete(session, doomed)


async def _delete(session, artifact_ids: list[str]) -> None:
    if not artifact_ids:
        return
    # Claiming refuses a face, but a face set after the claim would point at
    # a thumb that 404s: clear it in this transaction, as the pruner does.
    await store.unlink_agent_images(session, artifact_ids)
    await session.execute(delete(ArtifactBlob).where(ArtifactBlob.artifact_id.in_(artifact_ids)))
    await session.execute(delete(Artifact).where(Artifact.id.in_(artifact_ids)))
    await session.execute(delete(AppDataArtifact).where(
        AppDataArtifact.artifact_id.in_(artifact_ids)))
    await session.flush()


async def sweep_unreferenced(session, app_id: str, *, now: datetime | None = None) -> int:
    """Delete the App's uploads no record referenced within `ORPHAN_AFTER`.
    Commits; returns how many went."""
    cutoff = (now or utcnow()) - ORPHAN_AFTER
    held = select(AppDataArtifactRef.artifact_id).where(
        AppDataArtifactRef.artifact_id == AppDataArtifact.artifact_id).exists()
    ids = list((await session.execute(select(AppDataArtifact.artifact_id).where(
        AppDataArtifact.app_id == app_id, AppDataArtifact.created_at < cutoff, ~held)
    )).scalars())
    # Re-checked under the lock, so a reference landing meanwhile keeps it.
    await _collect(session, set(ids))
    await session.commit()
    return len(ids)


# --- the one-step upload ------------------------------------------------------------------

async def upload(session, ctx, caller: Caller, collection: str, field: str, data: bytes, *,
                 name=None, claimed_mime=None, run_id: str | None = None, producer=None,
                 settings=None, owner: str | None = None) -> Artifact:
    """Store bytes as an artifact App-owned by `collection.field`, for a tool
    or an agent to put in a record next. The caller must be able to write
    that field (create or update, through any tool-only writer), so an upload
    is no way into a field the caller couldn't fill. `owner` is the artifact
    row's participant; it defaults to the caller's own (`agent:<name>`, or
    `user:<principal>` for a human)."""
    from agentplatform.appdata.records import _require_active
    settings = settings or get_settings()
    _require_active(ctx)
    c = ctx.collection(collection)
    if field not in artifact_fields(c):
        raise RecordError("AD-NOT-ARTIFACT-FIELD", f"{collection}.{field} is not an "
                          "artifact field", 422, {"field": field})
    access = ctx.access(c, caller)
    verbs = [verb for verb in ("create", "update") if access.can_write(field, verb)]
    if not verbs:
        raise RecordError("AD-FIELD-FORBIDDEN", f"{caller.principal} may not write "
                          f"{field}", 403, {"field": field})
    if not any(access.tool_allowed(verb) for verb in verbs):
        raise RecordError("AD-TOOL-ONLY", f"{collection} records are written only through "
                          "their tool-only writers", 403, {"field": field})
    cap = settings.app_artifacts_max_bytes
    if len(data) > cap:
        raise RecordError("AD-ARTIFACT-TOO-LARGE", f"an App artifact is at most {cap} bytes",
                          413, {"field": field})
    await check_app_bytes(session, ctx, len(data))

    async def own(s, row):
        s.add(AppDataArtifact(artifact_id=row.id, app_id=ctx.app_id, collection=collection,
                              field=field, size=row.size))

    if owner is None:
        owner = caller.principal if caller.principal.startswith("agent:") \
            else f"user:{caller.principal}"
    try:
        return await store.create(
            session, data=data, owner=owner, name=name, claimed_mime=claimed_mime,
            source="tool" if caller.via_tool else "upload", run_id=run_id,
            meta={"tool": caller.via_tool} if caller.via_tool else None,
            producer=producer, settings=settings, max_bytes=cap, count_total=False,
            before_commit=own,
            event_extra={"app": {"app_id": ctx.app_id, "collection": collection,
                                 "field": field}})
    except store.ArtifactRuleError as exc:
        await session.rollback()
        raise RecordError("AD-ARTIFACT-REFUSED", str(exc), exc.status,
                          {"field": field}) from exc
