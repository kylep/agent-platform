"""Agent edit/get/inspect contract for native component sets."""
import copy
import json

import pytest

from agentplatform.ui_designer.errors import UiDesignerError
from agentplatform.ui_designer.ops import apply_ops
from agentplatform.ui_designer.schema import normalize_document, validate_document
from agentplatform.ui_designer.views import cut_document, inspect_node, size


def component_document():
    """Complete live-API fixture: two variants, text/boolean/swap properties."""
    components = {}
    for name, color in [('arrow', '#f00'), ('check', '#0f0')]:
        components[name] = {'type': 'frame', 'w': 12, 'h': 12,
                            'children': [{'id': name + '-shape', 'type': 'rect', 'w': 10, 'h': 10, 'fill': color}]}
    variants = []
    for state, color in [('normal', '#eee'), ('hover', '#ddd')]:
        components['button-' + state] = {'type': 'frame', 'w': 140, 'h': 40, 'fill': color,
            'children': [{'id': state + '-label', 'type': 'text', 'text': 'Base', 'x': 8, 'y': 8},
                         {'id': state + '-icon', 'type': 'instance', 'of': 'arrow', 'x': 112, 'y': 12}]}
        variants.append({'when': {'state': state}, 'of': 'button-' + state,
                         'bindings': {'label': [state + '-label'], 'show-icon': [state + '-icon'], 'icon': [state + '-icon']}})
    return normalize_document({'schema': 1, 'title': 'Component demo', 'components': components,
        'component_sets': {'button': {'axes': {'state': ['normal', 'hover']}, 'properties': {
            'label': {'type': 'text', 'default': 'Play'},
            'show-icon': {'type': 'boolean', 'default': True},
            'icon': {'type': 'instance-swap', 'default': 'arrow', 'allowed': ['arrow', 'check']}},
            'variants': variants}},
        'pages': [{'id': 'page', 'children': [{'id': 'button-instance', 'type': 'instance', 'of': 'button'}]}]})


def test_metadata_op_replaces_whole_definition_and_instance_update():
    doc = component_document()
    validate_document(doc)
    changed = copy.deepcopy(doc['component_sets']['button'])
    changed['properties']['label']['default'] = 'Launch'
    result = apply_ops(doc, [{'op': 'component_sets', 'set': {'button': changed}},
        {'op': 'update', 'id': 'button-instance', 'set': {'variant': {'state': 'hover'},
         'props': {'label': '$color.literal', 'show-icon': False, 'icon': 'check'}}}], version=1, expected_version=1)
    assert result.doc['component_sets']['button']['properties']['label']['default'] == 'Launch'
    assert result.doc['pages'][0]['children'][0]['props']['label'] == '$color.literal'
    assert doc['component_sets']['button']['properties']['label']['default'] == 'Play'


def test_remove_used_definition_is_atomic_and_unused_definition_can_be_removed():
    doc = component_document()
    before = copy.deepcopy(doc)
    with pytest.raises(UiDesignerError):
        apply_ops(doc, [{'op': 'component_sets', 'set': {'button': None}}], version=1, expected_version=1)
    assert doc == before
    result = apply_ops(doc, [{'op': 'remove', 'ids': ['button-instance']},
        {'op': 'component_sets', 'set': {'button': None}}], version=1, expected_version=1)
    assert result.doc['component_sets'] == {}


@pytest.mark.parametrize('op', [
    {'op': 'component_sets', 'set': []},
    {'op': 'component_sets', 'set': {'button': False}},
    {'op': 'component_sets', 'set': {}, 'extra': True},
])
def test_metadata_op_rejects_wrong_shapes(op):
    with pytest.raises(UiDesignerError):
        apply_ops(component_document(), [op], version=1, expected_version=1)


def test_large_metadata_uses_cursor_including_single_large_literal():
    doc = {'schema': 1, 'pages': [], 'component_sets': {'large': {'literal': '界' * 10000}}}
    whole = cut_document(doc, node=None, depth=1, after=None, budget=2048)
    assert whole['truncated'] and whole['cursor'] == {'node': '$component_sets'}
    assert size(whole) < 2048
    after, chunks, iterations = None, [], 0
    while True:
        page = cut_document(doc, node='$component_sets', depth=1, after=after, budget=2048)
        assert size(page) < 2048
        chunks.extend(e['value'] for e in page['doc']['component_sets']['_entries'])
        iterations += 1
        if not page['cursor']:
            break
        after = page['cursor']['after']
        assert iterations < 100
    assert ''.join(chunks) == '界' * 10000


def test_inspect_exposes_effective_selection_and_keeps_authored_true():
    doc = component_document()
    from agentplatform.ui_designer.layout import layout_document
    tree = layout_document(doc)
    result = inspect_node(doc, 'button-instance', {None: tree})
    details = result['component_set']
    assert details['component_set'] == 'button'
    assert details['variant'] == {'state': 'normal'}
    assert details['properties']['label']['value'] == 'Play'
    assert details['properties']['icon']['bindings'] == ['normal-icon']
    assert result['authored'] == doc['pages'][0]['children'][0]


async def test_component_set_edit_and_inspect_through_route(admin_client):
    import httpx
    from agentplatform.ui_designer.layout import layout_document
    from agentplatform.ui_designer.render_client import RenderClient
    def render(request):
        body = json.loads(request.content)
        tree = layout_document(body['doc'], body.get('mode'))
        tree.pop('glyphs')
        return httpx.Response(200, json={'tree': tree})
    admin_client._transport.app.state.ui_designer_renderer = RenderClient('http://renderer',
        client=httpx.AsyncClient(transport=httpx.MockTransport(render)))
    prefix = '/api/ui-designer/docs'
    created = await admin_client.post(prefix, json={'slug': 'component-demo', 'doc': component_document()})
    assert created.status_code == 200, created.text
    edited = await admin_client.post(prefix + '/component-demo/edit', json={'expected_version': 1,
        'ops': [{'op': 'update', 'id': 'button-instance', 'set': {
            'variant': {'state': 'hover'}, 'props': {'label': 'Start', 'show-icon': False, 'icon': 'check'}}}]})
    assert edited.status_code == 200, edited.text
    inspected = await admin_client.get(prefix + '/component-demo/inspect', params={'node': 'button-instance'})
    assert inspected.status_code == 200, inspected.text
    detail = inspected.json()['component_set']
    assert detail['axes'] == {'state': ['normal', 'hover']}
    assert detail['variant'] == {'state': 'hover'}
    assert detail['properties']['label']['value'] == 'Start'
    assert detail['properties']['show-icon']['value'] is False
    assert detail['properties']['icon']['value'] == 'check'
    assert inspected.json()['authored']['props']['label'] == 'Start'


@pytest.mark.parametrize('after', ['bad', '-1', '9' * 10000, '1000000'])
def test_metadata_cursor_invalid_inputs_are_typed_errors(after):
    with pytest.raises(UiDesignerError):
        cut_document({'component_sets': {'a': {'axes': {}}}}, node='$component_sets',
                     depth=0, after=after, budget=2048)


def test_inspect_typed_text_property_is_literal_not_token():
    doc = component_document()
    doc['pages'][0]['children'][0]['props'] = {'label': '$color.literal'}
    from agentplatform.ui_designer.layout import layout_document
    result = inspect_node(doc, 'button-instance', {None: layout_document(doc)})
    assert result['tokens'] == []
    assert result['component_set']['properties']['label']['value'] == '$color.literal'


def test_insert_generated_id_reserves_component_set_names():
    result = apply_ops(component_document(), [{'op': 'insert', 'parent': 'page',
        'node': {'type': 'rect', 'title': 'Button', 'w': 10, 'h': 10}}],
        version=1, expected_version=1)
    assert result.doc['pages'][0]['children'][-1]['id'] == 'button-2'


def test_define_component_rejects_existing_component_set_name_atomically():
    doc = component_document()
    before = copy.deepcopy(doc)
    with pytest.raises(UiDesignerError):
        apply_ops(doc, [{'op': 'insert', 'parent': 'page', 'node': {'id': 'new-frame', 'type': 'frame'}},
            {'op': 'define_component', 'id': 'button', 'from': 'new-frame'}], version=1, expected_version=1)
    assert doc == before
