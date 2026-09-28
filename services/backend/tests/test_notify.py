"""Retired external broadcasts never publish effects, even for administrators."""
import pytest


@pytest.mark.parametrize('body', [
    {'channel': 'alerts', 'text': 'down! @everyone'},
    {'channel_id': '123456789012345678', 'text': 'status'},
    {'identity_id': 'discord-second', 'channel_id': '123456789012345678', 'text': 'status'},
])
async def test_legacy_broadcast_returns_migration_error(admin_client, producer, body):
    response = await admin_client.post('/api/notify', json=body)
    assert response.status_code == 410
    assert 'persona' in response.json()['detail']
    assert not [d for topic, _, d in producer.published if topic == 'discord.channel.post']


async def test_legacy_broadcast_still_requires_authentication(token_client, producer):
    response = await token_client.post('/api/notify', json={'channel': 'alerts', 'text': 'test'})
    assert response.status_code == 401
    assert not producer.published
