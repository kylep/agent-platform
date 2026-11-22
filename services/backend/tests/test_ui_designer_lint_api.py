"""Read-only, pinned lint pages reuse the renderer's deterministic engine."""

import json
import httpx
import pytest
from agentplatform.ui_designer import svg
from agentplatform.ui_designer.lint import RULES_VERSION, findings
from agentplatform.ui_designer.render_client import RenderClient
from agentplatform.ui_designer.errors import UiDesignerError
from tests.test_ui_designer_api import P, SLUG, renderer, edit
from tests.test_ui_designer_api_auth import _agent, RELAY, UD


def document():
    return {
        "schema": 1,
        "tokens": {"color": {"match": "#aaaaaa", "unused": "#000000"}},
        "pages": [
            {
                "id": "p",
                "children": [
                    {
                        "id": "frame",
                        "type": "frame",
                        "w": 200,
                        "h": 100,
                        "fill": "#fff",
                        "children": [
                            {
                                "id": "text",
                                "type": "text",
                                "text": "Low contrast",
                                "color": "#aaa",
                                "style": {"family": "Inter", "size": 16},
                            }
                        ],
                    }
                ],
            }
        ],
    }


@pytest.fixture
def lint_renderer(renderer):
    original = renderer.handler

    def handle(request):
        if request.url.path == "/lint":
            body = json.loads(request.content)
            renderer.calls.append(("/lint", body))
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "renderer_version": svg.renderer_version(),
                    **findings(body["doc"], body.get("mode"), body.get("node")),
                },
            )
        return original(request)

    renderer.handler = handle
    return renderer


async def create(client, doc=None):
    response = await client.post(
        P + "/docs", json={"slug": SLUG, "doc": doc or document()}
    )
    assert response.status_code == 200, response.text


async def test_pinned_lint_cursor_hygiene_and_fix(admin_client, lint_renderer):
    await create(admin_client)
    path = f"{P}/docs/{SLUG}/lint"
    first = await admin_client.get(path, params={"version": 1, "limit": 1})
    assert first.status_code == 200, first.text
    result = first.json()
    assert result["version"] == 1 and result["rules_version"] == RULES_VERSION
    assert result["next_cursor"] and len(result["findings"]) == 1
    cursor = result["next_cursor"]
    all_findings = result["findings"][:]
    while cursor:
        page = await admin_client.get(
            path, params={"version": 1, "cursor": cursor, "limit": 2}
        )
        assert page.status_code == 200, page.text
        all_findings.extend(page.json()["findings"])
        cursor = page.json()["next_cursor"]
    assert any(f["code"] == "UD-LINT-CONTRAST" and f["node_id"] == "text" for f in all_findings)
    assert len(all_findings) == result["total"]
    bad = await admin_client.get(
        path, params={"version": 1, "node": "text", "cursor": result["next_cursor"]}
    )
    assert bad.status_code == 422
    assert (
        await edit(
            admin_client, 1, [{"op": "update", "id": "text", "set": {"color": "#000"}}]
        )
    ).status_code == 200
    latest = (await admin_client.get(path, params={"version": 2})).json()
    assert not any(f["code"] == "UD-LINT-CONTRAST" for f in latest["findings"])
    old = (await admin_client.get(path, params={"version": 1})).json()
    assert any(f["code"] == "UD-LINT-CONTRAST" for f in old["findings"])


async def test_lint_read_grant_and_modes(
    admin_client, lint_renderer, sf, seed_agent, agent_store, token_client
):
    doc = document()
    doc["modes"] = ["dark", "light"]
    doc["tokens"]["color"]["match"] = {"dark": "#aaaaaa", "light": "#aaaaaa"}
    await create(admin_client, doc)
    path = f"{P}/docs/{SLUG}/lint"
    denied = await _agent(sf, seed_agent, agent_store, "lint-denied", grants=(RELAY,))
    assert (
        await token_client.get(path, params={"version": 1}, headers=denied)
    ).status_code == 403
    allowed = await _agent(
        sf, seed_agent, agent_store, "lint-reader", grants=(RELAY, UD)
    )
    response = await token_client.get(
        path, params={"version": 1, "all_modes": "true"}, headers=allowed
    )
    assert response.status_code == 200, response.text
    assert {f["mode"] for f in response.json()["findings"]} == {"dark", "light"}
    assert (
        await admin_client.get(
            path, params={"version": 1, "all_modes": "true", "mode": "dark"}
        )
    ).status_code == 422
    assert (await admin_client.get(path)).status_code == 422
    assert (await admin_client.get(path, params={"version": 2})).status_code == 404
    assert (
        await admin_client.get(path, params={"version": 1, "limit": 101})
    ).status_code == 422


async def test_long_expanded_node_id_is_accepted(admin_client, lint_renderer):
    doc = document()
    doc["pages"][0]["children"] = [
        {"id": "instance-" + "a" * 39, "type": "instance", "of": "component"}
    ]
    doc["components"] = {
        "component": {
            "id": "component",
            "type": "frame",
            "w": 200,
            "h": 100,
            "fill": "#fff",
            "children": [
                {
                    "id": "nested-" + "b" * 40,
                    "type": "frame",
                    "w": 200,
                    "h": 100,
                    "children": [
                        {
                            "id": "text-" + "c" * 43,
                            "type": "text",
                            "text": "Low",
                            "color": "#aaa",
                            "style": {"family": "Inter", "size": 16},
                        }
                    ],
                }
            ],
        }
    }
    await create(admin_client, doc)
    node = (
        doc["pages"][0]["children"][0]["id"]
        + "/"
        + doc["components"]["component"]["children"][0]["children"][0]["id"]
    )
    # One component prefix alone is <96; use a long authored id plus a nested instance path.
    assert len(node) > 96
    response = await admin_client.get(
        f"{P}/docs/{SLUG}/lint", params={"version": 1, "node": node}
    )
    assert response.status_code == 200, response.text


async def test_lint_page_never_exceeds_text_budget(admin_client, renderer):
    await create(admin_client)
    one = {
        "code": "TEST",
        "severity": "info",
        "node_id": "text",
        "mode": None,
        "message": "x" * 500,
        "suggestion": "y" * 500,
        "approximate": True,
    }
    entries = [{**one, "message": str(i) + "x" * 490} for i in range(100)]
    original = renderer.handler

    def handle(request):
        if request.url.path == "/lint":
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "renderer_version": svg.renderer_version(),
                    "rules_version": RULES_VERSION,
                    "findings": entries,
                    "total": 100,
                    "by_severity": {"info": 100},
                    "truncated": False,
                },
            )
        return original(request)

    renderer.handler = handle
    response = await admin_client.get(
        f"{P}/docs/{SLUG}/lint", params={"version": 1, "limit": 100}
    )
    assert response.status_code == 200, response.text
    assert len(response.content) <= 32 << 10
    assert len(response.json()["findings"]) < 100 and response.json()["next_cursor"]


@pytest.mark.parametrize(
    "patch",
    [
        {"rules_version": "wrong"},
        {"total": True},
        {"findings": [{}]},
        {"by_severity": {"info": -1}},
    ],
)
async def test_lint_rejects_malformed_renderer(sf, patch):
    body = {
        "ok": True,
        "renderer_version": svg.renderer_version(),
        "rules_version": RULES_VERSION,
        "findings": [],
        "total": 0,
        "by_severity": {},
        "truncated": False,
        **patch,
    }
    client = RenderClient(
        "http://renderer",
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))
        ),
    )
    with pytest.raises(UiDesignerError):
        await client.lint(doc=document(), node=None, mode=None)
    await client.aclose()
