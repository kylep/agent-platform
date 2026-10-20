from sqlalchemy import select, text
from agentplatform.db import ToolAudit, _ensure_columns
from agentplatform.ingest import ToolAuditIngestor
from .test_relay_api import _agent_token


async def test_memory_call_ingestion_and_private_filtered_reader(admin_client, token_client, sf):
    ingest=ToolAuditIngestor(None,sf,None)
    for agent,run,count,q in [('one','r1',0,'no hit'),('one','r2',3,'three hits'),('two','r3',5,'private other')]:
        await ingest._record({'agent':agent,'run_id':run,'tool':'memory','action':'read','decision':'allow',
                              'memory_query':q,'memory_query_truncated':False,'memory_mode':'hybrid','memory_result_count':count,
                              'memory_details':{'sources':[{'id':'source-'+run,'source_hash':'revision'}], 'retrieval':{'state':'hybrid'}}})
    await ingest._record({'agent':'one','run_id':'old','tool':'memory','decision':'allow'})
    headers=await _agent_token(sf,'one',role='reader')
    r=await token_client.get('/api/metrics/memory',headers=headers)
    assert r.status_code==200 and len(r.json())==3
    assert {x['agent'] for x in r.json()}=={'one'}
    assert {x['result_count'] for x in r.json()}=={None,0,3}
    assert (await token_client.get('/api/metrics/memory',headers=headers,params={'agent':'two'})).status_code==403
    assert (await token_client.get('/api/metrics/memory',headers=headers,params={'run_id':'r3'})).json()==[]
    r=await admin_client.get('/api/metrics/memory',params={'agent':'two','run_id':'r3','limit':1})
    assert len(r.json())==1 and r.json()[0]['query']=='private other'
    assert r.json()[0]['details']['sources']==[{'id':'source-r3','source_hash':'revision'}]
    assert 'args_digest' not in r.json()[0]
    assert (await token_client.get('/api/metrics/memory')).status_code==401
    from .test_relay_api import _key
    broad_reader=await _key(sf,name='global-reader',role='reader')
    assert (await token_client.get('/api/metrics/memory',headers=broad_reader)).status_code==403
    assert (await token_client.get('/api/metrics/memory',headers=broad_reader,params={'agent':'one'})).status_code==403


async def test_additive_migration_preserves_old_audit_rows_and_is_repeatable(sf):
    async with sf() as s:
        s.add(ToolAudit(agent='old',tool='memory',decision='allow'));await s.commit()
    async with sf.kw['bind'].begin() as c:
        for col in ['memory_query','memory_query_truncated','memory_mode','memory_result_count','memory_details']:
            await c.execute(text('ALTER TABLE tool_audit DROP COLUMN '+col))
        await c.run_sync(_ensure_columns)
        await c.run_sync(_ensure_columns)
    async with sf() as s:
        row=(await s.execute(select(ToolAudit))).scalar_one()
        assert row.agent=='old' and row.memory_query is None and row.memory_result_count is None
