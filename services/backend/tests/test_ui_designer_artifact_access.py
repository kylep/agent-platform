"""Design source ACLs compose with all artifact doors, pages and derivations."""

from datetime import timedelta
from types import SimpleNamespace
import base64
import json
import pytest
from sqlalchemy import select
from fastapi import HTTPException
from agentplatform import artifact_store, image_gen_service
from agentplatform.db import (
    Artifact,
    UiDesignerDocument,
    UiDesignerAccess,
    UiDesignerAccessEntry,
    utcnow,
)
from agentplatform.ui_designer import store, access, artifact_access
from .test_artifact_store import png_bytes
from .test_relay_api import _human_token


async def setup(sf):
    async with sf() as s:
        doc = await store.create_document(s, slug="private-design", owner="user:alice")
        s.add(
            UiDesignerAccess(
                document_id=doc["id"],
                visibility="restricted",
                readers=["user:bob"],
                editors=[],
                access_version=1,
            )
        )
        s.add(
            UiDesignerAccessEntry(
                document_id=doc["id"], principal="user:bob", role="reader"
            )
        )
        art = await artifact_store.create(
            s,
            data=png_bytes(),
            owner="user:alice",
            ui_document_id=doc["id"],
            meta={"ui_designer": {"slug": "private-design", "document_id": doc["id"]}},
        )
        return doc, art.id


async def test_read_routes_lists_stats_and_revocation(token_client, sf):
    doc, identifier = await setup(sf)
    bob = await _human_token(sf, "bob", "reader")
    eve = await _human_token(sf, "eve", "reader")
    for tail in ["", "/content", "/thumb"]:
        response = await token_client.get(
            "/api/artifacts/" + identifier + tail, headers=bob
        )
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "private, no-store"
        assert (
            await token_client.get("/api/artifacts/" + identifier + tail, headers=eve)
        ).status_code == 404
    assert (
        await token_client.get(
            "/api/artifacts/" + identifier + "/resource", headers=eve
        )
    ).status_code == 404
    assert [
        r["id"] for r in (await token_client.get("/api/artifacts", headers=bob)).json()
    ] == [identifier]
    assert (await token_client.get("/api/artifacts", headers=eve)).json() == []
    assert (await token_client.get("/api/artifacts/stats", headers=bob)).json()[
        "count"
    ] == 1
    assert (await token_client.get("/api/artifacts/stats", headers=eve)).json()[
        "count"
    ] == 0
    async with sf() as s:
        entry = (
            await s.execute(
                select(UiDesignerAccessEntry).where(
                    UiDesignerAccessEntry.document_id == doc["id"]
                )
            )
        ).scalar_one()
        await s.delete(entry)
        await s.commit()
    assert (
        await token_client.get("/api/artifacts/" + identifier + "/content", headers=bob)
    ).status_code == 404


async def test_sql_filters_private_rows_before_limit_and_cursor(token_client, sf):
    _, hidden = await setup(sf)
    async with sf() as s:
        visible = await artifact_store.create(
            s, data=b"plain", name="public", owner="user:alice"
        )
        # Put the hidden record above it; limit=1 must still return public.
        (await s.get(Artifact, hidden)).created_at = utcnow() + timedelta(seconds=1)
        await s.commit()
    eve = await _human_token(sf, "eve", "reader")
    response = await token_client.get("/api/artifacts?limit=1", headers=eve)
    assert [r["id"] for r in response.json()] == [visible.id]
    assert (
        await token_client.get("/api/artifacts?before=" + hidden, headers=eve)
    ).status_code == 422


async def test_upload_cannot_forge_source_but_authorized_derivative_inherits(
    token_client, sf
):
    doc, parent = await setup(sf)
    bob = await _human_token(sf, "bob", "operator")
    eve = await _human_token(sf, "eve", "operator")
    payload = {"content_b64": base64.b64encode(png_bytes()).decode()}
    assert (
        await token_client.post(
            "/api/artifacts",
            headers=bob,
            json={**payload, "meta": {"ui_designer": {"document_id": doc["id"]}}},
        )
    ).status_code == 400
    denied = await token_client.post(
        "/api/artifacts", headers=eve, json={**payload, "meta": {"parent_id": parent}}
    )
    assert denied.status_code == 404
    derived = await token_client.post(
        "/api/artifacts", headers=bob, json={**payload, "meta": {"parent_id": parent}}
    )
    assert derived.status_code == 201, derived.text
    async with sf() as s:
        row = await s.get(Artifact, derived.json()["id"])
        assert row.ui_provenance and row.ui_document_id == doc["id"]
    assert (
        await token_client.get("/api/artifacts/" + derived.json()["id"], headers=eve)
    ).status_code == 404


async def test_missing_and_deleted_sources_do_not_open_reused_slug(token_client, sf):
    doc, identifier = await setup(sf)
    bob = await _human_token(sf, "bob", "reader")
    alice = await _human_token(sf, "alice", "reader")
    async with sf() as s:
        original = await s.get(UiDesignerDocument, doc["id"])
        original.deleted_at = utcnow()
        await s.commit()
    assert (
        await token_client.get("/api/artifacts/" + identifier, headers=bob)
    ).status_code == 404
    assert (
        await token_client.get("/api/artifacts/" + identifier, headers=alice)
    ).status_code == 200
    async with sf() as s:
        await store.create_document(s, slug="private-design", owner="user:bob")
        await s.delete(await s.get(UiDesignerDocument, doc["id"]))
        await s.commit()
    assert (
        await token_client.get("/api/artifacts/" + identifier, headers=bob)
    ).status_code == 404
    assert (
        await token_client.get("/api/artifacts/" + identifier, headers=alice)
    ).status_code == 200


async def test_legacy_backfill_is_time_bound_idempotent_and_copies_parent(sf):
    now = utcnow()
    async with sf() as s:
        past = await store.create_document(s, slug="reused", owner="user:alice")
        old = await s.get(UiDesignerDocument, past["id"])
        old.created_at = now - timedelta(days=3)
        old.deleted_at = now - timedelta(days=1)
        await s.flush()
        future = await store.create_document(s, slug="reused", owner="user:eve")
        (await s.get(UiDesignerDocument, future["id"])).created_at = now
        legacy = await artifact_store.create(s, data=png_bytes(), owner="user:alice")
        legacy.created_at = now - timedelta(days=2)
        legacy.meta = {"ui_designer": {"slug": "reused"}}
        child = await artifact_store.create(
            s, data=png_bytes(), owner="user:alice", meta={"parent_id": legacy.id}
        )
        orphan = await artifact_store.create(s, data=b"x", owner="user:alice")
        orphan.meta = {"ui_designer": {"slug": "gone"}}
        await s.flush()
        assert await artifact_access.backfill_design_artifacts(s) == 3
        assert (
            legacy.ui_document_id == past["id"] and child.ui_document_id == past["id"]
        )
        assert orphan.ui_provenance and orphan.ui_document_id is None
        assert await artifact_access.backfill_design_artifacts(s) == 0


async def test_generation_rejects_multiple_sources_or_codex_before_provider(sf):
    doc, parent = await setup(sf)
    async with sf() as s:
        other = await store.create_document(s, slug="another", owner="user:alice")
        second = await artifact_store.create(
            s, data=png_bytes(), owner="user:alice", ui_document_id=other["id"]
        )
        who = await access.subject_for(s, "user:alice")
        with pytest.raises(artifact_store.ArtifactRuleError, match="multiple"):
            await artifact_access.reference_document(
                s, [parent, second.id], "user:alice", who=who
            )
        with pytest.raises(
            artifact_store.ArtifactRuleError, match="subscription artist"
        ):
            await artifact_access.reference_document(
                s, [parent], "user:alice", who=who, reject_codex=True
            )


async def test_artifact_grant_and_explicit_doc_read_never_needs_ui_tool(
    token_client, sf, seed_agent, agent_store
):
    from .test_artifacts_api import _agent_headers

    doc, identifier = await setup(sf)
    async with sf() as s:
        s.add(
            UiDesignerAccessEntry(
                document_id=doc["id"], principal="agent:keeper", role="reader"
            )
        )
        await s.commit()
    headers = await _agent_headers(
        sf, seed_agent, agent_store, "keeper", grants=("mcp__platform__artifacts",)
    )
    assert (
        await token_client.get("/api/artifacts/" + identifier, headers=headers)
    ).status_code == 200
    assert (
        await token_client.get("/api/ui-designer/docs/private-design", headers=headers)
    ).status_code == 403


async def test_feed_rechecks_authoritative_source_not_frame_metadata(token_client, sf):
    from starlette.requests import Request
    from agentplatform.api.artifacts_feed import _may_see

    doc, identifier = await setup(sf)
    bob = await _human_token(sf, "bob", "reader")
    eve = await _human_token(sf, "eve", "reader")

    def request(headers):
        return Request(
            {
                "type": "http",
                "scheme": "http",
                "path": "/api/artifacts/events",
                "method": "GET",
                "headers": [(b"authorization", headers["Authorization"].encode())],
                "app": token_client._transport.app,
                "query_string": b"",
                "server": ("test", 80),
            }
        )

    frame = {
        "artifact": {
            "id": identifier,
            "meta": {"ui_designer": {"document_id": "forged-public"}},
        }
    }
    assert await _may_see(request(bob), None, frame)
    assert not await _may_see(request(eve), None, frame)
    async with sf() as s:
        await s.delete(
            (
                await s.execute(
                    select(UiDesignerAccessEntry).where(
                        UiDesignerAccessEntry.document_id == doc["id"]
                    )
                )
            ).scalar_one()
        )
        await s.commit()
    assert not await _may_see(request(bob), None, frame)


async def test_backfill_does_not_load_thumbnails_or_blobs(sf):
    from sqlalchemy import event

    async with sf() as s:
        row = await artifact_store.create(s, data=png_bytes(), owner="user:a")
        row.meta = {"ui_designer": {"slug": "gone"}}
        await s.commit()
    statements = []

    def record(conn, cursor, statement, params, context, executemany):
        statements.append(statement)

    event.listen(sf.kw["bind"].sync_engine, "before_cursor_execute", record)
    try:
        async with sf() as s:
            assert await artifact_access.backfill_design_artifacts(s) == 1
    finally:
        event.remove(sf.kw["bind"].sync_engine, "before_cursor_execute", record)
    assert all(
        "artifacts.thumb" not in sql and "artifact_blobs" not in sql
        for sql in statements
    )


from .test_image_gen import executor, fresh_process_state  # noqa:F401


async def test_direct_generation_inherits_source_and_suppresses_private_card(
    admin_client, token_client, sf, executor
):
    from .test_image_gen import _art_messages

    doc, parent = await setup(sf)
    before = len(await _art_messages(sf))
    response = await admin_client.post(
        "/api/artifacts/generate",
        json={"prompt": "private design detail", "reference_ids": [parent]},
    )
    assert response.status_code == 201, response.text
    assert len(executor.calls) == 1
    async with sf() as s:
        row = await s.get(Artifact, response.json()["id"])
        assert row.ui_provenance and row.ui_document_id == doc["id"]
    assert len(await _art_messages(sf)) == before
    eve = await _human_token(sf, "eve", "reader")
    assert (
        await token_client.get("/api/artifacts/" + response.json()["id"], headers=eve)
    ).status_code == 404


async def test_mixed_ui_reference_generation_refuses_before_executor(
    admin_client, sf, executor
):
    _, first = await setup(sf)
    async with sf() as s:
        doc = await store.create_document(s, slug="second-design", owner="user:alice")
        row = await artifact_store.create(
            s, data=png_bytes(), owner="user:alice", ui_document_id=doc["id"]
        )
        second = row.id
    response = await admin_client.post(
        "/api/artifacts/generate",
        json={"prompt": "combine", "reference_ids": [first, second]},
    )
    assert response.status_code == 422 and executor.calls == []


async def test_codex_ui_reference_refuses_before_agent_or_run_launch(sf):
    _, parent = await setup(sf)
    # Generate_codex executes this preflight before inspecting the artist or
    # materializing its Run; passing no artist infrastructure proves the seam.
    with pytest.raises(artifact_store.ArtifactRuleError, match="subscription artist"):
        await image_gen_service.generate_codex(
            session_factory=sf,
            producer=None,
            settings=None,
            agent_store=None,
            owner="user:alice",
            principal="alice",
            prompt="x",
            reference_ids=[parent],
        )
