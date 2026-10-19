import json
from datetime import timedelta
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from agentplatform.db import AgentDef, Memory, Run, RunStartupContext, utcnow
from agentplatform.config import Settings
from agentplatform.dispatcher import Dispatcher, FakeLauncher
from agentplatform.events import FakeProducer
from agentplatform.persona_memory import candidates, select_memory, digest
from agentplatform.startup_memory import envelope
from agentplatform.authority import assert_readable_run


async def pilot(sf, seed_agent, agent_store, **fields):
    await seed_agent('pilot', agent_type='persona', platform_tools=['mcp__platform__memory'])
    async with sf() as s:
        run = Run(agent='pilot', trigger='manual', requested_by='test', prompt='task', **fields)
        s.add(run)
        s.add(Memory(agent='pilot', key='preferences.durable', content='use migrations'))
        await s.commit()
    disp = Dispatcher(Settings(startup_memory_pilot_agents='pilot'), sf, FakeProducer(), agent_store, FakeLauncher())
    return disp, run


async def test_selector_churn_ties_labels_isolation_and_budgets(sf):
    now = utcnow()
    async with sf() as s:
        s.add(Memory(id='pin', agent='one', key='identity.first', content='durable', updated_at=now-timedelta(days=1), team_id='label'))
        s.add(Memory(id='other', agent='two', key='identity.secret', content='secret'))
        for i in range(30):
            s.add(Memory(id=f'{i:02}', agent='one', content='🦊'*3000, updated_at=now))
        await s.commit()
        rows = await candidates(s, 'one')
        assert len(rows) == 21 and rows[0].id == 'pin' and rows[1].id == '29'
        records, provenance = await select_memory(s, 'one')
    assert records[0]['content'] == 'durable'
    assert len({r['id'] for r in records}) == len(records)
    assert sum(len(r['content']) for r in records) == 12000
    assert all(len(r['content']) <= 2000 for r in records)
    assert provenance[1]['truncated'] and all(r['id'] != 'other' for r in records)


async def test_capture_atomic_and_redrive_immutable(sf, seed_agent, agent_store):
    disp, run = await pilot(sf, seed_agent, agent_store)
    assert (await disp._freeze_definition(run))[1] is None
    async with sf() as s:
        context = await s.get(RunStartupContext, run.id)
        original = context.digest
        assert context.enabled and context.payload['memories'][0]['content'] == 'use migrations'
        memory = (await s.execute(select(Memory).where(Memory.agent == 'pilot'))).scalar_one()
        memory.content = 'changed'
        await s.commit()
    disp.settings.startup_memory_pilot_agents = ''
    assert (await disp._freeze_definition(run))[1] is None
    async with sf() as s:
        assert (await s.get(RunStartupContext, run.id)).digest == original
        assert (await s.get(Run, run.id)).startup_memory_enabled


async def test_capture_failure_rolls_back_both(sf, seed_agent, agent_store, monkeypatch):
    disp, run = await pilot(sf, seed_agent, agent_store)
    async def fail(*a):
        raise ValueError('private memory must not appear')
    monkeypatch.setattr('agentplatform.startup_memory.capture', fail)
    _, error = await disp._freeze_definition(run)
    assert 'private' not in error
    async with sf() as s:
        assert (await s.get(Run, run.id)).definition_snapshot is None
        assert await s.get(RunStartupContext, run.id) is None


@pytest.mark.parametrize('fields', [{'trigger_message_id': 'x'}, {'conversation_id': 'x'}, {'parent_run_id': 'x'}, {'external_observation_id': 'x'}])
async def test_unsupported_triggers(sf, seed_agent, agent_store, fields):
    disp, run = await pilot(sf, seed_agent, agent_store, **fields)
    assert (await disp._freeze_definition(run))[1] is None
    async with sf() as s:
        context = await s.get(RunStartupContext, run.id)
        assert not context.enabled and context.reason == 'unsupported_trigger'


async def test_protocol_grant_terminal_and_corruption(sf, seed_agent, agent_store):
    disp, run = await pilot(sf, seed_agent, agent_store)
    await disp._freeze_definition(run)
    async with sf() as s:
        run = await s.get(Run, run.id)
        current = await s.get(AgentDef, 'pilot')
        with pytest.raises(HTTPException) as exc:
            await envelope(s, run, current, None)
        assert exc.value.status_code == 409
        packet = await envelope(s, run, current, '1')
        assert packet['digest'] == digest(packet['payload'])
        run.state = 'killed'
        with pytest.raises(HTTPException) as exc:
            await envelope(s, run, current, '1')
        assert exc.value.status_code == 403
        run.state = 'queued'
        current.platform_tools = []
        with pytest.raises(HTTPException):
            await envelope(s, run, current, '1')
        current.platform_tools = ['mcp__platform__memory']
        (await s.get(RunStartupContext, run.id)).digest = '0'*64
        with pytest.raises(HTTPException) as exc:
            await envelope(s, run, current, '1')
        assert exc.value.status_code == 409


@pytest.mark.parametrize('caller,role,allowed', [(None,'admin',True), (None,'reader',False),
    ('admin','reader',False), ('pilot','reader',True), ('other','admin',True), ('other','reader',False)])
async def test_private_output_uses_actual_role(sf, seed_agent, agent_store, caller, role, allowed):
    disp, run = await pilot(sf, seed_agent, agent_store)
    await disp._freeze_definition(run)
    request = SimpleNamespace(state=SimpleNamespace(api_key_agent=caller,resolved_role=role))
    async with sf() as s:
        if allowed:
            await assert_readable_run(s, request, run)
        else:
            with pytest.raises(HTTPException) as exc:
                await assert_readable_run(s, request, run)
            assert exc.value.status_code == 403


async def test_legacy_snapshot_and_cancellation_before_freeze(sf, seed_agent, agent_store):
    disp, run = await pilot(sf, seed_agent, agent_store)
    async with sf() as s:
        from agentplatform.agentdefs import model_of
        row = await s.get(Run,run.id)
        row.definition_snapshot = model_of(await s.get(AgentDef,'pilot')).model_dump(mode='json')
        await s.commit()
    await disp._freeze_definition(run)
    async with sf() as s:
        assert await s.get(RunStartupContext,run.id) is None
    async with sf() as s:
        killed = Run(agent='pilot',trigger='manual',requested_by='t',prompt='t',state='killed')
        s.add(killed); await s.commit()
    assert (await disp._freeze_definition(killed))[1] == 'run is no longer queued'


async def test_rest_private_output_and_admin_diagnostics(sf,seed_agent,agent_store,admin_client,token_client):
    from .test_relay_api import _human_token, _agent_token
    disp,run=await pilot(sf,seed_agent,agent_store)
    await disp._freeze_definition(run)
    async with sf() as s:
        row=await s.get(Run,run.id);row.tags=['private-pilot'];row.summary='private-summary';await s.commit()
    diag=await admin_client.get(f'/api/runs/{run.id}/startup-context')
    assert diag.status_code==200 and diag.json()['state']=='enabled'
    assert 'use migrations' not in diag.text
    assert (await admin_client.get(f'/api/runs/{run.id}')).status_code==200
    headers=await _human_token(sf,'admin',role='reader')
    assert (await token_client.get(f'/api/runs/{run.id}',headers=headers)).status_code==403
    assert all(r['id']!=run.id for r in (await token_client.get('/api/runs',headers=headers)).json())
    assert 'private-pilot' not in (await token_client.get('/api/tags',headers=headers)).json()
    assert (await token_client.get(f'/api/runs/{run.id}/events',headers=headers)).status_code==403
    headers=await _human_token(sf,'operator',role='operator')
    assert (await token_client.post(f'/api/runs/{run.id}/annotate',headers=headers,json={'summary':'x'})).status_code==403
    headers=await _agent_token(sf,'pilot',role='reader')
    assert (await token_client.get(f'/api/runs/{run.id}',headers=headers)).status_code==200


async def test_run_linked_artifacts_follow_pilot_visibility(sf,seed_agent,agent_store,admin_client,token_client):
    from agentplatform.db import Artifact
    from .test_relay_api import _human_token
    disp,run=await pilot(sf,seed_agent,agent_store)
    await disp._freeze_definition(run)
    result=await admin_client.post('/api/artifacts',json={'name':'pilot.txt','text':'private pilot output'})
    assert result.status_code==201
    aid=result.json()['id']
    async with sf() as s:
        (await s.get(Artifact,aid)).run_id=run.id
        await s.commit()
    reader=await _human_token(sf,'admin',role='reader')
    for suffix in ('','/content','/resource','/thumb'):
        response=await token_client.get(f'/api/artifacts/{aid}{suffix}',headers=reader)
        assert response.status_code==403
    assert (await admin_client.get(f'/api/artifacts/{aid}/content')).status_code==200


async def test_metadata_envelope_ceiling_fails_without_partial_freeze(sf,seed_agent,agent_store):
    disp,run=await pilot(sf,seed_agent,agent_store)
    async with sf() as s:
        memory=(await s.execute(select(Memory).where(Memory.agent=='pilot'))).scalar_one()
        memory.tags=['🦊'*17000]
        await s.commit()
    assert (await disp._freeze_definition(run))[1]=='startup definition/context capture failed'
    async with sf() as s:
        assert (await s.get(Run,run.id)).definition_snapshot is None
        assert await s.get(RunStartupContext,run.id) is None


async def test_recorder_refuses_automatic_publication_even_with_inconsistent_routing(sf):
    from agentplatform.db import Conversation,RelayMessage
    from agentplatform.recorder import Recorder
    from agentplatform.events import TOPIC_RUN_TRANSCRIPT,TOPIC_RUN_EVENTS
    producer=FakeProducer()
    async with sf() as s:
        room=Conversation(agent='pilot',kind='group',connector='web')
        s.add(room);await s.flush()
        run=Run(agent='pilot',trigger='manual',requested_by='t',prompt='t',state='running',
                startup_memory_enabled=True,conversation_id=room.id,
                definition_snapshot={'result_topic':'app.should-not-publish'})
        s.add(run);await s.commit()
    recorder=Recorder(sf,producer)
    await recorder.handle(TOPIC_RUN_TRANSCRIPT,run.id,{'seq':1,'type':'result','result':'private'})
    await recorder.handle(TOPIC_RUN_EVENTS,run.id,{'type':'state','state':'succeeded','terminal':True})
    assert not any(topic=='app.should-not-publish' for topic,_,_ in producer.published)
    async with sf() as s:
        assert not (await s.execute(select(RelayMessage).where(RelayMessage.channel_id==room.id))).scalars().all()
