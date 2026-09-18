"""The artifacts block's doors and its live feed (docs/design/23 T3): the
grant an agent needs to reach `/api/artifacts/*` at all, the stream off
`artifacts.events`, and the default grant a new agent is born holding.

What is held down is the same thing the wiki's tests hold down: the
participant ROLE is shared by every block, so a grant nobody gave an agent
must not be reachable through a grant somebody did.
"""
from agentplatform.api import relay as relay_api
from agentplatform.api.artifacts_feed import STREAM
from agentplatform.db import AgentDef
from agentplatform.events import TOPIC_ARTIFACTS_EVENTS

from .test_artifact_store import png_bytes
from .test_artifacts_api import _agent_headers, upload
from .test_relay_api import _human_token
from .test_relay_sse import StubConsumer, _msg, sse  # noqa: F401

RELAY_GRANT = "mcp__platform__relay"
TICKETS_GRANT = "mcp__platform__tickets"
WIKI_GRANT = "mcp__platform__wiki"
QUOTA_GRANT = "mcp__platform__get_quota_usage"
ARTIFACTS_GRANT = "mcp__platform__artifacts"
IMAGE_GEN_GRANT = "mcp__platform__image_gen"


# --- the fence -----------------------------------------------------------------

async def test_an_agent_holding_only_relay_is_refused(sf, seed_agent, agent_store,
                                                      token_client):
    """The `relay` rung reaches Relay, Tickets and the Wiki too, and the wiki
    is what taught this: with no room to be a member of, the grant itself is
    the only thing that bounds an agent here."""
    headers = await _agent_headers(sf, seed_agent, agent_store, "news",
                                   grants=(RELAY_GRANT,))
    r = await token_client.get("/api/artifacts", headers=headers)
    assert r.status_code == 403 and "artifacts" in r.json()["detail"]
    r = await token_client.post("/api/artifacts", json={"name": "a", "text": "x"},
                                headers=headers)
    assert r.status_code == 403, r.text
    r = await token_client.get("/api/artifacts/events", headers=headers)
    assert r.status_code == 403, r.text


async def test_either_artifact_grant_opens_the_door(sf, seed_agent, agent_store,
                                                    token_client):
    keeper = await _agent_headers(sf, seed_agent, agent_store, "news",
                                  grants=(RELAY_GRANT, ARTIFACTS_GRANT))
    assert (await token_client.get("/api/artifacts", headers=keeper)).status_code == 200
    a = await upload(token_client, png_bytes(), headers=keeper)
    assert a["owner"] == "agent:news"
    # The artist holds `image_gen` and needs the store its pictures land in.
    artist = await _agent_headers(sf, seed_agent, agent_store, "artist",
                                  grants=(RELAY_GRANT, IMAGE_GEN_GRANT))
    assert (await token_client.get("/api/artifacts", headers=artist)).status_code == 200


async def test_humans_are_judged_by_role_alone(sf, token_client):
    reader = await _human_token(sf, "kyle", "reader")
    assert (await token_client.get("/api/artifacts", headers=reader)).status_code == 200


# --- the stream ------------------------------------------------------------------

async def test_the_stream_replays_a_created_event_off_kafka(admin_client, sf):
    """Kafka-fed, the wiki shape: a create on ANY pod reaches this one's
    streams off `artifacts.events`, and the frame is the envelope's data —
    the event name and the artifact's metadata, never its bytes."""
    a = await upload(admin_client, png_bytes())
    feed = admin_client._transport.app.state.artifacts_feed
    async with sse(admin_client, "/api/artifacts/events") as (resp, stream):
        assert resp["status"] == 200
        assert resp["headers"]["content-type"].startswith("text/event-stream")
        payload = {"event": "created", "artifact": a, "agent": None}
        await feed.run(StubConsumer([_msg(TOPIC_ARTIFACTS_EVENTS, a["id"],
                                          "artifacts.event", payload)]))
        event, data, _ = await stream.event()
    assert event == "artifact"
    assert (data["event"], data["artifact"]["id"]) == ("created", a["id"])
    assert "thumb" not in data["artifact"]


async def test_the_stream_beats_and_carries_a_deletion_for_an_agent(
        admin_client, token_client, sf, seed_agent, agent_store, monkeypatch):
    monkeypatch.setattr(relay_api, "HEARTBEAT_SECONDS", 0.05)
    a = await upload(admin_client, png_bytes())
    headers = await _agent_headers(sf, seed_agent, agent_store, "news",
                                   grants=(RELAY_GRANT, ARTIFACTS_GRANT))
    feed = admin_client._transport.app.state.artifacts_feed
    async with sse(token_client, "/api/artifacts/events", headers=headers) as (_, stream):
        assert (await stream.frame()) == ": heartbeat"
        feed.publish(STREAM, "artifact", {"event": "deleted", "artifact": a,
                                          "agent": "news"})
        event, data, _ = await stream.event()
    assert (event, data["event"], data["agent"]) == ("artifact", "deleted", "news")


async def test_a_record_without_an_artifact_is_not_a_frame(admin_client):
    feed = admin_client._transport.app.state.artifacts_feed
    queue = feed.subscribe(STREAM)
    try:
        await feed.run(StubConsumer([_msg(TOPIC_ARTIFACTS_EVENTS, "k", "artifacts.event",
                                          {"event": "created"})]))
        assert queue.empty()
    finally:
        feed.unsubscribe(STREAM, queue)


async def test_a_face_clear_is_a_frame_without_an_artifact(admin_client):
    """`agent_image` with `artifact: null` is a CLEAR — the one record whose
    whole meaning is that there is no artifact — so it must pass the gate
    that drops artifact-less garbage."""
    feed = admin_client._transport.app.state.artifacts_feed
    queue = feed.subscribe(STREAM)
    try:
        await feed.run(StubConsumer([_msg(TOPIC_ARTIFACTS_EVENTS, "news", "artifacts.event",
                                          {"event": "agent_image", "artifact": None,
                                           "agent": "news"})]))
        event, data = queue.get_nowait()
        assert (event, data["event"], data["artifact"], data["agent"]) == (
            "artifact", "agent_image", None, "news")
    finally:
        feed.unsubscribe(STREAM, queue)


# --- the default grant --------------------------------------------------------

async def test_a_new_agent_is_born_holding_artifacts(admin_client, sf):
    r = await admin_client.post("/api/agents", json={"name": "newbie",
                                                     "description": "test",
                                                     "prompt": "# newbie"})
    assert r.status_code == 201, r.text
    assert ARTIFACTS_GRANT in r.json()["platform_tools"]
    assert IMAGE_GEN_GRANT not in r.json()["platform_tools"]
    async with sf() as s:
        assert ARTIFACTS_GRANT in (await s.get(AgentDef, "newbie")).platform_tools


async def test_the_artifacts_default_can_be_turned_off(admin_client, sf):
    admin_client._transport.app.state.settings.artifacts_default_grant = False
    r = await admin_client.post("/api/agents", json={"name": "plain",
                                                     "description": "test",
                                                     "prompt": "# plain"})
    assert r.status_code == 201, r.text
    assert ARTIFACTS_GRANT not in r.json()["platform_tools"]
    r = await admin_client.post("/api/agents", json={"name": "asked", "description": "t",
                                                     "prompt": "# asked",
                                                     "artifacts": True})
    assert r.status_code == 201, r.text
    assert ARTIFACTS_GRANT in r.json()["platform_tools"]
