"""The artifact store (docs/design/23 T2): bytes in, a row and a blob out.

What is held down here is the trust boundary — the mime is what the BYTES say,
never what the client claimed; a raster is only ever touched by Pillow with a
pixel cap in place; the caps refuse before a byte is written — and the seam
every reader relies on: a list never selects the blob, a soft delete hides a
row from everything but the record, and the event goes out after the commit.
"""
import io
import json
import struct
import zlib

import pytest
from PIL import Image
from sqlalchemy import select

from agentplatform import artifact_store as store
from agentplatform.artifact_store import ArtifactRuleError
from agentplatform.config import Settings
from agentplatform.db import Artifact, ArtifactBlob
from agentplatform.events import TOPIC_ARTIFACTS_EVENTS

OWNER = "user:admin"


def png_bytes(w: int = 4, h: int = 3, *, alpha: bool = False) -> bytes:
    img = Image.new("RGBA" if alpha else "RGB", (w, h), (200, 30, 30, 120) if alpha
                    else (200, 30, 30))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def jpeg_bytes(w: int = 4, h: int = 3) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (10, 200, 10)).save(buf, format="JPEG")
    return buf.getvalue()


def bomb_png(w: int = 10000, h: int = 8000) -> bytes:
    """A syntactically valid PNG header that CLAIMS `w`×`h` (80 MP, past the
    50 MP cap) and carries no pixel data at all: what a decompression bomb
    looks like to the header parser, without the megabytes."""
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    chunk = struct.pack(">I", len(ihdr)) + b"IHDR" + ihdr
    chunk += struct.pack(">I", zlib.crc32(b"IHDR" + ihdr) & 0xFFFFFFFF)
    end = struct.pack(">I", 0) + b"IEND" + struct.pack(">I", zlib.crc32(b"IEND") & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk + end


def settings(**over) -> Settings:
    return Settings(**over)


async def _create(sf, data: bytes, producer=None, **kw):
    kw.setdefault("owner", OWNER)
    kw.setdefault("name", "thing.bin")
    async with sf() as s:
        return await store.create(s, data=data, producer=producer,
                                  settings=kw.pop("settings", settings()), **kw)


# --- sniffing ----------------------------------------------------------------

async def test_png_upload_is_an_image_with_dims_thumb_and_hash(sf, producer):
    data = png_bytes(40, 30)
    a = await _create(sf, data, producer=producer, name="pic.png",
                      claimed_mime="application/octet-stream")
    assert (a.kind, a.mime, a.width, a.height) == ("image", "image/png", 40, 30)
    assert a.size == len(data)
    import hashlib
    assert a.sha256 == hashlib.sha256(data).hexdigest()
    assert a.thumb and Image.open(io.BytesIO(a.thumb)).format == "JPEG"
    assert a.source == "upload" and a.owner == OWNER
    events = [e for e in producer.envelopes if e["type"] == "artifacts.event"]
    assert len(events) == 1 and events[0]["key"] == a.id
    assert events[0]["data"]["event"] == "created"
    assert events[0]["data"]["artifact"]["id"] == a.id
    assert "thumb" not in events[0]["data"]["artifact"]
    assert [t for t, _, _ in producer.published] == [TOPIC_ARTIFACTS_EVENTS]


async def test_the_claimed_mime_never_wins_over_the_bytes(sf):
    html = b"<html><script>alert(1)</script></html>"
    a = await _create(sf, html, name="pic.png", claimed_mime="image/png")
    assert (a.kind, a.mime, a.width, a.thumb) == ("file", "application/octet-stream", None, None)

    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>1</script></svg>'
    a = await _create(sf, svg, name="logo.svg", claimed_mime="image/svg+xml")
    assert (a.kind, a.mime) == ("file", "application/octet-stream")

    a = await _create(sf, jpeg_bytes(), name="x", claimed_mime="text/plain")
    assert a.mime == "image/jpeg" and a.kind == "image"


async def test_a_text_mime_is_kept_only_when_the_bytes_are_utf8(sf):
    a = await _create(sf, b"hello\n", name="notes.txt", claimed_mime="text/plain")
    assert a.mime == "text/plain" and a.kind == "file"
    a = await _create(sf, b'{"a": 1}', name="x.json", claimed_mime="application/json")
    assert a.mime == "application/json"
    a = await _create(sf, b"\xff\xfe\x00bad", name="x.csv", claimed_mime="text/csv")
    assert a.mime == "application/octet-stream"
    # An allow-listed name is not an allow-listed type.
    a = await _create(sf, b"hello", name="x.txt", claimed_mime="text/html")
    assert a.mime == "application/octet-stream"


async def test_thumb_is_png_when_the_image_has_alpha_and_bounded(sf):
    a = await _create(sf, png_bytes(1400, 700, alpha=True), name="a.png")
    thumb = Image.open(io.BytesIO(a.thumb))
    assert thumb.format == "PNG" and max(thumb.size) <= 512
    assert len(a.thumb) <= 150 * 1024
    assert (a.width, a.height) == (1400, 700)

    a = await _create(sf, png_bytes(1, 1), name="dot.png")
    assert (a.width, a.height) == (1, 1) and a.thumb


# --- the caps ----------------------------------------------------------------

async def test_a_claimed_80mp_png_is_a_413_not_a_decode(sf):
    with pytest.raises(ArtifactRuleError) as e:
        await _create(sf, bomb_png(), name="bomb.png")
    assert e.value.status == 413
    # Under the cap, a header with no pixels behind it is merely undecodable.
    with pytest.raises(ArtifactRuleError) as e:
        await _create(sf, bomb_png(8000, 5000), name="short.png")
    assert e.value.status == 400
    async with sf() as s:
        assert (await s.execute(select(Artifact))).first() is None


async def test_size_and_total_caps(sf):
    small = settings(artifacts_max_bytes=100)
    with pytest.raises(ArtifactRuleError) as e:
        await _create(sf, b"x" * 101, settings=small)
    assert e.value.status == 413
    await _create(sf, b"x" * 100, settings=small)

    tight = settings(artifacts_total_max_bytes=150)
    with pytest.raises(ArtifactRuleError) as e:
        await _create(sf, b"y" * 60, settings=tight)
    assert e.value.status == 507
    # Nothing was written by the refusal, and the usage says so.
    async with sf() as s:
        assert await store.usage(s) == (1, 100)


async def test_empty_bytes_are_refused(sf):
    with pytest.raises(ArtifactRuleError) as e:
        await _create(sf, b"")
    assert e.value.status == 400


# --- the untrusted text ---------------------------------------------------------

async def test_name_and_tags_are_flattened_and_capped(sf):
    a = await _create(sf, b"data", name="  ../é\nvil<script>.txt  ",
                      tags=[" ops ", "ops", "x" * 40, ""])
    assert a.name == ".._vil_script_.txt"
    assert a.tags == ["ops", "x" * 40]
    a = await _create(sf, b"data", name="n" * 300)
    assert len(a.name) == 120
    a = await _create(sf, b"data", name="")
    assert a.name == "artifact"
    with pytest.raises(ArtifactRuleError):
        await _create(sf, b"data", tags=["x" * 41])
    with pytest.raises(ArtifactRuleError):
        await _create(sf, b"data", tags=[str(i) for i in range(21)])


async def test_meta_is_bounded_and_source_is_checked(sf):
    with pytest.raises(ArtifactRuleError) as e:
        await _create(sf, b"data", meta={"k": "v" * 9000})
    assert e.value.status == 400
    with pytest.raises(ArtifactRuleError):
        await _create(sf, b"data", meta=["not", "a", "dict"])
    with pytest.raises(ArtifactRuleError):
        await _create(sf, b"data", source="generated")
    a = await _create(sf, b"data", source="tool", meta={"tool": "fetch"})
    assert a.source == "tool" and a.meta == {"tool": "fetch"}


async def test_a_parent_image_makes_the_child_derived(sf):
    parent = await _create(sf, png_bytes(), name="p.png")
    child = await _create(sf, png_bytes(8, 8), name="c.png",
                          meta={"parent_id": parent.id, "operation": "markup"})
    assert child.source == "derived"
    # A parent that is not an image, or not there, does not make a derivative.
    blob = await _create(sf, b"text", name="t.txt")
    with pytest.raises(ArtifactRuleError):
        await _create(sf, png_bytes(), meta={"parent_id": blob.id}, source="derived")
    with pytest.raises(ArtifactRuleError):
        await _create(sf, png_bytes(), meta={"parent_id": "nope"}, source="derived")
    plain = await _create(sf, png_bytes(), meta={"parent_id": "nope"})
    assert plain.source == "upload"


# --- the reads and the record ---------------------------------------------------

async def test_list_filters_and_pages_without_loading_blobs(sf):
    ids = []
    for i in range(5):
        a = await _create(sf, png_bytes() if i % 2 else b"file %d" % i,
                          name=f"item-{i}", owner=OWNER if i < 4 else "agent:pai",
                          tags=["even"] if i % 2 == 0 else [], source="upload")
        ids.append(a.id)
    async with sf() as s:
        rows = await store.list_artifacts(s, limit=2)
        assert [r.id for r in rows] == ids[4:2:-1]
        page2 = await store.list_artifacts(s, limit=2, before=rows[-1].id)
        assert [r.id for r in page2] == ids[2:0:-1]
        page3 = await store.list_artifacts(s, limit=2, before=page2[-1].id)
        assert [r.id for r in page3] == ids[0:1]
        assert [r.id for r in await store.list_artifacts(s, kind="image")] == [ids[3], ids[1]]
        assert [r.id for r in await store.list_artifacts(s, owner="agent:pai")] == [ids[4]]
        assert [r.id for r in await store.list_artifacts(s, tag="even")] == [ids[4], ids[2], ids[0]]
        assert [r.id for r in await store.list_artifacts(s, q="ITEM-3")] == [ids[3]]
        with pytest.raises(ArtifactRuleError):
            await store.list_artifacts(s, before="nope")
        # The blob table is never in the list's SQL.
        assert "artifact_blobs" not in str(store.list_query())


async def test_soft_delete_hides_the_row_and_the_bytes_but_keeps_the_record(sf, producer):
    a = await _create(sf, b"data", producer=producer)
    async with sf() as s:
        gone = await store.soft_delete(s, a.id, producer=producer, agent="pai")
        assert gone.deleted_at is not None
        assert await store.get(s, a.id) is None
        assert await store.content(s, a.id) is None
        assert await store.list_artifacts(s) == []
        assert await store.usage(s) == (0, 0)
        # The row and the blob are still there for the pruner.
        assert await s.get(Artifact, a.id) is not None
        assert await s.get(ArtifactBlob, a.id) is not None
        assert await store.soft_delete(s, a.id) is None
    events = [e["data"] for e in producer.envelopes if e["type"] == "artifacts.event"]
    assert [e["event"] for e in events] == ["created", "deleted"]
    assert events[1]["agent"] == "pai" and events[1]["artifact"]["deleted_at"]


async def test_patch_renames_and_retags_within_the_caps(sf):
    a = await _create(sf, b"data", name="old", tags=["a"])
    async with sf() as s:
        p = await store.patch(s, a.id, name="new\nname", tags=["b", "c"])
        assert (p.name, p.tags) == ("new_name", ["b", "c"])
        p = await store.patch(s, a.id, tags=None, name=None)
        assert (p.name, p.tags) == ("new_name", ["b", "c"])
        with pytest.raises(ArtifactRuleError):
            await store.patch(s, a.id, tags=["x" * 41])
        assert await store.patch(s, "nope", name="x") is None


async def test_publish_is_best_effort(sf, caplog):
    class Broken:
        async def publish(self, *a, **k):
            raise RuntimeError("kafka down")
    a = await _create(sf, b"data", producer=Broken())
    assert a.id
    assert "artifacts.events publish failed" in caplog.text


def test_view_never_carries_bytes():
    a = Artifact(id="x", name="n", mime="image/png", size=1, sha256="s", kind="image",
                 width=1, height=1, thumb=b"jpg", owner=OWNER, source="upload",
                 meta={}, tags=[])
    view = store.artifact_view(a)
    assert "thumb" not in view and "data" not in view
    assert view["thumb_url"] == "/api/artifacts/x/thumb"
    assert view["content_url"] == "/api/artifacts/x/content"
    a.kind, a.mime = "file", "application/octet-stream"
    assert store.artifact_view(a)["thumb_url"] is None
    assert json.dumps(view)


async def test_search_wildcards_are_literal(sf):
    a = await _create(sf, b"1", name="a_b", tags=["a_b"])
    x = await _create(sf, b"2", name="aXb", tags=["aXb"])
    await _create(sf, b"3", name="plain")
    async with sf() as s:
        assert [r.id for r in await store.list_artifacts(s, tag="a_b")] == [a.id]
        assert [r.id for r in await store.list_artifacts(s, q="a_b")] == [a.id]
        assert await store.list_artifacts(s, q="%") == []
        assert [r.id for r in await store.list_artifacts(s, q="xb")] == [x.id]
