"""Pinned copies use one CAS write and replay without reading a vanished source."""

import pytest
from sqlalchemy import select
from agentplatform.db import UiDesignerDocumentAsset, UiDesignerAsset
from agentplatform.ui_designer import store
from tests.test_ui_designer_api import P, renderer, png_bytes
from tests.test_ui_designer_api_auth import _agent, RELAY, UD


def source_doc():
    return {
        "schema": 1,
        "tokens": {"color": {"brand": "#f00"}},
        "components": {
            "card": {
                "id": "card",
                "type": "frame",
                "w": 40,
                "h": 30,
                "fill": "$color.brand",
                "children": [
                    {
                        "id": "label",
                        "type": "text",
                        "text": "Base",
                        "style": {"family": "Inter", "size": 14},
                    }
                ],
            }
        },
        "pages": [{"id": "source-page", "children": []}],
    }


async def setup_docs(client):
    assert (
        await client.post(P + "/docs", json={"slug": "source", "doc": source_doc()})
    ).status_code == 200
    assert (
        await client.post(
            P + "/docs",
            json={
                "slug": "consumer",
                "doc": {"schema": 1, "pages": [{"id": "p", "children": []}]},
            },
        )
    ).status_code == 200
    return (await client.get(P + "/docs/source", params={"depth": 0})).json()["id"]


def import_body(source_id):
    return {
        "expected_version": 1,
        "request_id": "import-one",
        "library": {
            "source_id": source_id,
            "source_version": 1,
            "namespace": "shared",
            "components": ["card"],
        },
    }


async def edit(client, slug, version, ops):
    result = await client.post(
        f"{P}/docs/{slug}/edit", json={"expected_version": version, "ops": ops}
    )
    assert result.status_code == 200, result.text
    return result.json()


async def test_import_update_preview_pins_and_retry_after_source_deleted(admin_client):
    source_id = await setup_docs(admin_client)
    body = import_body(source_id)
    imported = await admin_client.post(P + "/docs/consumer/libraries/import", json=body)
    assert imported.status_code == 200, imported.text
    assert imported.json()["version"] == 2 and imported.json()["library_id"] == "shared"
    await edit(
        admin_client,
        "source",
        1,
        [{"op": "tokens", "set": {"color": {"brand": "#00f"}}}],
    )
    preview = await admin_client.post(
        P + "/docs/consumer/libraries/shared/preview", json={"source_version": 2}
    )
    assert preview.status_code == 200, preview.text
    assert (
        preview.json()["version"] == 2
        and preview.json()["updated"]
        and not preview.json()["conflicts"]
    )
    apply = {"source_version": 2, "expected_version": 2, "request_id": "apply-one"}
    updated = await admin_client.post(
        P + "/docs/consumer/libraries/shared/apply", json=apply
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["version"] == 3
    pins = (await admin_client.get(P + "/docs/consumer/libraries")).json()
    assert (
        pins["libraries"][0]["source_id"] == source_id
        and pins["libraries"][0]["source_version"] == 2
    )
    old = (
        await admin_client.get(P + "/docs/consumer/libraries", params={"version": 2})
    ).json()
    assert old["version"] == 2 and old["libraries"][0]["source_version"] == 1
    assert (
        await admin_client.delete(P + "/docs/source", params={"hard": "true"})
    ).status_code == 200
    for path, payload, version in [("import", body, 2), ("shared/apply", apply, 3)]:
        replay = await admin_client.post(
            P + "/docs/consumer/libraries/" + path, json=payload
        )
        assert replay.status_code == 200, replay.text
        assert replay.json()["replayed"] and replay.json()["version"] == version
    # Reusing a deleted slug creates a different UUID; the pin cannot follow it.
    assert (
        await admin_client.post(
            P + "/docs", json={"slug": "source", "doc": source_doc()}
        )
    ).status_code == 200
    missing = await admin_client.post(
        P + "/docs/consumer/libraries/shared/preview", json={"source_version": 2}
    )
    assert missing.status_code == 404


async def test_library_conflicts_cas_and_receipt_inputs_are_distinct(admin_client):
    source_id = await setup_docs(admin_client)
    body = import_body(source_id)
    assert (
        await admin_client.post(P + "/docs/consumer/libraries/import", json=body)
    ).status_code == 200
    changed = {**body, "library": {**body["library"], "namespace": "other"}}
    reused = await admin_client.post(
        P + "/docs/consumer/libraries/import", json=changed
    )
    assert (
        reused.status_code == 409
        and reused.json()["detail"]["code"] == "UD-REQUEST-REUSED"
    )
    await edit(
        admin_client,
        "source",
        1,
        [{"op": "tokens", "set": {"color": {"brand": "#00f"}}}],
    )
    stale = await admin_client.post(
        P + "/docs/consumer/libraries/shared/apply",
        json={"source_version": 2, "expected_version": 1, "request_id": "stale"},
    )
    assert (
        stale.status_code == 409
        and stale.json()["detail"]["code"] == "UD-VERSION-CONFLICT"
    )
    doc = (await admin_client.get(P + "/docs/consumer", params={"depth": 12})).json()[
        "doc"
    ]
    component = next(iter(doc["libraries"]["shared"]["owned"]["components"]))
    await edit(
        admin_client,
        "consumer",
        2,
        [{"op": "update", "id": component, "set": {"fill": "#0f0"}}],
    )
    preview = await admin_client.post(
        P + "/docs/consumer/libraries/shared/preview", json={"source_version": 2}
    )
    assert preview.status_code == 200 and preview.json()["conflicts"]
    conflict = await admin_client.post(
        P + "/docs/consumer/libraries/shared/apply",
        json={"source_version": 2, "expected_version": 3, "request_id": "conflict"},
    )
    assert (
        conflict.status_code == 409
        and conflict.json()["detail"]["code"] == "UD-LIBRARY-CONFLICT"
    )
    assert (await admin_client.get(P + "/docs/consumer/libraries")).json()[
        "version"
    ] == 3


async def test_library_grant_and_run_fences(
    admin_client, sf, seed_agent, agent_store, token_client
):
    source_id = await setup_docs(admin_client)
    body = import_body(source_id)
    denied = await _agent(sf, seed_agent, agent_store, "no-libraries", grants=(RELAY,))
    assert (
        await token_client.post(
            P + "/docs/consumer/libraries/import", headers=denied, json=body
        )
    ).status_code == 403
    runless = await _agent(
        sf, seed_agent, agent_store, "runless-library", grants=(RELAY, UD), run=False
    )
    assert (
        await token_client.post(
            P + "/docs/consumer/libraries/import", headers=runless, json=body
        )
    ).status_code == 403
    allowed = await _agent(
        sf, seed_agent, agent_store, "library-writer", grants=(RELAY, UD)
    )
    response = await token_client.post(
        P + "/docs/consumer/libraries/import", headers=allowed, json=body
    )
    assert response.status_code == 200, response.text


async def test_source_bound_asset_placement_survives_source_delete(admin_client, sf):
    source_id = await setup_docs(admin_client)
    async with sf() as session:
        placed = await store.place_asset(
            session,
            "source",
            data=png_bytes(2, 2),
            mime="image/png",
            placed_by="user:kyle",
            placed_from="test",
        )
        await session.commit()
    sha = placed["sha256"]
    await edit(
        admin_client,
        "source",
        1,
        [
            {
                "op": "insert",
                "parent": "card",
                "node": {"id": "image", "type": "image", "asset": sha, "w": 2, "h": 2},
            }
        ],
    )
    body = import_body(source_id)
    body["library"]["source_version"] = 2
    imported = await admin_client.post(P + "/docs/consumer/libraries/import", json=body)
    assert imported.status_code == 200, imported.text
    consumer = (
        await admin_client.get(P + "/docs/consumer", params={"depth": 0})
    ).json()["id"]
    assert (
        await admin_client.delete(P + "/docs/source", params={"hard": "true"})
    ).status_code == 200
    async with sf() as session:
        assert await session.get(UiDesignerDocumentAsset, (consumer, sha)) is not None
        assert await session.get(UiDesignerAsset, sha) is not None
        await store.gc_assets(session)
        await session.commit()
        assert await session.get(UiDesignerAsset, sha) is not None


async def test_unbound_plan_asset_is_refused_and_rolls_back(
    admin_client, sf, monkeypatch
):
    from agentplatform.ui_designer import libraries

    source_id = await setup_docs(admin_client)
    original = libraries.plan_import

    def malicious(*args, **kwargs):
        plan = original(*args, **kwargs)
        plan["assets"] = {"a" * 64}
        return plan

    monkeypatch.setattr(libraries, "plan_import", malicious)
    response = await admin_client.post(
        P + "/docs/consumer/libraries/import", json=import_body(source_id)
    )
    assert response.status_code == 403
    assert (await admin_client.get(P + "/docs/consumer/libraries")).json() == {
        "slug": "consumer",
        "version": 1,
        "libraries": [],
    }


def test_stable_receipt_inputs_are_not_a_general_edit_escape_hatch():
    with pytest.raises(ValueError,match='only for library mutations'):
        store.edit_fingerprint(op='edit',slug='consumer',ops=[],expected_version=1,reason='',stable_args={'anything':True})
