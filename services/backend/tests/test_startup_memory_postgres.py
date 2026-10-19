"""Real lock/migration/vector behavior. bin/ap-persona-postgres-test owns the DB."""
import asyncio
import os
import uuid
import pytest
from sqlalchemy import select,text
from agentplatform import semantic_memory as semantic
from agentplatform.db import AgentDef,Memory,Run,RunStartupContext,init_db,make_engine,make_session_factory
from agentplatform.config import Settings
from agentplatform.agents import AgentStore
from agentplatform.dispatcher import Dispatcher,FakeLauncher
from agentplatform.events import FakeProducer

pytestmark=pytest.mark.skipif(not os.environ.get('AP_PERSONA_TEST_POSTGRES_URL'),reason='isolated pgvector database required')


@pytest.mark.asyncio
async def test_vector_migration_reconciliation_live_revisions_and_generations(monkeypatch):
    engine=make_engine(os.environ['AP_PERSONA_TEST_POSTGRES_URL'])
    await init_db(engine)
    sf=make_session_factory(engine)
    # Disabled migration does not demand extension; enabling demands explicit DDL.
    async with sf() as s:
        with pytest.raises(RuntimeError,match='not provisioned'):
            await semantic.ready(s)
    await semantic.migrate(engine)
    await semantic.migrate(engine)
    agent='vec-'+uuid.uuid4().hex[:8]
    mid=uuid.uuid4().hex
    async with sf() as s:
        s.add(Memory(id=mid,agent=agent,key='database',content='apply an additive migration'))
        s.add(Memory(agent=agent+'-other',content='private migration'))
        await s.commit()
    settings=Settings(memory_semantic_enabled=True,memory_semantic_agents=agent)
    async def embed(*args):
        if args[1][0].startswith('title:'):
            assert args[0].memory_embedding_timeout_seconds == settings.memory_index_embedding_timeout_seconds
        else:
            assert args[0].memory_embedding_timeout_seconds == settings.memory_embedding_timeout_seconds
        return [[1.0]+[0.0]*767 for _ in args[1]]
    monkeypatch.setattr(semantic,'embed',embed)
    assert await semantic.reconcile(sf,settings)==1
    assert await semantic.reconcile(sf,settings)==0
    refs,status=await semantic.prepare(sf,settings,agent,'change database safely')
    assert status['state']=='hybrid' and [r['id'] for r in refs]==[mid]
    async with sf() as s:
        await s.execute(text("UPDATE public.memory_vectors_v1 SET generation='incompatible' WHERE memory_id=:id"),{'id':mid})
        await s.commit()
    refs,_=await semantic.prepare(sf,settings,agent,'unmatched paraphrase')
    assert not refs
    assert await semantic.reconcile(sf,settings)==1
    async with sf() as s:
        row=await s.get(Memory,mid);row.content='entirely changed'
        await s.commit()
    refs,_=await semantic.prepare(sf,settings,agent,'unmatched paraphrase')
    assert not refs # Overwrite is ineligible BEFORE cleanup/reindex.
    assert await semantic.reconcile(sf,settings)==1
    async with sf() as s:
        await s.delete(await s.get(Memory,mid));await s.commit()
    refs,_=await semantic.prepare(sf,settings,agent,'unmatched paraphrase')
    assert not refs # Delete immediately ineligible, regardless of stale vectors.
    assert await semantic.reconcile(sf,settings)==0
    await engine.dispose()


@pytest.mark.asyncio
async def test_populated_upgrade_atomic_freeze_concurrency_and_cancellation(monkeypatch):
    engine=make_engine(os.environ['AP_PERSONA_TEST_POSTGRES_URL'])
    await init_db(engine)
    sf=make_session_factory(engine)
    agent='freeze-'+uuid.uuid4().hex[:8]
    async with sf() as s:
        s.add(AgentDef(name=agent,agent_type='persona',platform_tools=['mcp__platform__memory']))
        s.add(Memory(agent=agent,key='preferences.db',content='preserve data'))
        run=Run(agent=agent,trigger='manual',requested_by='test',prompt='task')
        s.add(run);await s.commit()
    async with engine.begin() as c:
        await c.execute(text('ALTER TABLE runs DROP COLUMN startup_memory_enabled'))
    await init_db(engine)
    await init_db(engine)
    async with sf() as s:
        assert (await s.get(Run,run.id)).startup_memory_enabled is False
    store=AgentStore(sf);await store.reload()
    disp=Dispatcher(Settings(startup_memory_pilot_agents=agent),sf,FakeProducer(),store,FakeLauncher())
    results=await asyncio.gather(disp._freeze_definition(run),disp._freeze_definition(run))
    assert all(error is None for _,error in results)
    async with sf() as s:
        rows=(await s.execute(select(RunStartupContext).where(RunStartupContext.run_id==run.id))).scalars().all()
        assert len(rows)==1 and rows[0].enabled
        killed=Run(agent=agent,trigger='manual',requested_by='test',prompt='task',state='killed')
        s.add(killed);await s.commit()
    assert (await disp._freeze_definition(killed))[1]=='run is no longer queued'
    # A failure after the definition assignment must roll back both halves.
    async with sf() as s:
        failed=Run(agent=agent,trigger='manual',requested_by='test',prompt='task')
        s.add(failed);await s.commit()
    async def broken(*a): raise RuntimeError('synthetic')
    monkeypatch.setattr('agentplatform.startup_memory.capture',broken)
    assert (await disp._freeze_definition(failed))[1]=='startup definition/context capture failed'
    async with sf() as s:
        assert (await s.get(Run,failed.id)).definition_snapshot is None
        assert await s.get(RunStartupContext,failed.id) is None
    await engine.dispose()


@pytest.mark.asyncio
async def test_failed_indexing_is_durable_and_does_not_starve(monkeypatch):
    engine=make_engine(os.environ['AP_PERSONA_TEST_POSTGRES_URL'])
    await init_db(engine);await semantic.migrate(engine)
    sf=make_session_factory(engine)
    agent='retry-'+uuid.uuid4().hex[:8]
    async with sf() as s:
        s.add_all([Memory(id=uuid.uuid4().hex,agent=agent,content='first'),
                   Memory(id=uuid.uuid4().hex,agent=agent,content='second')])
        await s.commit()
    async def down(*args):raise TimeoutError()
    monkeypatch.setattr(semantic,'embed',down)
    settings=Settings(memory_semantic_enabled=True,memory_semantic_agents=agent)
    with pytest.raises(RuntimeError,match='deferred'):
        await semantic.reconcile(sf,settings,batch=1)
    async def recovered(*args):return [[1.0]+[0.0]*767 for _ in args[1]]
    monkeypatch.setattr(semantic,'embed',recovered)
    assert await semantic.reconcile(sf,settings,batch=1)==1
    async with sf() as s:
        assert (await s.execute(text('SELECT count(*) FROM public.memory_embedding_failures_v1'))).scalar()>=1
    await engine.dispose()
