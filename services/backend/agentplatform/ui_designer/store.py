"""The UI Designer store (docs/design/46 §2.1, §9, §10): the one place a
document, its history, its assets, receipts, imports and render cache change.

Session-first functions like `wiki_store`; they flush but never commit, so the
caller's transaction decides. Versions: the number increments on every accepted
write; what is folded is the ROW (§10), and every number stays resolvable by
replaying the row's stored chunks onto the previous row's snapshot.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import zlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from agentplatform.db import (UiDesignerAsset, UiDesignerComment, UiDesignerDocument,
                              UiDesignerDocumentAsset, UiDesignerImport, UiDesignerReceipt,
                              UiDesignerRender, UiDesignerVersion, utcnow)
from agentplatform.ui_designer import errors
from agentplatform.ui_designer.errors import UiDesignerError, invalid, issue
from agentplatform.ui_designer.ops import apply_ops
from agentplatform.ui_designer.schema import (SHA_RE, check_expansion, image_assets, index_nodes,
                                              new_document, normalize_document, validate_document)

UD_NOT_FOUND = errors.UD_NOT_FOUND
UD_SLUG_TAKEN = errors.UD_SLUG_TAKEN
UD_REQUEST_REUSED = errors.UD_REQUEST_REUSED
UD_BUDGET_STORAGE = errors.UD_BUDGET_STORAGE

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
FOLD_WINDOW = timedelta(seconds=120)
RECEIPT_TTL = timedelta(days=7)
MAX_ASSET_BYTES = 8 << 20
MAX_ASSET_BYTES_PER_VERSION = 16 << 20
MAX_RECEIPT_BYTES = 16 << 10
MAX_RECEIPT_WARNINGS = 20
RECEIPT_KEYS = {"version", "warnings", "text", "wants_snapshot", "slug", "ok"}
_SHA_IN_TEXT = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class Caps:
    """`ui_designer.max_total_bytes` / `max_version_bytes_per_doc` (chart values)."""
    max_total_bytes: int = 48 << 20
    max_version_bytes_per_doc: int = 12 << 20


# --- small helpers ----------------------------------------------------------

def _aware(ts: datetime | None) -> datetime | None:
    return ts if ts is None or ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _iso(ts: datetime | None) -> str | None:
    ts = _aware(ts)
    return ts.isoformat() if ts else None


def _canon(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def stored_size(*objs: Any) -> int:
    """Compressed JSON size: the proxy for JSONB TOAST that the caps count."""
    return len(zlib.compress(_canon([o for o in objs if o is not None]), 3))


def args_hash(**args: Any) -> str:
    return hashlib.sha256(_canon(args)).hexdigest()


def _for_update(s, stmt):
    return stmt.with_for_update() if s.get_bind().dialect.name == "postgresql" else stmt


def _not_found(what: str) -> UiDesignerError:
    return UiDesignerError(UD_NOT_FOUND, f"no {what}")


def _check_slug(slug: Any) -> str:
    if not isinstance(slug, str) or not SLUG_RE.match(slug):
        raise invalid([issue("UD-FORMAT", "$.slug", "slug must match ^[a-z0-9][a-z0-9-]{0,63}$", slug)])
    return slug


def document_view(row: UiDesignerDocument, *, doc: dict | None = None, at_version: int | None = None) -> dict:
    return {"id": row.id, "slug": row.slug, "title": row.title, "description": row.description,
            "owner": row.owner, "version": row.version, "doc": copy.deepcopy(row.doc if doc is None else doc),
            "at_version": row.version if at_version is None else at_version,
            "doc_sha256": row.doc_sha256, "node_count": row.node_count,
            "expanded_count": row.expanded_count, "bytes": row.bytes,
            "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at),
            "deleted_at": _iso(row.deleted_at)}


async def _live(s, slug: str, *, lock: bool = False) -> UiDesignerDocument:
    stmt = select(UiDesignerDocument).where(UiDesignerDocument.slug == slug,
                                            UiDesignerDocument.deleted_at.is_(None))
    if lock:
        # populate_existing: a row already in the identity map must be
        # refreshed from the locked read, or the CAS below sees stale columns.
        stmt = _for_update(s, stmt).execution_options(populate_existing=True)
    row = (await s.execute(stmt)).scalars().first()
    if row is None:
        raise _not_found(f"document {slug!r}")
    return row


# --- storage caps -----------------------------------------------------------

async def usage(s) -> dict:
    """Bytes the caps count: versions + documents (compressed), assets (raw)."""
    async def total(col):
        return int((await s.execute(select(func.coalesce(func.sum(col), 0)))).scalar_one())
    versions, docs, assets = (await total(UiDesignerVersion.bytes), await total(UiDesignerDocument.bytes),
                              await total(UiDesignerAsset.bytes))
    comments = await total(func.length(UiDesignerComment.body))
    staging = sum(len(_canon(v)) for v in (await s.execute(select(UiDesignerImport.staged).where(
        UiDesignerImport.state.in_(("queued", "running"))))).scalars())
    return {"versions": versions, "documents": docs, "assets": assets, "comments": comments,
            "total": versions + docs + assets + comments + staging}


async def _doc_version_bytes(s, document_id: str) -> int:
    return int((await s.execute(select(func.coalesce(func.sum(UiDesignerVersion.bytes), 0)).where(
        UiDesignerVersion.document_id == document_id))).scalar_one())


async def _check_caps(s, caps: Caps, add: int, document_id: str | None = None, what: str = "write") -> None:
    if add <= 0 and document_id is None:
        return
    used = (await usage(s))["total"]
    if used + add > caps.max_total_bytes:
        raise UiDesignerError(UD_BUDGET_STORAGE, f"this {what} would take the designer to "
                              f"{used + add} bytes; the total cap is {caps.max_total_bytes}",
                              detail={"cap": "max_total_bytes", "used": used, "limit": caps.max_total_bytes})
    if document_id is not None:
        doc_used = await _doc_version_bytes(s, document_id)
        if doc_used + add > caps.max_version_bytes_per_doc:
            raise UiDesignerError(UD_BUDGET_STORAGE, f"this {what} would take the document's versions to "
                                  f"{doc_used + add} bytes; the per-document cap is "
                                  f"{caps.max_version_bytes_per_doc}",
                                  detail={"cap": "max_version_bytes_per_doc", "used": doc_used,
                                          "limit": caps.max_version_bytes_per_doc})


# --- receipts ---------------------------------------------------------------

def cap_warnings(warnings: list) -> list:
    """The first MAX_RECEIPT_WARNINGS plus a count line, so lint volume can
    never push a valid edit's receipt past its size cap."""
    if len(warnings) <= MAX_RECEIPT_WARNINGS:
        return warnings
    kept = [{"code": w.get("code"), "message": str(w.get("message", ""))[:200],
             **({"path": w["path"]} if w.get("path") else {})} if isinstance(w, dict) else str(w)[:200]
            for w in warnings[:MAX_RECEIPT_WARNINGS]]
    return [*kept, {"code": "UD-WARN-TRUNCATED",
                    "message": f"{len(warnings) - MAX_RECEIPT_WARNINGS} more warnings not stored"}]


def _check_receipt_response(response: dict) -> dict:
    bad = set(response) - RECEIPT_KEYS
    if bad:
        raise ValueError(f"receipt responses are text only; refused keys {sorted(bad)}")
    raw = _canon(response)
    if len(raw) > MAX_RECEIPT_BYTES:
        raise ValueError("receipt response too large; a receipt never holds a snapshot")
    return json.loads(raw)


async def _receipt_lookup(s, scope: str, request_id: str, op: str, hash_: str) -> dict | None:
    row = (await s.execute(select(UiDesignerReceipt).where(
        UiDesignerReceipt.scope == scope, UiDesignerReceipt.request_id == request_id))).scalars().first()
    if row is None:
        return None
    if row.args_hash != hash_ or row.op != op:
        raise UiDesignerError(UD_REQUEST_REUSED, f"request_id {request_id!r} was already used for a "
                              "different call", detail={"request_id": request_id})
    return replay_view(row.response)


def replay_view(response: dict) -> dict:
    out = {**response, "replayed": True}
    if out.get("wants_snapshot"):
        out["text"] = (out.get("text", "") + "\nreplayed: call snapshot for the pictures").strip()
    return out


async def _receipt_write(s, scope: str, request_id: str, document_id: str | None, op: str,
                         hash_: str, response: dict, now: datetime) -> None:
    s.add(UiDesignerReceipt(scope=scope, request_id=request_id, document_id=document_id, op=op,
                            args_hash=hash_, response=_check_receipt_response(response), created_at=now))
    await s.flush()


def _scope(scope: str | None, run_id: str | None, request_id: str | None) -> str | None:
    if request_id is None:
        return None
    scope = scope or run_id
    if not scope:
        raise ValueError("a request_id needs a scope (the run id, or the session id for a human)")
    return scope


async def get_receipt(s, scope: str, request_id: str) -> dict | None:
    row = (await s.execute(select(UiDesignerReceipt).where(
        UiDesignerReceipt.scope == scope, UiDesignerReceipt.request_id == request_id))).scalars().first()
    return None if row is None else {"op": row.op, "args_hash": row.args_hash, "response": row.response,
                                     "document_id": row.document_id}


# --- documents --------------------------------------------------------------

def create_fingerprint(*, slug: str, title: str, description: str, doc: dict | None,
                       template: dict | None = None) -> str:
    if template is not None:
        if (set(template) != {"id", "revision"} or not isinstance(template["id"], str)
                or not isinstance(template["revision"], int) or isinstance(template["revision"], bool)
                or template["revision"] < 1):
            raise ValueError("template fingerprints require an explicit immutable id and revision")
        return args_hash(op="create", slug=slug, title=title, description=description, template=template)
    return args_hash(op="create", slug=slug, title=title, description=description, doc=doc)


async def create_document(s, *, slug: str, title: str = "", owner: str, author: str | None = None,
                          run_id: str | None = None, doc: dict | None = None, description: str = "",
                          reason: str = "", caps: Caps = Caps(), request_id: str | None = None,
                          scope: str | None = None, now: datetime | None = None,
                          imported_assets: set[str] | None = None,
                          import_id: str | None = None, _template: dict | None = None) -> dict:
    now = now or utcnow()
    author = author or owner
    scope = _scope(scope, run_id, request_id)
    h = create_fingerprint(slug=slug, title=title, description=description, doc=doc, template=_template)
    if scope:
        prior = await _receipt_lookup(s, scope, request_id, "create", h)
        if prior is not None:
            return prior
    _check_slug(slug)
    if len(description) > 2000:
        raise invalid([issue("UD-LIMIT", "$.description", "description is at most 2000 characters")])
    if len(title) > 120:
        raise invalid([issue("UD-LIMIT", "$.title", "title is at most 120 characters", title)])
    body = normalize_document(doc) if doc is not None else new_document(title)
    validate_document(body, known_assets=imported_assets or set())
    if imported_assets:
        total = (await s.execute(select(func.coalesce(func.sum(UiDesignerAsset.bytes), 0)).where(
            UiDesignerAsset.sha256.in_(imported_assets)))).scalar_one()
        if total > MAX_ASSET_BYTES_PER_VERSION:
            raise invalid([issue("UD-LIMIT", "$.assets", "imported assets exceed the per-version limit",
                                 total)])
    size = stored_size(body, description)
    await _check_caps(s, caps, size * 2, None, "create")  # the document row and version 1
    row = UiDesignerDocument(slug=slug, title=title or str(body.get("title") or "")[:120],
                             description=description, owner=owner, version=1, doc=body,
                             doc_sha256=hashlib.sha256(_canon(body)).hexdigest(),
                             node_count=len(index_nodes(body)), expanded_count=check_expansion(body),
                             bytes=size, last_reader=author, created_at=now, updated_at=now)
    try:
        async with s.begin_nested():
            s.add(row)
            await s.flush()
    except IntegrityError as exc:
        raise UiDesignerError(UD_SLUG_TAKEN, f"a document called {slug!r} already exists") from exc
    for sha in imported_assets or set():
        if await s.get(UiDesignerAsset, sha) is None:
            raise UiDesignerError("UD-ASSET-UNKNOWN", "import asset disappeared")
        s.add(UiDesignerDocumentAsset(document_id=row.id, sha256=sha, placed_by=author,
                                      placed_from=f"import:{import_id}" if import_id else "figma-import",
                                      placed_at=now))
    chunk = {"version": 1, "ops": [], "reason": reason or "create", "at": _iso(now)}
    s.add(UiDesignerVersion(document_id=row.id, version=1, first_version=1, doc=body, chunks=[chunk],
                            author=author, run_id=run_id, bytes=stored_size(body, [chunk]),
                            created_at=now, last_write_at=now))
    if scope:
        await _receipt_write(s, scope, request_id, row.id, "create", h,
                             {"version": 1, "warnings": [], "slug": slug, "text": "created"}, now)
    await s.flush()
    return {**document_view(row), "replayed": False, "warnings": []}


async def list_documents(s, *, include_deleted: bool = False, limit: int = 100, q: str | None = None,
                         before: str | None = None) -> list[dict]:
    """Newest-updated first. `q` matches slug or title (case-insensitive);
    `before` is the slug of the last row of the previous page (the cursor)."""
    stmt = select(UiDesignerDocument).order_by(UiDesignerDocument.updated_at.desc(),
                                               UiDesignerDocument.id.desc()).limit(limit)
    if not include_deleted:
        stmt = stmt.where(UiDesignerDocument.deleted_at.is_(None))
    if q:
        like = "%" + q.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        stmt = stmt.where(func.lower(UiDesignerDocument.slug).like(like, escape="\\")
                          | func.lower(UiDesignerDocument.title).like(like, escape="\\"))
    if before:
        anchor = await _live(s, before)
        stmt = stmt.where((UiDesignerDocument.updated_at < anchor.updated_at)
                          | ((UiDesignerDocument.updated_at == anchor.updated_at)
                             & (UiDesignerDocument.id < anchor.id)))
    return [{k: v for k, v in document_view(r).items() if k != "doc"}
            for r in (await s.execute(stmt)).scalars()]


def doc_sha256(doc: dict) -> str:
    return hashlib.sha256(_canon(doc)).hexdigest()


async def write_times(s, author: str, since: datetime) -> list[datetime]:
    """When `author` wrote (one entry per accepted write, folded rows included)
    since `since`, oldest first: the input to the hourly write budget."""
    since = _aware(since)
    out: list[datetime] = []
    for chunks, in (await s.execute(select(UiDesignerVersion.chunks).where(
            UiDesignerVersion.author == author, UiDesignerVersion.last_write_at >= since))):
        for c in chunks or []:
            try:
                at = _aware(datetime.fromisoformat(c["at"]))
            except (KeyError, TypeError, ValueError):
                continue
            if at >= since:
                out.append(at)
    for (at,) in (await s.execute(select(UiDesignerComment.created_at).where(
            UiDesignerComment.author == author, UiDesignerComment.created_at >= since))):
        out.append(_aware(at))
    return sorted(out)


async def get_document(s, slug: str, *, reader: str | None = None, version: int | None = None) -> dict:
    """The live document, or its exact state at `version`. A read by `reader`
    records them as the last reader, which stops another principal's burst
    folding into a row this reader has seen."""
    row = await _live(s, slug, lock=reader is not None)
    if reader is not None:
        row.last_reader = reader
    if version is None or version == row.version:
        return document_view(row)
    doc = await resolve_version(s, row, version)
    if doc is None:
        raise _not_found(f"version {version} of {slug!r}")
    return document_view(row, doc=doc, at_version=version)


async def _row_for(s, document_id: str, n: int) -> UiDesignerVersion | None:
    return (await s.execute(select(UiDesignerVersion).where(
        UiDesignerVersion.document_id == document_id, UiDesignerVersion.first_version <= n,
        UiDesignerVersion.version >= n))).scalars().first()


async def resolve_version(s, row: UiDesignerDocument, n: int) -> dict | None:
    """The document at number `n`: the row's snapshot when `n` is its last
    write, otherwise the previous row's snapshot (or the row's own `base_doc`
    after a prune) with the chunks up to `n` replayed. None when `n` was
    never written or has been pruned."""
    if isinstance(n, bool) or not isinstance(n, int) or n < 1 or n > row.version:
        return None
    v = await _row_for(s, row.id, n)
    if v is None:
        return None
    if n == v.version:
        return copy.deepcopy(v.doc)
    base = v.base_doc
    if base is None:
        prev = (await s.execute(select(UiDesignerVersion).where(
            UiDesignerVersion.document_id == row.id,
            UiDesignerVersion.version == v.first_version - 1))).scalars().first()
        base = prev.doc if prev is not None else None
    if base is None:
        return None
    cur = copy.deepcopy(base)
    for chunk in v.chunks:
        if chunk["version"] > n:
            break
        cur = apply_ops(cur, chunk["ops"], version=chunk["version"] - 1,
                        expected_version=chunk["version"] - 1).doc
    return cur


def row_is_open(v: UiDesignerVersion, doc: UiDesignerDocument, now: datetime) -> bool:
    """Can this row still fold? Only the current row can; the window runs from
    its first write; a label, a run-less write, the creation row, or another
    principal's read or write closes it."""
    return (v.version == doc.version and v.label is None and v.run_id is not None and v.first_version > 1
            and now - _aware(v.created_at) <= FOLD_WINDOW and doc.last_reader in (None, v.author))


async def version_state(s, slug: str, n: int, now: datetime | None = None) -> str | None:
    """`open` / `closed` / None (no such number). `immutable` caching is only
    for `closed`."""
    row = await _live(s, slug)
    v = await _row_for(s, row.id, n)
    if v is None:
        return None
    return "open" if row_is_open(v, row, now or utcnow()) else "closed"


async def chunks_after(s, document_id: str, n: int) -> list[dict]:
    rows = (await s.execute(select(UiDesignerVersion).where(
        UiDesignerVersion.document_id == document_id, UiDesignerVersion.version > n)
        .order_by(UiDesignerVersion.version))).scalars().all()
    return [c for v in rows for c in v.chunks if c["version"] > n]


async def placed_assets(s, document_id: str) -> set[str]:
    return set((await s.execute(select(UiDesignerDocumentAsset.sha256).where(
        UiDesignerDocumentAsset.document_id == document_id))).scalars())


def edit_fingerprint(*, op: str, slug: str, ops: Any, expected_version: Any,
                     reason: str, stable_args: dict | None = None) -> str:
    """Library receipts bind caller inputs, before any source lookup or planning."""
    if stable_args is not None:
        if op not in ("library-import", "library-apply"):
            raise ValueError("stable input fingerprints are only for library mutations")
        return args_hash(op=op, slug=slug, expected_version=expected_version,
                         reason=reason, stable_args=stable_args)
    return args_hash(op=op, slug=slug, ops=ops, expected_version=expected_version, reason=reason)


async def edit_document(s, slug: str, *, ops: Any, expected_version: Any, author: str,
                        run_id: str | None = None, reason: str = "", request_id: str | None = None,
                        scope: str | None = None, wants_snapshot: bool = False, op: str = "edit",
                        fold: bool = True, caps: Caps = Caps(), now: datetime | None = None,
                        _stable_args: dict | None = None) -> dict:
    """One accepted write = one version number. Returns `{version, warnings,
    doc, replayed}`; a repeat of `(scope, request_id)` with the same args
    answers from the receipt (text only) and writes nothing."""
    now = now or utcnow()
    scope = _scope(scope, run_id, request_id)
    h = edit_fingerprint(op=op, slug=slug, ops=ops, expected_version=expected_version,
                         reason=reason, stable_args=_stable_args)
    if scope:
        prior = await _receipt_lookup(s, scope, request_id, op, h)
        if prior is not None:
            return prior
    row = await _live(s, slug, lock=True)
    since = await chunks_after(s, row.id, expected_version) if (
        isinstance(expected_version, int) and not isinstance(expected_version, bool)
        and expected_version != row.version and 0 <= expected_version < row.version) else []
    res = apply_ops(row.doc, ops, version=row.version, expected_version=expected_version,
                    ops_since=since, known_assets=await placed_assets(s, row.id))
    new = res.doc
    shas = {a for _, a in image_assets(new)}
    if shas:
        total = int((await s.execute(select(func.coalesce(func.sum(UiDesignerAsset.bytes), 0)).where(
            UiDesignerAsset.sha256.in_(shas)))).scalar_one())
        if total > MAX_ASSET_BYTES_PER_VERSION:
            raise invalid([issue("UD-LIMIT", "$.ops", f"images in one document version total {total} "
                                 f"bytes; the limit is {MAX_ASSET_BYTES_PER_VERSION}")])
    n = res.version
    chunk = {"version": n, "ops": res.ops, "reason": reason, "at": _iso(now)}
    latest = (await s.execute(select(UiDesignerVersion).where(
        UiDesignerVersion.document_id == row.id, UiDesignerVersion.version == row.version))).scalars().first()
    fold_into = (latest if fold and run_id is not None and latest is not None and latest.run_id == run_id
                 and latest.author == author and row_is_open(latest, row, now) else None)
    doc_size = stored_size(new, row.description)
    if fold_into is not None:
        chunks = [*fold_into.chunks, chunk]
        new_size = stored_size(new, chunks, fold_into.base_doc)
        await _check_caps(s, caps, doc_size + max(new_size - fold_into.bytes, 0), row.id, "edit")
        fold_into.doc, fold_into.chunks, fold_into.version = new, chunks, n
        fold_into.bytes, fold_into.last_write_at = new_size, now
    else:
        new_size = stored_size(new, [chunk])
        await _check_caps(s, caps, doc_size + new_size, row.id, "edit")
        s.add(UiDesignerVersion(document_id=row.id, version=n, first_version=n, doc=new, chunks=[chunk],
                                author=author, run_id=run_id, bytes=new_size, created_at=now,
                                last_write_at=now))
    row.doc, row.version, row.updated_at, row.bytes = new, n, now, doc_size
    row.doc_sha256 = hashlib.sha256(_canon(new)).hexdigest()
    row.node_count, row.expanded_count = len(index_nodes(new)), check_expansion(new)
    row.title = str(new.get("title") or row.title)[:120]
    row.last_reader = author
    if scope:
        await _receipt_write(s, scope, request_id, row.id, op, h,
                             {"version": n, "warnings": cap_warnings(res.warnings), "slug": slug,
                              "text": f"applied {len(ops)} op(s); document is at version {n}",
                              "wants_snapshot": bool(wants_snapshot)}, now)
    await s.flush()
    return {"version": n, "warnings": res.warnings, "doc": copy.deepcopy(new), "replayed": False,
            "folded": fold_into is not None}


async def restore_version(s, slug: str, version: int, *, expected_version: int, author: str,
                          run_id: str | None = None, request_id: str | None = None,
                          scope: str | None = None, caps: Caps = Caps(), now: datetime | None = None) -> dict:
    """Restore is a new version holding an old one's content (never folded)."""
    row = await _live(s, slug, lock=True)  # lock before reading; edit_document's CAS then sees fresh state
    old = await resolve_version(s, row, version)
    if old is None:
        raise _not_found(f"version {version} of {slug!r}")
    return await edit_document(s, slug, ops=[{"op": "set_doc", "doc": old}], expected_version=expected_version,
                               author=author, run_id=run_id, reason=f"restore v{version}",
                               request_id=request_id, scope=scope, op="restore", fold=False,
                               caps=caps, now=now)


async def history(s, slug: str, *, limit: int = 100, before: int | None = None,
                  now: datetime | None = None) -> list[dict]:
    """Rows newest first, as `v7`, `v8–v12`, `v13`. `before` is a version
    number: only rows whose last number is below it (the cursor)."""
    now = now or utcnow()
    row = await _live(s, slug)
    stmt = select(UiDesignerVersion).where(UiDesignerVersion.document_id == row.id)
    if before is not None:
        stmt = stmt.where(UiDesignerVersion.version < before)
    rows = (await s.execute(stmt.order_by(UiDesignerVersion.version.desc()).limit(limit))).scalars().all()
    return [{"range": f"v{v.first_version}" if v.first_version == v.version
             else f"v{v.first_version}–v{v.version}", "first_version": v.first_version,
             "version": v.version, "author": v.author, "run_id": v.run_id, "label": v.label,
             "open": row_is_open(v, row, now), "created_at": _iso(v.created_at),
             "last_write_at": _iso(v.last_write_at),
             "reasons": [c.get("reason", "") for c in v.chunks]} for v in rows]


async def history_diff(s, slug: str, from_version: int, to_version: int) -> list[dict]:
    """The chunks `from+1 … to`, in order."""
    if not (isinstance(from_version, int) and isinstance(to_version, int)) or from_version >= to_version:
        raise invalid([issue("UD-BOUNDS", "$.to", "`to` must be greater than `from`")])
    row = await _live(s, slug)
    return [c for c in await chunks_after(s, row.id, from_version) if c["version"] <= to_version]


async def set_label(s, slug: str, label: str | None, version: int | None = None) -> None:
    """Name the row holding `version` (default: the current one). A label
    protects the whole row from prune and from folding."""
    if label is not None and not (1 <= len(label) <= 64):
        raise invalid([issue("UD-LIMIT", "$.label", "label is 1 to 64 characters", label)])
    row = await _live(s, slug)
    v = await _row_for(s, row.id, row.version if version is None else version)
    if v is None:
        raise _not_found(f"version {version} of {slug!r}")
    v.label = label
    await s.flush()


# --- delete and prune -------------------------------------------------------

async def soft_delete(s, slug: str, now: datetime | None = None) -> None:
    row = await _live(s, slug, lock=True)
    row.deleted_at = now or utcnow()
    await s.flush()


async def restore_deleted(s, document_id: str) -> None:
    """Undo a soft delete, unless the slug was reused meanwhile."""
    row = await s.get(UiDesignerDocument, document_id)
    if row is None or row.deleted_at is None:
        raise _not_found("deleted document")
    row.deleted_at = None
    try:
        async with s.begin_nested():
            await s.flush()
    except IntegrityError as exc:
        raise UiDesignerError(UD_SLUG_TAKEN, f"{row.slug!r} has been reused") from exc


async def _purge(s, document_id: str) -> None:
    for model in (UiDesignerVersion, UiDesignerComment, UiDesignerDocumentAsset, UiDesignerRender,
                  UiDesignerReceipt):
        await s.execute(delete(model).where(model.document_id == document_id))
    await s.execute(delete(UiDesignerDocument).where(UiDesignerDocument.id == document_id))


async def hard_delete(s, ref: str) -> dict:
    """Remove a document (by live slug, else by id), its versions, comments,
    placement rows, cached renders and receipts, then collect assets."""
    row = (await s.execute(select(UiDesignerDocument).where(
        UiDesignerDocument.slug == ref, UiDesignerDocument.deleted_at.is_(None)))).scalars().first()
    row = row or await s.get(UiDesignerDocument, ref)
    if row is None:
        raise _not_found(f"document {ref!r}")
    await _purge(s, row.id)
    return {"deleted": row.id, **await gc_assets(s)}


async def hard_delete_expired(s, cutoff: datetime) -> int:
    ids = (await s.execute(select(UiDesignerDocument.id).where(
        UiDesignerDocument.deleted_at.isnot(None), UiDesignerDocument.deleted_at < cutoff))).scalars().all()
    for i in ids:
        await _purge(s, i)
    if ids:
        await gc_assets(s)
    return len(ids)


async def prune_receipts(s, cutoff: datetime) -> int:
    return (await s.execute(delete(UiDesignerReceipt).where(
        UiDesignerReceipt.created_at < cutoff))).rowcount or 0


async def prune_versions(s, slug: str, *, older_than_days: int, keep_last: int = 20,
                         now: datetime | None = None) -> dict:
    """Remove unlabeled version rows last written more than N days ago beyond
    the newest `keep_last` rows. A surviving multi-write row whose predecessor
    goes keeps its replay base as `base_doc`, so every surviving number stays
    exact. The current row is never removed. Then asset GC."""
    now = now or utcnow()
    if keep_last < 1 or older_than_days < 0:
        raise invalid([issue("UD-BOUNDS", "$.keep_last", "keep_last >= 1 and older_than_days >= 0")])
    row = await _live(s, slug, lock=True)
    rows = (await s.execute(select(UiDesignerVersion).where(UiDesignerVersion.document_id == row.id)
                            .order_by(UiDesignerVersion.version.desc()))).scalars().all()
    cutoff = now - timedelta(days=older_than_days)
    doomed = {v.id for v in rows[keep_last:] if v.label is None and _aware(v.last_write_at) < cutoff}
    by_version = {v.version: v for v in rows}
    for v in rows:
        if v.id in doomed or v.base_doc is not None or v.first_version == v.version:
            continue
        prev = by_version.get(v.first_version - 1)
        if prev is not None and prev.id in doomed:
            v.base_doc = prev.doc
            v.bytes = stored_size(v.doc, v.chunks, v.base_doc)
    for v in rows:
        if v.id in doomed:
            await s.delete(v)
    await s.flush()
    return {"removed_rows": len(doomed), **await gc_assets(s)}


# --- assets -----------------------------------------------------------------

async def add_asset(s, *, data: bytes, mime: str, width: int | None = None, height: int | None = None,
                    caps: Caps = Caps(), now: datetime | None = None) -> str:
    """Store image bytes by sha256 (idempotent); returns the sha256."""
    if not data or len(data) > MAX_ASSET_BYTES:
        raise invalid([issue("UD-LIMIT", "$.asset", f"an asset is 1 to {MAX_ASSET_BYTES} bytes", len(data))])
    sha = hashlib.sha256(data).hexdigest()
    if await s.get(UiDesignerAsset, sha) is None:
        await _check_caps(s, caps, len(data), None, "place")
        s.add(UiDesignerAsset(sha256=sha, data=data, mime=mime, width=width, height=height,
                              bytes=len(data), created_at=now or utcnow()))
        await s.flush()
    return sha


async def place_asset(s, slug: str, *, data: bytes, mime: str, placed_by: str, placed_from: str,
                      width: int | None = None, height: int | None = None, caps: Caps = Caps(),
                      now: datetime | None = None) -> dict:
    """Copy bytes into the store and record the placement (the authorization,
    §9, is the API layer's). Idempotent per (document, sha256)."""
    now = now or utcnow()
    row = await _live(s, slug)
    await _check_caps(s, caps, 0, row.id, "place")
    sha = await add_asset(s, data=data, mime=mime, width=width, height=height, caps=caps, now=now)
    # Lock the asset row so a concurrent GC delete cannot interleave with the
    # placement insert; if GC won the race and removed it, store it again.
    locked = (await s.execute(_for_update(s, select(UiDesignerAsset.sha256).where(
        UiDesignerAsset.sha256 == sha)))).scalars().first()
    if locked is None:
        await add_asset(s, data=data, mime=mime, width=width, height=height, caps=caps, now=now)
    if await s.get(UiDesignerDocumentAsset, (row.id, sha)) is None:
        s.add(UiDesignerDocumentAsset(document_id=row.id, sha256=sha, placed_by=placed_by,
                                      placed_from=placed_from, placed_at=now))
        await s.flush()
    return {"sha256": sha, "bytes": len(data), "mime": mime}


async def get_asset(s, sha: str) -> UiDesignerAsset | None:
    return await s.get(UiDesignerAsset, sha) if SHA_RE.match(sha or "") else None


async def gc_assets(s) -> dict:
    """Delete assets that no placement row, no retained version (rows, chunks,
    replay bases), no current document and no queued/running import names."""
    placed = select(UiDesignerDocumentAsset.sha256)
    cands = set((await s.execute(select(UiDesignerAsset.sha256).where(
        UiDesignerAsset.sha256.notin_(placed)))).scalars())
    if not cands:
        return {"assets_removed": 0, "bytes_freed": 0}
    live: set[str] = set()

    def scan(*objs: Any) -> None:
        live.update(_SHA_IN_TEXT.findall(_canon(objs).decode()) if objs else [])
    for doc, in (await s.execute(select(UiDesignerDocument.doc))):
        scan(doc)
    for doc, chunks, base in (await s.execute(select(UiDesignerVersion.doc, UiDesignerVersion.chunks,
                                                     UiDesignerVersion.base_doc))):
        scan(doc, chunks, base)
    for staged, in (await s.execute(select(UiDesignerImport.staged).where(
            UiDesignerImport.state.in_(("queued", "running"))))):
        scan(staged)
    doomed = cands - live
    freed = 0
    if doomed:
        # Acquire row locks in their own statement. Under PostgreSQL READ
        # COMMITTED, DELETE's snapshot can otherwise predate a placement that
        # commits while DELETE waits for that asset lock (there is no FK).
        # The following DELETE gets a fresh snapshot after every lock wait.
        doomed = set((await s.execute(_for_update(s, select(UiDesignerAsset.sha256).where(
            UiDesignerAsset.sha256.in_(doomed)).order_by(UiDesignerAsset.sha256)))).scalars())
        if not doomed:
            return {"assets_removed": 0, "bytes_freed": 0}
        freed = int((await s.execute(select(func.coalesce(func.sum(UiDesignerAsset.bytes), 0)).where(
            UiDesignerAsset.sha256.in_(doomed)))).scalar_one())
        # The delete re-checks placement itself, so a placement committed after
        # the scan above keeps its asset.
        placed_now = select(UiDesignerDocumentAsset.sha256).where(
            UiDesignerDocumentAsset.sha256 == UiDesignerAsset.sha256).exists()
        res = await s.execute(delete(UiDesignerAsset).where(
            UiDesignerAsset.sha256.in_(doomed), ~placed_now).execution_options(synchronize_session=False))
        removed = res.rowcount if res.rowcount is not None and res.rowcount >= 0 else len(doomed)
        if removed != len(doomed):
            freed = int(freed * removed / len(doomed))
        return {"assets_removed": removed, "bytes_freed": freed}
    return {"assets_removed": 0, "bytes_freed": 0}


# --- imports (Figma, §8) ----------------------------------------------------

IMPORT_STATES = ("queued", "running", "done", "failed")


async def active_import(s, slug: str) -> UiDesignerImport | None:
    return (await s.execute(select(UiDesignerImport).where(
        UiDesignerImport.slug == slug, UiDesignerImport.state.in_(("queued", "running"))))).scalars().first()


async def start_import(s, *, slug: str, file_key: str, initiated_by: str,
                       now: datetime | None = None) -> tuple[UiDesignerImport, bool]:
    """Idempotent on `slug` while a job is queued or running: returns
    `(job, created)`."""
    _check_slug(slug)
    existing = await active_import(s, slug)
    if existing is not None:
        return existing, False
    now = now or utcnow()
    job = UiDesignerImport(slug=slug, file_key=file_key, initiated_by=initiated_by, created_at=now,
                           updated_at=now)
    try:
        async with s.begin_nested():
            s.add(job)
            await s.flush()
    except IntegrityError:
        return await active_import(s, slug), False
    return job, True


async def stage_page(s, import_id: str, page_key: str, *, staged: dict, counts: dict | None = None,
                     status: str = "done", now: datetime | None = None) -> UiDesignerImport:
    """The durable checkpoint: a completed page's converted subtree, asset
    sha256s and warnings, so a restart resumes from the next page."""
    if status not in ("pending", "done", "failed"):
        raise ValueError(f"bad page status {status!r}")
    job = await s.get(UiDesignerImport, import_id)
    if job is None:
        raise _not_found(f"import {import_id!r}")
    job.pages = {**job.pages, page_key: {"state": status, **(counts or {})}}
    if status == "done":
        job.staged = {**job.staged, page_key: staged}
    job.updated_at = now or utcnow()
    await s.flush()
    return job


async def set_import_state(s, import_id: str, state: str, *, report: dict | None = None,
                           error: str | None = None, now: datetime | None = None) -> UiDesignerImport:
    if state not in IMPORT_STATES:
        raise ValueError(f"bad import state {state!r}")
    job = await s.get(UiDesignerImport, import_id)
    if job is None:
        raise _not_found(f"import {import_id!r}")
    job.state, job.updated_at = state, now or utcnow()
    if report is not None:
        job.report = report
    if error is not None:
        job.error = error
    await s.flush()
    return job


# --- render cache (derivable; not backed up) --------------------------------

def render_key(renderer_version: str, doc_sha256: str, node_id: str, mode: str, scale: float | int,
               annotate: bool | str) -> str:
    return hashlib.sha256("\x1f".join(str(p) for p in (renderer_version, doc_sha256, node_id, mode,
                                                         scale, annotate)).encode()).hexdigest()


async def render_get(s, key: str, now: datetime | None = None) -> UiDesignerRender | None:
    row = await s.get(UiDesignerRender, key)
    if row is not None:
        row.last_used = now or utcnow()
        await s.flush()
    return row


async def render_put(s, key: str, *, document_id: str, png: bytes, width: int, height: int,
                     max_bytes: int = 256 << 20, now: datetime | None = None) -> bool:
    """Cache a render, then evict least-recently-used rows above `max_bytes`.
    False when one image alone exceeds the cap."""
    if len(png) > max_bytes:
        return False
    now = now or utcnow()
    row = await s.get(UiDesignerRender, key)
    if row is None:
        s.add(UiDesignerRender(key=key, document_id=document_id, png=png, width=width, height=height,
                               bytes=len(png), created_at=now, last_used=now))
    else:
        row.png, row.width, row.height, row.bytes, row.last_used = png, width, height, len(png), now
    await s.flush()
    total = int((await s.execute(select(func.coalesce(func.sum(UiDesignerRender.bytes), 0)))).scalar_one())
    if total > max_bytes:
        for k, b in (await s.execute(select(UiDesignerRender.key, UiDesignerRender.bytes).where(
                UiDesignerRender.key != key).order_by(UiDesignerRender.last_used))):
            await s.execute(delete(UiDesignerRender).where(UiDesignerRender.key == k))
            total -= b
            if total <= max_bytes:
                break
    return True


# --- comments ---------------------------------------------------------------

MAX_COMMENT_BYTES = 4096
MAX_COMMENTS_PER_DOC = 1000


def comment_view(c: UiDesignerComment) -> dict:
    return {"id": c.id, "node_id": c.node_id, "x": c.x, "y": c.y, "parent_id": c.parent_id,
            "author": c.author, "run_id": c.run_id, "body": c.body, "resolved_by": c.resolved_by,
            "resolved_at": _iso(c.resolved_at), "created_at": _iso(c.created_at)}


async def list_comments(s, slug: str, *, include_resolved: bool = True, node: str | None = None,
                        limit: int = 200, before: str | None = None) -> tuple[list[dict], str | None]:
    """A page of the thread, oldest first within the page. The filters run in SQL
    before the limit, and the page is the NEWEST `limit` rows (resolved ones
    excluded when asked), so old resolved threads never hide new ones. `before`
    is the `next_before` of the previous page (a comment id): older rows only."""
    row = await _live(s, slug)
    stmt = select(UiDesignerComment).where(UiDesignerComment.document_id == row.id)
    if node is not None:
        stmt = stmt.where(UiDesignerComment.node_id == node)
    if not include_resolved:
        stmt = stmt.where(UiDesignerComment.resolved_at.is_(None))
    if before is not None:
        ref = await s.get(UiDesignerComment, before)
        if ref is None or ref.document_id != row.id:
            raise _not_found(f"comment {before!r}")
        stmt = stmt.where((UiDesignerComment.created_at < ref.created_at) |
                          ((UiDesignerComment.created_at == ref.created_at) & (UiDesignerComment.id < ref.id)))
    rows = (await s.execute(stmt.order_by(UiDesignerComment.created_at.desc(), UiDesignerComment.id.desc())
                            .limit(limit + 1))).scalars().all()
    more = len(rows) > limit
    rows = rows[:limit]
    return [comment_view(c) for c in reversed(rows)], (rows[-1].id if more and rows else None)


async def add_comment(s, slug: str, *, body: str, author: str, run_id: str | None = None,
                      node_id: str | None = None, x: float | None = None, y: float | None = None,
                      parent_id: str | None = None, request_id: str | None = None,
                      caps: Caps = Caps(), scope: str | None = None, now: datetime | None = None) -> dict:
    now = now or utcnow()
    scope = _scope(scope, run_id, request_id)
    h = args_hash(op="comment", slug=slug, body=body, node=node_id, x=x, y=y, parent=parent_id)
    if scope:
        prior = await _receipt_lookup(s, scope, request_id, "comment", h)
        if prior is not None:
            return {"replayed": True, "comment": None, "id": prior.get("text"), "slug": slug}
    if not isinstance(body, str) or not body.strip():
        raise invalid([issue("UD-MISSING", "$.body", "a comment needs text")])
    if len(body.encode()) > MAX_COMMENT_BYTES:
        raise invalid([issue("UD-LIMIT", "$.body", f"a comment is at most {MAX_COMMENT_BYTES} bytes")])
    if (x is None) != (y is None):
        raise invalid([issue("UD-MISSING", "$.x", "a pin needs both x and y")])
    row = await _live(s, slug, lock=True)
    n = int((await s.execute(select(func.count(UiDesignerComment.id)).where(
        UiDesignerComment.document_id == row.id))).scalar_one())
    if n >= MAX_COMMENTS_PER_DOC:
        raise UiDesignerError(UD_BUDGET_STORAGE, f"{slug!r} already has {n} comments; the limit is "
                              f"{MAX_COMMENTS_PER_DOC}. Resolve and ask an administrator to prune, "
                              "or comment on another document",
                              detail={"cap": "max_comments_per_doc", "used": n,
                                      "limit": MAX_COMMENTS_PER_DOC})
    await _check_caps(s, caps, len(body.encode()), None, "comment")
    if node_id is not None:
        pages = {p.get("id") for p in row.doc.get("pages") or []}
        if node_id not in pages and node_id not in index_nodes(row.doc):
            raise invalid([issue("UD-OP-TARGET", "$.node", f"no node or page {node_id!r}", node_id)])
    if parent_id is not None:
        parent = await s.get(UiDesignerComment, parent_id)
        if parent is None or parent.document_id != row.id:
            raise _not_found(f"comment {parent_id!r}")
        parent_id = parent.parent_id or parent.id          # a thread is flat
    c = UiDesignerComment(document_id=row.id, node_id=node_id, x=x, y=y, parent_id=parent_id,
                          author=author, run_id=run_id, body=body.strip(), created_at=now)
    s.add(c)
    await s.flush()
    if scope:
        await _receipt_write(s, scope, request_id, row.id, "comment", h,
                             {"version": row.version, "slug": slug, "text": c.id}, now)
    return {"replayed": False, "comment": comment_view(c), "id": c.id, "slug": slug}


async def resolve_comment(s, slug: str, comment_id: str, *, by: str, resolved: bool = True,
                          now: datetime | None = None, restrict: bool = False) -> dict:
    """Resolve (or reopen) a thread: the root and every reply. With `restrict`
    (an agent caller), only the thread's author or the document's owner may."""
    row = await _live(s, slug, lock=True)
    c = await s.get(UiDesignerComment, comment_id)
    if c is None or c.document_id != row.id:
        raise _not_found(f"comment {comment_id!r}")
    root_id = c.parent_id or c.id
    if restrict:
        root = c if c.id == root_id else await s.get(UiDesignerComment, root_id)
        if by not in (row.owner, root.author if root else None):
            raise UiDesignerError(errors.UD_FORBIDDEN, "only the thread's author or the document's owner "
                                                "may resolve or reopen it")
    rows = (await s.execute(select(UiDesignerComment).where(
        UiDesignerComment.document_id == row.id,
        (UiDesignerComment.id == root_id) | (UiDesignerComment.parent_id == root_id)))).scalars().all()
    for r in rows:
        r.resolved_by = by if resolved else None
        r.resolved_at = (now or utcnow()) if resolved else None
    await s.flush()
    return {"slug": slug, "id": root_id, "resolved": resolved,
            "comment": comment_view(next(r for r in rows if r.id == root_id)), "document_id": row.id}
