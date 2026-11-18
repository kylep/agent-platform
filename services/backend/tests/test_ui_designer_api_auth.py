"""The UI Designer API's authorization seam (docs/design/46 §6, §9, T5).

Who reaches a document (the grant, not the role; a run to write from), the one
door an image comes in by (`place` and its refusals), and who sees pictures
(agent runs, people's sessions and human keys by document access; a run-less
agent key is refused).
"""
from agentplatform.db import Artifact, Conversation, Run, RunState, UiDesignerDocumentAsset
from agentplatform.ui_designer import store

from .test_artifact_store import png_bytes
from .test_artifacts_api import upload
from .test_local_persona import local  # noqa: F401  (fixture)
from .test_relay_api import _agent_token, _human_token, _seed
from .test_wiki_api import _run_id

RELAY = "mcp__platform__relay"
UD = "mcp__platform__ui_designer"
ART = "mcp__platform__artifacts"
SLUG = "hud"


async def _doc(sf, slug=SLUG):
    async with sf() as s:
        await store.create_document(s, slug=slug, owner="user:admin")
        await s.commit()


async def _agent(sf, seed_agent, agent_store, name, grants=(RELAY, UD, ART), run=True):
    await _seed(seed_agent, agent_store, name, platform_tools=list(grants))
    return await _agent_token(sf, name, run_id=await _run_id(sf, name) if run else None)


async def _place(client, artifact_id, headers=None, slug=SLUG):
    return await client.post(f"/api/ui-designer/docs/{slug}/place",
                             json={"artifact_id": artifact_id}, headers=headers or {})


async def _set_run(sf, artifact_id, **run_fields):
    """Attach the artifact to a fresh run with the given fields."""
    async with sf() as s:
        run = Run(agent="hello-world", trigger="relay", requested_by="test", prompt="p",
                  state=RunState.SUCCEEDED, **run_fields)
        s.add(run)
        await s.flush()
        (await s.get(Artifact, artifact_id)).run_id = run.id
        await s.commit()
        return run.id


async def _placements(sf):
    async with sf() as s:
        from sqlalchemy import select
        return list((await s.execute(select(UiDesignerDocumentAsset))).scalars())


# --- the door ------------------------------------------------------------------

async def test_no_grant_and_relay_role_alone_are_refused(sf, seed_agent, agent_store,
                                                         token_client):
    await _doc(sf)
    for name, grants in (("nogrant", (ART,)), ("relayonly", (RELAY,))):
        h = await _agent(sf, seed_agent, agent_store, name, grants=grants)
        r = await _place(token_client, "a" * 32, h)
        assert r.status_code == 403 and "ui_designer" in r.text, (name, r.text)
        r = await token_client.post(f"/api/ui-designer/docs/{SLUG}/edit", headers=h,
                                    json={"ops": [], "expected_version": 1})
        assert r.status_code == 403, (name, r.text)
    assert (await _place(token_client, "a" * 32)).status_code == 401


async def test_runless_agent_key_cannot_write(sf, seed_agent, agent_store, token_client,
                                              admin_client):
    await _doc(sf)
    art = await upload(admin_client, png_bytes())
    h = await _agent(sf, seed_agent, agent_store, "norun", run=False)
    r = await _place(token_client, art["id"], h)
    assert r.status_code == 403 and "no run" in r.text, r.text
    r = await token_client.post(f"/api/ui-designer/docs/{SLUG}/edit", headers=h,
                                json={"ops": [], "expected_version": 1})
    assert r.status_code == 403 and "no run" in r.text, r.text
    assert await _placements(sf) == []


async def test_human_reader_cannot_write(sf, token_client):
    await _doc(sf)
    reader = await _human_token(sf, "kyle", "reader")
    assert (await _place(token_client, "a" * 32, reader)).status_code == 403


# --- place ----------------------------------------------------------------------

async def test_place_needs_the_artifacts_grant(sf, seed_agent, agent_store, token_client,
                                               admin_client):
    await _doc(sf)
    art = await upload(admin_client, png_bytes())
    await _set_run(sf, art["id"])
    h = await _agent(sf, seed_agent, agent_store, "noart", grants=(RELAY, UD))
    r = await _place(token_client, art["id"], h)
    assert r.status_code == 403 and "artifacts" in r.text, r.text


async def test_place_refuses_app_owned(sf, seed_agent, agent_store, admin_client,
                                       token_client, monkeypatch):
    await _doc(sf)
    art = await upload(admin_client, png_bytes())
    from agentplatform.appdata import artifacts as app_artifacts

    async def owned(s, artifact_id):
        return object()
    monkeypatch.setattr(app_artifacts, "ownership", owned)
    r = await _place(admin_client, art["id"])
    assert r.status_code == 404, r.text
    h = await _agent(sf, seed_agent, agent_store, "pai")
    assert (await _place(token_client, art["id"], h)).status_code == 404
    assert await _placements(sf) == []


async def test_place_refuses_every_restricted_run(sf, seed_agent, agent_store, admin_client,
                                                  token_client):
    await _doc(sf)
    h = await _agent(sf, seed_agent, agent_store, "pai")
    async with sf() as s:
        closed = Conversation(kind="group", connector="web", title="private")
        external = Conversation(home="external", connector="discord", title="ext")
        s.add_all([closed, external])
        await s.commit()
        closed_id, external_id = closed.id, external.id
    cases = {
        "startup pilot": {"startup_memory_enabled": True},
        "local persona": {"local_persona_session_id": "b" * 32},
        "closed room": {"conversation_id": closed_id},
        "external room": {"conversation_id": external_id},
        "gone room": {"conversation_id": "c" * 32},
    }
    for label, fields in cases.items():
        art = await upload(admin_client, png_bytes(8, 8 + len(label)))
        await _set_run(sf, art["id"], **fields)
        # The admin reads all of these; it still may not place them.
        r = await _place(admin_client, art["id"])
        assert r.status_code == 403, (label, r.text)
        r = await _place(token_client, art["id"], h)
        assert r.status_code == 403, (label, r.text)
    assert await _placements(sf) == []


async def _run(sf, **fields) -> str:
    async with sf() as s:
        run = Run(agent="hello-world", trigger="relay", requested_by="test", prompt="p",
                  state=RunState.SUCCEEDED, **fields)
        s.add(run)
        await s.commit()
        return run.id


async def test_place_refuses_a_child_of_a_restricted_run(sf, seed_agent, agent_store,
                                                         admin_client, token_client):
    await _doc(sf)
    h = await _agent(sf, seed_agent, agent_store, "pai")
    pilot = await _run(sf, startup_memory_enabled=True)
    local_run = await _run(sf, local_persona_session_id="b" * 32)
    grandchild_parent = await _run(sf, parent_run_id=pilot)
    deep = await _run(sf)                          # an unrestricted chain past the bound
    from agentplatform.config import get_settings
    for _ in range(get_settings().max_run_chain_depth + 1):
        deep = await _run(sf, parent_run_id=deep)
    cases = {
        "child of startup pilot": {"parent_run_id": pilot},
        "child of local persona": {"parent_run_id": local_run},
        "grandchild of startup pilot": {"parent_run_id": grandchild_parent},
        "missing parent": {"parent_run_id": "f" * 32},
        "past the depth bound": {"parent_run_id": deep},
    }
    for label, fields in cases.items():
        art = await upload(admin_client, png_bytes(30, 8 + len(label)))
        await _set_run(sf, art["id"], **fields)
        r = await _place(admin_client, art["id"])
        assert r.status_code == 403, (label, r.text)
        r = await _place(token_client, art["id"], h)
        assert r.status_code == 403, (label, r.text)
    # A child of an unrestricted parent is placeable.
    art = await upload(admin_client, png_bytes(31, 31))
    await _set_run(sf, art["id"], parent_run_id=await _run(sf))
    assert (await _place(admin_client, art["id"])).status_code == 200
    assert len(await _placements(sf)) == 1


async def test_place_runless_only_by_its_uploader(sf, seed_agent, agent_store, admin_client,
                                                  token_client):
    await _doc(sf)
    art = await upload(admin_client, png_bytes())          # owner user:admin, no run
    other = await _human_token(sf, "operator-bob", "operator")
    r = await _place(token_client, art["id"], other)
    assert r.status_code == 403 and "uploaded" in r.text, r.text
    h = await _agent(sf, seed_agent, agent_store, "pai")
    assert (await _place(token_client, art["id"], h)).status_code == 403
    # An admin placing someone else's run-less upload is refused too.
    theirs = await upload(token_client, png_bytes(9, 9), headers=other)
    assert (await _place(admin_client, theirs["id"])).status_code == 403
    assert await _placements(sf) == []


async def test_place_refuses_non_images_and_unknown(sf, admin_client):
    await _doc(sf)
    r = await admin_client.post("/api/artifacts", json={"name": "a.txt", "text": "x"})
    assert (await _place(admin_client, r.json()["id"])).status_code == 422
    assert (await _place(admin_client, "d" * 32)).status_code == 404
    assert (await _place(admin_client, "not-an-id")).status_code == 422


async def test_successful_place(sf, seed_agent, agent_store, admin_client, token_client):
    await _doc(sf)
    data = png_bytes(16, 16)
    mine = await upload(admin_client, data)
    r = await _place(admin_client, mine["id"])
    assert r.status_code == 200, r.text
    assert r.json()["sha256"] == mine["sha256"] and r.json()["mime"] == "image/png"

    # An agent places an artifact from an unrestricted run (an open channel).
    h = await _agent(sf, seed_agent, agent_store, "pai")
    async with sf() as s:
        from sqlalchemy import select
        general = (await s.execute(select(Conversation.id).where(
            Conversation.kind == "channel", Conversation.name == "general"))).scalar_one()
    art = await upload(admin_client, png_bytes(20, 20))
    await _set_run(sf, art["id"], conversation_id=general)
    r = await _place(token_client, art["id"], h)
    assert r.status_code == 200, r.text

    rows = {(p.sha256, p.placed_by, p.placed_from) for p in await _placements(sf)}
    assert rows == {(mine["sha256"], "user:admin", f"artifact:{mine['id']}"),
                    (art["sha256"], "agent:pai", f"artifact:{art['id']}")}
    async with sf() as s:
        assert (await store.get_asset(s, mine["sha256"])).data == data
    # Unknown document: the store's UD-NOT-FOUND as a 404.
    r = await _place(admin_client, mine["id"], slug="nope")
    assert r.status_code == 404 and r.json()["detail"]["code"] == "UD-NOT-FOUND"


# --- snapshot ---------------------------------------------------------------------
# design/46 §6 (revised 2026-11-18): people's own MCP clients see pictures too.
# Only a run-less AGENT key is refused; document access still decides who.

def _fake_renderer(client):
    from .test_ui_designer_api import FakeRenderer
    fake = FakeRenderer(size=(40, 20))
    client._transport.app.state.ui_designer_renderer = fake.client()
    return fake


async def _real_doc(client, headers=None, slug=SLUG):
    from .test_ui_designer_access import DOC
    r = await client.post("/api/ui-designer/docs", headers=headers or {}, json={"slug": slug, "doc": DOC})
    assert r.status_code == 200, r.text


async def test_human_key_gets_snapshot_pictures_on_edit_and_render(sf, token_client):
    fake = _fake_renderer(token_client)
    key = await _human_token(sf, "facade", "admin")
    await _real_doc(token_client, key)
    body = {"ops": [{"op": "update", "id": "root", "set": {"w": 120}}], "expected_version": 1,
            "snapshot": ["root"], "scale": 2, "annotate": True}
    r = await token_client.post(f"/api/ui-designer/docs/{SLUG}/edit", json=body, headers=key)
    assert r.status_code == 200, r.text
    snap = r.json()["snapshots"]
    assert len(snap) == 1 and snap[0]["node"] == "root" and snap[0]["data"], r.text
    sent = [b for path, b in fake.calls if path == "/render"][-1]
    assert sent["scale"] == 2 and sent["annotate"] is True and sent["max_bytes"] == 1 << 20
    # A re-render without editing: the PNG, under the byte ladder (a model reads it).
    r = await token_client.get(f"/api/ui-designer/docs/{SLUG}/render?node=root&scale=2&annotate=true",
                               headers=key)
    assert r.status_code == 200 and r.headers["content-type"] == "image/png", r.text
    assert [b for path, b in fake.calls if path == "/render"][-1]["max_bytes"] == 1 << 20
    # The key's pictures count against the rolling pixel budget.
    window = token_client._transport.app.state.ui_designer_pixels["user:facade"]
    assert sum(mp for _t, mp in window) > 0


async def test_human_key_pixel_budget_is_enforced(sf, token_client):
    _fake_renderer(token_client)
    key = await _human_token(sf, "facade", "admin")
    await _real_doc(token_client, key)
    token_client._transport.app.state.settings.ui_designer_pixels_per_hour_mp = 0.0001
    url = f"/api/ui-designer/docs/{SLUG}/render?node=root"
    assert (await token_client.get(url, headers=key)).status_code == 200
    r = await token_client.get(url, headers=key)
    assert r.status_code == 429 and r.json()["detail"]["code"] == "UD-BUDGET-PIXELS", r.text


async def test_private_document_pictures_follow_document_access(sf, token_client):
    from .test_ui_designer_access import share
    _fake_renderer(token_client)
    owner = await _human_token(sf, "owner", "operator")
    reader = await _human_token(sf, "viewer", "operator")
    stranger = await _human_token(sf, "stranger", "operator")
    await _real_doc(token_client, owner, slug="private")
    assert (await share(token_client, owner, readers=["user:viewer"])).status_code == 200
    url = "/api/ui-designer/docs/private/render?node=root"
    assert (await token_client.get(url, headers=owner)).status_code == 200
    assert (await token_client.get(url, headers=reader)).status_code == 200
    assert (await token_client.get(url, headers=stranger)).status_code == 404
    body = {"ops": [{"op": "update", "id": "root", "set": {"w": 90}}], "expected_version": 1,
            "snapshot": ["root"]}
    for who in (reader, stranger):     # a reader may look but not edit; a stranger sees nothing
        r = await token_client.post("/api/ui-designer/docs/private/edit", json=body, headers=who)
        assert r.status_code == 404, r.text


async def test_runless_agent_key_gets_no_pictures(sf, seed_agent, agent_store, admin_client,
                                                  token_client):
    _fake_renderer(token_client)
    await _real_doc(admin_client)
    h = await _agent(sf, seed_agent, agent_store, "norun", run=False)
    r = await token_client.get(f"/api/ui-designer/docs/{SLUG}/render?node=root", headers=h)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "UD-NO-IMAGES-HERE", r.text
    body = {"ops": [], "expected_version": 1, "snapshot": True}
    r = await token_client.post(f"/api/ui-designer/docs/{SLUG}/edit", json=body, headers=h)
    assert r.status_code == 403 and "no run" in r.text, r.text
    # The admin session and an agent run get past the image fence (empty ops
    # are then refused as invalid).
    r = await admin_client.post(f"/api/ui-designer/docs/{SLUG}/edit", json=body)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "UD-INVALID", r.text
    h = await _agent(sf, seed_agent, agent_store, "pai")
    r = await token_client.post(f"/api/ui-designer/docs/{SLUG}/edit", json=body, headers=h)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "UD-INVALID", r.text


# --- local persona -------------------------------------------------------------------

async def test_local_persona_route_needs_the_grant(local, seed_agent, agent_store,  # noqa: F811
                                                   token_client, sf):
    from agentplatform import local_persona as lp
    path = f"/api/ui-designer/docs/{SLUG}/place"
    assert lp.route_allowed("POST", path, [UD])
    assert not lp.route_allowed("POST", path, [RELAY, ART])
    assert lp.route_allowed("GET", f"/api/ui-designer/docs/{SLUG}/outline", [UD])
    assert lp.route_allowed("GET", f"/api/ui-designer/docs/{SLUG}/render", [UD])     # standalone snapshot
    assert not lp.route_allowed("GET", f"/api/ui-designer/docs/{SLUG}/render", [RELAY])
    assert not lp.route_allowed("GET", f"/api/ui-designer/docs/{SLUG}/svg", [UD])
    base = f"/api/ui-designer/docs/{SLUG}"
    for method, p in (("GET", "/api/ui-designer/docs"), ("GET", base), ("GET", base + "/versions"),
                      ("GET", base + "/versions/3"), ("POST", base + "/edit"),
                      ("POST", base + "/restore"), ("POST", "/api/ui-designer/docs")):
        assert lp.route_allowed(method, p, [UD]) and not lp.route_allowed(method, p, [RELAY]), p
    for method, p in (("DELETE", base), ("POST", base + "/prune"), ("GET", "/api/ui-designer/assets/" + "a" * 64),
                      ("POST", "/api/ui-designer/assets/gc")):
        assert not lp.route_allowed(method, p, [UD]), p

    await _doc(sf)
    await seed_agent("carol", agent_type="persona", platform_tools=[
        "mcp__platform__memory", RELAY, UD, ART])
    await agent_store.reload()
    granted = await local(name="carol")
    r = await token_client.post(path, headers=granted["headers"], json={"artifact_id": "e" * 32})
    assert "not available to local personas" not in r.text, r.text
    assert r.status_code == 404, r.text            # through the fence to the route

    ungranted = await local()                       # alice: no ui_designer
    r = await token_client.post(path, headers=ungranted["headers"], json={"artifact_id": "e" * 32})
    assert r.status_code == 403 and "not available to local personas" in r.text, r.text
