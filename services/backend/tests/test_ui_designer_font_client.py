import hashlib
from types import SimpleNamespace
import pytest
from agentplatform.ui_designer.render_client import RenderClient
from agentplatform.ui_designer.errors import UiDesignerError
from agentplatform.ui_designer import store

RAW = b"validated stored fixture"
SHA = hashlib.sha256(RAW).hexdigest()
DOC = {"fonts": [{"family": "Custom", "weight": 400, "sha256": SHA}]}


async def test_fonts_retry_uploads_only_declared_validated_matching_digest(monkeypatch):
    client = RenderClient("http://renderer")
    calls = []

    async def call(method, path, **kwargs):
        calls.append((method, path, kwargs))
        if len(calls) == 1:
            return 409, {"code": "MISSING-FONTS", "missing_fonts": [SHA, SHA]}
        return 200, {"ok": True}

    async def get_font(s, sha):
        return SimpleNamespace(data=RAW, family="Custom", weight=400)

    monkeypatch.setattr(store, "get_font", get_font)
    client._call = call
    try:
        assert (
            await client._with_fonts("session", "POST", "/render", json={"doc": DOC})
        )[0] == 200
        assert [c[:2] for c in calls] == [
            ("POST", "/render"),
            ("PUT", "/fonts/" + SHA),
            ("POST", "/render"),
        ]
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    "missing",
    [["b" * 64], ["../../etc/passwd"], ["https://example.com"], [{}], [SHA] * 17],
)
async def test_unrelated_fonts_never_read_database(missing, monkeypatch):
    client = RenderClient("http://renderer")

    async def call(*args, **kwargs):
        return 409, {"code": "MISSING-FONTS", "missing_fonts": missing}

    async def get_font(*args):
        raise AssertionError("must not read unrelated stored bytes")

    client._call = call
    monkeypatch.setattr(store, "get_font", get_font)
    try:
        with pytest.raises(UiDesignerError):
            await client._with_fonts("s", "POST", "/render", json={"doc": DOC})
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    "row",
    [
        SimpleNamespace(data=RAW, family="Other", weight=400),
        SimpleNamespace(data=RAW, family="Custom", weight=700),
        SimpleNamespace(data=b"corrupt", family="Custom", weight=400),
    ],
)
async def test_font_metadata_or_digest_mismatch_never_uploads(row, monkeypatch):
    client = RenderClient("http://renderer")

    async def call(method, *args, **kwargs):
        assert method == "POST"
        return 409, {"code": "MISSING-FONTS", "missing_fonts": [SHA]}

    async def get_font(*args):
        return row

    client._call = call
    monkeypatch.setattr(store, "get_font", get_font)
    try:
        with pytest.raises(UiDesignerError):
            await client._with_fonts("s", "POST", "/render", json={"doc": DOC})
    finally:
        await client.aclose()


async def test_old_render_asset_retry_also_refuses_unrelated_sha():
    client = RenderClient("http://renderer")

    async def call(*args, **kwargs):
        return 409, {"missing": ["b" * 64]}

    async def send(*args):
        raise AssertionError("unrelated asset upload")

    client._call = call
    client._send_assets = send
    try:
        with pytest.raises(UiDesignerError):
            await client._fetch("s", {"doc": {"pages": []}}, None)
    finally:
        await client.aclose()
