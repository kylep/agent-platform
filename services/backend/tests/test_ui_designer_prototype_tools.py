"""Prototype links through atomic ops, REST history, inspect and resolved trees."""
import copy
import json

import httpx
import pytest

from agentplatform.ui_designer.errors import UiDesignerError
from agentplatform.ui_designer.layout import layout_document
from agentplatform.ui_designer.ops import apply_ops
from agentplatform.ui_designer.render_client import RenderClient
from agentplatform.ui_designer.schema import normalize_document, validate_document


def prototype_document():
    """Small complete fixture for live create/edit/inspect/play acceptance."""
    return normalize_document({'schema': 1, 'title': 'Prototype demo', 'pages': [{'id': 'page', 'children': [
        {'id': 'start', 'type': 'frame', 'title': 'Start', 'w': 320, 'h': 180, 'fill': '#eeeeee',
         'children': [{'id': 'next-button', 'type': 'rect', 'x': 80, 'y': 70, 'w': 160, 'h': 40,
                       'fill': '#3366cc', 'link_to': {'target': 'finish', 'trigger': 'tap',
                            'transition': 'dissolve', 'duration_ms': 200}},
                      {'id': 'start-label', 'type': 'text', 'text': 'Start', 'x': 20, 'y': 20}]},
        {'id': 'finish', 'type': 'frame', 'title': 'Finish', 'x': 400, 'w': 320, 'h': 180,
         'fill': '#ddffdd', 'children': [{'id': 'finish-label', 'type': 'text', 'text': 'Done', 'x': 20, 'y': 20}]}]}]})


def install_renderer(client):
    def render(request):
        body = json.loads(request.content)
        tree = layout_document(body['doc'], body.get('mode'))
        tree.pop('glyphs')
        return httpx.Response(200, json={'tree': tree})
    client._transport.app.state.ui_designer_renderer = RenderClient('http://renderer',
        client=httpx.AsyncClient(transport=httpx.MockTransport(render)))


def find_node(tree, identifier):
    if isinstance(tree, dict):
        if tree.get('id') == identifier:
            return tree
        for child in tree.values():
            found = find_node(child, identifier)
            if found is not None:
                return found
    elif isinstance(tree, list):
        for child in tree:
            found = find_node(child, identifier)
            if found is not None:
                return found
    return None


def test_delete_link_target_requires_clear_in_same_batch():
    doc = prototype_document()
    validate_document(doc)
    before = copy.deepcopy(doc)
    with pytest.raises(UiDesignerError):
        apply_ops(doc, [{'op': 'remove', 'ids': ['finish']}], version=1, expected_version=1)
    assert doc == before
    result = apply_ops(doc, [{'op': 'update', 'id': 'next-button', 'set': {'link_to': None}},
        {'op': 'remove', 'ids': ['finish']}], version=1, expected_version=1)
    assert [n['id'] for n in result.doc['pages'][0]['children']] == ['start']
    assert 'link_to' not in result.doc['pages'][0]['children'][0]['children'][0]


def test_component_inherits_link_and_legacy_root_override_can_replace_it():
    doc = prototype_document()
    doc['components'] = {'button': {'id': 'button', 'type': 'frame', 'w': 40, 'h': 20,
                                  'link_to': {'target': 'finish'}}}
    doc['pages'][0]['children'][0]['children'].extend([
        {'id': 'inherited', 'type': 'instance', 'of': 'button'},
        {'id': 'overridden', 'type': 'instance', 'of': 'button',
         'overrides': {'link_to': {'target': 'start', 'trigger': 'hover'}}}])
    validate_document(doc)
    tree = layout_document(doc)
    assert find_node(tree, 'inherited')['link_to'] == {'target': 'finish'}
    assert find_node(tree, 'overridden')['link_to'] == {'target': 'start', 'trigger': 'hover'}


async def test_prototype_real_api_update_inspect_and_historical_version(admin_client):
    install_renderer(admin_client)
    prefix = '/api/ui-designer/docs/prototype-demo'
    created = await admin_client.post('/api/ui-designer/docs', json={'slug': 'prototype-demo', 'doc': prototype_document()})
    assert created.status_code == 200, created.text
    changed = {'target': 'finish', 'trigger': 'after-delay', 'delay_ms': 1000,
               'transition': 'slide', 'duration_ms': 300}
    edited = await admin_client.post(prefix + '/edit', json={'expected_version': 1,
        'ops': [{'op': 'update', 'id': 'next-button', 'set': {'link_to': changed}}]})
    assert edited.status_code == 200, edited.text
    current = await admin_client.get(prefix + '/inspect', params={'node': 'next-button'})
    assert current.status_code == 200, current.text
    assert current.json()['version'] == 2
    assert current.json()['authored']['link_to'] == changed
    old = await admin_client.get(prefix + '/inspect', params={'node': 'next-button', 'version': 1})
    assert old.status_code == 200, old.text
    assert old.json()['version'] == 1
    assert old.json()['authored']['link_to']['trigger'] == 'tap'
    assert old.json()['authored']['link_to']['transition'] == 'dissolve'
    failed = await admin_client.post(prefix + '/edit', json={'expected_version': 2,
        'ops': [{'op': 'remove', 'ids': ['finish']}]})
    assert failed.status_code == 422, failed.text
    after = await admin_client.get(prefix + '/inspect', params={'node': 'next-button'})
    assert after.json()['version'] == 2 and after.json()['authored']['link_to'] == changed


@pytest.mark.parametrize('link', [
    {'target': 'https://example.com'},
    {'target': 'next-button'},
    {'target': 'finish', 'duration_ms': True},
    {'target': 'finish', 'trigger': 'tap', 'delay_ms': 1000},
    {'target': 'finish', 'trigger': 'after-delay', 'delay_ms': 99},
    {'target': 'finish', 'script': 'alert(1)'},
])
async def test_invalid_links_cannot_commit_via_api(admin_client, link):
    doc = prototype_document()
    made = await admin_client.post('/api/ui-designer/docs', json={'slug': 'prototype-demo', 'doc': doc})
    assert made.status_code == 200, made.text
    prefix = '/api/ui-designer/docs/prototype-demo'
    response = await admin_client.post(prefix + '/edit', json={'expected_version': 1,
        'ops': [{'op': 'update', 'id': 'next-button', 'set': {'link_to': link}}]})
    assert response.status_code == 422, response.text
    got = await admin_client.get(prefix)
    assert got.json()['version'] == 1
