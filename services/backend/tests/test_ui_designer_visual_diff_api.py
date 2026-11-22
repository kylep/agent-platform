"""Explicit retained-version visual diff transport, authority and work bounds."""

import base64
import json
import httpx
import pytest
from agentplatform.ui_designer import svg
from agentplatform.ui_designer.errors import UiDesignerError
from agentplatform.ui_designer.render_client import RenderClient
from tests.test_ui_designer_api import P, SLUG, mk, edit, renderer, settings, png_bytes
from tests.test_ui_designer_api_auth import _agent, RELAY, UD


def answer(**patch):
    image = base64.b64encode(png_bytes(20, 10)).decode()
    return {
        "ok": True,
        "renderer_version": svg.renderer_version(),
        "mime": "image/png",
        "image": image,
        "base_image": image,
        "width": 20,
        "height": 10,
        "box": [0, 0, 20, 10],
        "changed_pixels": 20,
        "total_pixels": 200,
        "regions": [{"x": 0, "y": 0, "w": 2, "h": 10, "node_ids": ["root"]}],
        "node_ids": ["root"],
        "truncated": False,
        "warnings": [],
        **patch,
    }


@pytest.fixture
def diff_renderer(renderer):
    original = renderer.handler

    def handle(request):
        if request.url.path == "/diff":
            renderer.calls.append(("/diff", json.loads(request.content)))
            return httpx.Response(200, json=answer())
        return original(request)

    renderer.handler = handle
    return renderer


async def test_diff_uses_two_explicit_versions_and_no_artifacts(
    admin_client, diff_renderer
):
    await mk(admin_client)
    assert (
        await edit(
            admin_client, 1, [{"op": "update", "id": "root", "set": {"fill": "#f00"}}]
        )
    ).status_code == 200
    response = await admin_client.post(
        f"{P}/docs/{SLUG}/visual-diff",
        json={
            "from_version": 1,
            "to_version": 2,
            "node": "root",
            "mode": "light",
            "tolerance": 7,
            "scale": 0.5,
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["from_version"] == 1 and result["to_version"] == 2
    assert result["changed_pixels"] == 20 and result["regions"][0]["node_ids"] == [
        "root"
    ]
    assert result["image"] == result["base_image"] and "artifact_id" not in result
    request = diff_renderer.calls[-1][1]
    assert request["before_doc"]["pages"][0]["children"][0].get("fill") is None
    assert request["after_doc"]["pages"][0]["children"][0]["fill"] == "#f00"
    assert request["tolerance"] == 7 and request["scale"] == 0.5


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"from_version": 1},
        {"from_version": 1, "to_version": True},
        {"from_version": 1, "to_version": 1, "tolerance": True},
        {"from_version": 1, "to_version": 1, "tolerance": -1},
        {"from_version": 1, "to_version": 1, "tolerance": 256},
        {"from_version": 1, "to_version": 1, "scale": 7},
    ],
)
async def test_diff_input_bounds(admin_client, body):
    await mk(admin_client)
    response = await admin_client.post(f"{P}/docs/{SLUG}/visual-diff", json=body)
    assert response.status_code == 422, response.text


async def test_diff_retained_version_required(admin_client, diff_renderer):
    await mk(admin_client)
    response = await admin_client.post(
        f"{P}/docs/{SLUG}/visual-diff", json={"from_version": 1, "to_version": 2}
    )
    assert response.status_code == 404
    assert not diff_renderer.calls


async def test_diff_read_grant_and_double_pixel_charge(
    sf, seed_agent, agent_store, admin_client, token_client, diff_renderer
):
    await mk(admin_client)
    no_grant = await _agent(sf, seed_agent, agent_store, "outsider", grants=(RELAY,))
    body = {"from_version": 1, "to_version": 1}
    assert (
        await token_client.post(
            f"{P}/docs/{SLUG}/visual-diff", json=body, headers=no_grant
        )
    ).status_code == 403
    headers = await _agent(
        sf, seed_agent, agent_store, "diff-reader", grants=(RELAY, UD)
    )
    settings(admin_client).ui_designer_pixels_per_hour_mp = 0.0003
    # Each 20x10 image is .0002MP; diff bills both (.0004MP), no artifacts grant required.
    first = await token_client.post(
        f"{P}/docs/{SLUG}/visual-diff", json=body, headers=headers
    )
    assert first.status_code == 200, first.text
    second = await token_client.post(
        f"{P}/docs/{SLUG}/visual-diff", json=body, headers=headers
    )
    assert (
        second.status_code == 429
        and second.json()["detail"]["code"] == "UD-BUDGET-PIXELS"
    )
    settings(admin_client).ui_designer_max_megapixels_per_request = 0.0003
    refused = await admin_client.post(f"{P}/docs/{SLUG}/visual-diff", json=body)
    assert refused.status_code == 413 and "both aligned images" in refused.text


@pytest.mark.parametrize(
    "patch",
    [
        {"width": True},
        {"changed_pixels": 201},
        {"total_pixels": 199},
        {"base_image": "bad!"},
        {"regions": [{"x": -1, "y": 0, "w": 1, "h": 1, "node_ids": []}]},
        {"node_ids": ["<script>"]},
        {"box": [0, 0, False, 10]},
        {"renderer_version": "old"},
    ],
)
async def test_diff_refuses_malformed_renderer_outputs(sf, patch):
    client = RenderClient(
        "http://renderer",
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(200, json=answer(**patch))
            )
        ),
    )
    async with sf() as s:
        with pytest.raises(UiDesignerError):
            await client.visual_diff(
                s,
                before_doc={"pages": []},
                after_doc={"pages": []},
                node=None,
                mode=None,
            )
    await client.aclose()


async def test_real_renderer_payload_matches_api_transport(sf, tmp_path):
    import importlib.util
    import sys
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "ui-renderer" / "app.py"
    spec = importlib.util.spec_from_file_location("visual_diff_contract_renderer", path)
    renderer_app = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = renderer_app
    spec.loader.exec_module(renderer_app)
    before = {
        "schema": 1,
        "pages": [
            {
                "id": "p",
                "children": [
                    {"id": "box", "type": "frame", "w": 10, "h": 10, "fill": "#f00"}
                ],
            }
        ],
    }
    after = json.loads(json.dumps(before))
    after["pages"][0]["children"][0]["x"] = 5

    def handle(request):
        body = json.loads(request.content)
        result = renderer_app.job_diff(
            renderer_app.Config(asset_dir=str(tmp_path)), renderer_app._diff_req(body)
        )
        assert result["ok"]
        for field in ("image", "base_image"):
            result[field] = base64.b64encode(result[field]).decode()
        return httpx.Response(200, json=result)

    client = RenderClient(
        "http://renderer",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    )
    async with sf() as session:
        result = await client.visual_diff(
            session, before_doc=before, after_doc=after, node="box", mode=None
        )
    assert result["box"] == [0, 0, 15, 10]
    assert result["width"] == 15 and result["changed_pixels"] == 100
    assert "box" in result["node_ids"]
    await client.aclose()
