"""Immutable source-document fences for design exports and their derivatives.

These checks compose with artifact grants, App ownership and run visibility;
they never grant access on the strength of caller-supplied JSON metadata.
"""

from __future__ import annotations
import copy
import re
from types import SimpleNamespace
from fastapi import HTTPException
from sqlalchemy import and_, exists, func, or_, select, true, update
from sqlalchemy.orm import aliased, load_only
from agentplatform.db import Artifact, Run, UiDesignerDocument
from agentplatform.authority import assert_readable_run
from . import access


def is_design(row):
    return bool(
        getattr(row, "ui_provenance", False)
        or getattr(row, "ui_document_id", None)
        or "ui_designer" in (row.meta or {})
    )


def design_clause(who):
    if who.is_admin:
        return true()
    doc = aliased(UiDesignerDocument)
    marked = or_(
        Artifact.ui_provenance.is_(True),
        Artifact.ui_document_id.is_not(None),
        Artifact.meta["ui_designer"].as_string().is_not(None),
    )
    owner_names = [who.participant]
    if who.participant.startswith("user:"):
        owner_names.append(who.participant[5:])
    known = exists(select(doc.id).where(doc.id == Artifact.ui_document_id))
    readable = exists(
        select(doc.id).where(
            doc.id == Artifact.ui_document_id,
            or_(doc.deleted_at.is_(None), doc.owner.in_(owner_names)),
            access.readable_clause(who, doc),
        )
    )
    return or_(~marked, readable, and_(~known, Artifact.owner == who.participant))


async def require_artifact_read(session, request, row):
    if not is_design(row):
        return
    who = await access.subject(request)
    identifier = getattr(row, "ui_document_id", None)
    if identifier and await session.get(UiDesignerDocument, identifier) is not None:
        await access.require_read_by_uuid(
            session, identifier, who, allow_deleted_owner=True
        )
        return
    if not who.is_admin and row.owner != who.participant:
        raise HTTPException(404, "unknown artifact")


async def visible_clause(session, request, caller=None):
    """Filter before LIMIT/count, retaining the existing run policy verbatim.

    Resolve distinct security envelopes, not artifact pages or byte rows.
    Calling the authoritative run check keeps future policy changes effective
    here without copying its human/local-session/room membership rules.
    """
    who = await access.subject(request, caller)
    envelopes = (
        await session.execute(
            select(
                Run.agent,
                Run.startup_memory_enabled,
                Run.local_persona_session_id,
                Run.conversation_id,
            )
            .join(Artifact, Artifact.run_id == Run.id)
            .distinct()
        )
    ).all()
    denied = []
    for agent, pilot, local, conversation in envelopes:
        run = SimpleNamespace(
            agent=agent,
            startup_memory_enabled=pilot,
            local_persona_session_id=local,
            conversation_id=conversation,
        )
        try:
            await assert_readable_run(session, request, run)
        except HTTPException as error:
            if error.status_code not in (403, 404):
                raise
            denied.append(
                and_(
                    Run.agent == agent,
                    Run.startup_memory_enabled == pilot,
                    Run.local_persona_session_id == local
                    if local
                    else Run.local_persona_session_id.is_(None),
                    Run.conversation_id == conversation
                    if conversation
                    else Run.conversation_id.is_(None),
                )
            )
    run_clause = (
        ~exists(select(Run.id).where(Run.id == Artifact.run_id, or_(*denied)))
        if denied
        else true()
    )
    return and_(design_clause(who), run_clause)


async def backfill_design_artifacts(session):
    """Idempotent startup migration. Caller owns the transaction/serialization.

    Source identity never follows a reused slug into the future. Unknown or
    purged sources stay marked and readable only by artifact owner/admin.
    """
    changed = 0
    query = (
        select(Artifact)
        .options(
            load_only(
                Artifact.id,
                Artifact.meta,
                Artifact.created_at,
                Artifact.ui_document_id,
                Artifact.ui_provenance,
            )
        )
        .where(
            Artifact.ui_document_id.is_(None),
            func.coalesce(Artifact.ui_provenance, False).is_(False),
            Artifact.meta["ui_designer"].as_string().is_not(None),
        )
        .order_by(Artifact.id)
    )
    while True:
        rows = (await session.execute(query.limit(500))).scalars().all()
        if not rows:
            break
        for row in rows:
            row.ui_provenance = True
            source = (row.meta or {}).get("ui_designer")
            source = source if isinstance(source, dict) else {}
            identifier = source.get("document_id")
            document = None
            if isinstance(identifier, str) and re.fullmatch("[0-9a-f]{32}", identifier):
                document = (
                    (
                        await session.execute(
                            select(UiDesignerDocument.id).where(
                                UiDesignerDocument.id == identifier,
                                UiDesignerDocument.created_at <= row.created_at,
                            )
                        )
                    )
                    .scalars()
                    .first()
                )
            # A supplied UUID which no longer exists must never fall back to a
            # different document's slug. Only old slug-only exports need lookup.
            if not identifier and isinstance(source.get("slug"), str):
                document = (
                    (
                        await session.execute(
                            select(UiDesignerDocument.id)
                            .where(
                                UiDesignerDocument.slug == source["slug"],
                                UiDesignerDocument.created_at <= row.created_at,
                            )
                            .order_by(
                                UiDesignerDocument.created_at.desc(),
                                UiDesignerDocument.id.desc(),
                            )
                            .limit(1)
                        )
                    )
                    .scalars()
                    .first()
                )
            if document is not None:
                row.ui_document_id = document
            row.meta = {
                **(row.meta or {}),
                "ui_designer": {**source, "document_id": row.ui_document_id},
            }
            changed += 1
        await session.flush()
    # Legacy derivatives had parent_id but no source UUID. Propagate until no
    # child can be newly classified; parent rows outlive soft deletion.
    while True:
        parent = aliased(Artifact)
        pairs = (
            await session.execute(
                select(Artifact, parent)
                .options(
                    load_only(
                        Artifact.id,
                        Artifact.meta,
                        Artifact.ui_document_id,
                        Artifact.ui_provenance,
                    ),
                    load_only(
                        parent.id,
                        parent.meta,
                        parent.ui_document_id,
                        parent.ui_provenance,
                    ),
                )
                .join(parent, Artifact.meta["parent_id"].as_string() == parent.id)
                .where(
                    or_(
                        func.coalesce(Artifact.ui_provenance, False).is_(False),
                        and_(
                            Artifact.ui_document_id.is_(None),
                            parent.ui_document_id.is_not(None),
                        ),
                    ),
                    parent.ui_provenance.is_(True),
                )
                .limit(500)
            )
        ).all()
        if not pairs:
            break
        for child, parent in pairs:
            child.ui_provenance = True
            child.ui_document_id = parent.ui_document_id
            child.meta = {
                **(child.meta or {}),
                "ui_designer": copy.deepcopy(
                    (parent.meta or {}).get(
                        "ui_designer", {"document_id": parent.ui_document_id}
                    )
                ),
            }
            changed += 1
        await session.flush()
    await session.execute(
        update(Artifact)
        .where(Artifact.ui_provenance.is_(None))
        .values(ui_provenance=False)
    )
    return changed


async def reference_document(
    session, reference_ids, owner, *, who=None, reject_codex=False
):
    """Preflight trusted generation references before any paid work starts."""
    from agentplatform.artifact_store import ArtifactRuleError

    documents = set()
    for identifier in reference_ids:
        row = await session.get(Artifact, identifier)
        if row is None or not is_design(row):
            continue
        if reject_codex:
            raise ArtifactRuleError(
                422,
                "Document-scoped references require direct image generation; the subscription artist cannot preserve source access",
            )
        if not row.ui_document_id:
            raise ArtifactRuleError(404, "reference source document is unavailable")
        who = who or await access.subject_for(session, access.owner_principal(owner))
        try:
            await access.require_read_by_uuid(
                session, row.ui_document_id, who, allow_deleted_owner=True
            )
        except HTTPException:
            raise ArtifactRuleError(404, "unknown reference artifact") from None
        documents.add(row.ui_document_id)
    if len(documents) > 1:
        raise ArtifactRuleError(
            422,
            "References from multiple design documents cannot be combined into one artifact",
        )
    return next(iter(documents), None)
