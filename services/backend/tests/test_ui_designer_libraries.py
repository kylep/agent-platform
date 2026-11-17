import copy

import pytest

from agentplatform.ui_designer.libraries import plan_import, plan_update, canonical_hash
from agentplatform.ui_designer.schema import validate_document, resolve_tokens, iter_token_refs
from agentplatform.ui_designer.errors import UiDesignerError
from .test_ui_designer_component_tools import component_document

DEST='a'*32
SOURCE='b'*32
THIRD='c'*32
EMPTY={'schema':1,'pages':[]}


def source():
    doc=component_document()
    doc['tokens']={'color':{'ink':'#112233','unused':'#eeeeee'}}
    for state in ('normal','hover'):
        label=doc['components']['button-'+state]['children'][0]
        label.update(color='$color.ink',text='literal $color.ink',meta={'literal':'button-normal'})
    doc['component_sets']['button']['properties']['label']['default']='$color.ink'
    doc['components']['wrapper']={'id':'wrapper','type':'frame','w':160,'h':60,'children':[
        {'id':'wrapped','type':'instance','of':'button','props':{'label':'button-normal $color.ink','icon':'check'},
         'overrides':{'normal-label.text':'literal button-normal $color.ink','normal-label.color':'$color.ink'}}]}
    return doc


def imported(src=None, dest=None, **kwargs):
    return plan_import(dest or EMPTY,src or source(),destination_id=DEST,source_id=SOURCE,source_version=1,
        source_slug='source',namespace='lib',components=['wrapper'],component_sets=[],tokens=[],**kwargs)


def test_dependency_closure_and_semantic_remapping_preserve_literals():
    result=imported(); doc=result['doc']; pin=doc['libraries']['lib']; ids=pin['maps']['ids']; tokens=pin['maps']['tokens']
    validate_document(doc)
    assert result['report']['counts']=={'components':5,'component_sets':1,'tokens':1,'assets':0,'added':7,'updated':0,'removed':0,'conflicts':0}
    wrapper=doc['components'][ids['wrapper']]['children'][0]
    assert wrapper['of']==ids['button']
    assert wrapper['props']['icon']==ids['check']
    assert wrapper['props']['label']=='button-normal $color.ink'
    assert wrapper['overrides'][ids['normal-label']+'.text']=='literal button-normal $color.ink'
    assert wrapper['overrides'][ids['normal-label']+'.color']=='$color.'+tokens['color.ink']
    definition=doc['component_sets'][ids['button']]
    assert definition['properties']['label']['default']=='$color.ink'
    assert definition['properties']['icon']['default']==ids['arrow']
    assert set(definition['properties']['icon']['allowed'])=={ids['arrow'],ids['check']}
    assert definition['variants'][0]['bindings']['label']==[ids['normal-label']]
    label=doc['components'][ids['button-normal']]['children'][0]
    assert label['text']=='literal $color.ink' and label['meta']=={'literal':'button-normal'}
    assert label['color']=='$color.'+tokens['color.ink']
    assert source()['components']['wrapper']['children'][0]['of']=='button'


def test_hashes_and_remaps_are_stable_across_update_and_local_change_conflicts():
    first=imported()['doc']; previous=copy.deepcopy(first)
    newer=source(); newer['components']['button-normal']['fill']='#abcdef'
    plan=plan_update(first,newer,destination_id=DEST,library_id='lib',source_version=2,source_slug='renamed')
    assert plan['doc']['libraries']['lib']['maps']==first['libraries']['lib']['maps']
    assert plan['doc']['libraries']['lib']['source_slug']=='renamed'
    assert first==previous
    local=copy.deepcopy(first)
    component=next(iter(local['libraries']['lib']['owned']['components']))
    local['components'][component]['title']='local change'
    conflict=plan_update(local,newer,destination_id=DEST,library_id='lib',source_version=2,source_slug='source')
    assert conflict['report']['conflicts'][0]['kind']=='local-modification'
    assert conflict['doc']==local
    assert not conflict['assets']


def test_update_refuses_removal_that_breaks_local_consumers_atomically():
    first=imported()['doc']; ids=first['libraries']['lib']['maps']['ids']
    first['pages']=[{'id':'page','children':[{'id':'local','type':'instance','of':ids['check']}]}]
    newer=source()
    newer['component_sets']['button']['properties']['icon']['allowed']=['arrow']
    newer['components']['wrapper']['children'][0]['props']['icon']='arrow'
    plan=plan_update(first,newer,destination_id=DEST,library_id='lib',source_version=2,source_slug='source')
    assert plan['report']['conflicts'] and plan['doc']==first
    assert ids['check'] in first['components']


def test_assets_are_collected_by_content_hash_without_rewriting_bytes():
    src=source(); sha='e'*64
    src['components']['arrow']['children'].append({'id':'pic','type':'image','asset':sha,'w':10,'h':10})
    result=imported(src)
    assert result['assets']=={sha}
    ids=result['doc']['libraries']['lib']['maps']['ids']
    assert result['doc']['components'][ids['arrow']]['children'][-1]['asset']==sha


def test_maximum_length_ids_and_tokens_remain_valid_and_distinct():
    a='a'*48; b='a'*47+'b'; t='T'*48
    src={'schema':1,'tokens':{'color':{t:'#123456'}},'components':{
        a:{'type':'rect','w':1,'h':1,'fill':'$color.'+t},b:{'type':'rect','w':1,'h':1}},'pages':[]}
    result=plan_import(EMPTY,src,destination_id=DEST,source_id=SOURCE,source_version=1,source_slug='source',
        namespace='n'*16,components=[a,b],component_sets=[],tokens=[])
    maps=result['doc']['libraries']['n'*16]['maps']
    assert len(set(maps['ids'].values()))==2
    assert all(len(name)<=48 for group in maps.values() for name in group.values())
    validate_document(result['doc'])


def test_collision_with_unowned_content_is_refused():
    first=imported(); dest=copy.deepcopy(EMPTY)
    key=next(iter(first['doc']['components']))
    dest['pages']=[{'id':'p','children':[{'id':key,'type':'rect','w':1,'h':1}]}]
    with pytest.raises(UiDesignerError): imported(dest=dest)


def test_explicit_tokens_only_and_rich_text_token_dependencies():
    src={'schema':1,'tokens':{'color':{'ink':'#123456'}},'components':{'text':{'type':'text',
         'text':[{'text':'literal $color.ink','color':'$color.ink'}]}},'pages':[]}
    result=plan_import(EMPTY,src,destination_id=DEST,source_id=SOURCE,source_version=1,source_slug='source',
        namespace='lib',components=['text'],component_sets=[],tokens=[])
    pin=result['doc']['libraries']['lib']
    run=result['doc']['components'][pin['maps']['ids']['text']]['text'][0]
    assert run['text']=='literal $color.ink'
    assert run['color']=='$color.'+pin['maps']['tokens']['color.ink']
    only=plan_import(EMPTY,src,destination_id=DEST,source_id=SOURCE,source_version=1,source_slug='source',
        namespace='tokens',components=[],component_sets=[],tokens=['color.ink'])
    assert only['report']['counts']['components']==0


def test_mode_names_must_match_or_empty_destination_adopts():
    src=source(); src['modes']=['dark','light']; src['tokens']['color']['ink']={'dark':'#112233','light':'#000000'}
    result=imported(src)
    assert result['doc']['modes']==['dark','light']
    nonempty={'schema':1,'pages':[{'id':'p','children':[{'id':'rect','type':'rect','w':1,'h':1}]}]}
    with pytest.raises(UiDesignerError): imported(src,nonempty)
    matching=copy.deepcopy(nonempty); matching['modes']=['dark','light']
    assert imported(src,matching)['doc']['modes']==['dark','light']


def test_foreign_prototype_links_are_refused_without_silent_changes():
    src=source(); src['pages'][0]['children'].append({'id':'destination','type':'frame','w':10,'h':10})
    src['components']['arrow']['link_to']={'target':'destination'}
    with pytest.raises(UiDesignerError,match='foreign page frame'): imported(src)


def test_direct_and_selected_transitive_document_cycles_refused():
    with pytest.raises(UiDesignerError):
        plan_import(EMPTY,source(),destination_id=SOURCE,source_id=SOURCE,source_version=1,source_slug='source',
                    namespace='lib',components=['wrapper'],component_sets=[],tokens=[])
    consumer=imported()['doc']
    imported_root=consumer['libraries']['lib']['maps']['ids']['wrapper']
    with pytest.raises(UiDesignerError,match='transitive'):
        plan_import(EMPTY,consumer,destination_id=SOURCE,source_id=DEST,source_version=2,source_slug='consumer',
                    namespace='back',components=[imported_root],component_sets=[],tokens=[])


def test_unselected_library_ancestry_does_not_create_false_cycles():
    consumer=imported()['doc']; consumer['components']['independent']={'id':'independent','type':'rect','w':1,'h':1}
    result=plan_import(EMPTY,consumer,destination_id=SOURCE,source_id=DEST,source_version=2,source_slug='consumer',
                      namespace='back',components=['independent'],component_sets=[],tokens=[])
    assert result['doc']['libraries']['back']['ancestry']==[DEST]


def test_metadata_closed_and_hashes_are_conflict_markers_not_edit_locks():
    doc=imported()['doc']; pin=doc['libraries']['lib']; key=next(iter(pin['owned']['components']))
    doc['components'][key]['title']='allowed local edit'
    validate_document(doc)
    broken=copy.deepcopy(doc); broken['libraries']['lib']['unexpected']=True
    with pytest.raises(UiDesignerError): validate_document(broken)
    assert resolve_tokens(doc)['libraries']==doc['libraries']
    assert not any('.libraries' in path for path,_ in iter_token_refs(doc))


def test_source_deletion_does_not_affect_copied_rendering():
    result=imported(); doc=result['doc']; source_doc=source(); source_doc.clear()
    from agentplatform.ui_designer.layout import layout_document
    key=doc['libraries']['lib']['maps']['ids']['wrapper']
    doc['pages']=[{'id':'p','children':[{'id':'local','type':'instance','of':key}]}]
    tree=layout_document(doc)
    assert tree['pages'][0]['children'][0]['children']


def test_unchanged_source_content_update_is_not_reported_as_definition_change():
    first=imported()['doc']
    result=plan_update(first,source(),destination_id=DEST,library_id='lib',source_version=2,source_slug='source')
    assert result['report']['updated']==[]
    assert result['report']['counts']['updated']==0
    assert result['report']['added']==result['report']['removed']==[]
    assert result['doc']['libraries']['lib']['source_version']==2


@pytest.mark.parametrize('change',[
    lambda p:p.update(source_version=True),
    lambda p:p.update(source_id='bad'),
    lambda p:p.update(namespace='other'),
    lambda p:p.update(ancestry=[]),
    lambda p:p['maps'].update(ids=[]),
    lambda p:p['owned']['components'].update({'bad':'not-a-hash'}),
])
def test_metadata_wrong_shapes_are_structured_validation_errors(change):
    doc=imported()['doc']; change(doc['libraries']['lib'])
    with pytest.raises(UiDesignerError): validate_document(doc)


def test_library_provenance_get_is_bounded_and_cursor_can_read_entire_pin():
    from agentplatform.ui_designer.views import cut_document,size
    doc=imported()['doc']
    first=cut_document(doc,node=None,depth=0,after=None,budget=2048)
    assert first['truncated'] and first['cursor']=={'node':'$libraries'}
    assert first['doc']['libraries']['_cut']
    assert size(first)<4096  # ordinary node properties have an existing floor
    after=None; entries=[]; rounds=0
    while True:
        page=cut_document(doc,node='$libraries',depth=0,after=after,budget=1024)
        assert size(page)<1024
        entries.extend(page['doc']['libraries']['_entries'])
        rounds+=1
        if page['cursor'] is None: break
        after=page['cursor']['after']
        assert rounds<100
    assert any(e['path']==['lib','source_id'] and e['value']==SOURCE for e in entries)
    assert any(e['path']==['lib','source_version'] and e['value']==1 for e in entries)
    full=cut_document(doc,node='$libraries',depth=0,after=None,budget=32000)
    assert full['doc']['libraries']==doc['libraries'] and not full['truncated']
