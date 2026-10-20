import pytest
from agentplatform.config import Settings
from agentplatform.db import Memory
from agentplatform import semantic_memory as semantic


async def test_disabled_lexical_exact_namespace_and_scope(sf):
    async with sf() as s:
        s.add_all([Memory(id='exact',agent='one',key='name.exact',content='older'),
                   Memory(id='words',agent='one',content='name.exact details'),
                   Memory(id='secret',agent='two',content='name.exact private'),
                   Memory(id='label',agent='one',content='labelled',team_id='team')])
        await s.commit()
    refs,status = await semantic.prepare(sf,Settings(),'one','name.exact')
    assert [r['id'] for r in refs] == ['exact','words']
    assert status['state']=='lexical'
    refs,_=await semantic.prepare(sf,Settings(),'one','labelled',team_id='other')
    assert not refs


async def test_failure_falls_back_and_stale_references_excluded(sf,monkeypatch):
    async with sf() as s:
        s.add(Memory(id='one',agent='one',content='schema migration'))
        await s.commit()
    async def down(*args):
        raise TimeoutError()
    monkeypatch.setattr(semantic,'embed',down)
    refs,status=await semantic.prepare(sf,Settings(memory_semantic_enabled=True,memory_semantic_agents='one'),'one','schema')
    assert refs[0]['id']=='one' and status['state']=='degraded'
    async with sf() as s:
        (await s.get(Memory,'one')).content='changed'
        await s.commit()
        assert not await semantic.resolve(s,'one',refs)
        assert not await semantic.resolve(s,'two',refs)


def test_embedding_preprocessing_is_bounded():
    result=semantic.bounded('🦊'*1000)
    assert len(result.encode())==1024
    assert len(semantic.chunks(Memory(content='x'*100000)))==16


async def test_hybrid_api_requires_active_owning_run_and_rechecks_grants(sf,seed_agent,agent_store,token_client):
    from agentplatform.db import AgentDef,Run
    from .test_relay_api import _agent_token
    await seed_agent('semantic-api',agent_type='persona',platform_tools=['mcp__platform__memory'])
    async with sf() as s:
        run=Run(agent='semantic-api',trigger='manual',requested_by='test',prompt='task',state='running',
                definition_snapshot={'platform_tools':['mcp__platform__memory']})
        s.add(run)
        s.add(Memory(agent='semantic-api',key='migration',content='own memory'))
        s.add(Memory(agent='other',key='migration',content='private other memory'))
        await s.commit()
    headers=await _agent_token(sf,'semantic-api',role='reader')
    assert (await token_client.post('/api/memory-hybrid',headers=headers,json={'q':'migration'})).status_code==403

    headers=await _agent_token(sf,'semantic-api',role='reader',run_id=run.id)
    response=await token_client.post('/api/memory-hybrid',headers=headers,json={'q':'migration'})
    assert response.status_code==200 and [r['content'] for r in response.json()['memories']]==['own memory']
    async with sf() as s:
        (await s.get(AgentDef,'semantic-api')).platform_tools=[];await s.commit()
    assert (await token_client.post('/api/memory-hybrid',headers=headers,json={'q':'migration'})).status_code==403


@pytest.mark.parametrize('revoked', ['current', 'frozen'])
async def test_hybrid_api_rechecks_authority_after_retrieval(sf, seed_agent, token_client, monkeypatch, revoked):
    from agentplatform.db import AgentDef, Run
    from .test_relay_api import _agent_token
    name = 'hybrid-recheck-' + revoked
    grant = 'mcp__platform__memory'
    await seed_agent(name, agent_type='persona', platform_tools=[grant])
    async with sf() as s:
        run = Run(agent=name, trigger='manual', requested_by='test', prompt='task', state='running',
                  definition_snapshot={'platform_tools': [grant]})
        s.add(run)
        await s.commit()
    calls = []
    async def prepare(*args):
        calls.append(True)
        # Inference runs outside the authority lock. A revoke during that
        # interval must still prevent results from leaving the API boundary.
        async with sf() as s:
            if revoked == 'current':
                (await s.get(AgentDef, name)).platform_tools = []
            else:
                (await s.get(Run, run.id)).definition_snapshot = {'platform_tools': []}
            await s.commit()
        return [], {'state': 'lexical'}
    monkeypatch.setattr(semantic, 'prepare', prepare)
    headers = await _agent_token(sf, name, role='reader', run_id=run.id)
    response = await token_client.post('/api/memory-hybrid', headers=headers, json={'q': 'migration'})
    assert calls == [True] and response.status_code == 403


async def test_pinned_runtime_request_and_model_identity(monkeypatch):
    import httpx,json
    calls=[]
    async def handle(request):
        calls.append(request.url.path)
        if request.url.path=='/api/tags':
            return httpx.Response(200,json={'models':[{'name':semantic.MODEL,'digest':semantic.ARTIFACT}]})
        body=json.loads(request.content)
        # Ollama Duration rejects the string '-1' (missing a unit); numeric
        # negative durations mean indefinite retention on the tested release.
        assert isinstance(body['keep_alive'],int) and body['keep_alive']<0
        assert body['truncate'] is False and body['options']['num_gpu']==0
        return httpx.Response(200,json={'embeddings':[[1.0]+[0.0]*767]})
    real=httpx.AsyncClient
    monkeypatch.setattr(semantic.httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(handle),**kw))
    assert len((await semantic.embed(Settings(),['task: search result | query: test']))[0])==768
    assert calls==['/api/tags','/api/embed']


async def test_fixed_relevance_fixture_prefers_lessons_over_receipt_churn(sf):
    import json
    from pathlib import Path
    from datetime import timedelta
    from agentplatform.db import utcnow
    fixture = json.loads((Path(__file__).parent/'fixtures/memory-relevance/corpus.json').read_text())
    async with sf() as s:
        for i, d in enumerate(fixture['documents']):
            s.add(Memory(id=d['id'], agent='relevance', key=d['memory_label'], content=d['content'],
                         updated_at=utcnow()+timedelta(days=1) if d['id'].startswith('churn') else utcnow()-timedelta(days=5)))
        await s.commit()
    queries = {q['id']:q for q in fixture['queries']}
    for case, expected in [('db-question',['db','lesson','infra']),('receipt-question',['receipt']),
                           ('exact-key',['db']),('unrelated',[])]:
        refs, _ = await semantic.prepare(sf, Settings(), 'relevance', queries[case]['q'])
        assert [r['id'] for r in refs][:len(expected)] == expected
        if not expected:
            assert not refs


async def test_relevance_signal_remains_unassessed_without_validated_gate(sf):
    async with sf() as s:
        s.add(Memory(agent='unassessed',content='generic review of database tools'))
        await s.commit()
    refs,status=await semantic.prepare(sf,Settings(),'unassessed','database')
    assert refs and status['relevance']=={'state':'unassessed','reason':'no_validated_abstention_policy'}
    refs,status=await semantic.prepare(sf,Settings(),'unassessed','database',team_id='empty')
    assert not refs and status['relevance']['state']=='unassessed'
