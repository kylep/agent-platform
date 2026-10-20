import pytest
from agentplatform.db import Memory, utcnow
from agentplatform.persona_memory import select_records, source_hash


def note(id, content):
    return Memory(
        id=id, agent="one", key=None, content=content, tags=[], updated_at=utcnow()
    )


@pytest.mark.parametrize(
    "contents", [["", "lesson"], ["first", "", "second"], [""] * 25 + ["lesson"] * 21]
)
def test_empty_notes_do_not_consume_delivery_slots(contents):
    rows = [note(str(i), content) for i, content in enumerate(contents)]
    records, provenance = select_records(rows)
    expected = [m.id for m in rows if m.content][:20]
    assert [r["id"] for r in records] == expected
    assert [p["position"] for p in provenance] == list(range(len(records)))
    assert all(
        r["content"] and r["content"] == rows[int(r["id"])].content for r in records
    )


def test_content_budget_stops_only_after_valid_content_and_metadata_is_accurate():
    rows = [note("empty", "")] + [note(str(i), "🦊" * 2500) for i in range(8)]
    records, provenance = select_records(rows)
    assert len(records) == 6 and sum(len(r["content"]) for r in records) == 12000
    assert all(len(r["content"]) == 2000 and r["truncated"] for r in records)
    for r, p in zip(records, provenance):
        assert (
            r["excerpt"]
            == p["excerpt"]
            == {
                "start": 0,
                "end": 2000,
                "source_length": 2500,
                "unit": "unicode_codepoints",
                "method": "prefix",
            }
        )
        assert r["source_hash"] == p["source_hash"]


def test_excerpts_are_unicode_offsets_and_stale_hints_are_not_applied():
    source = note("long", "🦊" * 2500 + " unique-late-fact " + "tail" * 500)
    refs = [{"id": source.id, "source_hash": source_hash(source), "match_start": 2400}]
    records, _ = select_records([source], references=refs)
    r = records[0]
    assert "unique-late-fact" in r["content"] and r["truncated"]
    assert source.content[r["excerpt"]["start"] : r["excerpt"]["end"]] == r["content"]
    source.content = "replacement " * 500
    records, _ = select_records([source], references=refs)
    assert records[0]["excerpt"]["method"] == "prefix"
    assert records[0]["excerpt"]["start"] == 0


def test_lexical_window_includes_the_value_after_a_late_matching_label():
    from agentplatform.memory_relevance import query_terms, lexical_start
    text = ('Ephemeral CA-3 synthetic acceptance fixture, not a user preference or instruction. '
            + 'Unrelated weather archive status. ' * 145
            + ' Synthetic archive verification code: violet-harbor-abcdef.')
    source = note('late-value', text)
    terms = query_terms('What is the synthetic archive verification code?')
    start = lexical_start(text, terms, [1.0] * len(terms))
    records, _ = select_records([source], references=[{'id': source.id,
        'source_hash': source_hash(source), 'match_start': start}])
    assert 'violet-harbor-abcdef.' in records[0]['content']
    assert records[0]['truncated'] and records[0]['excerpt']['start'] > 2000


async def test_rest_empty_note_and_long_lexical_passage_reach_hybrid_and_startup(
    admin_client, token_client, sf, seed_agent
):
    from agentplatform import persona_memory
    from agentplatform.db import Run
    from .test_relay_api import _agent_token

    await seed_agent(
        "delivery", agent_type="persona", platform_tools=["mcp__platform__memory"]
    )
    text = "unrelated preface " * 260 + "needlepassage: preserve the violet archive."
    valid = await admin_client.post(
        "/api/memories",
        json={"agent": "delivery", "key": "preferences.lesson", "content": text},
    )
    assert valid.status_code == 201
    empty = await admin_client.post(
        "/api/memories",
        json={"agent": "delivery", "key": "identity.needlepassage", "content": ""},
    )
    assert empty.status_code == 201
    async with sf() as s:
        records, _ = await persona_memory.select_memory(s, "delivery")
        assert [r["id"] for r in records] == [valid.json()["id"]]
        run = Run(
            agent="delivery",
            trigger="manual",
            requested_by="test",
            prompt="needlepassage",
            state="running",
            definition_snapshot={"platform_tools": ["mcp__platform__memory"]},
        )
        s.add(run)
        await s.commit()
    headers = await _agent_token(sf, "delivery", role="reader", run_id=run.id)
    response = await token_client.post(
        "/api/memory-hybrid", headers=headers, json={"q": "needlepassage"}
    )
    assert response.status_code == 200
    result = response.json()
    record = result["memories"][0]
    assert record["id"] == valid.json()["id"] and "needlepassage" in record["content"]
    assert record["truncated"] and record["excerpt"]["start"] > 2000
    assert record["source_hash"] == result["sources"][0]["source_hash"]
    assert record["excerpt"] == result["sources"][0]["excerpt"]
    assert (
        text[record["excerpt"]["start"] : record["excerpt"]["end"]] == record["content"]
    )
    unchanged = await admin_client.get("/api/memories", params={"agent": "delivery"})
    assert (
        next(m for m in unchanged.json() if m["id"] == valid.json()["id"])["content"]
        == text
    )


def budget_rows():
    return [note(str(i), 'x' * 2000) for i in range(5)] + [note('uneven', 'x' * 1900)]


@pytest.mark.parametrize('unicode_prefix', ['x', '🦊'])
def test_residual_budget_reanchors_complete_literal_and_label_value(unicode_prefix):
    from agentplatform.memory_relevance import lexical_start
    for fact, term in [(' unique-late-fact ', 'unique-late-fact'),
                       (' Archive code: violet-harbor-abcdef. ', 'code')]:
        source = note('last', unicode_prefix * 2500 + fact + 'y' * 2500)
        refs = [{'id': source.id, 'source_hash': source_hash(source),
                 'match_start': lexical_start(source.content, [term], [1.0]),
                 'match_terms': [term], 'match_weights': [1.0]}]
        records, provenance = select_records([*budget_rows(), source], refs)
        r, p = records[-1], provenance[-1]
        assert (term if term == 'unique-late-fact' else 'code: violet-harbor-abcdef.') in r['content']
        assert len(r['content']) == 100 and sum(len(r['content']) for r in records) == 12000
        assert source.content[r['excerpt']['start']:r['excerpt']['end']] == r['content']
        assert r['source_hash'] == p['source_hash'] and r['excerpt'] == p['excerpt']


@pytest.mark.parametrize('remaining', [100, 1100])
def test_semantic_chunk_complete_or_explicitly_omitted_at_residual_budget(remaining):
    source = note('last', 'x' * 2500 + ' unique-late-fact ' + 'y' * 2500)
    rows = [note(str(i), 'x' * 2000) for i in range(5)] + [note('uneven', 'x' * (2000-remaining)), source]
    ref = {'id': source.id, 'source_hash': source_hash(source), 'match_chunk': 2}
    records, provenance = select_records(rows, [ref])
    if remaining < 1024:
        assert source.id not in [r['id'] for r in records]
        assert provenance[-1]['outcome'] == 'omitted'
        assert provenance[-1]['reason'] == 'matching_evidence_exceeds_remaining_budget'
    else:
        assert 'unique-late-fact' in records[-1]['content']
        assert len(records[-1]['content']) <= remaining
    assert sum(len(r['content']) for r in records) <= 12000


def test_oversize_label_value_is_omitted_and_later_valid_note_survives():
    source = note('last', 'x' * 2500 + ' code: ' + 'v' * 150 + '.')
    ref = {'id': source.id, 'source_hash': source_hash(source), 'match_start': 2341,
           'match_terms': ['code'], 'match_weights': [1.0]}
    records, provenance = select_records([*budget_rows(), source, note('after', 'valid')], [ref])
    assert records[-1]['id'] == 'after'
    assert next(p for p in provenance if p['id'] == 'last')['outcome'] == 'omitted'
    source.content = 'changed'
    records, _ = select_records([source], [ref])
    assert records[0]['content'] == 'changed' and records[0]['excerpt']['method'] == 'prefix'


async def test_actual_hybrid_delivery_reanchors_after_uneven_records(admin_client, token_client, sf, seed_agent):
    from agentplatform.db import Run
    from .test_relay_api import _agent_token
    await seed_agent('residual', agent_type='persona', platform_tools=['mcp__platform__memory'])
    # Oldest matching source sorts after six uneven, newer matches.
    fact = ' budgetneedle: violet-harbor-abcdef. '
    content = 'x' * 2500 + fact + 'y' * 2500
    ids = []
    for text in [content, 'budgetneedle ' + 'x'*1887, *['budgetneedle ' + 'x'*1987]*5]:
        response = await admin_client.post('/api/memories', json={'agent':'residual', 'content':text})
        assert response.status_code == 201
        ids.append(response.json()['id'])
    async with sf() as s:
        run = Run(agent='residual', trigger='manual', requested_by='test', prompt='budgetneedle', state='running',
                  definition_snapshot={'platform_tools':['mcp__platform__memory']})
        s.add(run); await s.commit()
    headers = await _agent_token(sf, 'residual', role='reader', run_id=run.id)
    response = await token_client.post('/api/memory-hybrid', headers=headers, json={'q':'budgetneedle'})
    assert response.status_code == 200
    records = response.json()['memories']
    last = next(r for r in records if r['id']==ids[0])
    assert 'budgetneedle: violet-harbor-abcdef.' in last['content']
    assert len(last['content']) == 100 and sum(len(r['content']) for r in records) == 12000
    assert content[last['excerpt']['start']:last['excerpt']['end']] == last['content']


@pytest.mark.parametrize('prefix', ['x', '🦊'])
@pytest.mark.parametrize('value', [
    'amber.meadow-79d78292', 'https://archive.test/v1.2?q=a.b!c;d&e=1.25',
    'v2.10.3-rc.1', '1234.5678', '值.紫羅蘭-🦊',
])
@pytest.mark.parametrize('ending', ['. Next sentence.', '\nNext line.', '! Next sentence.', '; Next clause.'])
def test_required_value_span_budget_matrix(prefix, value, ending):
    from agentplatform.memory_relevance import lexical_start
    unit = 'code: ' + value + ending[0]
    source = note('value', prefix * 2500 + ' ' + unit + ending[1:] + ' y' * 1500)
    ref = {'id': source.id, 'source_hash': source_hash(source),
           'match_start': lexical_start(source.content, ['code'], [1.]),
           'match_terms': ['code'], 'match_weights': [1.]}
    # Exercise both sides of the required-span boundary, including CA-7's 18.
    for remaining in sorted({1, 18, len(unit)-1, len(unit), len(unit)+1, 100, 2000}):
        before = [note(str(i), 'x'*2000) for i in range(5)]
        before.append(note('uneven', 'x'*(2000-remaining)))
        later = note('later', 'z')
        records, sources = select_records([*before, note('empty', ''), source, later], [ref])
        delivered = next((r for r in records if r['id'] == source.id), None)
        if remaining < len(unit):
            assert delivered is None
            omitted = next(s for s in sources if s['id'] == source.id)
            assert omitted['outcome'] == 'omitted'
            assert omitted['available_codepoints'] == remaining
            assert records[-1]['id'] == later.id  # omission consumes neither slot nor content
        else:
            assert delivered and unit in delivered['content']
            excerpt = delivered['excerpt']; required = excerpt['required_span']
            assert required == {'start':2501, 'end':2501+len(unit), 'basis':'lexical_unit'}
            assert excerpt['start'] <= required['start'] < required['end'] <= excerpt['end']
            assert source.content[excerpt['start']:excerpt['end']] == delivered['content']
            assert delivered['source_hash'] == source_hash(source) and delivered['truncated']
        assert len(records) <= 20
        assert sum(len(r['content']) for r in records) <= 12000
        assert all(0 < len(r['content']) <= 2000 for r in records)


def test_substring_anchor_requires_the_whole_value_token_and_colon_does_not_cross_lines():
    from agentplatform.memory_relevance import lexical_window
    text = 'code: amber.meadow-79d78292. Next sentence.'
    assert lexical_window(text, ['meadow'], [1.], 18) is None
    window = lexical_window(text, ['meadow'], [1.], 30)
    assert text[window[1]:window[2]] == 'amber.meadow-79d78292.'
    text = 'code\n: unrelated value'
    window = lexical_window(text, ['code'], [1.], 4)
    assert window == (0, 0, 4)


@pytest.mark.parametrize('remaining', [18, 100])
async def test_ca7_api_omits_or_delivers_complete_punctuation_value(admin_client, token_client, sf, seed_agent, remaining):
    from agentplatform.db import Run
    from .test_relay_api import _agent_token
    await seed_agent('punctuation', agent_type='persona', platform_tools=['mcp__platform__memory'])
    unit = 'code: amber.meadow-79d78292.'
    content = '🦊'*2500 + ' ' + unit + ' ' + 'y'*2500
    ids=[]
    for text in [content, 'code '+ 'x'*(1995-remaining), *['code '+ 'x'*1995]*5]:
        r=await admin_client.post('/api/memories', json={'agent':'punctuation','content':text})
        assert r.status_code==201; ids.append(r.json()['id'])
    async with sf() as s:
        run=Run(agent='punctuation',trigger='manual',requested_by='test',prompt='code',state='running',
                definition_snapshot={'platform_tools':['mcp__platform__memory']})
        s.add(run);await s.commit()
    headers=await _agent_token(sf,'punctuation',role='reader',run_id=run.id)
    r=await token_client.post('/api/memory-hybrid',headers=headers,json={'q':'code'})
    assert r.status_code==200
    records=r.json()['memories'];source=next(s for s in r.json()['sources'] if s['id']==ids[0])
    if remaining==18:
        assert ids[0] not in [m['id'] for m in records]
        assert source['outcome']=='omitted' and source['available_codepoints']==18
    else:
        record=next(m for m in records if m['id']==ids[0])
        assert unit in record['content'] and len(record['content'])==100
        ex=record['excerpt'];assert content[ex['start']:ex['end']]==record['content']
        assert ex==source['excerpt'] and record['source_hash']==source['source_hash']


@pytest.mark.parametrize('start', [-1, 999999])
def test_invalid_legacy_locations_do_not_claim_a_required_span(start):
    source=note('invalid', 'canonical content')
    records,_=select_records([source],[{'id':source.id,'source_hash':source_hash(source),'match_start':start}])
    assert records[0]['content']==source.content
    assert records[0]['excerpt']['method']=='prefix'
    assert 'required_span' not in records[0]['excerpt']


@pytest.mark.parametrize('gap', ['', ' ', '    ', '\t\t', ' '*2001])
def test_aligned_colon_values_cannot_bypass_required_unit(gap):
    from agentplatform.memory_relevance import lexical_window
    text='code'+gap+': amber.meadow-79d78292.'
    assert lexical_window(text,['code'],[1.],4) is None
    if len(text)<=2000:
        window=lexical_window(text,['code'],[1.],len(text))
        assert window==(0,0,len(text))
    else:
        assert lexical_window(text,['code'],[1.],2000) is None
