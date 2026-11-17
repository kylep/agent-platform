"""Atomic comment notifications and UUID-bound retries; no real launches."""
import pytest
from sqlalchemy import select, func
from agentplatform.db import UiDesignerComment, UiDesignerCommentWake, UiDesignerReceipt, Run, RunState
from agentplatform.ui_designer import store
from agentplatform.ui_designer.errors import UiDesignerError
from .test_ui_designer_api import mk, P, SLUG
from .test_ui_designer_store import make
from .test_dispatcher import disp, make_run  # noqa: F401


async def test_unknown_notification_replay_and_uuid_pin(admin_client, sf):
    await mk(admin_client)
    url = f'{P}/docs/{SLUG}/comments'
    assert (await admin_client.post(url, json={'body': '@ui-no-such-agent-safe review'})).status_code == 422
    payload = {'body': '@ui-no-such-agent-safe review', 'request_id': 'mention-one'}
    first = await admin_client.post(url, json=payload)
    assert first.status_code == 200, first.text
    result = first.json()
    assert result['notifications'][0]['status'] == 'skipped'
    assert result['comment']['notifications'] == result['notifications']
    again = (await admin_client.post(url, json=payload)).json()
    assert again['replayed'] and again['comment'] is None
    assert again['notifications'] == result['notifications']
    listing = (await admin_client.get(url, params={'expected_document_id': result['document_id']})).json()
    assert listing['document_id'] == result['document_id']
    assert listing['comments'][0]['notifications'] == result['notifications']
    assert (await admin_client.get(url, params={'expected_document_id': '0'*32})).status_code == 404
    assert (await admin_client.post(url, json={**payload, 'expected_document_id': '0'*32})).status_code == 404
    async with sf() as s:
        assert await s.scalar(select(func.count()).select_from(UiDesignerCommentWake)) == 1


async def test_target_limit_rolls_back_comment_and_receipt(admin_client, sf):
    await mk(admin_client)
    payload = {'body': ' '.join('@unknown'+str(i) for i in range(9)), 'request_id': 'too-many'}
    response = await admin_client.post(f'{P}/docs/{SLUG}/comments', json=payload)
    assert response.status_code == 422, response.text
    async with sf() as s:
        assert await s.scalar(select(func.count()).select_from(UiDesignerComment)) == 0
        assert await s.scalar(select(func.count()).select_from(UiDesignerCommentWake)) == 0
        assert await s.scalar(select(func.count()).select_from(UiDesignerReceipt).where(
            UiDesignerReceipt.request_id == 'too-many')) == 0


async def test_legacy_receipt_binds_original_uuid_and_survives_purge(sf):
    await make(sf)
    args = dict(body='legacy comment', author='kyle', scope='human:kyle', request_id='legacy')
    async with sf() as s:
        first = await store.add_comment(s, 'hud', **args)
        receipt = (await s.execute(select(UiDesignerReceipt))).scalar_one()
        receipt.document_id = None
        receipt.response = {k:v for k,v in receipt.response.items() if k != 'document_id'}
        await s.commit()
    async with sf() as s:
        replay = await store.add_comment(s, 'hud', **args)
        assert replay['document_id'] == first['document_id']
        await s.commit()
    async with sf() as s:
        await store.hard_delete(s, 'hud')
        await s.commit()
    await make(sf)
    async with sf() as s:
        with pytest.raises(UiDesignerError) as exc:
            await store.add_comment(s, 'hud', **args)
        assert exc.value.code == "UD-NOT-FOUND"


async def test_dispatch_comment_without_outbox_never_launches(sf, disp):
    rid = await make_run(sf)
    async with sf() as s:
        run = await s.get(Run, rid)
        run.trigger = 'ui_comment'
        await s.commit()
    await disp.handle({'type':'run', 'run_id':rid})
    assert disp.launcher.launched == []
    async with sf() as s:
        assert (await s.get(Run, rid)).state == RunState.REJECTED


async def test_private_notifications_refresh_on_list_resolve(admin_client, sf):
    await mk(admin_client)
    shared = await admin_client.put(f'{P}/docs/{SLUG}/access', json={
        'expected_access_version':0, 'request_id':'private-access', 'visibility':'restricted'})
    assert shared.status_code == 200, shared.text
    response = await admin_client.post(f'{P}/docs/{SLUG}/comments', json={
        'body':'@pai please review', 'request_id':'private-mention'})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['notifications'] == [{'agent':'pai','status':'skipped',
        'reason':'Private documents cannot start comment runs yet','run_id':None}]
    async with sf() as s:
        wake = (await s.execute(select(UiDesignerCommentWake))).scalar_one()
        wake.reason = 'Current status changed'
        await s.commit()
    listed = (await admin_client.get(f'{P}/docs/{SLUG}/comments')).json()
    assert listed['comments'][0]['notifications'][0]['reason'] == 'Current status changed'
    resolved = (await admin_client.post(f'{P}/docs/{SLUG}/comments/{result["id"]}/resolve')).json()
    assert resolved['notifications'][0]['reason'] == 'Current status changed'


async def test_dispatch_guard_transaction_survives_through_launch(sf, disp, monkeypatch):
    from agentplatform.ui_designer import comment_wakes
    from sqlalchemy import text
    checked = {}
    async def guard(session, run, settings, agents):
        await session.execute(text('SELECT 1'))
        checked['session'] = session
        return None
    async def launch(run, manifest):
        assert checked['session'].in_transaction()
        disp.launcher.launched.append(run.id)
    monkeypatch.setattr(comment_wakes, 'guard_run', guard)
    monkeypatch.setattr(disp.launcher, 'launch', launch)
    rid = await make_run(sf)
    async with sf() as s:
        run = await s.get(Run, rid)
        run.trigger = 'ui_comment'
        await s.commit()
    await disp.handle({'type':'run', 'run_id':rid})
    assert disp.launcher.launched == [rid]
