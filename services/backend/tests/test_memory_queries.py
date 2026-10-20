"""Detached query data survives ingress, scheduling and immutable capture."""
import hashlib
import json
from datetime import timedelta

import pytest
from sqlalchemy import select

from agentplatform.config import Settings
from agentplatform.db import Memory, Run, RunStartupContext, ScheduledTask, utcnow
from agentplatform.dispatcher import Dispatcher, FakeLauncher
from agentplatform.events import FakeProducer
from agentplatform.materialize import materialize_run
from agentplatform.memory_queries import validate_memory_query
from agentplatform.task_scheduler import fire_due_tasks


@pytest.mark.parametrize('value', ['', ' \t\n', 'x'*1025, '🦊'*257, 7, [], {}, 'x\x00y', '\ud800'])
def test_invalid_explicit_queries(value):
    with pytest.raises(ValueError):
        validate_memory_query(value)


def test_null_and_unicode_byte_bound():
    assert validate_memory_query(None) is None
    assert validate_memory_query('🦊'*256)=='🦊'*256
    assert validate_memory_query('  archive badge  ')=='archive badge'


@pytest.mark.parametrize('value', ['', '  ', '🦊'*257, 7, 'x\x00y', '\ud800'])
async def test_invalid_query_is_422_without_creating_a_run(admin_client, sf, value):
    # ASCII JSON escapes ensure even an unpaired surrogate reaches validation.
    response=await admin_client.post('/api/runs', content=json.dumps(
        {'agent':'hello-world','prompt':'instructions','memory_query':value}),
        headers={'Content-Type':'application/json'})
    assert response.status_code==422
    async with sf() as s:
        assert not (await s.execute(select(Run))).scalars().all()


async def test_manual_ingress_and_idempotent_materialization(sf, producer, admin_client):
    response=await admin_client.post('/api/runs',json={
        'agent':'hello-world','prompt':'execute unchanged','memory_query':'🦊 badge'})
    assert response.status_code==200
    rid=response.json()['id']
    # Ingress redelivery cannot change a committed run's query or instructions.
    await materialize_run(sf,producer,{'run_id':rid,'agent':'hello-world','prompt':'replacement',
        'trigger':'manual','requested_by':'test','memory_query':'different'})
    async with sf() as s:
        run=await s.get(Run,rid)
        assert run.memory_query=='🦊 badge' and run.prompt=='execute unchanged'


async def test_task_query_persistence_null_reset_and_legacy_idempotency(admin_client,sf,producer,seed_agent):
    await seed_agent('query-task')
    body={'agent':'query-task','prompt':'unchanged execution','delay_minutes':2,'idempotency_key':'legacy'}
    response=await admin_client.post('/api/tasks',json=body);assert response.status_code==201
    task=response.json();assert task['memory_query'] is None
    from agentplatform.api.tasks import TaskIn
    old=TaskIn(**body).model_dump(mode='json');old.pop('memory_query')
    async with sf() as s:
        row=await s.get(ScheduledTask,task['id'])
        assert row.request_hash==hashlib.sha256(json.dumps(old,sort_keys=True).encode()).hexdigest()
    retry=await admin_client.post('/api/tasks',json={**body,'memory_query':None})
    assert retry.status_code==201 and retry.json()['id']==task['id']
    path='/api/tasks/'+task['id']
    r=await admin_client.patch(path,json={'version':1,'memory_query':'badge'});assert r.status_code==200
    r=await admin_client.patch(path,json={'version':2,'title':'new title'});assert r.json()['memory_query']=='badge'
    r=await admin_client.patch(path,json={'version':3,'memory_query':None});assert r.json()['memory_query'] is None
    r=await admin_client.patch(path,json={'version':4,'memory_query':'🦊 badge'});assert r.status_code==200
    async with sf() as s:
        row=await s.get(ScheduledTask,task['id']);row.run_at=utcnow()-timedelta(seconds=1);await s.commit()
    assert await fire_due_tasks(sf,producer)==1 and await fire_due_tasks(sf,producer)==0
    async with sf() as s:
        run=await s.get(Run,task['run_id'] or (await s.get(ScheduledTask,task['id'])).run_id)
        assert run.memory_query=='🦊 badge' and 'unchanged execution' in run.prompt
    assert (await admin_client.patch(path,json={'version':5,'memory_query':'changed'})).status_code==409


@pytest.mark.parametrize('position', ['head','middle','tail'])
@pytest.mark.parametrize('explicit', [False,True])
async def test_dispatch_query_source_and_delivered_evidence(sf,seed_agent,agent_store,monkeypatch,position,explicit):
    agent='query-pilot'
    await seed_agent(agent,agent_type='persona',platform_tools=['mcp__platform__memory'])
    question='What is atticbadge?'
    filler='Follow the execution instructions carefully.\n'*40
    prompt={'head':question+filler*2,'middle':filler+question+filler,'tail':filler*2+question}[position]
    async with sf() as s:
        s.add(Memory(id='target',agent=agent,content='atticbadge: indigo-canal-42.',updated_at=utcnow()-timedelta(days=2)))
        s.add(Memory(id='foreign',agent='other',content='atticbadge: forbidden.'))
        s.add_all([Memory(agent=agent,content=f'Routine delivery receipt {i}.') for i in range(30)])
        run=Run(agent=agent,trigger='manual',requested_by='test',prompt=prompt,
                memory_query='atticbadge' if explicit else None)
        s.add(run);await s.commit()
    async def unavailable(*args):raise TimeoutError('isolated embedding outage')
    monkeypatch.setattr('agentplatform.semantic_memory.embed',unavailable)
    disp=Dispatcher(Settings(memory_semantic_enabled=True,memory_semantic_agents=agent,
        startup_memory_pilot_agents=agent),sf,FakeProducer(),agent_store,FakeLauncher())
    assert (await disp._freeze_definition(run))[1] is None
    async with sf() as s:
        packet=await s.get(RunStartupContext,run.id)
        ids=[r['id'] for r in packet.payload['memories']]
        assert ('target' in ids)==(explicit or position=='head')
        assert 'foreign' not in ids
        query=packet.provenance['retrieval']['query']
        assert query['source']==('explicit_memory_query' if explicit else 'prompt')
        assert query['truncated'] is (not explicit)
        assert packet.provenance['retrieval']['state']=='degraded'
        assert (await s.get(Run,run.id)).prompt==prompt
        original=(packet.digest,packet.provenance)
        (await s.get(Memory,'target')).content='changed later';await s.commit()
    assert (await disp._freeze_definition(run))[1] is None
    async with sf() as s:
        packet=await s.get(RunStartupContext,run.id)
        assert (packet.digest,packet.provenance)==original


async def test_explicit_query_does_not_enable_an_ineligible_pilot(sf,seed_agent,agent_store):
    await seed_agent('outside',agent_type='persona',platform_tools=['mcp__platform__memory'])
    async with sf() as s:
        run=Run(agent='outside',trigger='manual',requested_by='t',prompt='instructions',memory_query='private query')
        s.add(run);await s.commit()
    disp=Dispatcher(Settings(memory_semantic_enabled=True,startup_memory_pilot_agents='pai'),sf,FakeProducer(),agent_store,FakeLauncher())
    assert (await disp._freeze_definition(run))[1] is None
    async with sf() as s:
        context=await s.get(RunStartupContext,run.id);assert not context.enabled


async def test_job_query_persists_to_run_now_and_scheduled_ingress(admin_client,sf,producer,agent_store):
    from agentplatform.db import ScheduledJob
    from agentplatform.scheduler import Scheduler
    from agentplatform.events import TOPIC_RUN_INBOUND
    r=await admin_client.post('/api/jobs',json={'name':'query job','agent':'hello-world',
        'cron':'0 12 * * *','prompt':'execute unchanged','memory_query':'archive badge'})
    assert r.status_code==201;job=r.json();path='/api/jobs/'+job['id']
    r=await admin_client.post(path+'/run');assert r.status_code==200
    async with sf() as s:
        assert (await s.get(Run,r.json()['id'])).memory_query=='archive badge'
        row=await s.get(ScheduledJob,job['id']);row.next_fire=utcnow()-timedelta(seconds=1);await s.commit()
    await Scheduler(sf,agent_store,producer)._tick_job(job['id'],utcnow())
    inbound=[p for topic,_,p in producer.published if topic==TOPIC_RUN_INBOUND][-1]
    assert inbound['memory_query']=='archive badge' and inbound['prompt']=='execute unchanged'
    await materialize_run(sf,producer,inbound)
    async with sf() as s:assert (await s.get(Run,inbound['run_id'])).memory_query=='archive badge'
    r=await admin_client.patch(path,json={'prompt':'new instructions'});assert r.json()['memory_query']=='archive badge'
    r=await admin_client.patch(path,json={'memory_query':None});assert r.json()['memory_query'] is None
    r=await admin_client.post('/api/jobs',json={'name':'invalid relay','relay_channel':'general',
        'cron':'0 12 * * *','prompt':'message','memory_query':'cannot apply'})
    assert r.status_code==422


async def test_declared_cron_query_survives_ingress(sf,producer,agent_store):
    from agentplatform.agentdefs import CronEntry
    from agentplatform.db import Schedule
    from agentplatform.scheduler import Scheduler
    from agentplatform.events import TOPIC_RUN_INBOUND
    assert 'memory_query' not in CronEntry(schedule='0 12 * * *').model_dump(mode='json')
    cron=CronEntry(schedule='0 12 * * *',prompt='cron instructions',memory_query='🦊 archive')
    assert cron.model_dump(mode='json')['memory_query']=='🦊 archive'
    scheduler=Scheduler(sf,agent_store,producer);now=utcnow()
    await scheduler._tick_agent('hello-world',[cron],now)
    async with sf() as s:
        (await s.get(Schedule,'hello-world')).next_fire=now-timedelta(seconds=1);await s.commit()
    await scheduler._tick_agent('hello-world',[cron],now)
    inbound=[p for topic,_,p in producer.published if topic==TOPIC_RUN_INBOUND][-1]
    assert inbound['memory_query']=='🦊 archive'
    await materialize_run(sf,producer,inbound)
    async with sf() as s:assert (await s.get(Run,inbound['run_id'])).memory_query=='🦊 archive'


async def test_query_does_not_enter_general_run_output(admin_client):
    r=await admin_client.post('/api/runs',json={'agent':'hello-world','prompt':'ordinary instructions',
        'memory_query':'PRIVATE-QUERY-ONLY'})
    detail=await admin_client.get('/api/runs/'+r.json()['id'])
    assert detail.status_code==200 and 'PRIVATE-QUERY-ONLY' not in detail.text


async def test_task_query_cannot_be_read_across_namespaces(admin_client,token_client,sf,seed_agent):
    from .test_relay_api import _agent_token
    await seed_agent('query-owner',platform_tools=['mcp__platform__tasks'])
    await seed_agent('query-outsider',platform_tools=['mcp__platform__tasks'])
    r=await admin_client.post('/api/tasks',json={'agent':'query-owner','prompt':'instructions',
        'memory_query':'PRIVATE-OWNER-QUERY','delay_minutes':5})
    assert r.status_code==201;tid=r.json()['id']
    async with sf() as s:
        run=Run(agent='query-outsider',prompt='t',trigger='manual',requested_by='test',state='running',
                definition_snapshot={'platform_tools':['mcp__platform__tasks']})
        s.add(run);await s.commit()
    headers=await _agent_token(sf,'query-outsider',role='reader',run_id=run.id)
    r=await token_client.get('/api/tasks/'+tid,headers=headers)
    assert r.status_code==404 and 'PRIVATE-OWNER-QUERY' not in r.text
    r=await token_client.get('/api/tasks',headers=headers)
    assert r.status_code==200 and 'PRIVATE-OWNER-QUERY' not in r.text
