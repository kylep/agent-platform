import copy

import pytest

from agentplatform.ui_designer.layout import layout_document
from agentplatform.ui_designer.schema import validate_document
from agentplatform.ui_designer.errors import UiDesignerError
from agentplatform.ui_designer.export_formats import html_document


def scene(children,**frame):
    return {'schema':1,'pages':[{'id':'p','children':[{'id':'grid','type':'frame','children':children,**frame}]}]}


def rect(id,w='fill',h='fill',**fields):
    return {'id':id,'type':'rect','w':w,'h':h,'fill':'#123456',**fields}


def root(doc): return layout_document(doc)['pages'][0]['children'][0]


def test_equal_columns_gap_padding_and_row_height_distribution():
    doc=scene([rect(f'r{i}') for i in range(4)],w=220,h=160,
        layout={'dir':'grid','columns':2,'gap':3,'column_gap':10,'row_gap':20,'pad':10,'align':'stretch'})
    validate_document(doc)
    nodes=root(doc)['children']
    assert [(n['x'],n['y'],n['w'],n['h']) for n in nodes]==[(10,10,95,60),(115,10,95,60),(10,90,95,60),(115,90,95,60)]


def test_default_columns_hug_tracks_and_bounded_text_wrapping():
    doc=scene([rect('a',10,10),rect('b',20,20),rect('c',30,30)],layout={'dir':'grid','gap':5})
    resolved=root(doc)
    assert (resolved['w'],resolved['h'])==(65,55)
    assert resolved['children'][1]['x']==35 and resolved['children'][2]['y']==25
    text=[{'id':f't{i}','type':'text','text':'Some words with care','w':'fill','style':{'size':14}} for i in range(2)]
    wrapped=root(scene(text,w=132,layout={'dir':'grid','gap':12}))
    assert all(n['w']==60 and n['h']>30 for n in wrapped['children'])
    assert wrapped['h']==max(n['h'] for n in wrapped['children'])


def test_grid_alignment_fixed_overflow_and_child_clamp():
    doc=scene([rect('a',20,10),rect('b',150,10,max_w=80)],w=220,h=100,
        layout={'dir':'grid','gap':20,'align':'center'})
    resolved=root(doc)
    assert [(n['x'],n['y'],n['w']) for n in resolved['children']]==[(40,45,20),(130,45,80)]
    doc['pages'][0]['children'][0]['children'][1].pop('max_w')
    assert root(doc)['children'][1]['w']==150


def test_absolute_mask_and_hidden_children_do_not_consume_cells():
    doc=scene([rect('mask',20,20),rect('a',20,20),rect('hidden',20,20,visible=False),rect('b',20,20),
               rect('overlay',20,20,absolute=True,x=177,y=5)],w=220,h=100,mask='alpha',
              layout={'dir':'grid','gap':20})
    nodes={n['id']:n for n in root(doc)['children']}
    assert nodes['a']['x']==0 and nodes['b']['x']==120
    assert nodes['hidden']['w']==nodes['hidden']['h']==0
    assert (nodes['overlay']['x'],nodes['overlay']['y'])==(177,5)


def test_minimum_hug_box_remeasures_fill_children_and_grid_tracks():
    doc=scene([rect('child')],min_w=200,min_h=100)
    resolved=root(doc)
    assert (resolved['w'],resolved['h'])==(200,100)
    assert (resolved['children'][0]['w'],resolved['children'][0]['h'])==(200,100)
    doc['pages'][0]['children'][0]['layout']={'dir':'grid','columns':2,'gap':20}
    assert root(doc)['children'][0]['w']==90


def test_numeric_clamp_preserves_authored_size_and_limits_override_allocation():
    doc=scene([rect('a',max_w=80),rect('b')],w=220,h=100,layout={'dir':'row','gap':10})
    original=copy.deepcopy(doc)
    resolved=root(doc)
    assert [n['w'] for n in resolved['children']]==[80,105]
    assert resolved['children'][1]['x']==90
    assert doc==original
    fixed=scene([],w=500,h=100,max_w=400)
    assert root(fixed)['w']==400 and fixed['pages'][0]['children'][0]['w']==500
    minimum=scene([rect('large',min_w=200)],w=100,h=100,layout={'dir':'row'})
    assert root(minimum)['children'][0]['w']==200


def test_max_width_reflows_text_and_max_height_clamps_final_box():
    doc=scene([{'id':'body','type':'text','text':'A paragraph with enough words to wrap onto more than one line.',
               'w':'fill','style':{'size':16}}],max_w=120,max_h=40)
    resolved=root(doc)
    assert resolved['w']==120 and resolved['h']==40
    assert len(resolved['children'][0]['text']['lines'])>1


def instance_scene(**instance):
    return {'schema':1,'components':{'base':{'id':'base','type':'frame','w':300,'h':100,
        'children':[rect('stretch',100,20,x=10,max_w=120,constraints={'horizontal':'stretch'}),
                    rect('end',20,20,x=250,constraints={'horizontal':'end'})]}},
        'pages':[{'id':'p','children':[{'id':'copy','type':'instance','of':'base',**instance}]}]}


def test_instance_pins_use_effective_size_and_never_mutate_shared_component():
    doc=instance_scene(w=500,max_w=400)
    before=copy.deepcopy(doc)
    resolved=root(doc)
    assert resolved['w']==400
    assert resolved['children'][0]['w']==120
    assert resolved['children'][1]['x']==350
    assert doc==before
    assert doc['components']['base']['children'][0]['w']==100


def test_instance_root_limits_overrides_and_descendant_geometry_overrides_win():
    doc=instance_scene(overrides={'w':500,'max_w':400,'end.x':5})
    resolved=root(doc)
    assert resolved['w']==400 and resolved['children'][0]['w']==120
    assert resolved['children'][1]['x']==5
    doc=instance_scene(w=500)
    doc['components']['base']['max_w']=400
    assert root(doc)['w']==400 and root(doc)['children'][1]['x']==350


def test_instance_token_geometry_resolves_before_pin_helper():
    doc=instance_scene(w=400)
    doc['tokens']={'space':{'width':300,'edge':250}}
    doc['components']['base']['w']='$space.width'
    doc['components']['base']['children'][1]['x']='$space.edge'
    assert root(doc)['children'][1]['x']==350


def test_instance_unknown_resized_pin_axis_refuses_instead_of_ignoring():
    with pytest.raises(UiDesignerError): root(instance_scene(w='fill'))
    doc=instance_scene(w='fill')
    doc['components']['base']['w']='hug'
    with pytest.raises(UiDesignerError): root(doc)


def test_nested_instance_pins_and_component_set_selected_variant():
    doc=instance_scene(w=400)
    doc['components']['outer']={'id':'outer','type':'frame','w':300,'h':100,'children':[
        {'id':'inner','type':'instance','of':'base','w':300,'h':100,'constraints':{'horizontal':'stretch'}}]}
    doc['component_sets']={'family':{'axes':{},'variants':[{'when':{},'of':'outer','bindings':{}}]}}
    doc['pages'][0]['children'][0].update(of='family',w=400)
    validate_document(doc)
    resolved=root(doc)['children'][0]
    assert resolved['w']==400 and resolved['children'][1]['x']==350


def test_grid_html_uses_resolved_dom_geometry_without_new_renderer_features():
    doc=scene([rect('a'),rect('b')],w=220,h=100,layout={'dir':'grid','gap':20,'align':'stretch'})
    data,_,_=html_document(doc,layout_document(doc),'grid',{},1<<20)
    assert b'data-node="a"' in data and b'left:120px' in data


def test_shared_instance_preflight_matches_renderer_and_never_bakes_mode_tokens():
    from agentplatform.ui_designer.constraints import validate_instance_resize
    doc=instance_scene(w=400)
    doc['modes']=['light','dark']
    doc['tokens']={'space':{'base-width':300,'edge':250}}
    doc['components']['base']['w']='$space.base-width'
    doc['components']['base']['children'][1]['x']='$space.edge'
    before=copy.deepcopy(doc)
    validate_instance_resize(doc,doc['pages'][0]['children'][0])
    assert root(doc)['children'][1]['x']==350
    assert doc==before
    invalid=instance_scene(w='fill')
    with pytest.raises(UiDesignerError): validate_instance_resize(invalid,invalid['pages'][0]['children'][0])
    with pytest.raises(UiDesignerError): root(invalid)


def test_nested_indeterminate_pins_have_same_save_and_render_refusal():
    from agentplatform.ui_designer.constraints import validate_instance_resize
    doc=instance_scene(w=400)
    doc['components']['base']['children']=[{'id':'unknown','type':'frame','w':'fill','h':100,'children':[
        rect('end',20,20,x=30,constraints={'horizontal':'end'})]}]
    with pytest.raises(UiDesignerError): validate_instance_resize(doc,doc['pages'][0]['children'][0])
    with pytest.raises(UiDesignerError): root(doc)
