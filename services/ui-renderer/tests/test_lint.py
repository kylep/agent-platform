import copy
import json

import pytest

import app as renderer
from agentplatform.ui_designer.lint import findings
from agentplatform.ui_designer.errors import UiDesignerError
from test_app import client  # noqa: F401


def document(color='#777777',size=14,weight=400):
    return {'schema':1,'pages':[{'id':'p','children':[{'id':'root','type':'frame','w':200,'h':100,'fill':'#ffffff',
        'children':[{'id':'text','type':'text','text':'Readable','w':160,'h':60,'color':color,
                     'style':{'size':size,'weight':weight}}]}]}]}


def codes(result): return [f['code'] for f in result['findings']]


@pytest.mark.parametrize('size,weight,warning',[(14,400,True),(24,400,False),(23.999,400,True),
    (56/3,700,False),(18.66,700,True),(20,600,True)])
def test_contrast_size_and_bold_thresholds(size,weight,warning):
    result=findings(document(size=size,weight=weight))
    assert ('UD-LINT-CONTRAST' in codes(result)) is warning


def test_positive_contrast_and_alpha_foreground_compositing():
    assert 'UD-LINT-CONTRAST' not in codes(findings(document('#000000')))
    result=findings(document('#00000080'))
    warning=next(f for f in result['findings'] if f['code']=='UD-LINT-CONTRAST')
    assert '4.00:1' in warning['message'] and warning['approximate']


def test_alpha_ancestor_fill_is_composited_against_known_parent():
    doc=document('#ffffff')
    doc['pages'][0]['children'][0]['children']=[{'id':'inner','type':'frame','w':200,'h':100,'fill':'#00000080',
         'children':doc['pages'][0]['children'][0]['children']}]
    result=findings(doc)
    warning=next(f for f in result['findings'] if f['code']=='UD-LINT-CONTRAST')
    assert '4.00:1' in warning['message']


def test_unknown_gradient_background_does_not_claim_numeric_contrast():
    doc=document()
    doc['pages'][0]['children'][0]['fill']={'gradient':'linear','stops':[[0,'#000000'],[1,'#ffffff']]}
    result=findings(doc)
    assert 'UD-LINT-CONTRAST-UNKNOWN' in codes(result) and 'UD-LINT-CONTRAST' not in codes(result)


def test_overlapping_earlier_sibling_background_is_unknown_but_separate_sibling_is_safe():
    doc=document()
    root=doc['pages'][0]['children'][0]
    root['children'].insert(0,{'id':'behind','type':'rect','w':160,'h':60,'fill':'#000000'})
    assert 'UD-LINT-CONTRAST-UNKNOWN' in codes(findings(doc))
    root['children'][0]['x']=170
    assert 'UD-LINT-CONTRAST' in codes(findings(doc))


def test_ancestor_group_opacity_is_reported_as_unknown():
    doc=document()
    doc['pages'][0]['children'][0]['opacity']=.5
    assert 'UD-LINT-CONTRAST-UNKNOWN' in codes(findings(doc))


def test_overflow_clipping_spacing_and_token_hygiene():
    doc=document('#112233')
    doc['tokens']={'color':{'ink':'#112233','unused':'#abcdef'}}
    root=doc['pages'][0]['children'][0]
    root.update(clip=True,layout={'dir':'row','gap':6})
    root['children'][0].update(x=150,h=5)
    result=findings(doc)
    assert {'UD-LINT-TEXT-OVERFLOW','UD-LINT-XY-IN-LAYOUT','UD-LINT-SPACING-GRID',
            'UD-LINT-COLOR-TOKEN','UD-LINT-TOKEN-UNUSED'} <= set(codes(result))
    root.pop('layout'); root['children'][0]['h']=60
    assert 'UD-LINT-CLIPPED' in codes(findings(doc))


def test_literal_text_is_not_a_token_reference_and_valid_tokens_are_used():
    doc=document()
    doc['tokens']={'color':{'ink':'#777777'},'space':{'gap':8}}
    root=doc['pages'][0]['children'][0]
    root['layout']={'gap':'$space.gap'}
    root['children'][0]['text']='$color.ink'
    result=findings(doc)
    unused=[f['message'] for f in result['findings'] if f['code']=='UD-LINT-TOKEN-UNUSED']
    assert any('$color.ink' in line for line in unused)
    assert not any('$space.gap' in line for line in unused)
    assert 'UD-LINT-SPACING-GRID' not in codes(result)


def test_mode_selection_and_missing_selection_are_explicit():
    doc=document(); doc['modes']=['dark','light']
    doc['tokens']={'color':{'ink':{'dark':'#777777','light':'#000000'}}}
    doc['pages'][0]['children'][0]['children'][0]['color']='$color.ink'
    assert 'UD-LINT-CONTRAST' in codes(findings(doc,'dark'))
    assert 'UD-LINT-CONTRAST' not in codes(findings(doc,'light'))
    with pytest.raises(UiDesignerError): findings(doc,'unknown')
    with pytest.raises(UiDesignerError): findings(doc,node='missing')


def test_findings_work_and_response_are_bounded():
    doc=document()
    doc['tokens']={'color':{f'c{i}':'#123456' for i in range(2500)}}
    result=findings(doc)
    assert len(result['findings'])<=2000 and result['total']>=2500 and result['truncated']
    assert len(json.dumps(result,ensure_ascii=False).encode())<=2<<20
    assert all(len(f[key].encode())<=500 for f in result['findings'] for key in ('message','suggestion'))


def test_lint_cache_hits_invalidates_modes_and_defensively_copies(tmp_path,monkeypatch):
    import agentplatform.ui_designer.lint as engine
    renderer._LINT_CACHE.clear()
    original=engine.findings; calls=[]
    def call(*args): calls.append(True); return original(*args)
    monkeypatch.setattr(engine,'findings',call)
    doc=document(); doc['modes']=['dark','light']
    req=renderer._lint_req({'doc':doc,'mode':'dark'})
    first=renderer.job_lint(renderer.Config(),req)
    first['findings'].clear()
    assert renderer.job_lint(renderer.Config(),req)['findings']
    assert len(calls)==1
    renderer.job_lint(renderer.Config(),{**req,'mode':'light'})
    assert len(calls)==2
    assert len(renderer._LINT_CACHE)<=8
    assert sum(item[1] for item in renderer._LINT_CACHE.values())<=32<<20


@pytest.mark.parametrize('extra',[{'node':''},{'node':3},{'all_modes':True},{'mode':3}])
def test_lint_request_closed(extra):
    with pytest.raises(renderer.BadRequest): renderer._lint_req({'doc':document(),**extra})


async def test_lint_http_normalized_transport(client):
    response=await client.post('/lint',json={'doc':document()})
    assert response.status==200, await response.text()
    result=await response.json()
    assert result['rules_version']=='lint-1' and result['renderer_version']
    warning=next(f for f in result['findings'] if f['code']=='UD-LINT-CONTRAST')
    assert set(warning)=={'code','severity','node_id','mode','message','suggestion','approximate'}
    assert warning['node_id']=='text' and warning['severity']=='warning'


def test_cache_retains_all_eight_modes_for_repeated_pages(monkeypatch):
    import agentplatform.ui_designer.lint as engine
    renderer._LINT_CACHE.clear()
    original=engine.findings; calls=[]
    def call(*args): calls.append(True); return original(*args)
    monkeypatch.setattr(engine,'findings',call)
    doc=document(); doc['modes']=[f'm{i}' for i in range(8)]
    for mode in doc['modes']: renderer.job_lint(renderer.Config(),{'doc':doc,'mode':mode,'node':None})
    for mode in doc['modes']: renderer.job_lint(renderer.Config(),{'doc':doc,'mode':mode,'node':None})
    assert len(calls)==8 and len(renderer._LINT_CACHE)==8


def test_component_set_variants_are_not_incorrectly_reported_as_unused():
    doc=document()
    doc['components']={'base':{'id':'base','type':'frame','w':10,'h':10}}
    doc['component_sets']={'set':{'axes':{},'variants':[{'when':{},'of':'base','bindings':{}}]}}
    doc['pages'][0]['children'].append({'id':'instance','type':'instance','of':'set','x':300})
    assert not any(f['code']=='UD-LINT-COMPONENT-UNUSED' and f['node_id']=='base' for f in findings(doc)['findings'])
