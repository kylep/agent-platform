"""Browser presence is UUID-bound ephemeral state, never a content edit."""
from datetime import timedelta
from types import SimpleNamespace

import pytest

from agentplatform.db import UiDesignerPresence, utcnow
from agentplatform.api import ui_designer as api
from agentplatform.events import TOPIC_UI_DESIGNER_EVENTS
from .test_ui_designer_api import P, SLUG, mk
from .test_relay_api import _human_token
from .test_relay_sse import sse

TAB = 'a' * 32
DOC = {'schema': 1, 'pages': [{'id': 'p', 'children': [{'id': 'root', 'type': 'frame', 'w': 100, 'h': 100}]}]}


@pytest.fixture
async def document(admin_client, monkeypatch):
    from agentplatform.ui_designer import presence
    clock = [utcnow()]

    async def now(session, explicit=None):
        return explicit or clock[0]

    monkeypatch.setattr(presence, '_now', now)
    await mk(admin_client, doc=DOC)
    identifier = (await admin_client.get(f'{P}/docs/{SLUG}')).json()['id']
    return identifier, clock


def payload(identifier, **changes):
    return {'document_id': identifier, 'version': 1, 'page_id': 'p', 'state': 'viewing',
            'node_id': 'root', 'cursor': {'x': 12, 'y': 34}, **changes}


async def test_presence_identity_uuid_bounds_throttle_and_no_content_version(admin_client, document):
    identifier, clock = document
    path = f'{P}/docs/{SLUG}/presence/{TAB}'
    first = await admin_client.put(path, json=payload(identifier))
    assert first.status_code == 200, first.text
    own = first.json()['sessions'][0]
    assert own['participant'] == 'user:admin' and own['client_session'] == TAB and own['sequence'] == 1
    assert own['cursor'] == {'x': 12, 'y': 34}
    assert (await admin_client.get(f'{P}/docs/{SLUG}')).json()['version'] == 1
    fast = await admin_client.put(f'{P}/docs/{SLUG}/presence/' + 'b'*32, json=payload(identifier))
    assert fast.status_code == 429 and 'retry-after' in fast.headers
    assert (await admin_client.put(path, json={**payload(identifier), 'participant': 'user:other'})).status_code == 422
    assert (await admin_client.put(path, json={**payload(identifier), 'padding': 'x'*3000})).status_code == 413
    clock[0] += timedelta(seconds=1)
    assert (await admin_client.put(path, json=payload('0'*32))).status_code == 409
    second = await admin_client.put(path, json=payload(identifier, state='editing'))
    assert second.status_code == 200 and second.json()['sessions'][0]['sequence'] == 2
    clock[0] += timedelta(seconds=1)
    left = await admin_client.delete(path + '?document_id=' + identifier)
    assert left.status_code == 204
    assert (await admin_client.get(f'{P}/docs/{SLUG}/presence')).json()['sessions'] == []


async def test_reader_can_view_but_cannot_advertise_editing(sf, token_client, admin_client, document):
    identifier, _ = document
    headers = await _human_token(sf, 'viewer', 'reader')
    path = f'{P}/docs/{SLUG}/presence/{TAB}'
    assert (await token_client.put(path, headers=headers, json=payload(identifier, state='editing'))).status_code == 403
    joined = await token_client.put(path, headers=headers, json=payload(identifier))
    assert joined.status_code == 200 and joined.json()['sessions'][0]['participant'] == 'user:viewer'
    # Leaving another principal's identical tab ID touches only one's own key.
    response = await admin_client.delete(path + '?document_id=' + identifier)
    assert response.status_code == 204
    assert len((await admin_client.get(f'{P}/docs/{SLUG}/presence')).json()['sessions']) == 1


async def test_presence_feed_has_no_global_cursor_fanout():
    feed = api.ui_designer_feed()
    global_queue = feed.subscribe(api.STREAM)
    selected = feed.subscribe('presence:' + 'a'*32)
    other = feed.subscribe('presence:' + 'b'*32)
    message = SimpleNamespace(topic=TOPIC_UI_DESIGNER_EVENTS)
    data = {'type': 'presence', 'slug': SLUG, 'document_id': 'a'*32,
            'participant': 'user:a', 'client_session': TAB, 'sequence': 1}
    await feed._on_message(message, data)
    assert global_queue.empty() and other.empty() and (await selected.get())[1] == data
    changed = {'type': 'access', 'slug': SLUG, 'document_id': 'a'*32, 'access_version': 1}
    await feed._on_message(message, changed)
    assert not global_queue.empty() and (await selected.get())[1] == changed


async def test_scoped_stream_sends_full_rosters_and_expires_without_kafka(admin_client, document, sf, monkeypatch):
    from agentplatform.api import relay as relay_api
    identifier, clock = document
    assert (await admin_client.put(f'{P}/docs/{SLUG}/presence/{TAB}', json=payload(identifier))).status_code == 200
    monkeypatch.setattr(relay_api, 'HEARTBEAT_SECONDS', .05)
    feed = admin_client._transport.app.state.ui_designer_feed
    async with sse(admin_client, f'{P}/docs/{SLUG}/presence/events') as (response, reader):
        assert response['status'] == 200
        event, roster, _ = await reader.event()
        assert event == 'presence' and len(roster['sessions']) == 1 and roster['document_id'] == identifier
        clock[0] += timedelta(seconds=60)
        event, roster, _ = await reader.event()
        assert event == 'presence' and roster['sessions'] == []
    assert feed.subscriber_count('presence:' + identifier) == 0


async def test_scoped_stream_closes_on_viewer_acl_revocation(sf, token_client, admin_client, document, monkeypatch):
    from agentplatform.api import relay as relay_api
    identifier, _ = document
    headers = await _human_token(sf, 'viewer', 'reader')
    monkeypatch.setattr(relay_api, 'HEARTBEAT_SECONDS', .05)
    async with sse(token_client, f'{P}/docs/{SLUG}/presence/events', headers=headers) as (response, reader):
        assert response['status'] == 200
        assert (await reader.event())[0] == 'presence'
        changed = await admin_client.put(f'{P}/docs/{SLUG}/access', json={
            'visibility': 'restricted', 'readers': [], 'editors': [], 'expected_access_version': 0,
            'request_id': 'presence-revoke'})
        assert changed.status_code == 200
        with pytest.raises(Exception):
            await reader.event(.2)


async def test_runless_agent_cannot_advertise_editing(sf, token_client, document, seed_agent, agent_store):
    from .test_ui_designer_api_auth import _agent
    identifier, _ = document
    headers = await _agent(sf, seed_agent, agent_store, 'presence-agent', run=False)
    response = await token_client.put(f'{P}/docs/{SLUG}/presence/{TAB}', headers=headers,
                                     json=payload(identifier, state='editing'))
    assert response.status_code == 403


async def test_document_named_presence_keeps_normal_edit_body_limit(admin_client):
    await mk(admin_client, slug='presence', doc=DOC)
    response = await admin_client.post(f'{P}/docs/presence/edit', json={'expected_version': 1,
        'ops': [{'op': 'insert', 'parent': 'p', 'node': {'id': 'long', 'type': 'text', 'text': 'x'*3000}}]})
    assert response.status_code == 200, response.text
