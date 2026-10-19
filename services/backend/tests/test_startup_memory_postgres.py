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
@pytest.mark.parametrize('unavailable', [False, True])
async def test_exact_key_precedes_null_keys_before_candidate_limit(monkeypatch, unavailable):
    engine = make_engine(os.environ['AP_PERSONA_TEST_POSTGRES_URL'])
    try:
        await init_db(engine)
        sf = make_session_factory(engine)
        agent = 'exact-' + uuid.uuid4().hex[:8]
        key = 'preferences.exact-key'
        exact = uuid.uuid4().hex
        async with sf() as s:
            s.add(Memory(id=exact, agent=agent, key=key, content='older grounded summary',
                         team_id='own-team', project_id='own-project'))
            await s.commit()
            s.add_all([Memory(agent=agent, content=key + ' unkeyed note',
                              team_id='own-team', project_id='own-project') for _ in range(25)])
            # These must be excluded before LIMIT, even though they have exact
            # keys or newer timestamps and would otherwise consume candidates.
            s.add_all([Memory(agent=agent + '-foreign-' + str(i), key=key, content=key) for i in range(25)])
            s.add_all([Memory(agent=agent, content=key, team_id='other-team',
                              project_id='own-project') for _ in range(25)])
            s.add_all([Memory(agent=agent, content=key, team_id='own-team',
                              project_id='other-project') for _ in range(25)])
            await s.commit()
        async def down(*args):
            raise TimeoutError('synthetic embedding outage')
        monkeypatch.setattr(semantic, 'embed', down)
        refs, status = await semantic.prepare(sf, Settings(memory_semantic_enabled=unavailable,
            memory_semantic_agents=agent), agent, key, 'own-team', 'own-project')
        assert len(refs) == 20 and refs[0]['id'] == exact
        assert status['state'] == ('degraded' if unavailable else 'lexical')
        async with sf() as s:
            rows = await semantic.resolve(s, agent, refs)
            assert len(rows) == 20
            assert all(m.agent == agent and m.team_id == 'own-team'
                       and m.project_id == 'own-project' for m in rows)
    finally:
        await engine.dispose()


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


@pytest.mark.asyncio
async def test_task_cancel_after_capture_before_dispatch_claim(monkeypatch):
    from datetime import timedelta
    from types import SimpleNamespace
    from agentplatform.db import ScheduledTask,utcnow
    from agentplatform.api.tasks import cancel_task,Caller
    engine=make_engine(os.environ['AP_PERSONA_TEST_POSTGRES_URL']);await init_db(engine)
    sf=make_session_factory(engine);agent='cancel-'+uuid.uuid4().hex[:8]
    rid,tid=uuid.uuid4().hex,uuid.uuid4().hex
    async with sf() as s:
        s.add(AgentDef(name=agent,agent_type='persona',platform_tools=['mcp__platform__memory']))
        s.add(Memory(agent=agent,key='preferences.test',content='synthetic'))
        s.add(ScheduledTask(id=tid,title='Synthetic cancellation',agent=agent,prompt='task',runtime='claude',model='',
            run_at=utcnow(),expires_at=utcnow()+timedelta(hours=1),creator='user:admin',status='launched',run_id=rid))
        s.add(Run(id=rid,agent=agent,trigger='task',requested_by='test',prompt='task',task_id=tid))
        await s.commit()
    store=AgentStore(sf);await store.reload();launcher=FakeLauncher()
    disp=Dispatcher(Settings(startup_memory_pilot_agents=agent),sf,FakeProducer(),store,launcher)
    async def cancel_after_capture(manifest):
        async with sf() as s:
            assert (await s.get(RunStartupContext,rid)).enabled
        request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(session_factory=sf)))
        await cancel_task(request,tid,Caller(name='admin'))
        return None
    monkeypatch.setattr(disp,'_readiness_blocks',cancel_after_capture)
    await asyncio.wait_for(disp.handle({'type':'run','run_id':rid}),timeout=5)
    assert not launcher.launched
    async with sf() as s:
        assert (await s.get(Run,rid)).state=='killed'
        assert (await s.get(ScheduledTask,tid)).status=='cancelled'
        assert (await s.get(RunStartupContext,rid)).enabled
    await engine.dispose()


@pytest.mark.asyncio
async def test_authority_change_serializes_before_capture(monkeypatch):
    from agentplatform.authority import authority_lock
    import agentplatform.authority as authority
    engine=make_engine(os.environ['AP_PERSONA_TEST_POSTGRES_URL']);await init_db(engine)
    sf=make_session_factory(engine);agent='authority-'+uuid.uuid4().hex[:8]
    async with sf() as s:
        s.add(AgentDef(name=agent,agent_type='persona',platform_tools=['mcp__platform__memory']))
        run=Run(agent=agent,trigger='manual',requested_by='test',prompt='task');s.add(run);await s.commit()
    store=AgentStore(sf);await store.reload()
    disp=Dispatcher(Settings(startup_memory_pilot_agents=agent),sf,FakeProducer(),store,FakeLauncher())
    reached_lock=asyncio.Event()
    async def observed_lock(session):
        reached_lock.set();await authority_lock(session)
    async with sf() as s:
        await authority_lock(s)
        target=await s.get(AgentDef,agent,with_for_update=True);target.enabled=False;await s.flush()
        monkeypatch.setattr(authority,'authority_lock',observed_lock)
        freeze=asyncio.create_task(disp._freeze_definition(run))
        await asyncio.wait_for(reached_lock.wait(),timeout=5)
        assert not freeze.done()
        await s.commit()
    _,error=await asyncio.wait_for(freeze,timeout=5)
    assert error=='agent is disabled'
    async with sf() as s:
        assert (await s.get(Run,run.id)).definition_snapshot is None
        assert await s.get(RunStartupContext,run.id) is None
    await engine.dispose()


@pytest.mark.asyncio
async def test_semantic_late_chunk_delivery_is_bound_to_live_revision_and_frozen_packet(monkeypatch):
    from agentplatform.persona_memory import select_records
    engine = make_engine(os.environ['AP_PERSONA_TEST_POSTGRES_URL'])
    try:
        await init_db(engine); await semantic.migrate(engine)
        sf = make_session_factory(engine); agent = 'passage-' + uuid.uuid4().hex[:8]
        content = 'Unrelated 🦊 weather receipt. ' * 165 + 'The archive verification code is violet-harbor-672.'
        mid = uuid.uuid4().hex
        async with sf() as s:
            s.add(AgentDef(name=agent, agent_type='persona', platform_tools=['mcp__platform__memory']))
            s.add(Memory(id=mid, agent=agent, key='lessons.archive', content=content))
            s.add(Memory(agent=agent, key='identity.empty', content=''))
            s.add(Memory(agent=agent+'-foreign', content='The archive verification code is private.'))
            run = Run(agent=agent, trigger='manual', requested_by='test', prompt='What is the archive verification code?')
            s.add(run); await s.commit()
        async def embed(settings, inputs):
            return [[1., 0.] + [0.] * 766 if text.startswith('task:') or 'violet-harbor-672' in text
                    else [0., 1.] + [0.] * 766 for text in inputs]
        monkeypatch.setattr(semantic, 'embed', embed)
        settings = Settings(memory_semantic_enabled=True, memory_semantic_agents=agent, startup_memory_pilot_agents=agent)
        assert await semantic.reconcile(sf, settings) == 1 # Empty sources do not consume indexing work.
        refs, status = await semantic.prepare(sf, settings, agent, run.prompt)
        assert status['state'] == 'hybrid' and refs[0]['match_chunk'] >= 2
        async with sf() as s:
            rows = await semantic.resolve(s, agent, refs)
            records, sources = select_records(rows, references=refs)
            assert len(records) == 1 and 'violet-harbor-672' in records[0]['content']
            assert records[0]['truncated'] and records[0]['excerpt']['start'] > 2000
            assert content[records[0]['excerpt']['start']:records[0]['excerpt']['end']] == records[0]['content']
            assert sources[0]['source_hash'] == records[0]['source_hash']
            assert not await semantic.resolve(s, agent+'-foreign', refs)
        store = AgentStore(sf); await store.reload()
        disp = Dispatcher(settings, sf, FakeProducer(), store, FakeLauncher())
        assert (await disp._freeze_definition(run))[1] is None
        async with sf() as s:
            captured = await s.get(RunStartupContext, run.id)
            assert 'violet-harbor-672' in captured.payload['memories'][0]['content']
            original_digest = captured.digest
            memory = await s.get(Memory, mid); assert memory.content == content
            memory.content = 'Overwritten source'; await s.commit()
            assert not await semantic.resolve(s, agent, refs)
        assert (await disp._freeze_definition(run))[1] is None
        async with sf() as s:
            assert (await s.get(RunStartupContext, run.id)).digest == original_digest
            await s.delete(await s.get(Memory, mid)); await s.commit()
            assert not await semantic.resolve(s, agent, refs)
            # Literal evidence beyond the bounded embedding tail must still
            # beat an unrelated indexed opening passage of that same source.
            s.add(Memory(id=mid, agent=agent, content='Unrelated weather. ' * 1500 + ' tailuniquefact: amber.'))
            await s.commit()
        assert await semantic.reconcile(sf, settings) == 1
        tail_refs, _ = await semantic.prepare(sf, settings, agent, 'tailuniquefact')
        assert tail_refs[0]['match_start'] > 16000
        async with sf() as s:
            tail_records, _ = select_records(await semantic.resolve(s, agent, tail_refs), references=tail_refs)
            assert 'tailuniquefact: amber.' in tail_records[0]['content']
            assert tail_records[0]['excerpt']['start'] > 16000
    finally:
        await engine.dispose()
