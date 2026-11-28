"""The village spectator API (GEN-11 U6): allowlist serializers, cursoring,
config validation, the stubbed admin controls and the pg-following stream."""
from agentplatform.api import village as village_api
from agentplatform.db import (VillageEvent, VillageNote, VillageSession,
                              VillageSnapshot)

from .test_relay_sse import sse  # noqa: F401

TOKEN = "PRIVATE-TOKEN-7731"


async def _seed(sf, state="live"):
    async with sf() as s:
        sess = VillageSession(id="s1", day="2026-11-27", kind="daily", state=state)
        s.add(sess)
        s.add(VillageNote(agent="pai", session_id="s1", text=TOKEN))
        s.add(VillageSnapshot(session_id="s1", tick=40, state_hash="h", state={
            "tick": 40, "secret_root": TOKEN,
            "characters": {"pai": {"x": 3, "y": 4, "face": "P", "notes": TOKEN,
                                   "steps": [{"op": "walk"}] * 9, "how_i_play": TOKEN}},
            "objects": [{"id": "o1", "section": TOKEN}]}))
        for i, kind in enumerate(("session_opened", "step_done"), 1):
            s.add(VillageEvent(session_id="s1", epoch="e", tick=i, kind=kind, actor="pai",
                               public_text=f"t{i}", data={"note": TOKEN, "ok": 1}))
        await s.commit()


async def test_requires_admin(client):
    assert (await client.get("/api/village/state")).status_code == 401


async def test_empty_state(admin_client):
    r = await admin_client.get("/api/village/state")
    assert r.status_code == 200
    body = r.json()
    assert body["session"] is None and body["snapshot"] is None and body["last_seq"] == 0


async def test_state_is_an_allowlist_and_never_carries_private_text(admin_client, sf):
    await _seed(sf)
    body = (await admin_client.get("/api/village/state")).json()
    assert body["session"]["id"] == "s1" and body["last_seq"] == 2
    chars = body["snapshot"]["state"]["characters"]
    assert len(chars["pai"]["steps"]) == village_api.MAX_PUBLIC_STEPS
    assert "secret_root" not in body["snapshot"]["state"]
    for path in ("/api/village/state", "/api/village/events", "/api/village/sessions",
                 "/api/village/admin", "/api/village/config"):
        assert TOKEN not in (await admin_client.get(path)).text, path


async def test_events_cursor(admin_client, sf):
    await _seed(sf)
    all_ = (await admin_client.get("/api/village/events")).json()
    assert [e["kind"] for e in all_["events"]] == ["session_opened", "step_done"]
    rest = (await admin_client.get(f"/api/village/events?after={all_['events'][0]['seq']}")).json()
    assert [e["kind"] for e in rest["events"]] == ["step_done"]
    assert rest["events"][0]["data"] == {"ok": 1}


async def test_config_validates_and_merges(admin_client):
    ok = await admin_client.put("/api/village/config", json={"rules": {"water_cycle": "off"}})
    assert ok.status_code == 200 and ok.json()["rules"]["water_cycle"] == "off"
    assert (await admin_client.get("/api/village/config")).json()["rules"]["water_cycle"] == "off"
    assert (await admin_client.put("/api/village/config",
                                   json={"rules": {"water_cycle": "maybe"}})).status_code == 422
    assert (await admin_client.put("/api/village/config",
                                   json={"rules": {"nope": 1}})).status_code == 422


async def test_admin_controls_are_stubbed_until_the_engine_command_port_exists(admin_client):
    assert (await admin_client.post("/api/village/sessions/test")).status_code == 503
    calls = []

    async def sender(action, args):
        calls.append((action, args.get("session_id")))
        return {"state": "applied"}

    admin_client._transport.app.state.village_command_sender = sender
    r = await admin_client.post("/api/village/sessions/s1/stop")
    assert r.json() == {"state": "applied"} and calls == [("stop", "s1")]


async def test_glossary_degrades_to_empty(admin_client):
    assert "terms" in (await admin_client.get("/api/village/glossary")).json()


async def test_stream_follows_the_event_rows(admin_client, sf):
    await _seed(sf)
    async with sse(admin_client, "/api/village/stream?after=1") as (resp, stream):
        assert resp["status"] == 200
        seen = {}
        for _ in range(3):
            event, data, _ = await stream.event()
            seen[event] = data
    assert seen["session"]["id"] == "s1"
    assert seen["event"]["kind"] == "step_done" and TOKEN not in str(seen)
    assert seen["frame"]["characters"][0]["id"] == "pai"
