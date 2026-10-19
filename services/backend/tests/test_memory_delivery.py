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
