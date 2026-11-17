"""Motion export commits one bounded artifact and receipt after source checks."""
import base64
import hashlib
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from agentplatform import artifact_store
from agentplatform.db import Artifact, UiDesignerReceipt
from agentplatform.ui_designer import store, svg
from .test_artifact_store import png_bytes
from .test_ui_designer_api import P, SLUG, mk

BODY = {"version": 1, "from_node": "a", "to_node": "b"}
MP4 = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 16


def manifest():
    raw = png_bytes(24, 24)
    png = base64.b64encode(raw).decode()
    sha = hashlib.sha256(raw).hexdigest()
    pose = {"x": 0, "y": 0, "w": 24, "h": 24, "rotate": 0, "opacity": 1, "asset": sha}
    return {"ok": True, "mode": None, "renderer_version": svg.renderer_version(), "motion_version": "motion-1", "width": 24, "height": 24,
        "box": [0, 0, 24, 24], "duration_ms": 800, "fps": 24, "frames": 19,
        "easing": "ease-in-out", "from_image": png, "to_image": png,
        "assets": {sha: png}, "layers": [{"id": "layer", "from": pose, "to": pose}], "warnings": []}


@pytest.fixture
def motion(admin_client):
    state = SimpleNamespace(previews=0, exports=0, failure=False, callback=None, bad=False)

    async def preview(session, **kwargs):
        state.previews += 1
        if state.failure:
            raise AssertionError("replay must not invoke renderer")
        result = manifest()
        if state.bad:
            result["from_image"] = "bad"
        return result

    async def export(session, **kwargs):
        state.exports += 1
        if state.callback:
            await state.callback()
        return {"data": MP4, "mime": "video/mp4", "width": 24, "height": 24, "frames": 19,
                "duration_ms": 800, "warnings": []}

    admin_client._transport.app.state.ui_designer_renderer = SimpleNamespace(motion_preview=preview, motion_export=export)
    return state


async def test_preview_is_pinned_validated_and_has_independent_motion_budget(admin_client, motion):
    await mk(admin_client)
    admin_client._transport.app.state.settings.ui_designer_max_megapixels_per_request = .00001
    response = await admin_client.post(f"{P}/docs/{SLUG}/motion/preview", json=BODY)
    assert response.status_code == 200 and response.json()["motion_version"] == "motion-1", response.text
    assert (await admin_client.post(f"{P}/docs/{SLUG}/motion/preview", json={"from_node": "a", "to_node": "b"})).status_code == 422
    motion.bad = True
    bad = await admin_client.post(f"{P}/docs/{SLUG}/motion/preview", json=BODY)
    assert bad.status_code == 502 and bad.json()["detail"]["code"] == "UD-RENDER-FAILED"


async def test_export_receipt_replays_before_renderer_and_no_relay_card(admin_client, motion, sf, producer):
    await mk(admin_client)
    payload = {**BODY, "format": "mp4", "request_id": "motion-one"}
    response = await admin_client.post(f"{P}/docs/{SLUG}/motion/export", json=payload)
    assert response.status_code == 200, response.text
    out = response.json()
    assert out["mime"] == "video/mp4" and not out["replayed"] and out["frames"] == 19
    motion.failure = True
    replay = await admin_client.post(f"{P}/docs/{SLUG}/motion/export", json=payload)
    assert replay.status_code == 200 and replay.json()["replayed"]
    assert replay.json()["artifact_id"] == out["artifact_id"] and motion.exports == 1 and motion.previews == 1
    reused = await admin_client.post(f"{P}/docs/{SLUG}/motion/export", json={**payload, "duration_ms": 1000})
    assert reused.status_code == 409 and reused.json()["detail"]["code"] == "UD-REQUEST-REUSED"
    async with sf() as session:
        assert await session.scalar(select(func.count()).select_from(Artifact)) == 1
        receipt = await session.scalar(select(UiDesignerReceipt).where(UiDesignerReceipt.op == "motion-export"))
        artifact = await session.get(Artifact, out["artifact_id"])
        assert receipt.document_id == out["document_id"] == artifact.meta["ui_designer"]["document_id"]
    content = await admin_client.get(f"/api/artifacts/{out['artifact_id']}/content")
    assert content.headers["content-type"] == "video/mp4"
    assert 'attachment' in content.headers['content-disposition'] and content.headers['x-content-type-options'] == 'nosniff'
    assert not any(topic == "relay.messages" for topic, _, _ in producer.published)


async def test_receipt_failure_rolls_back_artifact(admin_client, motion, sf, monkeypatch):
    await mk(admin_client)

    async def refuse(*args, **kwargs):
        raise ValueError("receipt rejected")

    monkeypatch.setattr(store, "_receipt_write", refuse)
    response = await admin_client.post(f"{P}/docs/{SLUG}/motion/export", json={**BODY, "request_id": "rollback"})
    assert response.status_code == 422, response.text
    async with sf() as session:
        assert await session.scalar(select(func.count()).select_from(Artifact)) == 0


async def test_source_deleted_and_slug_reused_during_encode_is_not_exported(admin_client, motion, sf):
    await mk(admin_client)

    async def replace_source():
        async with sf() as session:
            row = await store._live(session, SLUG, lock=True)
            await session.delete(row)
            await session.commit()
        async with sf() as session:
            await store.create_document(session, slug=SLUG, owner="user:other")
            await session.commit()

    motion.callback = replace_source
    response = await admin_client.post(f"{P}/docs/{SLUG}/motion/export", json={**BODY, "request_id": "identity"})
    assert response.status_code == 404, response.text
    async with sf() as session:
        assert await session.scalar(select(func.count()).select_from(Artifact)) == 0


async def test_mp4_trust_is_internal_only(sf):
    async with sf() as session:
        untrusted = await artifact_store.create(session, data=MP4, owner="user:admin", claimed_mime="video/mp4")
        assert untrusted.mime == "application/octet-stream"


def test_motion_pixel_meter_charges_all_frames_before_work_and_enforces_hourly():
    from agentplatform.api.ui_designer import _motion_charge
    from agentplatform.ui_designer.errors import UiDesignerError
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        settings=SimpleNamespace(ui_designer_pixels_per_hour_mp=1), ui_designer_pixels={})))
    caller = SimpleNamespace(agent="artist")
    _motion_charge(request, caller, 800000, cap=64000000)
    assert request.app.state.ui_designer_pixels["artist"][0][1] == .8
    with pytest.raises(UiDesignerError, match="hourly"):
        _motion_charge(request, caller, 300000, cap=64000000)
    with pytest.raises(UiDesignerError, match="lower max_edge"):
        _motion_charge(request, caller, 64000001, cap=64000000)


@pytest.mark.parametrize("phase", ["preview", "export"])
async def test_disconnected_export_cancels_renderer_and_does_not_create_artifact(admin_client, sf, monkeypatch, phase):
    import asyncio
    from fastapi import Request

    await mk(admin_client)
    cancelled = asyncio.Event()
    started = False

    async def wait_for_cancel():
        nonlocal started
        started = True
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def preview(session, **kwargs):
        if phase == "preview":
            return await wait_for_cancel()
        return manifest()

    async def export(session, **kwargs):
        return await wait_for_cancel()

    async def disconnected(self):
        return started

    monkeypatch.setattr(Request, "is_disconnected", disconnected)
    admin_client._transport.app.state.ui_designer_renderer = SimpleNamespace(
        motion_preview=preview, motion_export=export)
    response = await admin_client.post(f"{P}/docs/{SLUG}/motion/export", json={**BODY, "request_id": "cancelled"})
    assert response.status_code == 499 and cancelled.is_set()
    async with sf() as session:
        assert await session.scalar(select(func.count()).select_from(Artifact)) == 0
        assert await session.scalar(select(func.count()).select_from(UiDesignerReceipt).where(
            UiDesignerReceipt.op == "motion-export")) == 0
