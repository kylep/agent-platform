"""Exercise both memory transports and the actual audit publisher allowlist."""
import asyncio
import json
import time
import httpx
import pytest
from test_relay_tool import broker, caller  # noqa: F401


@pytest.mark.parametrize('mode,count', [('hybrid', 2), ('lexical', 0)])
def test_memory_reads_record_query_and_hit_count_but_not_results(monkeypatch, mode, count):
    published=[]
    class Producer:
        async def send_and_wait(self, topic, data, **kw):
            published.append(json.loads(data)['data'])
    monkeypatch.setattr(broker, '_KAFKA', 'test')
    monkeypatch.setattr(broker, '_audit_producer', Producer())
    monkeypatch.setattr(broker, '_rate_ok', lambda *a: True)
    async def identity(): return {'agent':'one', 'run_id':'verified-run', 'tools':['mcp__platform__memory']}
    monkeypatch.setattr(broker, '_whoami', identity)
    rows=[{'id':'source-id','updated_at':'2026-10-19T00:00:00Z','content':'PRIVATE_RESULT_CONTENT'}]*count
    output=json.dumps({'memories':rows} if mode=='hybrid' else rows)
    async def request(*a,**k):return httpx.Response(200,text=output,request=httpx.Request('POST','http://api'))
    monkeypatch.setattr(broker,'_request',request)
    class Client:
        def __init__(self,**kw):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*a):pass
        async def post(self,*a,**kw):return httpx.Response(200,json={'ok':True,'output':output})
    monkeypatch.setattr(broker.httpx,'AsyncClient',Client)
    tool=broker.CustomTool(name='memory',description='Memory telemetry test',parameters={'type':'object'})
    asyncio.run(tool.run({'action':'read','mode':mode,'q':'query needle','content':'NEVER_CAPTURE_CONTENT'}))
    data=published[-1]
    assert data['agent']=='one' and data['run_id']=='verified-run'
    assert data['action']=='read' and data['memory_mode']==mode
    assert data['memory_query']=='query needle' and data['memory_result_count']==count
    assert data['memory_details']['sources']==[{'id':'source-id','updated_at':'2026-10-19T00:00:00Z'}]*count
    assert not data['memory_query_truncated']
    assert 'PRIVATE_RESULT_CONTENT' not in json.dumps(data) and 'NEVER_CAPTURE_CONTENT' not in json.dumps(data)


def test_query_bounded_save_content_excluded_and_unknown_count_is_null(monkeypatch):
    rows=[]
    class Producer:
        async def send_and_wait(self,topic,data,**kw):rows.append(json.loads(data)['data'])
    monkeypatch.setattr(broker,'_KAFKA','test')
    monkeypatch.setattr(broker,'_audit_producer',Producer())
    async def probe():
        await broker._audit('one','run','','memory',{'action':'read','q':'🦊'*1100},'error:tool',time.monotonic())
        await broker._audit('one','run','','memory',{'action':'save','content':'PRIVATE_SAVE'},'allow',time.monotonic())
        await broker._audit('','','','memory',{'action':'read','q':'UNVERIFIED'},'deny:unauthenticated',time.monotonic())
    asyncio.run(probe())
    assert len(rows[0]['memory_query'])==1000 and rows[0]['memory_query_truncated']
    assert rows[0]['memory_result_count'] is None
    assert 'memory_query' not in rows[1] and 'PRIVATE_SAVE' not in json.dumps(rows)
    assert 'memory_query' not in rows[2] and 'UNVERIFIED' not in json.dumps(rows)
    assert broker._memory_count('not json') is None
    assert broker._memory_count('{"error":"failure"}') is None


def test_joinable_metadata_is_bounded_and_allowlisted():
    output=json.dumps({'memories':[], 'sources':[{'id':str(i),'source_hash':'revision','content':'PRIVATE'} for i in range(25)],
                       'retrieval':{'state':'degraded','reason':'source_changed','policy':'rank-v2','generation':'model-v1','unexpected':'PRIVATE'}})
    details=broker._memory_details(output)
    assert len(details['sources'])==20 and details['sources_truncated']
    assert details['retrieval']=={'state':'degraded','reason':'source_changed','policy':'rank-v2','generation':'model-v1'}
    assert 'PRIVATE' not in json.dumps(details)
