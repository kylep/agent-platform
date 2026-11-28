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


async def test_start_test_inserts_a_scheduled_row_the_clock_opens(admin_client, sf):
    r = await admin_client.post("/api/village/sessions/test", json={"length_min": 8})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "test" and body["state"] == "scheduled" and body["length_min"] == 8
    assert body["quota_at_start"]["override"] is True
    assert (await admin_client.post("/api/village/sessions/test")).status_code == 409
    stop = await admin_client.post(f"/api/village/sessions/{body['id']}/stop")
    assert stop.json()["state"] == "skipped" and stop.json()["end_reason"].startswith("stopped by")


async def test_stop_pulls_a_live_sessions_end_in(admin_client, sf):
    from datetime import timedelta
    from agentplatform.db import utcnow
    async with sf() as s:
        s.add(VillageSession(id="s9", day="2026-11-27", kind="test", state="live", length_min=60,
                             planned_end=utcnow() + timedelta(hours=1)))
        await s.commit()
    r = await admin_client.post("/api/village/sessions/s9/stop")
    assert r.status_code == 200 and r.json()["state"] == "live"
    async with sf() as s:
        row = await s.get(VillageSession, "s9")
    end = row.planned_end.replace(tzinfo=None) if row.planned_end.tzinfo else row.planned_end
    assert end <= utcnow().replace(tzinfo=None) + timedelta(minutes=3, seconds=5)
    assert (await admin_client.post("/api/village/sessions/nope/stop")).status_code == 404


async def test_state_reads_the_real_world_snapshot_shape(admin_client, sf):
    from agentplatform.village.world import new_world
    w = new_world()
    w.actors["pai"]["section"] = TOKEN
    w.actors["pai"]["goals"] = [TOKEN]
    w.actors["pai"]["caption"] = "fetching wood"
    async with sf() as s:
        s.add(VillageSession(id="s2", day="2026-11-27", kind="test", state="live"))
        s.add(VillageSnapshot(session_id="s2", tick=w.tick, state_hash=w.state_hash(),
                              state=w.to_snapshot()))
        await s.commit()
    r = await admin_client.get("/api/village/state")
    st = r.json()["snapshot"]["state"]
    assert st["map"] == {"w": 64, "h": 40}
    (pai,) = st["characters"]
    assert pai["id"] == "pai" and pai["now"] == "fetching wood" and isinstance(pai["x"], int)
    assert TOKEN not in r.text and "sources" in st


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
