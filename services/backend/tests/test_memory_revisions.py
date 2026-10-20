"""Conditional correction without a new history store or changed retrieval."""
import asyncio
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
import uuid

from fastapi import HTTPException
import pytest
from sqlalchemy import select, text

from agentplatform.api.memory import MemoryIn, save_memory
from agentplatform.db import Memory, init_db, make_engine, make_session_factory
from agentplatform.persona_memory import source_hash


async def test_conditional_correction_retries_and_delete_recreate(admin_client):
    body = {'agent': 'cas', 'key': 'preferences.state', 'content': 'first', 'expected_revision': 'absent'}
    first = (await admin_client.post('/api/memories', json=body)).json()
    revision = first['revision']
    assert len(revision) == 64
    assert (await admin_client.get('/api/memories/'+first['id'])).json()['revision'] == revision
    assert (await admin_client.post('/api/memories', json=body)).status_code == 409
    body.update(expected_revision=revision)
    # A matching, identical state is a no-op; the same token remains usable.
    same = (await admin_client.post('/api/memories', json=body)).json()
    assert same['revision'] == revision and same['updated_at'] == first['updated_at']
    body['content'] = 'corrected'
    second = (await admin_client.post('/api/memories', json=body)).json()
    assert second['revision'] != revision
    stale = await admin_client.post('/api/memories', json=body)
    assert stale.status_code == 409
    assert stale.json()['detail'] == {'code':'memory_revision_conflict', 'expected_revision':revision,
                                    'current_revision':second['revision']}
    # Even identical desired content with an old token conflicts.
    assert (await admin_client.patch('/api/memories/'+first['id'], json={
        'content':'corrected', 'expected_revision':revision})).status_code == 409
    assert (await admin_client.delete('/api/memories/'+first['id'], params={
        'expected_revision':revision})).status_code == 409
    assert (await admin_client.delete('/api/memories/'+first['id'], params={
        'expected_revision':second['revision']})).status_code == 200
    assert (await admin_client.post('/api/memories', json=body)).status_code == 409
    body['expected_revision'] = 'absent'
    recreated = (await admin_client.post('/api/memories', json=body)).json()
    assert recreated['id'] != first['id'] and recreated['revision'] != second['revision']
    # Legacy clients remain unconditional.
    body.pop('expected_revision');body['content']='legacy'
    assert (await admin_client.post('/api/memories', json=body)).json()['content'] == 'legacy'


async def test_conditional_validation_and_foreign_metadata(admin_client, token_client, sf):
    from agentplatform.apikeys import generate_token, hash_token, token_prefix
    from agentplatform.db import ApiKey
    token = generate_token()
    auth = {'Authorization':'Bearer '+token}
    async with sf() as s:
        s.add(ApiKey(name='cas-owner',role='annotator',agent='owner',key_hash=hash_token(token),prefix=token_prefix(token)))
        await s.commit()
    foreign = (await admin_client.post('/api/memories', json={
        'agent':'foreign', 'key':'k', 'content':'private'})).json()
    for expected in ['invalid', '', 123, 'A'*64]:
        assert (await admin_client.post('/api/memories', json={
            'agent':'owner', 'key':'k', 'content':'x', 'expected_revision':expected})).status_code == 422
    assert (await admin_client.post('/api/memories', json={
        'agent':'owner','content':'x','expected_revision':'absent'})).status_code == 422
    r = await token_client.post('/api/memories', headers=auth, json={
        'agent':'foreign','key':'k','content':'x','expected_revision':foreign['revision']})
    assert r.status_code == 403 and foreign['revision'] not in r.text
    r = await token_client.patch('/api/memories/'+foreign['id'], headers=auth, json={
        'content':'x','expected_revision':foreign['revision']})
    assert r.status_code == 404 and foreign['revision'] not in r.text


@pytest.mark.skipif(not os.environ.get('AP_PERSONA_TEST_POSTGRES_URL'), reason='isolated PostgreSQL required')
async def test_postgres_tool_and_api_concurrent_corrections_rollback_and_index(monkeypatch):
    import psycopg
    from agentplatform import semantic_memory as semantic
    from agentplatform.config import Settings
    url=os.environ['AP_PERSONA_TEST_POSTGRES_URL']
    engine=make_engine(url); await init_db(engine);await semantic.migrate(engine)
    sf=make_session_factory(engine);agent='cas-'+uuid.uuid4().hex[:8]
    req=SimpleNamespace(state=SimpleNamespace(), app=SimpleNamespace(state=SimpleNamespace(session_factory=sf)))
    spec=importlib.util.spec_from_file_location('memory_cas_tool',Path(__file__).resolve().parents[3]/'tools/memory/run.py')
    tool=importlib.util.module_from_spec(spec);spec.loader.exec_module(tool)
    def tool_save(content, expected, rollback=False):
        with psycopg.connect(url.replace('postgresql+asyncpg:', 'postgresql:')) as conn, conn.cursor() as cur:
            cur.execute("SET LOCAL TIME ZONE 'America/Toronto'")
            out=tool.save(cur,agent,{'key':'state','content':content,'expected_revision':expected})
            if rollback:conn.rollback()
            return out
    async def api_save(content, expected):
        try:return await save_memory(req,MemoryIn(agent=agent,key='state',content=content,expected_revision=expected))
        except HTTPException as error:return {'ok':False,'error':error.detail}
    try:
        first=await api_save('initial','absent')
        settings=Settings(memory_semantic_enabled=True,memory_semantic_agents=agent)
        async def embed(*args):return [[1.0]+[0.0]*767 for _ in args[1]]
        monkeypatch.setattr(semantic,'embed',embed)
        await semantic.reconcile(sf,settings,batch=100)
        async with sf() as s:
            row=await s.get(Memory,first['id']);assert source_hash(row)==first['revision']
            vector_before=(await s.execute(text('SELECT source_hash,embedding::text FROM public.memory_vectors_v1 WHERE memory_id=:id'),{'id':row.id})).all()
        # Two independent connections/transactions, different implementations.
        writes=await asyncio.gather(api_save('API winner?',first['revision']),
                                    asyncio.to_thread(tool_save,'Tool winner?',first['revision']))
        winners=[r for r in writes if r.get('ok') is not False]
        losers=[r for r in writes if r.get('ok') is False]
        assert len(winners)==len(losers)==1
        winner=winners[0];assert losers[0]['error']['current_revision']==winner['revision']
        # Failed attempts must leave both canonical state and stale index rows untouched.
        assert (await api_save('stale',first['revision']))['ok'] is False
        assert (await asyncio.to_thread(tool_save,'stale',first['revision']))['ok'] is False
        async with sf() as s:
            current=await s.get(Memory,first['id'])
            assert current.content==winner['content'] and source_hash(current)==winner['revision']
            assert (await s.execute(text('SELECT source_hash,embedding::text FROM public.memory_vectors_v1 WHERE memory_id=:id'),{'id':current.id})).all()==vector_before
            assert await semantic.resolve(s,agent,[{'id':current.id,'source_hash':first['revision']}]) == []
        same=await asyncio.to_thread(tool_save,winner['content'],winner['revision'])
        assert same['revision']==winner['revision']
        rolled=await asyncio.to_thread(tool_save,'rolled back',winner['revision'],True)
        assert rolled['revision'] != winner['revision']
        retried=await api_save('retry after rollback',winner['revision'])
        assert retried.get('ok') is not False
        # Unique-index arbitration also protects missing-key creation.
        async with sf() as s:
            await s.delete(await s.get(Memory,first['id']));await s.commit()
        creates=await asyncio.gather(api_save('create A','absent'),asyncio.to_thread(tool_save,'create B','absent'))
        assert sum(r.get('ok') is not False for r in creates)==1
        assert sum(r.get('ok') is False for r in creates)==1
        # Deterministically interleave independent real transactions: a
        # create-only loser sees the winning row deleted before its reread.
        def disappeared_winner():
            sync_url=url.replace('postgresql+asyncpg:', 'postgresql:')
            with psycopg.connect(sync_url) as conn, conn.cursor() as real:
                class Interleave:
                    phase=0
                    sql=''
                    def execute(self,sql,params=None):
                        self.sql=sql;return real.execute(sql,params)
                    def fetchone(self):
                        row=real.fetchone()
                        if self.phase==0 and self.sql.startswith('SELECT') and row is None:
                            with psycopg.connect(sync_url) as other, other.cursor() as cur:
                                cur.execute("INSERT INTO tool_memory.memories(id,agent,key,content,tags,created_at,updated_at) VALUES (%s,%s,'transient','winner','[]',now(),now())",(uuid.uuid4().hex,agent))
                            self.phase=1
                        elif self.phase==1 and self.sql.startswith('INSERT') and row is None:
                            with psycopg.connect(sync_url) as other, other.cursor() as cur:
                                cur.execute("DELETE FROM tool_memory.memories WHERE agent=%s AND key='transient'",(agent,))
                            self.phase=2
                        return row
                cur=Interleave()
                out=tool.save(cur,agent,{'key':'transient','content':'must not be saved','expected_revision':'absent'})
                assert cur.phase==2
                return out
        transient=await asyncio.to_thread(disappeared_winner)
        assert transient=={'ok':False,'error':{'code':'memory_revision_conflict',
            'expected_revision':'absent','current_revision':None}}
        async with sf() as s:
            assert (await s.execute(select(Memory).where(Memory.agent==agent,Memory.key=='transient'))).scalar_one_or_none() is None
    finally:await engine.dispose()
