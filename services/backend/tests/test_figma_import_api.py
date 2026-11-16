"""Import administration and dispatcher-only executor bridge."""
from unittest.mock import AsyncMock

import pytest

from .test_relay_api import _human_token

P = '/api/ui-designer/imports'
BODY = {'slug':'figma-test','file_key':'fixtureKey','pages':['1:2']}


async def test_admin_key_can_start_read_and_duplicate_is_refused(sf, token_client):
    headers = await _human_token(sf, 'designer-admin', 'admin')
    result = await token_client.post(P, json=BODY, headers=headers)
    assert result.status_code == 202, result.text
    job = result.json()
    assert job['state'] == 'queued' and job['slug'] == BODY['slug']
    read = await token_client.get(P + '/' + job['id'], headers=headers)
    assert read.status_code == 200 and read.json()['id'] == job['id']
    assert (await token_client.post(P, json=BODY, headers=headers)).status_code == 409


@pytest.mark.parametrize('role', ['reader','annotator','approver','dev','relay'])
async def test_non_admin_keys_cannot_start_or_read(sf, token_client, role):
    headers = await _human_token(sf, 'non-admin-' + role, role)
    assert (await token_client.post(P, json=BODY, headers=headers)).status_code == 403
    assert (await token_client.get(P + '/unknown', headers=headers)).status_code == 403


async def test_limits_and_unknown_fields(admin_client):
    for patch in ({'pages':['x'*129]}, {'pages':['1:2']*51}, {'node_ids':['a']*9},
                  {'file_key':'https://evil.example'}, {'token':'do-not-accept'}):
        assert (await admin_client.post(P, json={**BODY, **patch})).status_code == 422


async def test_dispatcher_identity_is_namespace_and_audience_bound(admin_client):
    path = '/api/internal/ui-designer/figma-fetch'
    app = admin_client._transport.app
    assert (await admin_client.post(path, json={'mode':'file_meta'})).status_code == 403
    app.state.sa_validator = AsyncMock(return_value='system:serviceaccount:other:ap-dispatcher')
    headers = {'Authorization':'Bearer test.workload.token'}
    assert (await admin_client.post(path, json={'mode':'file_meta'}, headers=headers)).status_code == 403
    app.state.sa_validator.return_value = 'system:serviceaccount:agent-platform:ap-dispatcher'
    # A valid identity reaches validation, but cannot supply executor-controlled arguments.
    assert (await admin_client.post(path, json={'mode':'file_meta','caller':{}}, headers=headers)).status_code == 422
    app.state.sa_validator.assert_awaited_with('test.workload.token')
