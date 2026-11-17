"""Completed imports announce once even if the event broker fails."""
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from agentplatform.db import RelayMessage, UiDesignerAccess, UiDesignerImport
from agentplatform.ui_designer import store
from agentplatform.ui_designer.import_notifications import notify_import


async def test_notification_retry_keeps_one_card(sf):
    async with sf() as s:
        document = await store.create_document(s, slug='imported-design', owner='user:kyle')
        job = UiDesignerImport(slug='imported-design', file_key='filekey', state='done',
                               initiated_by='user:kyle', report={
                                   'document_id': document['id'], 'pages_imported': 2, 'notification_pending': True})
        s.add(job)
        await s.commit()
        job_id = job.id
    producer = AsyncMock()
    producer.publish.side_effect = RuntimeError('broker unavailable')
    with pytest.raises(RuntimeError):
        await notify_import(sf, producer, job_id)
    async with sf() as s:
        job = await s.get(UiDesignerImport, job_id)
        message_id = job.report['notification_message_id']
        assert job.report['notification_pending']
    producer.publish.side_effect = None
    await notify_import(sf, producer, job_id)
    calls = producer.publish.call_count
    await notify_import(sf, producer, job_id)
    assert producer.publish.call_count == calls
    async with sf() as s:
        job = await s.get(UiDesignerImport, job_id)
        assert not job.report['notification_pending']
        assert job.report['notification_message_id'] == message_id
        cards = (await s.execute(select(RelayMessage).where(
            RelayMessage.id == message_id))).scalars().all()
        assert len(cards) == 1 and cards[0].kind == 'event' and cards[0].mentions == []
        assert cards[0].card['import_id'] == job_id


async def test_import_notification_uses_uuid_and_never_posts_a_private_card(sf):
    async with sf() as session:
        document = await store.create_document(session, slug='private-import', owner='user:kyle')
        session.add(UiDesignerAccess(document_id=document['id'], visibility='restricted', access_version=1))
        job = UiDesignerImport(slug='private-import', file_key='filekey', state='done',
            initiated_by='user:kyle', report={'document_id': document['id'], 'notification_pending': True})
        session.add(job)
        await session.commit()
        identifier = job.id
    producer = AsyncMock()
    await notify_import(sf, producer, identifier)
    assert producer.publish.call_count == 1
    call = producer.publish.call_args
    assert call.args[0] == 'ui_designer.events'
    assert call.args[2]['document_id'] == document['id']
    async with sf() as session:
        job = await session.get(UiDesignerImport, identifier)
        assert not job.report['notification_pending']
        assert 'notification_message_id' not in job.report


async def test_legacy_import_notification_without_source_uuid_is_not_rebound(sf):
    async with sf() as session:
        await store.create_document(session, slug='reused-import', owner='user:other')
        job = UiDesignerImport(slug='reused-import', file_key='filekey', state='done',
            initiated_by='user:kyle', report={'notification_pending': True})
        session.add(job)
        await session.commit()
        identifier = job.id
    producer = AsyncMock()
    await notify_import(sf, producer, identifier)
    producer.publish.assert_not_called()


def test_public_progress_is_bounded_and_hides_worker_state():
    import json
    from types import SimpleNamespace
    from agentplatform.ui_designer.figma_import_job import job_view
    job = SimpleNamespace(id='x', slug='s', file_key='key', state='done', pages={},
        error=None, created_at=None, updated_at=None, report={
            'options': {'title':'private worker option'}, 'notification_pending':True,
            'pages': {str(n): {'items':[{'what':'x'*500} for _ in range(100)],
                              'converted':{'TEXT':100}} for n in range(50)},
            'references':[{'artifact_id':str(n),'node_id':str(n)} for n in range(1000)]})
    view = job_view(job)
    assert len(json.dumps(view).encode()) < 32 * 1024
    assert 'options' not in view['report'] and 'notification_pending' not in view['report']
    assert view['report']['references_omitted'] == 900
    assert job.report['pages']['0']['items']  # no mutation of durable record


async def test_import_create_enforces_per_version_asset_budget(sf, monkeypatch):
    from agentplatform.ui_designer import store
    from agentplatform.ui_designer.errors import UiDesignerError
    monkeypatch.setattr(store, 'MAX_ASSET_BYTES_PER_VERSION', 3)
    async with sf() as s:
        sha = await store.add_asset(s, data=b'1234', mime='image/png')
        with pytest.raises(UiDesignerError):
            await store.create_document(s, slug='too-large', owner='user:admin',
                                        imported_assets={sha})
