"""The ONE place an artifact changes (docs/design/23).

Bytes come in here from three doors — the REST upload, the executor's file
sink, the generate route — and every one of them gets the same treatment,
which is the point of there being one place: the mime is sniffed from the
bytes and the client's claim is at most a hint for a handful of text types;
the only code that ever interprets the bytes is Pillow, for the four rasters,
behind an explicit pixel cap; the per-artifact and whole-store caps are
checked before anything is written; and the row, its blob and the event leave
here in that order — one commit, then a best-effort publish, the wiki shape.

Reads are as narrow as the writes are careful: a list never selects the blob
table, and a soft-deleted row reads as absent to everything but the pruner.
"""
import asyncio
import hashlib
import io
import json
import logging
import re

from PIL import Image
from sqlalchemy import Text, cast, func, select
from sqlalchemy.orm import defer

from agentplatform.config import get_settings
from agentplatform.db import Artifact, ArtifactBlob, utcnow
from agentplatform.events import TOPIC_ARTIFACTS_EVENTS

log = logging.getLogger("artifact_store")

# A decompression bomb is a small file that claims an enormous canvas; Pillow
# reads the claim from the header before decoding a pixel, so with this in
# place the claim itself is the refusal (a 413) and no allocation happens.
# 50 MP is an 8K frame with room to spare — nothing a screenshot or a
# generated image reaches. Set at import, once, so no code path can open an
# image before the cap exists.
MAX_PIXELS = 50_000_000
Image.MAX_IMAGE_PIXELS = MAX_PIXELS

# The four rasters the platform will call an image: what Pillow thumbnails,
# what the content route serves inline, what the Studio renders. Sniffed by
# magic; a claim of any of these over other bytes is ignored.
RASTERS = ("image/png", "image/jpeg", "image/webp", "image/gif")
# The text types a client may CLAIM, honoured only when the bytes decode as
# UTF-8: served as attachments regardless, so the claim buys a download with
# the right extension and nothing a browser would run.
TEXT_MIMES = ("text/plain", "text/markdown", "text/csv", "application/json")
OCTET = "application/octet-stream"
SOURCES = ("upload", "generated", "derived", "tool")
# What a create may call itself. `generated` is reserved for the generate
# route, which is the only code that has actually paid for a generation.
CLIENT_SOURCES = ("upload", "tool", "derived")

NAME_LIMIT, TAG_LIMIT, TAGS_MAX = 120, 40, 20
META_MAX_BYTES = 8 * 1024
THUMB_SIDE, THUMB_MAX_BYTES, THUMB_QUALITY = 512, 150 * 1024, 80
LIST_LIMIT, LIST_MAX = 50, 200
# Everything outside this set is model- or human-supplied punctuation the
# platform has no use for in a filename it will put in a header and a URL.
_UNSAFE_RE = re.compile(r"[^A-Za-z0-9._ -]+")


class ArtifactRuleError(Exception):
    """A write the store refuses, carrying the HTTP status the API should
    answer with: 400 for a malformed request, 413 for one too big to keep,
    507 for a store that is full. The store names the status because the
    three are the same refusal to the caller — "not stored, here is why" —
    and only differ in which cap said so."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


# --- the untrusted text ----------------------------------------------------------

def flatten(text, limit: int) -> str:
    """Somebody else's text, reduced to the characters a filename and a header
    may safely carry. Each run of anything else becomes one underscore, so
    `r/é\\nsumé.pdf` keeps its shape without keeping its bytes."""
    text = _UNSAFE_RE.sub("_", str(text or "")).strip()
    return text[:limit].strip()


def clean_name(name) -> str:
    return flatten(name, NAME_LIMIT) or "artifact"


def clean_tags(tags) -> list[str]:
    out: list[str] = []
    for tag in tags or []:
        text = flatten(tag, TAG_LIMIT + 1)
        if not text:
            continue
        if len(text) > TAG_LIMIT:
            raise ArtifactRuleError(400, f"a tag is at most {TAG_LIMIT} characters")
        if text not in out:
            out.append(text)
    if len(out) > TAGS_MAX:
        raise ArtifactRuleError(400, f"an artifact carries at most {TAGS_MAX} tags")
    return out


def clean_meta(meta) -> dict:
    if meta is None:
        return {}
    if not isinstance(meta, dict):
        raise ArtifactRuleError(400, "meta must be an object")
    try:
        encoded = json.dumps(meta)
    except (TypeError, ValueError):
        raise ArtifactRuleError(400, "meta must be JSON")
    if len(encoded.encode()) > META_MAX_BYTES:
        raise ArtifactRuleError(400, f"meta is at most {META_MAX_BYTES} bytes of JSON")
    return meta


# --- the bytes -------------------------------------------------------------------

def sniff(data: bytes, claimed: str | None) -> str:
    """The mime the BYTES are. Magic first; then, for a claimed text type,
    whether the bytes decode; then octet-stream, which is what an HTML file
    calling itself a PNG, an SVG and everything else become."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    claim = (claimed or "").split(";", 1)[0].strip().lower()
    if claim in TEXT_MIMES:
        try:
            data.decode("utf-8")
            return claim
        except UnicodeDecodeError:
            pass
    return OCTET


def _has_alpha(img) -> bool:
    return img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)


def measure_and_thumb(data: bytes) -> tuple[int, int, bytes]:
    """Dimensions from the header and a thumb from the pixels — the one place
    bytes are decoded, and only after the header's claim passed the cap.
    Pillow's own check only WARNS between the cap and twice it, and a
    warning is not a refusal, so the header's claim is compared against the
    cap here, explicitly, before a pixel is decoded. (Not by promoting the
    warning to an error: the warnings filter is process-global state, and
    this runs on a worker thread beside other uploads.)

    Synchronous on purpose — `create` runs it in a thread, because a decode
    and a resize of an 8 MiB image is tens of milliseconds the event loop
    must not spend."""
    try:
        img = Image.open(io.BytesIO(data))
        width, height = img.size
        if width * height > MAX_PIXELS:
            raise ArtifactRuleError(413, f"an image is at most {MAX_PIXELS // 1_000_000} megapixels")
        alpha = _has_alpha(img)
        img = img.convert("RGBA" if alpha else "RGB")
    except ArtifactRuleError:
        raise
    except Image.DecompressionBombError:
        raise ArtifactRuleError(413, f"an image is at most {MAX_PIXELS // 1_000_000} megapixels")
    except Exception:
        raise ArtifactRuleError(400, "the image could not be decoded")
    side = THUMB_SIDE
    while True:
        thumb = img.copy()
        thumb.thumbnail((side, side))
        buf = io.BytesIO()
        if alpha:
            thumb.save(buf, format="PNG", optimize=True)
        else:
            thumb.save(buf, format="JPEG", quality=THUMB_QUALITY, optimize=True)
        # A noisy PNG at 512 px can pass 150 KiB; shrinking is the only lever
        # that always works, and a 64 px thumb is still a thumb.
        if buf.tell() <= THUMB_MAX_BYTES or side <= 64:
            return width, height, buf.getvalue()
        side = side * 3 // 4


# --- writes ----------------------------------------------------------------------

async def create(session, *, data: bytes, owner: str, name=None, claimed_mime=None,
                 source: str = "upload", meta=None, tags=None, run_id: str | None = None,
                 producer=None, settings=None, allow_generated: bool = False) -> Artifact:
    """Store bytes as a new artifact and tell the topic. `owner` is a
    participant string the CALLER resolved from a token — nothing here checks
    it, because nothing here can. `allow_generated` is the generate route's
    key to the one source a client may not claim."""
    settings = settings or get_settings()
    if not data:
        raise ArtifactRuleError(400, "an artifact needs bytes")
    if len(data) > settings.artifacts_max_bytes:
        raise ArtifactRuleError(413, f"an artifact is at most {settings.artifacts_max_bytes} bytes")
    name, tags, meta = clean_name(name), clean_tags(tags), clean_meta(meta)
    allowed = SOURCES if allow_generated else CLIENT_SOURCES
    if source not in allowed:
        raise ArtifactRuleError(400, f"source must be one of {', '.join(allowed)}")
    parent = await _parent_image(session, meta.get("parent_id"))
    if parent is not None:
        source = "derived"
    elif source == "derived":
        raise ArtifactRuleError(400, "a derived artifact names an existing image in meta.parent_id")
    mime = sniff(data, claimed_mime)
    width = height = thumb = None
    if mime in RASTERS:
        width, height, thumb = await asyncio.to_thread(measure_and_thumb, data)
    # Read-then-insert, not a lock: two uploads landing together can each
    # pass this and overrun the cap by at most one artifact (8 MiB) per
    # concurrent writer, which the cap — a budget, not a disk — can absorb.
    _, used = await usage(session)
    if used + len(data) > settings.artifacts_total_max_bytes:
        raise ArtifactRuleError(507, "the artifact store is at its cap "
                                     f"({settings.artifacts_total_max_bytes} bytes); "
                                     "delete something before saving more")
    row = Artifact(name=name, mime=mime, size=len(data),
                   sha256=hashlib.sha256(data).hexdigest(),
                   kind="image" if mime in RASTERS else "file",
                   width=width, height=height, thumb=thumb, owner=owner,
                   run_id=run_id, source=source, meta=meta, tags=tags)
    session.add(row)
    await session.flush()
    session.add(ArtifactBlob(artifact_id=row.id, data=data))
    await session.commit()
    await publish_artifact_event(producer, event="created", artifact=artifact_view(row),
                                 agent=_agent_of(owner))
    return row


async def patch(session, artifact_id: str, *, name=None, tags=None) -> Artifact | None:
    row = await get(session, artifact_id)
    if row is None:
        return None
    if name is not None:
        row.name = clean_name(name)
    if tags is not None:
        row.tags = clean_tags(tags)
    await session.commit()
    return row


async def soft_delete(session, artifact_id: str, *, producer=None,
                      agent: str | None = None) -> Artifact | None:
    """Hide an artifact; the pruner reclaims the bytes later. `agent` is who
    deleted it when that was an agent — the event names them so the Studio's
    strip can say so."""
    row = await get(session, artifact_id)
    if row is None:
        return None
    row.deleted_at = utcnow()
    undressed = await unlink_agent_images(session, [artifact_id])
    await session.commit()
    await publish_artifact_event(producer, event="deleted", artifact=artifact_view(row),
                                 agent=agent)
    await publish_face_clears(producer, undressed)
    return row


async def unlink_agent_images(session, artifact_ids: list[str]) -> list[str]:
    """Take the artifacts off every agent wearing one as its picture and name
    the agents. A face must never point at a thumb that 404s, and the column
    is deliberately not a foreign key (db.AgentDef), so the delete paths do by
    hand what a cascade would: the soft delete here, the pruner's hard delete
    again for a row that got its image by a path the store never saw. Flushed
    into the caller's transaction, not committed — the clear lands with the
    delete or not at all."""
    from agentplatform.db import AgentDef
    if not artifact_ids:
        return []
    rows = (await session.execute(select(AgentDef).where(
        AgentDef.image_artifact_id.in_(artifact_ids)))).scalars().all()
    for row in rows:
        row.image_artifact_id = None
    await session.flush()
    return sorted(row.name for row in rows)


async def publish_face_clears(producer, agents: list[str]) -> None:
    """One `agent_image` clear per undressed agent, after the commit."""
    for name in agents:
        await publish_artifact_event(producer, event="agent_image", artifact=None, agent=name)


# --- reads -----------------------------------------------------------------------

def list_query():
    """The metadata columns and nothing else — never the blob table, and the
    thumb deferred so the grid's query is rows of a few hundred bytes."""
    return select(Artifact).options(defer(Artifact.thumb)).where(Artifact.deleted_at.is_(None))


async def get(session, artifact_id: str) -> Artifact | None:
    row = await session.get(Artifact, artifact_id)
    return row if row is not None and row.deleted_at is None else None


async def content(session, artifact_id: str) -> bytes | None:
    """The bytes, or None for an unknown or deleted artifact — the deleted
    check is the row's, so a blob outliving its soft delete serves nothing."""
    if await get(session, artifact_id) is None:
        return None
    blob = await session.get(ArtifactBlob, artifact_id)
    return blob.data if blob is not None else None


async def list_artifacts(session, *, kind: str | None = None, owner: str | None = None,
                         source: str | None = None, q: str | None = None,
                         tag: str | None = None, limit: int = LIST_LIMIT,
                         before: str | None = None) -> list[Artifact]:
    """Newest first, keyset-paged on (created_at, id) from the `before` cursor
    — an artifact id, which is what the client already has — so a page is
    stable while uploads land above it."""
    stmt = list_query()
    if kind:
        stmt = stmt.where(Artifact.kind == kind)
    if owner:
        stmt = stmt.where(Artifact.owner == owner)
    if source:
        stmt = stmt.where(Artifact.source == source)
    if q:
        # Escaped, as Relay escapes its own search: a `%` or `_` somebody
        # typed is that character, not a wildcard.
        stmt = stmt.where(func.lower(Artifact.name).contains(q.lower(), autoescape=True))
    if tag:
        # A tag is stored inside a JSON array; matching its quoted form in
        # the array's text is exact on both dialects without either's JSON
        # operators, and the tag grammar has no quote to escape.
        stmt = stmt.where(cast(Artifact.tags, Text).contains(f'"{tag}"', autoescape=True))
    if before:
        cursor = await session.get(Artifact, before)
        if cursor is None:
            raise ArtifactRuleError(422, "unknown `before` cursor")
        stmt = stmt.where((Artifact.created_at < cursor.created_at) |
                          ((Artifact.created_at == cursor.created_at) & (Artifact.id < cursor.id)))
    stmt = stmt.order_by(Artifact.created_at.desc(), Artifact.id.desc())
    limit = max(1, min(int(limit or LIST_LIMIT), LIST_MAX))
    return list((await session.execute(stmt.limit(limit))).scalars())


async def usage(session) -> tuple[int, int]:
    """(count, bytes) of the live artifacts — what the total cap is measured
    against and what the stats show. Soft-deleted rows do not count: the
    caller was told to delete something to make room, and it did."""
    row = (await session.execute(select(func.count(Artifact.id), func.sum(Artifact.size))
                                 .where(Artifact.deleted_at.is_(None)))).one()
    return int(row[0] or 0), int(row[1] or 0)


async def _parent_image(session, parent_id) -> Artifact | None:
    if not isinstance(parent_id, str) or not parent_id:
        return None
    parent = await get(session, parent_id)
    return parent if parent is not None and parent.kind == "image" else None


# --- the view and the event ----------------------------------------------------------

def artifact_view(a: Artifact) -> dict:
    """The artifact as every reader outside this module sees it: the API row,
    the Kafka payload, the card. Metadata only — the thumb and the bytes are
    the two byte routes' to serve, never a JSON field."""
    return {"id": a.id, "name": a.name, "mime": a.mime, "size": a.size,
            "sha256": a.sha256, "kind": a.kind, "width": a.width, "height": a.height,
            "owner": a.owner, "run_id": a.run_id, "source": a.source,
            "meta": dict(a.meta or {}), "tags": list(a.tags or []),
            "created_at": _iso(a.created_at), "deleted_at": _iso(a.deleted_at),
            "thumb_url": f"/api/artifacts/{a.id}/thumb" if a.kind == "image" else None,
            "content_url": f"/api/artifacts/{a.id}/content"}


async def publish_artifact_event(producer, *, event: str, artifact: dict | None,
                                 agent: str | None = None) -> None:
    """Best-effort, after the commit: the row is the record, so a broker that
    is down costs the Studio a live update and never the artifact. Keyed by
    the artifact; an `agent_image` clear has none, so it keys by the agent
    whose face changed."""
    if producer is None:
        return
    key = artifact["id"] if artifact else agent
    try:
        await producer.publish(TOPIC_ARTIFACTS_EVENTS, key,
                               {"event": event, "artifact": artifact, "agent": agent},
                               type="artifacts.event")
    except Exception:
        log.warning("artifacts.events publish failed for %s %s", key, event, exc_info=True)


def _agent_of(owner: str) -> str | None:
    return owner[len("agent:"):] if owner.startswith("agent:") else None


def _iso(ts):
    return ts.isoformat() if ts else None
