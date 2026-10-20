"""Benchmark validity and grading boundaries, without answer-model calls."""
import importlib.machinery
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import pytest

ROOT=Path(__file__).resolve().parents[3]
loader=importlib.machinery.SourceFileLoader('memory_answer_eval',str(ROOT/'bin/ap-memory-answer-eval'))
spec=importlib.util.spec_from_loader(loader.name,loader);evaluation=importlib.util.module_from_spec(spec);loader.exec_module(evaluation)


def test_frozen_cases_have_valid_labels_budget_and_unchanged_controls():
    f=json.loads(evaluation.FIXTURE.read_text());cases=list(evaluation.variants(f))
    assert len(cases)==25<=f['budget']['max_calls']
    for case in cases:
        by_id={d['memory_id']:d for d in case['docs']}
        for span in case['spans']:
            assert by_id[span['id']]['content'][span['start']:span['end']]
        if case['expected']['value'] is not None:
            assert case['spans'] and case['expected']['value'] not in case['task']
    controls=[v for v in cases if v['case']=='direct']
    assert all(v['task']==controls[0]['task'] and v['docs']==controls[0]['docs'] for v in controls)
    assert all(len(v['query'].encode())<1024 for v in cases if v['condition']=='explicit_query')
    for position in ['middle','tail']:
        v=next(v for v in cases if v['case']=='verbose-'+position and v['condition']=='baseline')
        assert v['task'].index('What preservation marker')>1024


def test_grading_separates_lucky_answers_delivery_scope_and_uncertainty():
    c={'case':'case','condition':'baseline','expected':{'value':'birch','scope':'simulator','support':[]},
       'spans':[{'id':'source','start':0,'end':5}], 'records':[{'id':'source'}],
       'candidate_covered':True,'delivered_required_evidence':False,'prompt_sha256':'fixture'}
    r={'result':json.dumps({'value':'birch','scope':'simulator','source_ids':['source']})}
    score=evaluation.score_case(c,r)
    assert score['answer_correct'] and not score['grounded_correct'] and score['unsupported_claim']
    c['delivered_required_evidence']=True
    assert evaluation.score_case(c,r)['grounded_correct']
    r['result']=json.dumps({'value':'invented','scope':'simulator','source_ids':['source']})
    assert evaluation.score_case(c,r)['unsupported_claim']
    r['result']=json.dumps({'value':'birch','scope':'production','source_ids':['source']})
    assert evaluation.score_case(c,r)['scope_error']
    c['expected']={'value':None,'scope':'unknown','support':[]};c['spans']=[]
    assert evaluation.score_case(c,r)['unsupported_claim']
    r['result']=json.dumps({'value':None,'scope':'unknown','source_ids':[]})
    assert evaluation.score_case(c,r)['appropriate_uncertainty']
    r['result']='A personal fact outside the output contract'
    assert evaluation.score_case(c,r)['format_error']


def test_terminal_failure_cleans_worker_and_cannot_be_bypassed_by_resume(tmp_path,monkeypatch):
    import httpx
    fixture=json.loads(evaluation.FIXTURE.read_text())
    prepared=tmp_path/'prepared.json'
    prepared.write_text(json.dumps({'frozen':{'budget':fixture['budget'],'answer_model':fixture['answer_model'],
        'system_prompt':evaluation.SYSTEM},'cases':[{'case':'one','condition':'baseline','prompt':'fixture'}]}))
    state={'worker':None,'removed':False,'calls':0}
    def response(status,data):return httpx.Response(status,json=data,request=httpx.Request('GET','http://test'))
    class Client:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def request(self,method,path,**kwargs):
            if path=='/api/login':return response(200,{})
            if path=='/api/quota':return response(200,{'stale':False,'status':'allowed','five_hour':{'utilization':.1},'seven_day':{'utilization':.1}})
            if path=='/api/agents':state['worker']=kwargs['json'];return response(201,state['worker'])
            if path.startswith('/api/agents/'):
                if method=='DELETE':state['removed']=True;return response(200,{})
                return response(200,state['worker']) if state['worker'] else response(404,{})
            if path=='/api/runs':state['calls']+=1;return response(200,{'id':'fixture-run','state':'queued'})
            if path.endswith('/events'):return response(200,[])
            if path=='/api/runs/fixture-run':return response(200,{'state':'timed_out','started_at':None,'finished_at':None})
            raise AssertionError(path)
        def get(self,path,**kwargs):return self.request('GET',path,**kwargs)
        def post(self,path,**kwargs):return self.request('POST',path,**kwargs)
        def delete(self,path,**kwargs):return self.request('DELETE',path,**kwargs)
    monkeypatch.setattr(httpx,'Client',lambda **kwargs:Client());monkeypatch.setenv('AGENT_PLATFORM_ADMIN','fixture-only')
    args=SimpleNamespace(prepared=prepared,out=tmp_path/'answers',api_url='http://test')
    with pytest.raises(ValueError,match='terminal run'):evaluation.answer(args)
    assert state['removed'] and state['calls']==1
    with pytest.raises(ValueError,match='prior invalid'):evaluation.answer(args)
    assert state['calls']==1


def test_counted_messages_contains_only_frozen_prompt_and_cannot_retry_overspend(tmp_path,monkeypatch):
    import httpx
    loader=importlib.machinery.SourceFileLoader('memory_answer_messages',str(ROOT/'bin/ap-memory-answer-messages'))
    spec=importlib.util.spec_from_loader(loader.name,loader);module=importlib.util.module_from_spec(spec);loader.exec_module(module)
    case={'case':'one','condition':'baseline','prompt':'Synthetic task; no gold labels in this string.'}
    prepared=tmp_path/'prepared.json';prepared.write_text(json.dumps({'frozen':{
        'answer_model':'claude-sonnet-5-5','system_prompt':'Use evidence.'},'cases':[case]}))
    requests=[]
    class Client:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def post(self,path,**kwargs):
            requests.append((path,kwargs['json']))
            data={'input_tokens':1} if path.endswith('count_tokens') else {'id':'fixture','model':'claude-sonnet-5-5',
                'content':[{'type':'text','text':'{}'}],'stop_reason':'end_turn','usage':{
                    'input_tokens':100,'output_tokens':1,'cache_read_input_tokens':0,'cache_creation_input_tokens':0}}
            return httpx.Response(200,json=data,request=httpx.Request('POST','http://localhost'+path))
    monkeypatch.setattr(httpx,'Client',lambda **kwargs:Client());monkeypatch.setattr(module,'quota',lambda url:{'status':'allowed'})
    args=SimpleNamespace(prepared=prepared,out=tmp_path/'out',proxy_url='http://127.0.0.1:1',api_url='http://test',command='count')
    module.run(args);args.command='run'
    with pytest.raises(ValueError,match='actual usage'):module.run(args)
    assert len(requests)==2 and requests[-1][1]['max_tokens']==256 and 'tools' not in requests[-1][1]
    assert requests[-1][1]['messages']==[{'role':'user','content':case['prompt']}]
    with pytest.raises(ValueError,match='prior failed'):module.run(args)
    assert len(requests)==2
