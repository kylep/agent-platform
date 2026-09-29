import asyncio
import pytest
from test_relay_tool import broker, caller


def test_self_model_requires_version_and_does_not_accept_other_profile_fields(monkeypatch):
    calls = []
    async def call(method, path, params=None, json=None, **kw):
        calls.append((method, path, json))
        return '{}'
    monkeypatch.setattr(broker, '_call', call)
    fn = broker.agent_self.__wrapped__
    assert 'expected_version' in asyncio.run(fn(action='model', model='gpt-5.6-luna'))
    assert calls == []
    asyncio.run(fn(action='model', model='gpt-5.6-luna', prompt='must not change', expected_version=4))
    assert calls == [('PATCH', '/api/agent-self', {'model': 'gpt-5.6-luna', 'expected_version': 4})]
    asyncio.run(fn(action='avatar', artifact_id=None))
    assert calls[-1] == ('PUT', '/api/agent-self/avatar', {'artifact_id': None})


def test_self_backup_call_cannot_smuggle_primary_fields(monkeypatch):
    calls = []
    async def call(method, path, params=None, json=None, **kw):
        calls.append(json)
        return '{}'
    monkeypatch.setattr(broker, '_call', call)
    fn = broker.agent_self.__wrapped__
    asyncio.run(fn(action='backup', runtime='claude', model='claude-sonnet-5-5', expected_version=2))
    assert calls == [{'backup_runtime': 'claude', 'backup_model': 'claude-sonnet-5-5', 'expected_version': 2}]
    asyncio.run(fn(action='backup', model='', expected_version=3))
    assert calls[-1] == {'backup_runtime': None, 'backup_model': '', 'expected_version': 3}
