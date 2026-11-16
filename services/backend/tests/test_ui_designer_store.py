"""UI Designer store (design 46 T3): documents, folded history rows, exact replay,
assets and GC, receipts, imports, delete/prune, render cache and storage caps."""
import copy
import hashlib
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from agentplatform.db import (UiDesignerAsset, UiDesignerDocument, UiDesignerDocumentAsset,
                              UiDesignerReceipt, UiDesignerRender, UiDesignerVersion, utcnow)
from agentplatform.pruning import UiDesignerAssetSweeper, UiDesignerPruner
from agentplatform.ui_designer import store
from agentplatform.ui_designer.errors import UD_ASSET_UNKNOWN, UD_VERSION_CONFLICT, UiDesignerError
from agentplatform.ui_designer.schema import new_document

T0 = utcnow()
A, B = "agent:pai", "agent:kai"


def base_doc():
    d = new_document("Demo")
    d["pages"] = [{"id": "p1", "title": "Main", "children": [
        {"id": "root", "type": "frame", "w": 400, "h": 300, "children": []}]}]
    return d


def ins(text, parent="root", **node):
    return {"op": "insert", "parent": parent, "node": {"type": "text", "text": text, **node}}


async def make(sf, slug="hud", **kw):
    async with sf() as s:
        out = await store.create_document(s, slug=slug, title="Demo", owner="kyle", author="kyle",
                                          doc=kw.pop("doc", base_doc()), now=T0, **kw)
        await s.commit()
        return out


async def edit(sf, ver, ops=None, *, slug="hud", author=A, run="run1", at=0, **kw):
    async with sf() as s:
        out = await store.edit_document(s, slug, ops=ops or [ins(f"t{ver}")], expected_version=ver,
                                        author=author, run_id=run, now=T0 + timedelta(seconds=at), **kw)
        await s.commit()
        return out


async def rows(sf, slug="hud"):
    async with sf() as s:
        d = (await s.execute(select(UiDesignerDocument).where(UiDesignerDocument.slug == slug))).scalars().first()
        return (await s.execute(select(UiDesignerVersion).where(UiDesignerVersion.document_id == d.id)
                                .order_by(UiDesignerVersion.version))).scalars().all()


async def at_version(sf, n, slug="hud"):
    async with sf() as s:
        return (await store.get_document(s, slug, version=n))["doc"]


# --- documents ---------------------------------------------------------------

async def test_create_and_slug_rules(sf):
    out = await make(sf)
    assert out["version"] == 1 and out["node_count"] >= 2
    with pytest.raises(UiDesignerError) as e:
        await make(sf)
    assert e.value.code == store.UD_SLUG_TAKEN
    with pytest.raises(UiDesignerError) as e:
        await make(sf, slug="Bad Slug")
    assert e.value.code == "UD-INVALID"


async def test_soft_delete_frees_slug_and_keeps_tombstone(sf):
    first = await make(sf)
    async with sf() as s:
        await store.soft_delete(s, "hud")
        await s.commit()
        with pytest.raises(UiDesignerError):
            await store.get_document(s, "hud")
    second = await make(sf)
    assert second["id"] != first["id"]
    async with sf() as s:
        assert len(await store.list_documents(s)) == 1
        assert len(await store.list_documents(s, include_deleted=True)) == 2


# --- versions, folding, replay ----------------------------------------------

async def test_fold_rules(sf):
    await make(sf)
    await edit(sf, 1, at=1)                     # v2: new row (creation row never folds)
    await edit(sf, 2, at=10)                    # v3: folds into v2's row
    await edit(sf, 3, at=119)                   # v4: still inside the 120 s window
    assert [(r.first_version, r.version) for r in await rows(sf)] == [(1, 1), (2, 4)]
    await edit(sf, 4, at=125)                   # window runs from the row's first write
    await edit(sf, 5, author=B, run="run2", at=126)   # other principal: new row
    await edit(sf, 6, at=127)                   # A again, but B wrote since
    await edit(sf, 7, author="kyle", run=None, at=128)
    await edit(sf, 8, author="kyle", run=None, at=129)  # run-less never folds
    assert [(r.first_version, r.version) for r in await rows(sf)] == [
        (1, 1), (2, 4), (5, 5), (6, 6), (7, 7), (8, 8), (9, 9)]


async def test_reader_and_label_stop_folding(sf):
    await make(sf)
    await edit(sf, 1, at=1)
    async with sf() as s:
        await store.get_document(s, "hud", reader=B)
        await s.commit()
    await edit(sf, 2, at=5)                     # B read since: new row
    async with sf() as s:
        await store.set_label(s, "hud", "milestone")
        await s.commit()
    await edit(sf, 3, at=6)                     # labeled row: new row
    assert [(r.first_version, r.version) for r in await rows(sf)] == [(1, 1), (2, 2), (3, 3), (4, 4)]


async def test_replay_matches_snapshot_at_every_write(sf):
    """The T3 gate: every number of a folded burst replays to exactly what a
    snapshot taken at that write held, including inserts that generate ids."""
    await make(sf)
    snaps = {1: (await at_version(sf, 1))}
    plan = [[ins("a", id="a")], [ins("a")], [{"op": "update", "id": "root", "set": {"fill": "#112233"}}],
            [ins("b", "root", id="b"), ins("c", "root", id="c")],
            [{"op": "insert", "parent": "root", "node": {"type": "frame", "title": "Card", "children": [
                {"type": "text", "text": "x"}]}}],
            [{"op": "set_text", "id": "a", "text": "renamed"}],
            [{"op": "tokens", "set": {"color": {"hot": "#ff0000"}}}],
            [{"op": "move", "id": "b", "parent": "root", "index": 0}],
            [{"op": "remove", "ids": ["c"]}]]
    for i, ops in enumerate(plan):
        out = await edit(sf, i + 1, ops, at=i)
        snaps[out["version"]] = out["doc"]
    assert len(await rows(sf)) == 2             # one creation row + one folded row of 9 writes
    for n, want in snaps.items():
        assert await at_version(sf, n) == want, f"version {n}"
    async with sf() as s:
        assert (await store.get_document(s, "hud", version=5))["at_version"] == 5
        with pytest.raises(UiDesignerError):
            await store.get_document(s, "hud", version=99)


async def test_restore_is_a_new_version_with_exact_content(sf):
    await make(sf)
    for i in range(4):
        await edit(sf, i + 1, at=i)
    want = await at_version(sf, 3)
    async with sf() as s:
        out = await store.restore_version(s, "hud", 3, expected_version=5, author=A, run_id="run1",
                                          now=T0 + timedelta(seconds=9))
        await s.commit()
    assert out["version"] == 6 and out["doc"] == want
    assert await at_version(sf, 6) == want and await at_version(sf, 5) != want
    assert (await rows(sf))[-1].first_version == 6      # restore never folds


async def test_open_closed_and_history(sf):
    await make(sf)
    await edit(sf, 1, at=1)
    await edit(sf, 2, at=2)
    async with sf() as s:
        assert await store.version_state(s, "hud", 3, T0 + timedelta(seconds=3)) == "open"
        assert await store.version_state(s, "hud", 3, T0 + timedelta(seconds=300)) == "closed"
        assert await store.version_state(s, "hud", 1) == "closed"
        assert await store.version_state(s, "hud", 9) is None
        h = await store.history(s, "hud", now=T0 + timedelta(seconds=3))
        assert [x["range"] for x in h] == ["v2–v3", "v1"] and h[0]["open"] and not h[1]["open"]
        diff = await store.history_diff(s, "hud", 1, 3)
        assert [c["version"] for c in diff] == [2, 3]
        with pytest.raises(UiDesignerError):
            await store.history_diff(s, "hud", 3, 3)


async def test_cas_conflict_returns_chunks_since(sf):
    await make(sf)
    await edit(sf, 1, at=1)
    await edit(sf, 2, at=2)
    with pytest.raises(UiDesignerError) as e:
        await edit(sf, 1, at=3)
    assert e.value.code == UD_VERSION_CONFLICT
    assert e.value.detail["current_version"] == 3
    assert [c["version"] for c in e.value.detail["ops_since"]] == [2, 3]
    assert len(await rows(sf)) == 2                      # the loser wrote nothing


# --- receipts ----------------------------------------------------------------

async def test_receipts_replay_scope_and_hash(sf):
    await make(sf)
    out = await edit(sf, 1, [ins("a")], request_id="r1", wants_snapshot=True, at=1)
    assert out["replayed"] is False
    again = await edit(sf, 1, [ins("a")], request_id="r1", wants_snapshot=True, at=2)
    assert again["replayed"] is True and again["version"] == 2 and "doc" not in again
    assert "call snapshot" in again["text"]
    async with sf() as s:
        assert (await store.get_document(s, "hud"))["version"] == 2      # nothing written twice
    with pytest.raises(UiDesignerError) as e:
        await edit(sf, 1, [ins("different")], request_id="r1", at=3)
    assert e.value.code == store.UD_REQUEST_REUSED
    # the same request_id in another scope (another run) is a different request
    other = await edit(sf, 2, [ins("a")], request_id="r1", run="run2", author=B, at=4)
    assert other["replayed"] is False and other["version"] == 3


async def test_receipts_are_text_only(sf):
    await make(sf)
    await edit(sf, 1, request_id="r1", at=1)
    async with sf() as s:
        rec = await store.get_receipt(s, "run1", "r1")
        assert set(rec["response"]) <= store.RECEIPT_KEYS and "doc" not in rec["response"]
        with pytest.raises(ValueError):
            await store._receipt_write(s, "run1", "r9", None, "edit", "h", {"doc": {}}, T0)
        with pytest.raises(ValueError):
            await store._receipt_write(s, "run1", "r9", None, "edit", "h", {"text": "x" * 20000}, T0)
        with pytest.raises(ValueError):
            await store.edit_document(s, "hud", ops=[ins("z")], expected_version=2, author=A,
                                      request_id="needs-scope")


async def test_create_receipt_and_unique_constraint(sf):
    out = await make(sf, request_id="c1", scope="sess1")
    async with sf() as s:
        again = await store.create_document(s, slug="hud", title="Demo", owner="kyle", doc=base_doc(),
                                            request_id="c1", scope="sess1")
        assert again["replayed"] is True and again["version"] == 1
        s.add(UiDesignerReceipt(scope="sess1", request_id="c1", op="create", args_hash="x", response={}))
        with pytest.raises(Exception):
            await s.flush()
    assert out["version"] == 1


async def test_prune_receipts_after_seven_days(sf):
    await make(sf)
    await edit(sf, 1, request_id="r1", at=1)
    async with sf() as s:
        await store.get_receipt(s, "run1", "r1")
    pr = UiDesignerPruner(sf)
    assert await pr.prune_once(now=T0 + timedelta(days=6)) == 0
    assert await pr.prune_once(now=T0 + timedelta(days=8)) == 1


# --- assets ------------------------------------------------------------------

PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 100


async def test_place_required_before_image_nodes(sf):
    await make(sf)
    sha = hashlib.sha256(PNG).hexdigest()
    img = [{"op": "insert", "parent": "root", "node": {"type": "image", "asset": sha}}]
    with pytest.raises(UiDesignerError) as e:
        await edit(sf, 1, img, at=1)
    assert e.value.code == UD_ASSET_UNKNOWN
    async with sf() as s:
        placed = await store.place_asset(s, "hud", data=PNG, mime="image/png", placed_by=A,
                                         placed_from="artifact1")
        await store.place_asset(s, "hud", data=PNG, mime="image/png", placed_by=A, placed_from="artifact1")
        await s.commit()
        assert placed["sha256"] == sha
        assert (await s.execute(select(func.count()).select_from(UiDesignerDocumentAsset))).scalar_one() == 1
    assert (await edit(sf, 1, img, at=2))["version"] == 2
    # a second document can't use the same sha without its own placement
    await make(sf, slug="other")
    with pytest.raises(UiDesignerError) as e:
        await edit(sf, 1, img, slug="other", at=3)
    assert e.value.code == UD_ASSET_UNKNOWN


async def test_asset_size_limit(sf):
    await make(sf)
    async with sf() as s:
        with pytest.raises(UiDesignerError):
            await store.add_asset(s, data=b"x" * ((8 << 20) + 1), mime="image/png")


async def _asset_count(sf):
    async with sf() as s:
        return (await s.execute(select(func.count()).select_from(UiDesignerAsset))).scalar_one()


async def test_gc_keeps_placed_or_versioned_assets(sf):
    await make(sf)
    sha = hashlib.sha256(PNG).hexdigest()
    async with sf() as s:
        await store.place_asset(s, "hud", data=PNG, mime="image/png", placed_by=A, placed_from="a1")
        orphan = await store.add_asset(s, data=b"orphan-bytes", mime="image/png")
        await s.commit()
    assert await _asset_count(sf) == 2
    assert await UiDesignerAssetSweeper(sf).prune_once() == 1       # only the orphan goes
    async with sf() as s:
        assert await store.get_asset(s, orphan) is None and await store.get_asset(s, sha) is not None
    # drop the placement row: the retained version that names it still keeps it
    await edit(sf, 1, [{"op": "insert", "parent": "root", "node": {"type": "image", "asset": sha}}], at=1)
    async with sf() as s:
        await s.execute(UiDesignerDocumentAsset.__table__.delete())
        await s.commit()
    assert await UiDesignerAssetSweeper(sf).prune_once() == 0
    # remove the image (v3) and prune the old rows: nothing retained names it any more
    img_id = (await at_version(sf, 2))["pages"][0]["children"][0]["children"][0]["id"]
    await edit(sf, 2, [{"op": "remove", "ids": [img_id]}], author=B, run="run2", at=300)
    async with sf() as s:
        out = await store.prune_versions(s, "hud", older_than_days=0, keep_last=1, now=T0 + timedelta(days=1))
        await s.commit()
    assert out["assets_removed"] == 1 and await _asset_count(sf) == 0


# --- hard delete, expiry, prune ----------------------------------------------

async def test_hard_delete_removes_everything_and_collects_assets(sf):
    await make(sf)
    async with sf() as s:
        await store.place_asset(s, "hud", data=PNG, mime="image/png", placed_by=A, placed_from="a1")
        await store.render_put(s, "k1", document_id=(await store.get_document(s, "hud"))["id"],
                               png=b"png", width=1, height=1)
        await s.commit()
    await edit(sf, 1, request_id="r1", at=1)
    async with sf() as s:
        out = await store.hard_delete(s, "hud")
        await s.commit()
    assert out["assets_removed"] == 1
    async with sf() as s:
        for model in (UiDesignerDocument, UiDesignerVersion, UiDesignerDocumentAsset, UiDesignerAsset,
                      UiDesignerRender, UiDesignerReceipt):
            assert (await s.execute(select(func.count()).select_from(model))).scalar_one() == 0, model


async def test_pruner_hard_deletes_after_window(sf):
    await make(sf)
    async with sf() as s:
        await store.soft_delete(s, "hud", now=T0)
        await s.commit()
    pr = UiDesignerPruner(sf, prune_days=30)
    assert await pr.prune_once(now=T0 + timedelta(days=29)) == 0
    assert await pr.prune_once(now=T0 + timedelta(days=31)) == 1
    async with sf() as s:
        assert (await s.execute(select(func.count()).select_from(UiDesignerDocument))).scalar_one() == 0


async def test_prune_versions_keeps_every_survivor_exact(sf):
    """Prune the old rows around a folded row; the folded row keeps resolving
    every one of its numbers from its own re-based snapshot."""
    await make(sf)
    for i in range(1, 4):                       # v2 (row), v3, v4 folded
        await edit(sf, i, at=i)
    await edit(sf, 4, author=B, run="run2", at=500)     # v5: its own row
    await edit(sf, 5, author=B, run="run2", at=900)     # v6: new row (window passed)
    before = {n: await at_version(sf, n) for n in (3, 4, 5, 6)}
    async with sf() as s:
        await store.set_label(s, "hud", "keep-me", version=5)
        # rows: (1) (2-4) (5 labeled) (6): keep_last=1 → doomed = rows 1 and 2-4, minus nothing labeled
        out = await store.prune_versions(s, "hud", older_than_days=0, keep_last=1, now=T0 + timedelta(days=1))
        await s.commit()
    assert out["removed_rows"] == 2
    assert [(r.first_version, r.version) for r in await rows(sf)] == [(5, 5), (6, 6)]
    for n in (5, 6):
        assert await at_version(sf, n) == before[n]
    async with sf() as s:
        with pytest.raises(UiDesignerError):
            await store.get_document(s, "hud", version=3)


async def test_prune_rebases_a_folded_row_whose_predecessor_goes(sf):
    await make(sf)
    await edit(sf, 1, at=1)                              # v2 row
    await edit(sf, 2, author=B, run="r2", at=300)        # v3 row
    await edit(sf, 3, author=B, run="r2", at=301)        # v4 folded into v3's row
    await edit(sf, 4, author=B, run="r2", at=302)        # v5
    before = {n: await at_version(sf, n) for n in (3, 4, 5)}
    async with sf() as s:
        await store.set_label(s, "hud", "lab", version=3)
        await store.prune_versions(s, "hud", older_than_days=0, keep_last=1, now=T0 + timedelta(days=1))
        await s.commit()
    survivors = await rows(sf)
    assert [(r.first_version, r.version) for r in survivors] == [(3, 5)]
    # the pruned row's snapshot was kept as the survivor's replay base
    assert survivors[0].base_doc is not None
    for n in (3, 4, 5):
        assert await at_version(sf, n) == before[n]


# --- imports -----------------------------------------------------------------

async def test_import_idempotent_while_active_and_staged_survives_gc(sf):
    async with sf() as s:
        job, created = await store.start_import(s, slug="desk", file_key="FK", initiated_by="kyle")
        again, created2 = await store.start_import(s, slug="desk", file_key="FK", initiated_by="kyle")
        assert created and not created2 and again.id == job.id
        sha = await store.add_asset(s, data=b"staged-image", mime="image/png")
        await store.stage_page(s, job.id, "0:1", staged={"nodes": [], "assets": [sha], "warnings": []},
                               counts={"nodes": 3})
        await store.stage_page(s, job.id, "0:2", staged={}, status="failed")
        await store.set_import_state(s, job.id, "running")
        await s.commit()
        assert job.pages["0:1"]["state"] == "done" and "0:2" not in job.staged
    assert await UiDesignerAssetSweeper(sf).prune_once() == 0     # the running import protects it
    async with sf() as s:
        await store.set_import_state(s, job.id, "done", report={"ok": True})
        new, created = await store.start_import(s, slug="desk", file_key="FK", initiated_by="kyle")
        await s.commit()
        assert created and new.id != job.id                       # a finished job frees the slug
    assert await UiDesignerAssetSweeper(sf).prune_once() == 1


# --- render cache ------------------------------------------------------------

async def test_render_cache_key_and_lru(sf):
    k = store.render_key("v1", "sha", "hud", "dark", 1, False)
    assert k != store.render_key("v2", "sha", "hud", "dark", 1, False) and len(k) == 64
    async with sf() as s:
        for i, name in enumerate("abc"):
            await store.render_put(s, name, document_id="d", png=b"x" * 40, width=1, height=1,
                                   max_bytes=100, now=T0 + timedelta(seconds=i))
            if name == "b":
                await store.render_get(s, "a", now=T0 + timedelta(seconds=5))   # a is now newest
        await s.commit()
        keys = set((await s.execute(select(UiDesignerRender.key))).scalars())
        assert keys == {"a", "c"}                                 # b was least recently used
        assert not await store.render_put(s, "big", document_id="d", png=b"x" * 101, width=1, height=1,
                                          max_bytes=100)


# --- storage caps ------------------------------------------------------------

async def test_total_cap_refuses_edit_place_and_create(sf):
    await make(sf)
    used = (await usage(sf))["total"]
    tight = store.Caps(max_total_bytes=used + 10, max_version_bytes_per_doc=1 << 30)
    with pytest.raises(UiDesignerError) as e:
        await edit(sf, 1, caps=tight, at=1)
    assert e.value.code == store.UD_BUDGET_STORAGE and e.value.detail["cap"] == "max_total_bytes"
    async with sf() as s:
        with pytest.raises(UiDesignerError) as e:
            await store.place_asset(s, "hud", data=PNG * 50, mime="image/png", placed_by=A,
                                    placed_from="a", caps=tight)
        assert e.value.code == store.UD_BUDGET_STORAGE
        with pytest.raises(UiDesignerError) as e:
            await store.create_document(s, slug="two", owner="kyle", doc=base_doc(), caps=tight)
        assert e.value.code == store.UD_BUDGET_STORAGE
    assert (await at_version(sf, 1)) is not None and len(await rows(sf)) == 1


async def test_per_document_cap_counts_versions_only(sf):
    await make(sf)
    async with sf() as s:
        mine = await store._doc_version_bytes(s, (await store.get_document(s, "hud"))["id"])
    tight = store.Caps(max_version_bytes_per_doc=mine + 10)
    with pytest.raises(UiDesignerError) as e:
        await edit(sf, 1, caps=tight, at=1)
    assert e.value.detail["cap"] == "max_version_bytes_per_doc"


async def test_usage_counts_assets_raw(sf):
    await make(sf)
    before = (await usage(sf))["assets"]
    async with sf() as s:
        await store.place_asset(s, "hud", data=PNG, mime="image/png", placed_by=A, placed_from="a")
        await s.commit()
    assert (await usage(sf))["assets"] == before + len(PNG)


async def usage(sf):
    async with sf() as s:
        return await store.usage(s)
