"""Approved font placement is required for every persisted declaration."""
import base64
import hashlib
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, func, select

from agentplatform.db import UiDesignerFont, UiDesignerDocumentFont
from agentplatform.ui_designer import store, schema
from agentplatform.ui_designer.errors import UiDesignerError
from .test_ui_designer_api import P, SLUG, mk, edit

DATA = b'\x00\x01\x00\x00static-test-font'
SHA = hashlib.sha256(DATA).hexdigest()
REF = {"family": "Custom Sans", "weight": 400, "sha256": SHA}
META = {**REF, "license": "OFL-1.1", "license_text": "SIL OPEN FONT LICENSE Version 1.1",
        "copyright": "Test", "license_url": "https://openfontlicense.org", "font_format": "ttf",
        "glyphs": 100, "units_per_em": 1000}
DOC = {"schema": 1, "pages": [{"id": "p", "children": []}]}


@pytest.fixture
def validator(admin_client):
    state = SimpleNamespace(calls=0, offline=False, callback=None)

    async def validate(data):
        state.calls += 1
        if state.offline:
            raise AssertionError("receipt replay must not invoke font parsing")
        if state.callback:
            await state.callback()
        return {**META, "sha256": hashlib.sha256(data).hexdigest()}

    admin_client._transport.app.state.ui_designer_renderer = SimpleNamespace(validate_font=validate)
    return state


async def upload(client, version=1, request_id="font-one", data=DATA):
    return await client.post(f"{P}/docs/{SLUG}/fonts", json={"data_base64": base64.b64encode(data).decode(),
        "expected_version": version, "request_id": request_id})


async def test_upload_metadata_historical_list_and_stable_replay(admin_client, validator, sf):
    await mk(admin_client, doc=DOC)
    response = await upload(admin_client)
    assert response.status_code == 200, response.text
    out = response.json()
    assert out["version"] == 2 and out["fonts"] == [REF] and not out["replayed"]
    assert 'license_text' not in out["fonts"][0]
    listing = await admin_client.get(f"{P}/docs/{SLUG}/fonts?version=2")
    assert listing.status_code == 200 and listing.json()["fonts"] == [META]
    assert (await admin_client.get(f"{P}/docs/{SLUG}/fonts?version=1")).json()["fonts"] == []
    validator.offline = True
    replay = await upload(admin_client)
    assert replay.status_code == 200 and replay.json()["replayed"] and replay.json()["fonts"] == [REF]
    reused = await upload(admin_client, data=DATA + b'different')
    assert reused.status_code == 409 and validator.calls == 1
    async with sf() as session:
        assert await session.scalar(select(func.count()).select_from(UiDesignerFont)) == 1
        assert (await store.usage(session))["fonts"] > len(DATA)


async def test_guessed_fonts_and_forged_metadata_never_persist(admin_client, validator):
    guessed = {**DOC, "fonts": [REF]}
    response = await admin_client.post(f"{P}/docs", json={"slug": SLUG, "doc": guessed})
    assert response.status_code == 422
    await mk(admin_client, doc=DOC)
    denied = await edit(admin_client, 1, [{"op": "set_doc", "doc": guessed}])
    assert denied.status_code == 422
    assert (await upload(admin_client)).status_code == 200
    for key, value in (("family", "Forged Family"), ("weight", 700)):
        forged = {**DOC, "fonts": [{**REF, key: value}]}
        response = await edit(admin_client, 2, [{"op": "set_doc", "doc": forged}])
        assert response.status_code == 422, response.text


async def test_stale_and_concurrent_cas_never_store_upload_bytes(admin_client, validator, sf):
    await mk(admin_client, doc=DOC)

    async def edit_during_validation():
        changed = await edit(admin_client, 1, [{"op": "update", "id": "p", "set": {"title": "Human"}}])
        assert changed.status_code == 200

    validator.callback = edit_during_validation
    response = await upload(admin_client)
    assert response.status_code == 409, response.text
    async with sf() as session:
        assert await session.scalar(select(func.count()).select_from(UiDesignerFont)) == 0
    validator.offline = True
    stale = await upload(admin_client, request_id="stale")
    assert stale.status_code == 409 and validator.calls == 1


async def test_old_font_refs_restore_and_gc_retains_history(sf):
    async with sf() as session:
        initial = await store.create_document(session, slug=SLUG, owner="user:admin", doc=DOC)
        await store.add_document_font(session, initial["id"], data=DATA, metadata=META, placed_by="user:admin")
        await store.edit_document(session, SLUG, ops=[{"op": "set_doc", "doc": {**DOC, "fonts": [REF]}}],
                                  expected_version=1, author="user:admin")
        await store.edit_document(session, SLUG, ops=[{"op": "set_doc", "doc": DOC}],
                                  expected_version=2, author="user:admin")
        restored = await store.restore_version(session, SLUG, 2, expected_version=3, author="user:admin")
        assert restored["doc"]["fonts"] == [REF]
        await session.execute(delete(UiDesignerDocumentFont))
        assert (await store.gc_fonts(session))["fonts_removed"] == 0
        await store.hard_delete(session, SLUG)
        assert await store.get_font(session, SHA) is None
        await session.commit()


async def test_successful_upload_receipt_does_not_retarget_reused_slug(admin_client, validator, sf):
    await mk(admin_client, doc=DOC)
    assert (await upload(admin_client)).status_code == 200
    async with sf() as session:
        await store.hard_delete(session, SLUG)
        await store.create_document(session, slug=SLUG, owner="user:other", doc=DOC)
        await session.commit()
    validator.offline = True
    response = await upload(admin_client)
    assert response.status_code == 404 and validator.calls == 1


@pytest.mark.parametrize('refs', [[{**REF, 'family': 'inter'}], [{**REF, 'weight': True}], [REF, REF]])
def test_font_schema_rejects_shadowing_duplicate_and_bad_weight(refs):
    with pytest.raises(UiDesignerError):
        schema.validate_document({**DOC, 'fonts': refs})


async def test_duplicate_first_insert_race_reselects_blob_without_poisoning_upload(sf, monkeypatch):
    async with sf() as session:
        source = await store.create_document(session, slug='source-font', owner='user:a', doc=DOC)
        dest = await store.create_document(session, slug='dest-font', owner='user:b', doc=DOC)
        await store.add_document_font(session, source['id'], data=DATA, metadata=META, placed_by='user:a')
        await session.commit()
    async with sf() as session:
        execute = session.execute
        missed = False

        async def first_lookup_missed(statement, *args, **kwargs):
            nonlocal missed
            descriptions = getattr(statement, 'column_descriptions', [])
            if not missed and descriptions and descriptions[0].get('entity') is UiDesignerFont:
                missed = True
                return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: None))
            return await execute(statement, *args, **kwargs)

        monkeypatch.setattr(session, 'execute', first_lookup_missed)
        # The competing transaction already committed after our simulated first
        # lookup. INSERT still exercises a real primary-key conflict/savepoint.
        assert await store.add_document_font(session, dest['id'], data=DATA, metadata=META, placed_by='user:b') == SHA
        await session.commit()
    async with sf() as session:
        assert await session.scalar(select(func.count()).select_from(UiDesignerFont)) == 1
        assert await session.scalar(select(func.count()).select_from(UiDesignerDocumentFont)) == 2
