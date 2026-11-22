"""Native sprint scenes are validated data, checkpointed only by the controller."""
import copy
import json
from types import SimpleNamespace

import pytest

from agentplatform import rapid_chat as rc
from agentplatform.db import RapidChatTurn, utcnow
from agentplatform.ui_designer import store
from agentplatform.ui_designer.errors import UiDesignerError


def frame(label="Hello"):
    return {"id": "screen", "type": "frame", "w": 400, "h": 300, "fill": "#fff",
            "children": [{"id": "label", "type": "text", "text": label}]}


def block(scene):
    return '```json\n' + json.dumps(scene) + '\n```'


def reply(role="reviser", scene=None):
    scene = scene or frame()
    content = ('### Option A\n' + block(scene) + '\n### Option B\n' + block(frame("Other"))) if role == "author" else block(scene)
    return '## Summary\nDesigned a screen.\n## Proposal\n' + content + '\n## Alternatives\n- Another layout\n## Open questions\n'


def chat():
    return SimpleNamespace(id="native-chat", title="Native sprint", goal="Build a dashboard",
        created_by="user:admin", sink={"kind": "ui_designer", "slug": "native-sprint", "mode": "replace"},
        areas=rc.make_areas(["Home"]), output_ref=None, last_error=None)


async def test_sharing_change_stops_controller_without_changing_content_version(sf):
    from agentplatform.db import UiDesignerAccess
    c = chat()
    c.created_by = 'admin'  # historical rapid-chat owners used the raw principal
    await rc.init_sink(sf, None, SimpleNamespace(), c)
    assert c.sink['access_version'] == 0
    async with sf() as session:
        session.add(UiDesignerAccess(document_id=c.sink['document_id'],
            visibility='restricted', access_version=1, readers=[], editors=[]))
        await session.commit()
    c.areas[0]['design'] = frame()
    c.areas[0]['turns'] = 1
    assert not await rc.checkpoint(sf, None, SimpleNamespace(), c, now=utcnow(), force=True)
    assert c.sink['conflict'] and 'sharing changed' in c.last_error
    async with sf() as session:
        assert (await store.get_document(session, c.sink['slug']))['version'] == 1


async def test_legacy_sink_without_acl_version_can_checkpoint_unchanged_workspace(sf):
    c = chat()
    await rc.init_sink(sf, None, SimpleNamespace(), c)
    c.sink.pop('access_version')
    c.areas[0]['design'] = frame()
    c.areas[0]['turns'] = 1
    assert await rc.checkpoint(sf, None, SimpleNamespace(), c, now=utcnow(), force=True)
    assert c.output_ref['version'] == 2


def test_native_options_pick_merge_and_malformed_frames():
    author = rc.parse_design_result(reply("author"), "author")
    assert author.outcome == "ok"
    assert len(author.data["options"]) == 2 and author.data["options"][1]["design"] == frame("Other")
    area = {"options": author.data["options"]}
    critic = '## Summary\nChoose B\n## Critique\nPick: Option B\n## Alternatives\n- Keep A'
    picked = rc.parse_design_result(critic, "critic", area=area, converge=True, option_labels=["A", "B"])
    assert picked.outcome == "ok" and picked.data["design"] == frame("Other")
    merged = rc.parse_design_result(critic.replace('Pick: Option B', 'Merge: A+B\n' + block(frame("Merged"))),
                                    "critic", area=area, converge=True, option_labels=["A", "B"])
    assert merged.outcome == "ok" and merged.data["design"] == frame("Merged")
    assert rc.parse_design_result(critic.replace('Pick: Option B', 'Merge: A+B\nJust prose'),
        "critic", area=area, converge=True, option_labels=["A", "B"]).outcome == "malformed"
    for scene in [[], {**frame(), "w": "fill"}, {**frame(), "link_to": {"target": "x"}},
                  {**frame(), "fill": "$color.missing"}, {**frame(), "fill": {"image": "a" * 64}},
                  {**frame(), "children": [{"id": "instance", "type": "instance", "of": "x"}]}]:
        with pytest.raises(ValueError):
            rc.design_frame(block(scene))
    with pytest.raises(ValueError):
        rc.design_frame(block(frame()) + block(frame()))


def test_composition_namespaces_ids_preserving_user_text_and_prompt_is_readonly():
    c = chat()
    c.areas[0]["design"] = frame("label")
    c.areas.append({**rc.new_area("second", "Second"), "design": frame()})
    doc = rc.assemble_design(c)
    a, b = doc["pages"][0]["children"]
    assert a["id"] != b["id"] and a["children"][0]["id"] != b["children"][0]["id"]
    assert a["children"][0]["text"] == "label" and a["clip"] is True and b["x"] == 1632
    assert c.areas[0]["design"] == frame("label")
    prompt = rc.build_prompt(c, rc.Job("author", "pai", "home", 0), c.areas, [])
    assert 'exactly one fenced' in prompt and 'Never call edit/create/place/export' in prompt


async def test_controller_merge_rejects_invalid_design_without_changing_scene():
    c = chat()
    c.areas[0]["design"] = frame("Original")
    before = copy.deepcopy(c.areas)
    t = RapidChatTurn(chat_id=c.id, seq=1, agent="pai", area_key="home", role="reviser", pass_no=0)
    await rc._apply_result(None, c, t, reply(scene={**frame(), "w": "hug"}), utcnow())
    assert t.outcome == "malformed" and c.areas == before
    await rc._apply_result(None, c, t, reply(scene=frame("Revised")), utcnow())
    assert t.outcome == "ok" and c.areas[0]["design"] == frame("Revised")


async def test_new_document_checkpoint_receipt_recovery_and_external_edit_fence(sf):
    c = chat()
    settings = SimpleNamespace()
    await rc.init_sink(sf, None, settings, c)
    assert c.sink["document_id"] and c.output_ref["kind"] == "ui_designer"
    async with sf() as s:
        original = await store.get_document(s, c.sink["slug"])
        assert original["owner"] == "user:admin"
    c.areas[0]["design"] = frame()
    c.areas[0]["turns"] = 1
    old_sink = dict(c.sink)
    assert await rc.checkpoint(sf, None, settings, c, now=utcnow(), force=True)
    assert c.output_ref["version"] == 2
    c.sink = old_sink  # dispatcher crash after document commit, before chat row commit
    assert await rc.checkpoint(sf, None, settings, c, now=utcnow(), force=True)
    assert c.output_ref["version"] == 2
    async with sf() as s:
        await store.edit_document(s, c.sink["slug"], ops=[{"op": "update", "id": "sprint", "set": {"title": "Human"}}],
                                  expected_version=2, author="user:admin")
        await s.commit()
    assert not await rc.checkpoint(sf, None, settings, c, now=utcnow(), force=True)
    assert c.sink["conflict"] and 'design sink write failed' in c.last_error
    with pytest.raises(ValueError):
        await rc.checkpoint(sf, None, settings, c, now=utcnow(), final=True)
    async with sf() as s:
        assert (await store.get_document(s, c.sink["slug"]))["version"] == 3


async def test_existing_document_is_never_claimed(sf):
    c = chat()
    async with sf() as s:
        await store.create_document(s, slug=c.sink["slug"], owner="user:other")
        await s.commit()
    with pytest.raises(UiDesignerError):
        await rc.init_sink(sf, None, SimpleNamespace(), c)


async def test_initial_sink_replay_keeps_identity_and_refuses_slug_reuse(sf):
    c = chat()
    settings = SimpleNamespace()
    await rc.init_sink(sf, None, settings, c)
    identifier = c.sink["document_id"]
    c.sink = {"kind": "ui_designer", "slug": "native-sprint", "mode": "replace"}
    await rc.init_sink(sf, None, settings, c)
    assert c.sink["document_id"] == identifier and c.sink["version"] == 1
    async with sf() as s:
        row = await store._live(s, c.sink["slug"], lock=True)
        await s.delete(row)
        await s.commit()
    async with sf() as s:
        await store.create_document(s, slug="native-sprint", owner="user:other")
        await s.commit()
    c.sink = {"kind": "ui_designer", "slug": "native-sprint", "mode": "replace"}
    with pytest.raises(ValueError, match="identity"):
        await rc.init_sink(sf, None, settings, c)


def test_node_bound_text_literal_and_root_id_stability():
    too_many = {**frame(), "children": [{"id": f"n{i}", "type": "rect"} for i in range(100)]}
    with pytest.raises(ValueError):
        rc.design_frame(block(too_many))
    literal = frame("$color.is-literal-text")
    assert rc.design_frame(block(literal)) == literal
    c = chat()
    c.areas[0]["design"] = literal
    identifier = rc.assemble_design(c)["pages"][0]["children"][0]["id"]
    c.areas[0]["design"]["id"] = "renamed-root"
    assert rc.assemble_design(c)["pages"][0]["children"][0]["id"] == identifier


def test_effective_scene_bounds_and_new_path_syntax_are_validated():
    for scene in [{**frame(), "min_w": 100000},
                  {**frame(), "children": [{"id": "path", "type": "path", "d": "M0 0L"}]}]:
        with pytest.raises(ValueError):
            rc.design_frame(block(scene))


async def test_init_replay_preserves_original_cas_version_after_human_edit(sf, producer):
    c = chat()
    await rc.init_sink(sf, producer, SimpleNamespace(), c)
    async with sf() as s:
        await store.edit_document(s, c.sink["slug"], ops=[{"op": "update", "id": "sprint", "set": {"title": "Human"}}],
                                  expected_version=1, author="user:admin")
        await s.commit()
    c.sink = {"kind": "ui_designer", "slug": "native-sprint", "mode": "replace"}
    await rc.init_sink(sf, producer, SimpleNamespace(), c)
    assert c.sink["version"] == 1
    c.areas[0]["design"] = frame()
    assert not await rc.checkpoint(sf, producer, SimpleNamespace(), c, now=utcnow(), force=True)
    assert c.sink["conflict"]
    async with sf() as s:
        current = await store.get_document(s, "native-sprint")
        assert current["version"] == 2 and current["doc"]["pages"][0]["title"] == "Human"


async def test_designer_events_publish_only_after_new_commits_and_failure_is_best_effort(sf, producer):
    c = chat()
    settings = SimpleNamespace()
    await rc.init_sink(sf, producer, settings, c)
    assert len(producer.published) == 1
    topic, slug, event = producer.published[0]
    assert topic == "ui_designer.events" and slug == c.sink["slug"]
    assert event == {"type": "version", "slug": slug, "version": 1,
                     "document_id": c.sink["document_id"],
                     "author": "rapid-chat:native-chat", "run_id": None}
    c.sink = {"kind": "ui_designer", "slug": slug, "mode": "replace"}
    await rc.init_sink(sf, producer, settings, c)
    assert len(producer.published) == 1
    c.areas[0]["design"] = frame()
    old = dict(c.sink)
    assert await rc.checkpoint(sf, producer, settings, c, now=utcnow(), force=True)
    assert len(producer.published) == 2 and producer.published[-1][2]["version"] == 2
    c.sink = old
    assert await rc.checkpoint(sf, producer, settings, c, now=utcnow(), force=True)
    assert len(producer.published) == 2

    async def unavailable(*args, **kwargs):
        raise RuntimeError("Kafka unavailable")

    c.areas[0]["design"] = frame("Next")
    assert await rc.checkpoint(sf, SimpleNamespace(publish=unavailable), settings, c, now=utcnow(), force=True)
    assert c.sink["version"] == 3
    async with sf() as s:
        assert (await store.get_document(s, slug))["version"] == 3
