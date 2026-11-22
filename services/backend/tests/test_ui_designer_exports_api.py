"""Generated export MIME is internal; every non-raster stays a download."""

import base64
import httpx
import pytest
from agentplatform import artifact_store
from agentplatform.ui_designer import svg
from agentplatform.ui_designer.errors import UiDesignerError
from agentplatform.ui_designer.render_client import RenderClient
from tests.test_ui_designer_api import P, SLUG, mk, renderer  # noqa: F401

EXPORTS = {
    "svg": (
        "image/svg+xml",
        b'<svg xmlns="http://www.w3.org/2000/svg" width="20" height="10"/>',
    ),
    "pdf": ("application/pdf", b"%PDF-1.4\n%%EOF"),
    "html": ("text/html", b"<!doctype html><html><body>&lt;script&gt;</body></html>"),
}


def answer(format="svg", **patch):
    mime, data = EXPORTS[format]
    return {
        "ok": True,
        "renderer_version": svg.renderer_version(),
        "format": format,
        "mime": mime,
        "data": base64.b64encode(data).decode(),
        "width": 20,
        "height": 10,
        "box": [0, 0, 20, 10],
        "warnings": [],
        **patch,
    }


@pytest.mark.parametrize("format", list(EXPORTS))
async def test_export_formats_mime_metadata_and_attachment_policy(
    admin_client, renderer, format
):
    await mk(admin_client)
    original = renderer.handler

    def handle(request):
        if request.url.path == "/export":
            renderer.calls.append(
                ("/export", __import__("json").loads(request.content))
            )
            return httpx.Response(
                200, json=answer(format, warnings=["Approximation notice"])
            )
        return original(request)

    renderer.handler = handle
    response = await admin_client.post(
        f"{P}/docs/{SLUG}/export",
        json={
            "format": format,
            "node": "root",
            "version": 1,
            "mode": "light",
            "name": "example",
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert (
        result["mime"] == EXPORTS[format][0] and result["name"] == f"example.{format}"
    )
    assert result["width"] == 20 and result["warnings"] == ["Approximation notice"]
    artifact = await admin_client.get(f"/api/artifacts/{result['artifact_id']}")
    assert artifact.json()["kind"] == "file"
    assert artifact.json()["meta"]["ui_designer"]["format"] == format
    downloaded = await admin_client.get(
        f"/api/artifacts/{result['artifact_id']}/content"
    )
    assert downloaded.content == EXPORTS[format][1]
    assert downloaded.headers["content-type"].startswith(EXPORTS[format][0])
    assert downloaded.headers["content-disposition"].startswith("attachment;")
    assert downloaded.headers["x-content-type-options"] == "nosniff"
    resource = await admin_client.get(
        f"/api/artifacts/{result['artifact_id']}/resource"
    )
    assert resource.headers["content-disposition"].startswith("attachment;")
    assert resource.headers["x-content-type-options"] == "nosniff"

    req = renderer.calls[-1][1]
    assert (
        req["node"] == "root" and req["mode"] == "light" and req["max_bytes"] <= 8 << 20
    )


async def test_public_svg_claim_or_forged_trust_flag_never_changes_mime(admin_client):
    response = await admin_client.post(
        "/api/artifacts",
        json={
            "name": "unsafe.svg",
            "mime": "image/svg+xml",
            "source": "tool",
            "text": '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
            "_trusted_mime": "image/svg+xml",
        },
    )
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["mime"] == "application/octet-stream"
    content = await admin_client.get(f"/api/artifacts/{result['id']}/content")
    assert content.headers["content-disposition"].startswith("attachment;")
    assert content.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize(
    "patch",
    [
        {"mime": "text/html"},
        {"box": [0, 0, True, 1]},
        {"box": [0, 0, -1, 1]},
        {"box": [0, 0, 1]},
        {"renderer_version": "old"},
        {"width": True},
        {"width": 0},
        {"width": 10_000, "height": 10_000},
        {"data": "invalid!"},
        {"data": "a" * 1000},
        {"warnings": ["x" * 501]},
        {"warnings": ["x"] * 33},
    ],
)
async def test_renderer_export_bad_or_over_budget_response_refused(sf, patch):
    client = RenderClient(
        "http://renderer",
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(200, json=answer(**patch))
            )
        ),
    )
    async with sf() as session:
        with pytest.raises(UiDesignerError):
            await client.export_file(
                session,
                doc={"schema": 1, "pages": []},
                mode=None,
                node=None,
                format="svg",
                max_bytes=256,
            )
    await client.aclose()


async def test_renderer_cannot_request_unrelated_asset(sf):
    client = RenderClient(
        "http://renderer",
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(409, json={"missing": ["a" * 64]})
            )
        ),
    )
    async with sf() as session:
        with pytest.raises(UiDesignerError, match="unrelated asset"):
            await client.export_file(
                session,
                doc={"schema": 1, "pages": []},
                mode=None,
                node=None,
                format="svg",
            )
    await client.aclose()


async def test_trusted_mime_only_accepts_generated_tool_exports(sf):
    async with sf() as session:
        with pytest.raises(artifact_store.ArtifactRuleError):
            await artifact_store.create(
                session,
                data=EXPORTS["svg"][1],
                owner="user:kyle",
                source="upload",
                _trusted_mime="image/svg+xml",
            )


async def test_export_missing_assets_retry_only_uploads_document_assets(sf):
    from agentplatform.ui_designer import store
    from tests.test_ui_designer_api import png_bytes

    async with sf() as session:
        sha = await store.add_asset(
            session, data=png_bytes(1, 1), mime="image/png", width=1, height=1
        )
        await session.commit()
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        if len(calls) == 1:
            return httpx.Response(409, json={"missing": [sha]})
        if request.method == "PUT":
            assert request.url.path == "/assets/" + sha
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(200, json=answer())

    client = RenderClient(
        "http://renderer",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    doc = {
        "schema": 1,
        "pages": [
            {"id": "page", "children": [{"id": "image", "type": "image", "asset": sha}]}
        ],
    }
    async with sf() as session:
        exported = await client.export_file(
            session, doc=doc, mode=None, node=None, format="svg"
        )
    assert exported.data == EXPORTS["svg"][1]
    assert calls == [
        ("POST", "/export"),
        ("PUT", "/assets/" + sha),
        ("POST", "/export"),
    ]
    await client.aclose()
