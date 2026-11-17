"""Document ACLs remain an AND gate on global authority and content history."""
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from agentplatform.db import RelayParticipant, Team, TeamAgent, UiDesignerDocument, UiDesignerAccess
from agentplatform.ui_designer import access, store
from .test_relay_api import _human_token
from .test_artifact_store import png_bytes
from .test_ui_designer_api import P

DOC = {"schema": 1, "pages": [{"id": "p", "children": [{"id": "root", "type": "frame", "w": 100, "h": 100}]}]}


async def create(client, headers, slug='private'):
    response = await client.post(f'{P}/docs', headers=headers, json={'slug': slug, 'doc': DOC})
    assert response.status_code == 200, response.text


async def share(client, headers, *, slug='private', version=0, readers=(), editors=(), visibility='restricted', request_id=None):
    return await client.put(f'{P}/docs/{slug}/access', headers=headers, json={
        'expected_access_version': version, 'request_id': request_id or f'access-{version}',
        'visibility': visibility, 'readers': list(readers), 'editors': list(editors)})


async def test_reader_editor_owner_admin_and_spoofed_admin_name(sf, token_client):
    owner = await _human_token(sf, 'owner', 'operator')
    reader = await _human_token(sf, 'viewer', 'operator')
    editor = await _human_token(sf, 'editor', 'operator')
    spoof = await _human_token(sf, 'admin', 'operator')
    admin = await _human_token(sf, 'auditor', 'admin')
    await create(token_client, owner)
    policy = await share(token_client, owner, readers=['user:viewer'], editors=['user:editor'])
    assert policy.status_code == 200, policy.text
    for headers, expected in [(owner, 200), (reader, 200), (editor, 200), (admin, 200), (spoof, 404)]:
        assert (await token_client.get(f'{P}/docs/private', headers=headers)).status_code == expected
    op = {'ops': [{'op': 'update', 'id': 'p', 'set': {'title': 'Edit'}}], 'expected_version': 1}
    assert (await token_client.post(f'{P}/docs/private/edit', headers=reader, json=op)).status_code == 404
    assert (await token_client.post(f'{P}/docs/private/edit', headers=editor, json=op)).status_code == 200
    assert (await share(token_client, editor, version=1)).status_code == 403
    assert (await token_client.get(f'{P}/sharing-choices?slug=private', headers=owner)).status_code == 200
    assert (await token_client.get(f'{P}/sharing-choices?slug=private', headers=reader)).status_code == 403


async def test_access_cas_receipt_and_content_restore_are_independent(sf, token_client):
    owner = await _human_token(sf, 'owner', 'operator')
    await create(token_client, owner)
    first = await share(token_client, owner, request_id='first')
    assert first.json()['access_version'] == 1
    changed = await token_client.post(f'{P}/docs/private/edit', headers=owner,
        json={'ops': [{'op': 'update', 'id': 'p', 'set': {'title': 'Changed'}}], 'expected_version': 1})
    assert changed.status_code == 200
    assert (await share(token_client, owner, version=1, visibility='workspace')).json()['access_version'] == 2
    restored = await token_client.post(f'{P}/docs/private/restore', headers=owner,
        json={'version': 1, 'expected_version': 2, 'request_id': 'restore'})
    assert restored.status_code == 200, restored.text
    current = (await token_client.get(f'{P}/docs/private/access', headers=owner)).json()
    assert current['access_version'] == 2 and current['visibility'] == 'workspace'
    replay = await share(token_client, owner, request_id='first')
    assert replay.status_code == 200 and replay.json()['replayed']
    assert (await token_client.get(f'{P}/docs/private/access', headers=owner)).json()['access_version'] == 2
    conflict = await share(token_client, owner, version=1, request_id="stale")
    assert conflict.status_code == 409 and conflict.json()['detail']['code'] == 'UD-ACCESS-VERSION-CONFLICT'
    reused = await share(token_client, owner, request_id='first', visibility='workspace')
    assert reused.status_code == 409 and reused.json()['detail']['code'] == 'UD-REQUEST-REUSED'


async def test_sql_paging_filters_hidden_documents_and_cursor_lookup(sf, token_client):
    owner = await _human_token(sf, 'owner', 'operator')
    viewer = await _human_token(sf, 'viewer', 'reader')
    await create(token_client, owner, 'a')
    await create(token_client, owner, 'b')
    await create(token_client, owner)
    assert (await share(token_client, owner)).status_code == 200
    page = (await token_client.get(f'{P}/docs?limit=1', headers=viewer)).json()
    assert len(page['docs']) == 1 and page['docs'][0]['slug'] != 'private' and page['next_before']
    second = (await token_client.get(f"{P}/docs?limit=1&before={page['next_before']}", headers=viewer)).json()
    assert {page['docs'][0]['slug'], second['docs'][0]['slug']} == {'a', 'b'}
    assert (await token_client.get(f'{P}/docs?before=private', headers=viewer)).status_code == 404
    assert (await token_client.get(f'{P}/docs?q=private', headers=viewer)).json()['docs'] == []


async def test_team_membership_and_archive_refresh_without_global_grant_widening(sf, token_client):
    owner = await _human_token(sf, 'owner', 'operator')
    viewer = await _human_token(sf, 'viewer', 'reader')
    await create(token_client, owner)
    async with sf() as session:
        session.add(Team(id='team-id', slug='design-test', name='Design test', relay_channel_id='team-room'))
        session.add(RelayParticipant(channel_id='team-room', participant='user:viewer'))
        session.add(TeamAgent(team_id='team-id', agent='artist'))
        await session.commit()
    assert (await share(token_client, owner, readers=['team:team-id'])).status_code == 200
    assert (await token_client.get(f'{P}/docs/private', headers=viewer)).status_code == 200
    async with sf() as session:
        team = await session.get(Team, 'team-id')
        from agentplatform.db import utcnow
        team.archived_at = utcnow()
        await session.commit()
    assert (await token_client.get(f'{P}/docs/private', headers=viewer)).status_code == 404


async def test_raw_asset_needs_a_readable_live_placement(sf, token_client):
    owner = await _human_token(sf, 'owner', 'operator')
    viewer = await _human_token(sf, 'viewer', 'reader')
    await create(token_client, owner)
    async with sf() as session:
        placed = await store.place_asset(session, 'private', data=png_bytes(2, 2), mime='image/png',
            placed_by='user:owner', placed_from='test', width=2, height=2)
        await session.commit()
    path = f"{P}/assets/{placed['sha256']}"
    assert (await token_client.get(path, headers=viewer)).status_code == 200
    assert (await share(token_client, owner)).status_code == 200
    assert (await token_client.get(path, headers=viewer)).status_code == 404
    allowed = await token_client.get(path, headers=owner)
    assert allowed.status_code == 200 and allowed.headers['cache-control'] == 'private, no-store'


async def test_revocation_during_render_discards_response(sf, token_client):
    owner = await _human_token(sf, 'owner', 'operator')
    viewer = await _human_token(sf, 'viewer', 'reader')
    await create(token_client, owner)
    assert (await share(token_client, owner, readers=['user:viewer'])).status_code == 200

    async def resolved(session, *, doc, mode):
        assert (await share(token_client, owner, version=1)).status_code == 200
        from agentplatform.ui_designer.layout import layout_document
        return layout_document(doc, mode)

    token_client._transport.app.state.ui_designer_renderer = SimpleNamespace(resolved=resolved)
    response = await token_client.get(f'{P}/docs/private?resolve=true', headers=viewer)
    assert response.status_code == 404, response.text


async def test_revocation_during_boolean_prevents_content_commit(sf, token_client):
    owner = await _human_token(sf, 'owner', 'operator')
    editor = await _human_token(sf, 'editor', 'operator')
    doc = {'schema': 1, 'pages': [{'id': 'p', 'children': [
        {'id': 'a', 'type': 'rect', 'w': 20, 'h': 20}, {'id': 'b', 'type': 'rect', 'w': 20, 'h': 20}]}]}
    response = await token_client.post(f'{P}/docs', headers=owner, json={'slug': 'private', 'doc': doc})
    assert response.status_code == 200
    assert (await share(token_client, owner, editors=['user:editor'])).status_code == 200

    async def boolean(kind, operands):
        assert (await share(token_client, owner, version=1)).status_code == 200
        return {'geometry': {'x': 0, 'y': 0, 'w': 20, 'h': 20, 'd': 'M0 0H20V20H0Z', 'empty': False}, 'warnings': []}

    token_client._transport.app.state.ui_designer_renderer = SimpleNamespace(boolean_geometry=boolean)
    response = await token_client.post(f'{P}/docs/private/edit', headers=editor, json={
        'ops': [{'op': 'boolean', 'kind': 'union', 'ids': ['a', 'b']}], 'expected_version': 1, 'request_id': 'revoked'})
    assert response.status_code == 404, response.text
    async with sf() as session:
        assert (await store.get_document(session, 'private'))['version'] == 1


async def test_sse_filter_denies_private_content_and_sends_only_safe_revocation(sf, token_client):
    from agentplatform.api.ui_designer import _visible_design_event
    from agentplatform.api.relay import Caller
    owner = await _human_token(sf, 'owner', 'operator')
    await create(token_client, owner)
    assert (await share(token_client, owner)).status_code == 200
    async with sf() as session:
        identifier = (await store.get_document(session, 'private'))['id']
    request = SimpleNamespace(state=SimpleNamespace(resolved_role='operator'),
        app=SimpleNamespace(state=SimpleNamespace(session_factory=sf)))
    viewer = Caller('user:viewer', None, 'viewer')
    secret = {'type': 'version', 'slug': 'private', 'document_id': identifier, 'label': 'SECRET'}
    assert await _visible_design_event(request, viewer, secret) is None
    assert await _visible_design_event(request, Caller('user:owner', None, 'owner'), secret) == secret
    signal = {'type': 'access', 'slug': 'private', 'document_id': identifier, 'access_version': 2,
              'audience': ['user:viewer'], 'workspace': False, 'readers': ['user:owner']}
    public = await _visible_design_event(request, viewer, signal)
    assert public == {key: signal[key] for key in ('type', 'slug', 'document_id', 'access_version')}
    assert await _visible_design_event(request, Caller('user:stranger', None, 'stranger'), signal) is None


async def test_global_role_still_limits_a_team_editor(sf, token_client):
    owner = await _human_token(sf, 'owner', 'operator')
    viewer = await _human_token(sf, 'viewer', 'reader')
    await create(token_client, owner)
    assert (await share(token_client, owner, editors=['user:viewer'])).status_code == 200
    caps = (await token_client.get(f'{P}/docs/private/access', headers=viewer)).json()
    assert caps['can_read'] and not caps['can_write'] and not caps['can_manage_sharing']
    response = await token_client.post(f'{P}/docs/private/edit', headers=viewer,
        json={'ops': [{'op': 'update', 'id': 'p', 'set': {'title': 'x'}}], 'expected_version': 1})
    assert response.status_code == 403


async def test_sharing_choices_include_api_key_only_humans(sf, token_client):
    owner = await _human_token(sf, 'owner', 'operator')
    await _human_token(sf, 'key-only-editor', 'operator')
    await create(token_client, owner)
    choices = await token_client.get(f'{P}/sharing-choices?slug=private', headers=owner)
    assert choices.status_code == 200
    assert 'user:key-only-editor' in {row['principal'] for row in choices.json()['principals']}


async def test_subject_without_caller_uses_the_platform_participant_identity(sf, token_client):
    from starlette.requests import Request
    headers = await _human_token(sf, 'user:odd', 'operator')
    request = Request({'type': 'http', 'method': 'GET', 'path': '/api/ui-designer/docs',
        'headers': [(key.lower().encode(), value.encode()) for key, value in headers.items()],
        'app': token_client._transport.app, 'state': {}})
    who = await access.subject(request)
    assert who.participant == 'user:user:odd' and not who.is_admin


async def test_sharing_receipt_cannot_retarget_a_reused_slug(sf, token_client):
    owner = await _human_token(sf, 'owner', 'operator')
    await create(token_client, owner)
    assert (await share(token_client, owner, request_id='original')).status_code == 200
    async with sf() as session:
        await store.hard_delete(session, 'private')
        await session.commit()
    await create(token_client, owner)
    replay = await share(token_client, owner, request_id='original')
    assert replay.status_code == 404
    current = (await token_client.get(f'{P}/docs/private/access', headers=owner)).json()
    assert current['visibility'] == 'workspace' and current['access_version'] == 0


async def test_literal_prefixed_username_cannot_alias_a_canonical_owner(sf, token_client):
    owner = await _human_token(sf, 'odd', 'operator')
    other = await _human_token(sf, 'user:odd', 'operator')
    await create(token_client, owner)
    assert (await share(token_client, owner)).status_code == 200
    assert (await token_client.get(f'{P}/docs/private', headers=other)).status_code == 404
    assert (await token_client.get(f'{P}/docs', headers=other)).json()['docs'] == []
