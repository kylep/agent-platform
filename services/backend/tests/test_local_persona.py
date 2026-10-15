"""Exercise direct REST boundaries, durable projection and fencing, not prompts."""
from datetime import timedelta
import uuid

import pytest
from sqlalchemy import select, func
from agentplatform.db import (AgentDef, ApiKey, Conversation, LocalPersonaCredential,
    LocalPersonaEnrollment, LocalPersonaEvent, LocalPersonaSession, LocalPersonaTurn,
    RelayMessage, RelayParticipant, Run, RunState, utcnow)
from agentplatform.apikeys import hash_token
from agentplatform import local_persona as lp


@pytest.fixture
async def local(admin_client, token_client, seed_agent):
    token_client._transport.app.state.settings.local_persona_enabled = True
    await seed_agent('alice', agent_type='persona', platform_tools=[
        'mcp__platform__memory', 'mcp__platform__relay', 'mcp__platform__runs_read',
        'mcp__platform__agent_self', 'mcp__platform__wiki'])
    async def create(harness='claude', name='alice'):
        e = await admin_client.post('/api/local-persona/enrollments', json={
            'agent': name, 'device_id': 'test-laptop', 'device_label': 'Laptop'})
        assert e.status_code == 200, e.text
        e = e.json()
        result = await token_client.post('/api/local-persona/sessions',
            headers={'Authorization': 'Bearer ' + e['token']}, json={
                'harness': harness, 'launch_nonce': uuid.uuid4().hex})
        assert result.status_code == 200, result.text
        state = result.json()
        state['enrollment'] = e
        state['headers'] = {'Authorization': 'Bearer ' + state['token']}
        state['path'] = '/api/local-persona/sessions/' + state['session_id']
        return state
    return create


def event(seq, kind='user_message', payload=None, turn='t1'):
    return {'schema_version': 1, 'sequence': seq, 'event_id': f'record:{seq}',
            'local_turn_id': turn, 'kind': kind, 'source': 'main',
            'payload': {'text': '@alice @all sample'} if payload is None else payload}


async def test_binding_cookie_confusion_and_future_route(local, admin_client, token_client):
    state = await local()
    result = await admin_client.get('/api/whoami', headers=state['headers'])
    assert result.json()['agent'] == 'alice'
    assert result.json()['role'] == 'annotator'
    app = token_client._transport.app
    @app.get('/api/future-unprotected-management')
    async def future():
        return {'secret': 'must stay unreachable'}
    for path in ('/api/future-unprotected-management', '/api/api-keys', '/api/memories',
                 '/api/secrets', '/api/setup-state', '/api/runs/abc/codex-auth'):
        result = await admin_client.get(path, headers=state['headers'])
        assert result.status_code == 403, (path, result.text)
    assert not any(p.startswith('/api/local-persona') for p in app.openapi()['paths'])


async def test_ingest_atomic_replay_and_no_dispatch(local, token_client, sf, producer, agent_store):
    state = await local()
    path, headers = state['path'], state['headers']
    batch = [event(1), event(2, 'assistant_message', {'text': '@alice final', 'final': True}),
             event(3, 'turn_end', {'outcome': 'complete', 'usage': {'input_tokens': 7}})]
    for _ in range(2):
        response = await token_client.post(path + '/events', headers=headers, json={'events': batch})
        assert response.status_code == 200, response.text
        assert response.json()['projected_sequence'] == 3
    conflict = [event(4), {**event(5), 'payload': {'author': 'admin'}}]
    assert (await token_client.post(path + '/events', headers=headers, json={'events': conflict})).status_code == 422
    assert (await token_client.post(path + '/events', headers=headers,
        json={'events': [{**event(1), 'payload': {'text': 'rewrite'}}]})).status_code == 409
    from agentplatform.relay_router import RelayRouter
    from agentplatform.recorder import Recorder
    async with sf() as s:
        row = await s.get(LocalPersonaSession, state['session_id'])
        assert row.accepted_sequence == 3
        messages = list((await s.execute(select(RelayMessage).where(
            RelayMessage.channel_id == row.conversation_id))).scalars())
        assert len(messages) == 2
        assert (await s.scalar(select(func.count()).select_from(LocalPersonaEvent))) == 3
        run = await s.get(Run, row.run_id)
        assert run.prompt == '' and run.result is None and run.execution_location == 'laptop'
        run.state, run.finished_at = RunState.INTERRUPTED, utcnow() - timedelta(minutes=3)
        await s.commit()
    recorder = Recorder(sf, producer=producer, agent_store=agent_store)
    assert await recorder.reconcile_replies(0) == 0
    from agentplatform.config import Settings
    from agentplatform.relay_store import relay_message_payload
    router = RelayRouter(Settings(), sf, producer, agent_store)
    for msg in messages:
        await router.handle({'id': msg.id, 'channel_id': msg.channel_id, 'kind': msg.kind, 'body': msg.body})
    assert not any(x[0] in ('run.requests', 'conversation.outbound') for x in producer.published)


async def test_resume_fences_hooks_tools_and_old_run(local, token_client, sf):
    state = await local('codex')
    path, headers = state['path'], state['headers']
    assert (await token_client.post(path + '/bind', headers=headers,
        json={'native_session_id': 'native-1'})).status_code == 200
    eheaders = {'Authorization': 'Bearer ' + state['enrollment']['token']}
    resumed = await token_client.post(path + '/resume', headers=eheaders,
        json={'native_session_id': 'native-1'})
    assert resumed.status_code == 200, resumed.text
    new = resumed.json()
    assert new['writer_epoch'] == 2
    assert (await token_client.get('/api/whoami', headers=headers)).status_code == 401
    assert (await token_client.post(path + '/events', headers=headers, json={'events': [event(1)]})).status_code == 401
    assert (await token_client.post(path + '/renew', headers={'Authorization': 'Bearer ' + state['renewal']})).status_code == 401
    async with sf() as s:
        runs = list((await s.execute(select(Run).where(Run.local_persona_session_id == state['session_id']))).scalars())
        assert sorted(r.state for r in runs) == ['interrupted', 'running']
        assert not await lp.run_authorized(s, next(r for r in runs if r.state == 'interrupted'), 'memory')


async def test_grant_intersection_generation_and_access_loss(local, token_client, sf):
    state = await local()
    headers, path = state['headers'], state['path']
    async with sf() as s:
        agent = await s.get(AgentDef, 'alice')
        agent.platform_tools = ['mcp__platform__memory', 'mcp__platform__relay', 'mcp__platform__image_gen']
        await s.commit()
    who = (await token_client.get('/api/whoami', headers=headers)).json()
    assert who['role'] == 'relay'
    assert 'mcp__platform__image_gen' not in who['tools']
    assert (await token_client.get('/api/runs', headers=headers)).status_code == 403
    async with sf() as s:
        agent = await s.get(AgentDef, 'alice')
        agent.authorization_generation += 1
        s.add(Conversation(id='a'*32, agent='alice', kind='group', connector='web'))
        s.add(RelayParticipant(channel_id='a'*32, participant='agent:alice'))
        await s.commit()
    assert (await token_client.get('/api/whoami', headers=headers)).status_code == 409
    assert (await token_client.get(path + '/bootstrap', headers=headers)).status_code == 200
    async with sf() as s:
        run = await s.get(Run, (await s.get(LocalPersonaSession, state['session_id'])).run_id)
        assert run.authorization_generation == 1
        member = await s.get(RelayParticipant, ('a'*32, 'agent:alice'))
        await s.delete(member)
        await s.commit()
    assert (await token_client.get('/api/whoami', headers=headers)).status_code == 403
    assert (await token_client.post(path + '/events', headers=headers, json={'events': [event(1)]})).status_code == 200
    assert (await token_client.get(path + '/bootstrap', headers=headers)).status_code == 403


async def test_native_clear_suspend_close_recovery_and_revoke(local, token_client, admin_client, sf):
    state = await local()
    path, headers = state['path'], state['headers']
    for native, code in [('native-1', 200), ('native-2', 409)]:
        assert (await token_client.post(path + '/bind', headers=headers,
            json={'native_session_id': native})).status_code == code
    assert (await token_client.get('/api/whoami', headers=headers)).status_code == 409
    assert (await token_client.post(path + '/events', headers=headers, json={'events': [event(1)]})).status_code == 200
    await token_client.post(path + '/close', headers=headers)
    await token_client.post(path + '/finalize', headers=headers)
    assert (await token_client.post(path + '/events', headers=headers, json={'events': [event(2)]})).status_code == 409
    recovery = await token_client.post(path + '/recover', headers={
        'Authorization': 'Bearer ' + state['enrollment']['token']}, json={})
    assert recovery.status_code == 200
    recovered = {'Authorization': 'Bearer ' + recovery.json()['token']}
    assert (await token_client.get('/api/whoami', headers=recovered)).status_code == 409
    assert (await token_client.post(path + '/events', headers=recovered, json={'events': [event(2)]})).status_code == 200
    await admin_client.delete(path)
    assert (await token_client.post(path + '/events', headers=recovered, json={'events': [event(3)]})).status_code == 401


async def test_cluster_cannot_read_local_history_or_post(local, token_client, admin_client, sf):
    state = await local()
    await token_client.post(state['path'] + '/events', headers=state['headers'], json={'events': [event(1)]})
    token = 'ap_cluster_test_token'
    async with sf() as s:
        s.add(ApiKey(name='cluster', role='annotator', agent='alice', key_hash=hash_token(token), prefix='ap_cluster'))
        row = await s.get(LocalPersonaSession, state['session_id'])
        run_id = row.run_id
        await s.commit()
    h = {'Authorization': 'Bearer ' + token}
    room = '/api/relay/channels/' + state['conversation_id']
    assert (await token_client.get('/api/runs/' + run_id, headers=h)).status_code == 403
    assert (await token_client.get(room + '/messages', headers=h)).status_code == 403
    search = await token_client.get('/api/relay/search?q=sample', headers=h)
    assert search.status_code == 200 and not search.json()
    assert (await admin_client.post(room + '/messages', json={'body': '@alice wake up'})).status_code == 403
    assert (await admin_client.patch(room, json={'open': True})).status_code == 403
    assert (await admin_client.get(room + '/messages')).status_code == 200


@pytest.mark.parametrize('transition', ['drain', 'revoke', 'grant_removed', 'takeover'])
async def test_delegated_executor_follows_session_fence(local, token_client, admin_client,
        seed_agent, sf, tmp_path, transition):
    from agentplatform.toolregistry import ToolRegistry
    await seed_agent('alice', agent_type='persona', platform_tools=[
        'mcp__platform__memory', 'mcp__platform__ledger'])
    app = token_client._transport.app
    root = tmp_path/'tools'
    (root/'ledger').mkdir(parents=True)
    (root/'ledger/tool.yaml').write_text('''name: ledger
description: A synthetic ledger used to exercise delegated credentials
params: {type: object, properties: {}}
app_access:
  roles: [results]
  verbs: [read, create]
''')
    (root/'ledger/run.py').write_text('print("test")')
    app.state.tool_registry = ToolRegistry(root)
    async def validate(token):
        name = {'broker.sa.token':app.state.settings.broker_service_account,
                'executor.sa.token':app.state.settings.tool_executor_service_account}.get(token)
        return 'system:serviceaccount:ap:'+name if name else None
    app.state.sa_validator = validate
    state = await local()
    minted = await token_client.post('/api/tool-calls', headers={'Authorization':'Bearer broker.sa.token'},
        json={'caller':{'authorization':'Bearer '+state['token']}, 'tool':'ledger', 'action':'save'})
    assert minted.status_code == 200, minted.text
    credential = minted.json()
    headers = {'Authorization':'Bearer executor.sa.token', 'X-AP-Tool-Call':credential['credential'],
               'X-AP-Tool-Call-Id':credential['call_id']}
    assert (await token_client.get('/api/whoami', headers=headers)).status_code == 200
    if transition == 'drain':
        await token_client.post(state['path']+'/close', headers=state['headers'])
    elif transition == 'revoke':
        await admin_client.delete('/api/local-persona/enrollments/'+state['enrollment']['id'])
    elif transition == 'grant_removed':
        async with sf() as s:
            agent = await s.get(AgentDef, 'alice')
            agent.platform_tools = ['mcp__platform__memory']
            await s.commit()
    else:
        await token_client.post(state['path']+'/bind', headers=state['headers'], json={'native_session_id':'bound'})
        resumed = await token_client.post(state['path']+'/resume', headers={
            'Authorization':'Bearer '+state['enrollment']['token']}, json={'native_session_id':'bound'})
        assert resumed.status_code == 200
    assert (await token_client.get('/api/whoami', headers=headers)).status_code == 401


async def test_own_session_history_is_pinned_and_upload_only_has_no_reads(local, token_client):
    first, second = await local(), await local()
    for state, text in ((first,'first history'), (second,'second history')):
        r = await token_client.post(state['path']+'/events', headers=state['headers'], json={
            'events':[event(1,payload={'text':text})]})
        assert r.status_code == 200
    result = await token_client.get('/api/local-persona/current/history', headers=first['headers'])
    assert [e['payload']['text'] for e in result.json()] == ['first history']
    assert (await token_client.get(second['path']+'/status', headers=first['headers'])).status_code == 403
    await token_client.post(first['path']+'/close', headers=first['headers'])
    assert (await token_client.get('/api/local-persona/current/history', headers=first['headers'])).status_code == 409
    await token_client.post(first['path']+'/finalize', headers=first['headers'])
    assert (await token_client.get(first['path']+'/status', headers=first['headers'])).status_code == 200


async def test_bounded_invalid_and_unconfirmed_events(local, token_client, sf):
    state = await local()
    headers, path = state['headers'], state['path']
    large = await token_client.post(path+'/events', headers=headers,
        content=b'{"events":[],'+b' '*524288+b'}')
    assert large.status_code == 413
    invalid = event(1, payload={'text':'nul\x00'})
    assert (await token_client.post(path+'/events', headers=headers, json={'events':[invalid]})).status_code == 422
    events = [event(1), event(2, 'adapter_gap', {'reason':'unsupported record'}),
              event(3,'turn_end',{'outcome':'complete'}), event(4,turn='t2'), event(5,turn='t3')]
    assert (await token_client.post(path+'/events', headers=headers,json={'events':events})).status_code == 200
    async with sf() as s:
        turns = {r.local_turn_id:r.sync_state for r in (await s.execute(select(LocalPersonaTurn))).scalars()}
        assert turns == {'t1':'degraded','t2':'unconfirmed','t3':'open'}


async def test_reader_key_named_admin_cannot_read_recording(local, token_client, sf):
    state = await local()
    await token_client.post(state['path']+'/events', headers=state['headers'],
        json={'events':[event(1,payload={'text':'private laptop history'})]})
    token = 'ap_reader_named_admin_test'
    async with sf() as s:
        s.add(ApiKey(name='admin', role='reader', key_hash=hash_token(token), prefix='ap_reader'))
        run_id = (await s.get(LocalPersonaSession,state['session_id'])).run_id
        await s.commit()
    headers = {'Authorization':'Bearer '+token}
    room = '/api/relay/channels/'+state['conversation_id']
    assert (await token_client.get(room+'/messages',headers=headers)).status_code == 403
    assert (await token_client.get('/api/runs/'+run_id,headers=headers)).status_code == 403
    assert not (await token_client.get('/api/relay/search?q=laptop',headers=headers)).json()
    assert state['conversation_id'] not in [r['id'] for r in
        (await token_client.get('/api/relay/channels',headers=headers)).json()]


async def test_retention_keeps_receipts_but_removes_tool_payload(local, token_client, sf, agent_store):
    from agentplatform.pruning import TranscriptPruner
    from agentplatform.config import Settings
    state = await local()
    batch = [event(1)]
    response = await token_client.post(state['path']+'/events',headers=state['headers'],json={'events':batch})
    assert response.status_code == 200
    pruner = TranscriptPruner(sf, agent_store, Settings(transcript_retention_days=30))
    assert await pruner.prune_once(utcnow()+timedelta(days=40)) == 1
    async with sf() as s:
        row = (await s.execute(select(LocalPersonaEvent))).scalar_one()
        assert row.payload == {} and row.payload_hash and row.payload_pruned_at
    retry = await token_client.post(state['path']+'/events',headers=state['headers'],json={'events':batch})
    assert retry.status_code == 200 and retry.json()['accepted_sequence'] == 1
