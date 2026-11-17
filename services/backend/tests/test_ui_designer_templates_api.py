"""Starter catalogue creates are ordinary authorized writes with immutable receipts."""

import sys
import types
import copy
import pytest
from agentplatform.ui_designer import store
from tests.test_ui_designer_api import P
from tests.test_ui_designer_api_auth import _agent, RELAY, UD


@pytest.fixture
def catalogue(monkeypatch):
    # API contract fixture; the actual four designs have separate provider/render tests.
    module = types.ModuleType("agentplatform.ui_designer.templates")
    meta = {
        "id": "starter",
        "revision": 1,
        "title": "Starter",
        "description": "A bounded starter.",
        "width": 480,
        "height": 320,
        "tags": ["device"],
        "preview_node": "canvas",
    }
    doc = {
        "schema": 1,
        "title": "Starter",
        "pages": [
            {
                "id": "page",
                "children": [
                    {
                        "id": "canvas",
                        "type": "frame",
                        "w": 480,
                        "h": 320,
                        "children": [],
                    }
                ],
            }
        ],
    }
    module.list_templates = lambda: [copy.deepcopy(meta)]

    def get_template(identifier, revision):
        if identifier != "starter" or revision != 1:
            raise store._not_found("template revision")
        return copy.deepcopy(doc)

    module.get_template = get_template
    monkeypatch.setitem(sys.modules, module.__name__, module)
    return module


async def test_catalogue_and_template_create_with_title_override(
    admin_client, catalogue
):
    listed = await admin_client.get(P + "/templates")
    assert listed.status_code == 200 and listed.json()["templates"][0]["revision"] == 1
    body = {
        "slug": "starter-copy",
        "template_id": "starter",
        "template_revision": 1,
        "request_id": "starter-one",
    }
    created = await admin_client.post(P + "/docs", json=body)
    assert created.status_code == 200, created.text
    doc = (await admin_client.get(P + "/docs/starter-copy")).json()
    assert doc["title"] == "Starter" and doc["doc"]["title"] == "Starter"
    assert doc["doc"]["pages"][0]["children"][0]["w"] == 480
    custom = await admin_client.post(
        P + "/docs",
        json={**body, "slug": "custom", "title": "Mine", "request_id": "custom-one"},
    )
    assert custom.status_code == 200
    assert (await admin_client.get(P + "/docs/custom")).json()["doc"]["title"] == "Mine"
    assert catalogue.get_template("starter", 1)["title"] == "Starter"


async def test_template_receipt_replays_before_catalogue_lookup(
    admin_client, catalogue, monkeypatch
):
    body = {
        "slug": "receipt-copy",
        "template_id": "starter",
        "template_revision": 1,
        "request_id": "one",
    }
    assert (await admin_client.post(P + "/docs", json=body)).status_code == 200

    def retired(*args):
        raise AssertionError("successful replay must not access the current catalogue")

    monkeypatch.setattr(catalogue, "get_template", retired)
    repeated = await admin_client.post(P + "/docs", json=body)
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["replayed"] and repeated.json()["version"] == 1
    changed = await admin_client.post(
        P + "/docs", json={**body, "template_revision": 2}
    )
    assert (
        changed.status_code == 409
        and changed.json()["detail"]["code"] == "UD-REQUEST-REUSED"
    )


@pytest.mark.parametrize(
    "extra",
    [
        {"template_id": "starter"},
        {"template_revision": 1},
        {"template_id": "starter", "template_revision": True},
        {
            "template_id": "starter",
            "template_revision": 1,
            "doc": {"schema": 1, "pages": []},
        },
        {"template_id": "starter", "template_revision": 1, "request_id": ""},
    ],
)
async def test_template_inputs_are_explicit_and_exclusive(
    admin_client, catalogue, extra
):
    response = await admin_client.post(
        P + "/docs", json={"slug": "bad", "request_id": "bad-one", **extra}
    )
    assert response.status_code == 422, response.text


async def test_unknown_revision_and_normal_create_unchanged(admin_client, catalogue):
    unknown = await admin_client.post(
        P + "/docs",
        json={
            "slug": "unknown",
            "template_id": "starter",
            "template_revision": 2,
            "request_id": "unknown-one",
        },
    )
    assert unknown.status_code == 404
    blank = await admin_client.post(
        P + "/docs", json={"slug": "blank", "title": "Blank"}
    )
    assert blank.status_code == 200
    explicit = await admin_client.post(
        P + "/docs", json={"slug": "explicit", "doc": {"schema": 1, "pages": []}}
    )
    assert explicit.status_code == 200
    assert (await admin_client.get(P + "/docs/explicit")).json()["doc"]["pages"] == []


async def test_template_grant_and_run_fences_and_replay_at_write_limit(
    admin_client, catalogue, sf, seed_agent, agent_store, token_client
):
    from tests.test_ui_designer_api import settings

    denied = await _agent(
        sf, seed_agent, agent_store, "template-denied", grants=(RELAY,)
    )
    assert (await token_client.get(P + "/templates", headers=denied)).status_code == 403
    body = {
        "slug": "agent-starter",
        "template_id": "starter",
        "template_revision": 1,
        "request_id": "agent-one",
    }
    runless = await _agent(
        sf, seed_agent, agent_store, "template-runless", grants=(RELAY, UD), run=False
    )
    assert (
        await token_client.post(P + "/docs", headers=runless, json=body)
    ).status_code == 403
    allowed = await _agent(
        sf, seed_agent, agent_store, "template-writer", grants=(RELAY, UD)
    )
    settings(admin_client).ui_designer_writes_per_hour = 1
    first = await token_client.post(P + "/docs", headers=allowed, json=body)
    assert first.status_code == 200, first.text
    repeated = await token_client.post(P + "/docs", headers=allowed, json=body)
    assert repeated.status_code == 200 and repeated.json()["replayed"]


async def test_real_starter_catalogue_creates_a_matching_document(admin_client):
    listed = await admin_client.get(P + "/templates")
    assert listed.status_code == 200, listed.text
    entries = listed.json()["templates"]
    assert len(entries) == 4
    for index, entry in enumerate(entries):
        slug = f"real-starter-{index}"
        created = await admin_client.post(
            P + "/docs",
            json={
                "slug": slug,
                "template_id": entry["id"],
                "template_revision": entry["revision"],
                "request_id": f"real-starter-{index}",
            },
        )
        assert created.status_code == 200, created.text
        doc = (await admin_client.get(P + "/docs/" + slug, params={"depth": 1})).json()[
            "doc"
        ]
        root = doc["pages"][0]["children"][0]
        assert root["id"] == entry["preview_node"]
        assert (root["w"], root["h"]) == (entry["width"], entry["height"])
