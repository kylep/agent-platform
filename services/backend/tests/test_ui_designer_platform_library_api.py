"""Native Agent Platform insertions use the ordinary Designer edit contract."""
import pytest

from agentplatform.ui_designer import platform_catalog

from .test_ui_designer_api import P, SLUG, edit, mk, renderer  # noqa: F401


OP = {"op": "platform_component", "component": "Button", "variant": "default",
      "id": "button", "parent": "p", "revision": 1}


async def test_platform_library_discovery(admin_client):
    response = await admin_client.get(f"{P}/component-library")
    assert response.status_code == 200, response.text
    manifest = response.json()
    assert (manifest["id"], manifest["revision"], len(manifest["components"])) == ("agent-platform", 1, 21)
    response = await admin_client.get(f"{P}/component-library", params={"name": "button"})
    assert [component["name"] for component in response.json()["components"]] == ["Button"]
    assert (await admin_client.get(f"{P}/component-library", params={"name": "x" * 81})).status_code == 422


async def test_insert_replay_and_inspect_native_component(admin_client, renderer):
    await mk(admin_client, doc={"schema": 1, "tokens": {"color": {"local": "#123"}},
                                "pages": [{"id": "p", "children": []}]})
    label = 'Custom "label" <script>'
    op = {**OP, "x": 40, "y": 25, "props": {"children": label}}
    saved = await edit(admin_client, 1, [op], request_id="platform-button")
    assert saved.status_code == 200, saved.text
    replay = await edit(admin_client, 1, [op], request_id="platform-button")
    assert replay.status_code == 200 and replay.json()["replayed"]
    assert (await edit(admin_client, 1, [op], request_id="stale")).status_code == 409
    reused = await edit(admin_client, 1, [{**op, "x": 50}], request_id="platform-button")
    assert reused.status_code == 409 and reused.json()["detail"]["code"] == "UD-REQUEST-REUSED"

    response = await admin_client.get(f"{P}/docs/{SLUG}/inspect", params={"node": "button"})
    assert response.status_code == 200, response.text
    inspected = response.json()
    assert inspected["authored"]["x"] == 40
    mapping = inspected["implementation"]
    assert mapping["status"] == "exact"
    assert mapping["props"]["children"] == label
    assert r'children={"Custom \"label\" \u003cscript>"}' in mapping["snippet"]
    assert set(inspected["modes"]) == {"dark", "light"}

    updated = await edit(admin_client, 2, [{"op": "update", "id": "button",
                                          "set": {"props": {"children": "Updated"}}}])
    assert updated.status_code == 200, updated.text
    response = await admin_client.get(f"{P}/docs/{SLUG}/inspect", params={"node": "button"})
    assert response.json()["implementation"]["props"]["children"] == "Updated"
    tokens = platform_catalog.platform_tokens()
    token = next(iter(tokens["color"]))
    changed = await edit(admin_client, 3, [{"op": "tokens", "set": {"color": {token: "#000"}}}])
    assert changed.status_code == 200, changed.text
    response = await admin_client.get(f"{P}/docs/{SLUG}/inspect", params={"node": "button"})
    mapping = response.json()["implementation"]
    assert mapping["status"] == "detached" and mapping["snippet"] is None
    current = (await admin_client.get(f"{P}/docs/{SLUG}")).json()["doc"]
    assert current["tokens"]["color"]["local"] == "#123"


@pytest.mark.parametrize("override", [
    {"revision": 2}, {"revision": True}, {"variant": []}, {"props": []},
    {"component": "unknown"}, {"id": "Not valid"}, {"parent": "missing"},
])
async def test_invalid_insertion_does_not_install_library(admin_client, override):
    await mk(admin_client, doc={"schema": 1, "pages": [{"id": "p", "children": []}]})
    response = await edit(admin_client, 1, [{**OP, **override}])
    assert response.status_code == 422, response.text
    current = (await admin_client.get(f"{P}/docs/{SLUG}")).json()
    assert current["version"] == 1
    assert "component_sets" not in current["doc"]


async def test_namespaced_token_collision_aborts_whole_edit(admin_client):
    token = next(iter(platform_catalog.platform_tokens()["color"]))
    await mk(admin_client, doc={"schema": 1, "tokens": {"color": {token: "#123"}},
                                "pages": [{"id": "p", "children": []}]})
    response = await edit(admin_client, 1, [{"op": "update", "id": "p", "set": {"title": "Changed"}}, OP])
    assert response.status_code == 422 and token in response.text
    current = (await admin_client.get(f"{P}/docs/{SLUG}")).json()
    assert current["version"] == 1
    assert current["doc"]["tokens"]["color"][token] == "#123"
    assert "title" not in current["doc"]["pages"][0]


def test_catalog_can_share_one_document_and_compound_snippets_include_sample_children():
    from agentplatform.ui_designer.ops import apply_ops
    from agentplatform.ui_designer.platform_library import inspect_instance

    initial = {"schema": 1, "modes": ["light"], "pages": [{"id": "p", "children": []}]}
    manifest = platform_catalog.catalog_manifest()
    ops = [{**OP, "component": component["name"], "variant": component["variants"][0]["id"],
            "id": f"placed-{index}"} for index, component in enumerate(manifest["components"])]
    doc = apply_ops(initial, ops, version=1, expected_version=1).doc
    assert doc["modes"] == ["light", "dark"]
    snippets = {}
    for instance in doc["pages"][0]["children"]:
        mapping = inspect_instance(doc, instance)
        assert mapping["status"] == "exact", mapping
        snippets[mapping["component"]] = mapping["snippet"]
    assert len(snippets) == 21
    assert '<option value=' in snippets["Select"]
    assert "<TH" in snippets["Table"] and "<TD" in snippets["Table"]
    assert "<thead><tr><TH " in snippets["TH"]
    assert "<tbody><tr><TD " in snippets["TD"]
    assert "<StatRow>" in snippets["StatRow"] and "<Stat " in snippets["StatRow"]
    assert "footer={<ThemeToggle />}" in snippets["SideNav"]
    assert "onConfirm={onConfirm}" in snippets["ConfirmDialog"]


async def test_pinned_versions_are_snapshots_even_during_agent_edit_bursts(sf, monkeypatch):
    from .test_ui_designer_store import at_version, edit as edit_in_store, make

    await make(sf)
    before = await edit_in_store(sf, 1)
    installed = await edit_in_store(sf, 2, [{**OP, "parent": "root"}])
    after = await edit_in_store(sf, 3)
    await edit_in_store(sf, 4)
    assert not installed["folded"] and not after["folded"]

    def unavailable(*args, **kwargs):
        raise AssertionError("stored versions must not access the catalog")

    monkeypatch.setattr(platform_catalog, "get_definition", unavailable)
    assert await at_version(sf, 2) == before["doc"]
    assert await at_version(sf, 3) == installed["doc"]
    assert await at_version(sf, 4) == after["doc"]


async def test_catalog_requires_the_designer_grant(sf, seed_agent, agent_store, token_client):
    from .test_ui_designer_api_auth import UD, _agent

    route = f"{P}/component-library"
    assert (await token_client.get(route)).status_code == 401
    denied = await _agent(sf, seed_agent, agent_store, "no-catalog", grants=())
    assert (await token_client.get(route, headers=denied)).status_code == 403
    allowed = await _agent(sf, seed_agent, agent_store, "can-catalog", grants=(UD,))
    assert (await token_client.get(route, headers=allowed)).status_code == 200


def test_local_component_definition_collision_is_never_overwritten():
    from agentplatform.ui_designer.errors import UiDesignerError
    from agentplatform.ui_designer.ops import apply_ops
    from agentplatform.ui_designer.platform_library import inspect_instance

    canonical = platform_catalog.component_document("Button")
    sample = canonical["pages"][0]["children"][0]
    set_id = sample["of"]
    selected = canonical["component_sets"][set_id]["variants"][0]["of"]
    canonical["components"][selected]["title"] = "Local override"
    assert inspect_instance(canonical, sample)["status"] == "detached"
    with pytest.raises(UiDesignerError, match="Conflicts with the pinned"):
        apply_ops(canonical, [{**OP, "parent": "preview"}], version=1, expected_version=1)
    assert canonical["components"][selected]["title"] == "Local override"
