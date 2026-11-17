"""Copied typography stays self-contained and never substitutes local fonts."""
import copy
import hashlib

from sqlalchemy import select

from agentplatform.ui_designer.libraries import plan_import, plan_update
from agentplatform.ui_designer import store
from agentplatform.db import UiDesignerFont, UiDesignerDocumentFont
from .test_ui_designer_api import P
from .test_ui_designer_libraries_api import setup_docs, source_doc, import_body, edit


SOURCE = 'b' * 32
DEST = 'a' * 32
REGULAR = {'family': 'Library Sans', 'weight': 400, 'sha256': '1' * 64}
BOLD = {'family': 'Library Sans', 'weight': 700, 'sha256': '2' * 64}
UNUSED = {'family': 'Unused Face', 'weight': 400, 'sha256': '3' * 64}


def source():
    return {'schema': 1, 'fonts': [REGULAR, BOLD, UNUSED], 'pages': [],
            'tokens': {'type': {'body': {'family': 'Library Sans', 'size': 16}}},
            'components': {'label': {'id': 'label', 'type': 'text', 'text': 'Hello',
                                      'style': '$type.body',
                                      'meta': {'family': 'Unused Face'}}}}


def imported(src=None, dest=None, components=None, tokens=None):
    return plan_import(dest or {'schema': 1, 'pages': []}, src or source(),
                       destination_id=DEST, source_id=SOURCE, source_version=1,
                       source_slug='source', namespace='shared',
                       components=['label'] if components is None else components,
                       component_sets=[], tokens=tokens or [])


def test_library_fonts_follow_selected_type_tokens_and_ignore_metadata():
    result = imported()
    assert result['doc']['fonts'] == [REGULAR, BOLD]
    assert result['fonts'] == {REGULAR['sha256'], BOLD['sha256']}
    assert result['report']['counts']['fonts'] == 2
    assert imported(components=[], tokens=['type.body'])['doc']['fonts'] == [REGULAR, BOLD]
    assert source()['fonts'] == [REGULAR, BOLD, UNUSED]


def test_library_fonts_include_rich_runs():
    src = source()
    src['components']['label']['style'] = {'family': 'Inter', 'size': 16}
    src['components']['label']['text'] = [{'text': 'Hello', 'style': {'family': 'Library Sans'}}]
    result = imported(src)
    assert result['fonts'] == {REGULAR['sha256'], BOLD['sha256']}


def test_font_collision_is_reported_without_mutating_destination():
    dest = {'schema': 1, 'pages': [], 'fonts': [{**REGULAR, 'sha256': '4' * 64}]}
    original = copy.deepcopy(dest)
    result = imported(dest=dest)
    assert result['doc'] == original == dest
    assert result['report']['conflicts'][0]['kind'] == 'font-collision'
    assert not result['fonts'] and not result['assets']


def test_library_font_updates_deduplicate_and_refuse_changed_bytes():
    first = imported()['doc']
    result = plan_update(first, source(), destination_id=DEST, library_id='shared',
                         source_version=2, source_slug='source')
    assert result['doc']['fonts'] == [REGULAR, BOLD]
    changed = source()
    changed['fonts'] = [{**REGULAR, 'sha256': '4' * 64}, BOLD]
    refused = plan_update(first, changed, destination_id=DEST, library_id='shared',
                          source_version=2, source_slug='source')
    assert refused['doc'] == first
    assert refused['report']['conflicts'][0]['kind'] == 'font-collision'


async def test_library_font_placement_survives_source_purge_and_history_restore(admin_client, sf):
    source_id = await setup_docs(admin_client)
    raw = b'worker-validated font fixture'
    sha = hashlib.sha256(raw).hexdigest()
    face = {**REGULAR, 'sha256': sha}
    async with sf() as session:
        session.add(UiDesignerFont(sha256=sha, data=raw, bytes=len(raw),
                    family=face['family'], weight=face['weight'], font_meta={'license': 'OFL-1.1'}))
        session.add(UiDesignerDocumentFont(document_id=source_id, sha256=sha, placed_by='user:admin'))
        await session.commit()
    doc = source_doc()
    doc['fonts'] = [face]
    doc['components']['card']['children'][0]['style']['family'] = face['family']
    await edit(admin_client, 'source', 1, [{'op': 'set_doc', 'doc': doc}])
    body = import_body(source_id)
    body['library']['source_version'] = 2
    response = await admin_client.post(P + '/docs/consumer/libraries/import', json=body)
    assert response.status_code == 200, response.text
    consumer = (await admin_client.get(P + '/docs/consumer', params={'depth': 12})).json()
    assert consumer['doc']['fonts'] == [face]
    async with sf() as session:
        placements = (await session.scalars(select(UiDesignerDocumentFont).where(
            UiDesignerDocumentFont.sha256 == sha))).all()
        assert {row.document_id for row in placements} == {source_id, consumer['id']}
    assert (await admin_client.delete(P + '/docs/source', params={'hard': 'true'})).status_code == 200
    async with sf() as session:
        await store.gc_assets(session)
        await session.commit()
        assert await session.get(UiDesignerFont, sha) is not None
    replay = await admin_client.post(P + '/docs/consumer/libraries/import', json=body)
    assert replay.status_code == 200 and replay.json()['replayed'], replay.text
    await edit(admin_client, 'consumer', 2, [{'op': 'set_doc', 'doc': {'schema': 1, 'pages': []}}])
    restored = await admin_client.post(P + '/docs/consumer/restore',
        json={'version': 2, 'expected_version': 3, 'request_id': 'restore-copied-font'})
    assert restored.status_code == 200, restored.text
    current = (await admin_client.get(P + '/docs/consumer', params={'depth': 12})).json()
    assert current['doc']['fonts'] == [face]
