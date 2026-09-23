"""Relay's REST surface (docs/design/19): channels, messages, DMs, reactions,
search, presence and stats. The authorship rules are the load-bearing part —
who a message is from is decided by the token, never by the payload."""
from datetime import timedelta

import pytest
from sqlalchemy import select

from agentplatform.apikeys import generate_token, hash_token, token_prefix
from agentplatform.config import Settings
from agentplatform.db import (ApiKey, Conversation, RelayInvocation, RelayMessage,
                              RelayParticipant, Run, RunState, utcnow)
from agentplatform.events import TOPIC_RELAY_MESSAGES
from agentplatform.relay_router import RelayRouter


async def _key(sf, *, name: str, role: str, agent: str | None = None,
               run_id: str | None = None) -> dict:
    token = generate_token()
    async with sf() as s:
        s.add(ApiKey(name=name, role=role, agent=agent, run_id=run_id,
                     key_hash=hash_token(token), prefix=token_prefix(token)))
        await s.commit()
    return {"Authorization": f"Bearer {token}"}


async def _agent_token(sf, agent: str, *, role: str = "relay", run_id: str | None = None) -> dict:
    """A per-run agent token. `relay` is T5's role and is deliberately used
    here already: the API must accept it before `auth.ROLES` has it, so the new
    role slots in without touching these routes."""
    return await _key(sf, name=f"run:{agent}", role=role, agent=agent, run_id=run_id)


async def _human_token(sf, principal: str, role: str) -> dict:
    """A human API key: no agent scope, so the caller is `user:<principal>`."""
    return await _key(sf, name=principal, role=role)


async def _revoke(sf, name: str) -> None:
    async with sf() as s:
        key = (await s.execute(select(ApiKey).where(ApiKey.name == name))).scalar_one()
        key.revoked_at = utcnow()
        await s.commit()


async def _channel_id(sf, name: str) -> str:
    async with sf() as s:
        return (await s.execute(select(Conversation.id).where(
            Conversation.kind == "channel", Conversation.name == name))).scalar_one()


async def _seed(seed_agent, agent_store, name: str, **fields):
    await seed_agent(name, description=f"{name} agent", **fields)
    await agent_store.reload()


async def test_post_resolves_mentions_and_publishes(admin_client, sf, producer,
                                                    seed_agent, agent_store):
    await _seed(seed_agent, agent_store, "news")
    cid = await _channel_id(sf, "general")
    r = await admin_client.post(f"/api/relay/channels/{cid}/messages",
                                json={"body": "morning @news — anything on the wire?"})
    assert r.status_code == 200, r.text
    m = r.json()
    assert m["author"] == "user:admin"
    assert m["mentions"] == ["news"]
    assert m["kind"] == "text" and m["thread_root"] is None

    envs = [e for e in producer.envelopes if e["type"] == "relay.message"]
    assert len(envs) == 1
    assert envs[0]["key"] == cid
    assert envs[0]["data"]["id"] == m["id"]
    assert envs[0]["data"]["channel_kind"] == "channel"
    assert envs[0]["data"]["mentions"] == ["news"]
    assert producer.published[-1][0] == TOPIC_RELAY_MESSAGES


async def test_reply_sets_thread_root(admin_client, sf):
    cid = await _channel_id(sf, "general")
    root = (await admin_client.post(f"/api/relay/channels/{cid}/messages",
                                    json={"body": "root"})).json()
    child = (await admin_client.post(f"/api/relay/channels/{cid}/messages",
                                     json={"body": "child", "reply_to": root["id"]})).json()
    grand = (await admin_client.post(f"/api/relay/channels/{cid}/messages",
                                     json={"body": "grand", "reply_to": child["id"]})).json()
    assert child["thread_root"] == root["id"]
    assert grand["thread_root"] == root["id"]

    r = await admin_client.get(f"/api/relay/channels/{cid}/messages",
                               params={"thread": root["id"]})
    assert {m["id"] for m in r.json()} == {root["id"], child["id"], grand["id"]}


async def test_agent_token_authors_as_its_own_agent(client, token_client, sf,
                                                    seed_agent, agent_store):
    """An agent posts as itself and cannot claim another author — there is no
    author field to send — and its room mentions are defanged at post time."""
    await _seed(seed_agent, agent_store, "news")
    cid = await _channel_id(sf, "general")
    headers = await _agent_token(sf, "news")
    r = await token_client.post(f"/api/relay/channels/{cid}/messages",
                                json={"body": "@all done — over to @hello-world"},
                                headers=headers)
    assert r.status_code == 200, r.text
    m = r.json()
    assert m["author"] == "agent:news"
    assert m["mentions"] == ["hello-world"]
    assert "@all" not in m["body"] and "all done" in m["body"]
    assert m["face"]["emoji"]


@pytest.mark.parametrize("run_id, hop", [("r-deep", 4), ("r-gone", 1)])
async def test_a_tool_post_carries_its_runs_next_hop(client, token_client, sf,
                                                     seed_agent, agent_store,
                                                     run_id, hop):
    """A message an agent posts through the `relay` tool is one hop further
    along than the mention that summoned it — the same sum the recorder makes
    for the run's final answer, read off `Run.depth`. Stamped here or the
    router's hop cap never fires for tool posts, and two agents can address
    each other forever. A token whose run is gone still posts as a run, so it
    is at least one hop from the human who started it."""
    await _seed(seed_agent, agent_store, "news")
    cid = await _channel_id(sf, "general")
    async with sf() as s:
        s.add(Run(id="r-deep", agent="news", trigger="mention",
                  requested_by="user:admin", conversation_id=cid, depth=3,
                  prompt="ctx", state=RunState.RUNNING))
        await s.commit()
    headers = await _agent_token(sf, "news", run_id=run_id)

    m = (await token_client.post(f"/api/relay/channels/{cid}/messages",
                                 json={"body": "still going"}, headers=headers)).json()
    assert (m["hop"], m["run_id"]) == (hop, run_id)


async def test_a_humans_message_starts_a_fresh_chain(admin_client, sf):
    """Hop 0 is what lets a person restart a thread the guards paused."""
    cid = await _channel_id(sf, "general")
    m = (await admin_client.post(f"/api/relay/channels/{cid}/messages",
                                 json={"body": "carry on"})).json()
    assert m["hop"] == 0 and m["run_id"] is None


async def _dm_with(client, other: str, headers=None) -> dict:
    return (await client.post("/api/relay/dm", json={"with": other},
                              headers=headers or {})).json()


async def _all_runs(sf):
    async with sf() as s:
        return list((await s.execute(select(Run))).scalars())


async def test_a_human_dm_post_is_a_turn_the_facade_owns(admin_client, sf, producer,
                                                         seed_agent, agent_store):
    """A DM with an agent is an implicit summons: every human message there is
    a turn. Both doors onto that room — this route and /api/conversations —
    must create exactly ONE run for it, which is why this one delegates to the
    facade and the router keeps its hands off."""
    await _seed(seed_agent, agent_store, "news")
    dm = await _dm_with(admin_client, "agent:news")
    r = await admin_client.post(f"/api/relay/channels/{dm['id']}/messages",
                                json={"body": "@news morning"})
    assert r.status_code == 200, r.text
    m = r.json()
    assert (m["author"], m["hop"], m["channel_id"]) == ("user:admin", 0, dm["id"])

    runs = await _all_runs(sf)
    assert len(runs) == 1
    assert (runs[0].trigger, runs[0].trigger_message_id) == ("conversation", m["id"])
    assert runs[0].conversation_id == dm["id"]

    # The router sees the very same message off `relay.messages`; it must not
    # answer a turn the facade already owns.
    router = RelayRouter(Settings(), sf, producer, agent_store)
    published = [d for t, _, d in producer.published if t == TOPIC_RELAY_MESSAGES]
    await router.handle(published[-1])
    assert len(await _all_runs(sf)) == 1
    async with sf() as s:
        decisions = [(i.agent, i.decision, i.reason)
                     for i in (await s.execute(select(RelayInvocation))).scalars()]
    assert decisions == [("news", "suppressed", "facade_owns_turn")]


async def test_a_dm_turn_while_one_is_in_flight_is_refused(admin_client, sf,
                                                           seed_agent, agent_store):
    """Turns are serialized, and the answer is the facade's own: a room with a
    run still thinking has nowhere to put a second one."""
    await _seed(seed_agent, agent_store, "news")
    dm = await _dm_with(admin_client, "agent:news")
    first = await admin_client.post(f"/api/relay/channels/{dm['id']}/messages",
                                    json={"body": "one"})
    second = await admin_client.post(f"/api/relay/channels/{dm['id']}/messages",
                                     json={"body": "two"})
    assert first.status_code == 200
    assert second.status_code == 409 and "turn in progress" in second.json()["detail"]
    assert len(await _all_runs(sf)) == 1


async def test_a_dm_turn_for_a_disabled_agent_is_refused(admin_client, sf,
                                                        seed_agent, agent_store):
    """The soft off-switch reaches this door too: a turn for a disabled agent
    would be a run nothing will ever pick up. Same answer, same words as
    /api/conversations gives."""
    await _seed(seed_agent, agent_store, "news")
    dm = await _dm_with(admin_client, "agent:news")
    await _seed(seed_agent, agent_store, "news", enabled=False)
    r = await admin_client.post(f"/api/relay/channels/{dm['id']}/messages",
                                json={"body": "still there?"})
    assert r.status_code == 409 and r.json()["detail"] == "agent is disabled"
    assert await _all_runs(sf) == []


async def test_an_agents_dm_post_takes_the_generic_path(client, token_client, sf,
                                                        seed_agent, agent_store):
    """Only a HUMAN's DM message is a turn. An agent posting in a DM is posting
    a message: the run it is already inside answers for it, and the router
    summons on the mention as it does anywhere else."""
    await _seed(seed_agent, agent_store, "news")
    await _seed(seed_agent, agent_store, "ada")
    headers = await _agent_token(sf, "news")
    dm = await _dm_with(token_client, "agent:ada", headers)
    r = await token_client.post(f"/api/relay/channels/{dm['id']}/messages",
                                json={"body": "@ada thoughts?"}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["author"] == "agent:news" and r.json()["mentions"] == ["ada"]
    assert await _all_runs(sf) == []


async def test_open_channel_admits_every_agent_but_a_group_does_not(
        admin_client, token_client, sf, seed_agent, agent_store):
    await _seed(seed_agent, agent_store, "news")
    headers = await _agent_token(sf, "news")
    group = (await admin_client.post("/api/relay/channels", json={
        "kind": "group", "topic": "humans only", "participants": ["user:admin"]})).json()

    r = await token_client.post(f"/api/relay/channels/{group['id']}/messages",
                                json={"body": "hello?"}, headers=headers)
    assert r.status_code == 403

    # The group is not even visible to a non-member, but every open channel is.
    listed = (await token_client.get("/api/relay/channels", headers=headers)).json()
    assert group["id"] not in {c["id"] for c in listed}
    assert {"general", "ops", "standup"} <= {c["name"] for c in listed}
    open_id = await _channel_id(sf, "ops")
    assert (await token_client.post(f"/api/relay/channels/{open_id}/messages",
                                    json={"body": "ping"}, headers=headers)).status_code == 200


async def test_agent_may_only_create_groups(token_client, sf, seed_agent, agent_store):
    await _seed(seed_agent, agent_store, "news")
    headers = await _agent_token(sf, "news")
    assert (await token_client.post("/api/relay/channels", json={
        "kind": "channel", "name": "agent-made"}, headers=headers)).status_code == 403
    r = await token_client.post("/api/relay/channels",
                                json={"kind": "group", "topic": "triage"}, headers=headers)
    assert r.status_code == 201, r.text
    # Auto-added, or it could not post in the room it just made.
    assert "agent:news" in r.json()["participants"]


async def test_channel_name_is_a_slug_and_unique(admin_client):
    assert (await admin_client.post("/api/relay/channels",
                                    json={"kind": "channel", "name": "Not A Slug"})).status_code == 422
    assert (await admin_client.post("/api/relay/channels",
                                    json={"kind": "channel"})).status_code == 422
    assert (await admin_client.post("/api/relay/channels",
                                    json={"kind": "channel", "name": "design"})).status_code == 201
    assert (await admin_client.post("/api/relay/channels",
                                    json={"kind": "channel", "name": "design"})).status_code == 409
    assert (await admin_client.post("/api/relay/channels",
                                    json={"kind": "nope", "name": "x"})).status_code == 422
    # An open channel has no membership rows, so a participant list there would
    # be rows nothing reads.
    assert (await admin_client.post("/api/relay/channels", json={
        "kind": "channel", "name": "briefing",
        "participants": ["user:admin"]})).status_code == 422
    assert (await admin_client.post("/api/relay/channels",
                                    json={"kind": "group", "open": True})).status_code == 422
    assert (await admin_client.post("/api/relay/channels", json={
        "kind": "group", "participants": ["not-a-participant"]})).status_code == 422


async def test_a_closed_channel_is_membership_only(admin_client, token_client, sf,
                                                   seed_agent, agent_store):
    await _seed(seed_agent, agent_store, "news")
    headers = await _agent_token(sf, "news")
    private = (await admin_client.post("/api/relay/channels", json={
        "kind": "channel", "name": "war-room", "open": False,
        "participants": ["user:admin"]})).json()
    assert private["open"] is False
    assert (await token_client.post(f"/api/relay/channels/{private['id']}/messages",
                                    json={"body": "let me in"}, headers=headers)).status_code == 403
    assert (await admin_client.post(f"/api/relay/channels/{private['id']}/messages",
                                    json={"body": "just us"})).status_code == 200


async def test_dm_is_get_or_create_and_keeps_the_legacy_agent(admin_client, sf,
                                                              seed_agent, agent_store):
    await _seed(seed_agent, agent_store, "news")
    first = await admin_client.post("/api/relay/dm", json={"with": "agent:news"})
    assert first.status_code == 200, first.text
    again = await admin_client.post("/api/relay/dm", json={"with": "agent:news"})
    assert again.json()["id"] == first.json()["id"]
    assert first.json()["kind"] == "dm"
    assert set(first.json()["participants"]) == {"agent:news", "user:admin"}

    async with sf() as s:
        conv = await s.get(Conversation, first.json()["id"])
        assert conv.agent == "news"                       # the legacy facade still works
        assert conv.title == "dm:agent:news:user:admin"
    assert (await admin_client.post("/api/relay/dm",
                                    json={"with": "agent:nobody"})).status_code == 404


async def test_reactions_toggle(admin_client, sf):
    cid = await _channel_id(sf, "general")
    m = (await admin_client.post(f"/api/relay/channels/{cid}/messages",
                                 json={"body": "ship it"})).json()
    on = await admin_client.post(f"/api/relay/messages/{m['id']}/reactions",
                                 json={"emoji": "🎉"})
    assert on.json() == {"emoji": "🎉", "count": 1, "mine": True}
    off = await admin_client.post(f"/api/relay/messages/{m['id']}/reactions",
                                  json={"emoji": "🎉"})
    assert off.json() == {"emoji": "🎉", "count": 0, "mine": False}

    await admin_client.post(f"/api/relay/messages/{m['id']}/reactions", json={"emoji": "🎉"})
    listed = (await admin_client.get(f"/api/relay/channels/{cid}/messages")).json()
    assert listed[0]["reactions"] == [{"emoji": "🎉", "count": 1, "mine": True}]


async def test_a_reaction_has_to_be_a_glyph(admin_client, sf):
    """The field is called `emoji` and its value is echoed to every viewer of
    the room, in the message payload and in the SSE frame. `<script>` is not a
    reaction, and a pill is no place to find out what a client does with one."""
    cid = await _channel_id(sf, "general")
    m = (await admin_client.post(f"/api/relay/channels/{cid}/messages",
                                 json={"body": "ship it"})).json()
    for bad in ("<script>", "a&b", "a b", "\u0007", "", "x" * 17):
        r = await admin_client.post(f"/api/relay/messages/{m['id']}/reactions",
                                    json={"emoji": bad})
        assert r.status_code == 422, (bad, r.text)
    # One emoji is not one code point: a skin-toned family of four is eleven,
    # and a cap that counted them as characters refused an ordinary reaction.
    family = "\U0001f468\U0001f3fd\u200d\U0001f469\U0001f3fd\u200d" \
             "\U0001f467\U0001f3fd\u200d\U0001f466\U0001f3fd"
    assert len(family) == 11
    for good in ("🎉", "👍", "🏳️‍🌈", family):
        r = await admin_client.post(f"/api/relay/messages/{m['id']}/reactions",
                                    json={"emoji": good})
        assert r.status_code == 200, (good, r.text)


async def test_search_finds_a_word_and_respects_visibility(
        admin_client, token_client, sf, seed_agent, agent_store):
    await _seed(seed_agent, agent_store, "news")
    headers = await _agent_token(sf, "news")
    cid = await _channel_id(sf, "general")
    await admin_client.post(f"/api/relay/channels/{cid}/messages",
                            json={"body": "the Kafka lag is back to zero"})
    group = (await admin_client.post("/api/relay/channels", json={
        "kind": "group", "participants": ["user:admin"]})).json()
    await admin_client.post(f"/api/relay/channels/{group['id']}/messages",
                            json={"body": "kafka rotation plan, humans only"})

    mine = (await admin_client.get("/api/relay/search", params={"q": "kafka"})).json()
    assert len(mine) == 2
    theirs = (await token_client.get("/api/relay/search",
                                     params={"q": "kafka"}, headers=headers)).json()
    assert [m["channel_id"] for m in theirs] == [cid]
    scoped = (await admin_client.get("/api/relay/search",
                                     params={"q": "kafka", "channel": cid})).json()
    assert [m["channel_id"] for m in scoped] == [cid]


async def test_project_search_keeps_room_visibility(
        admin_client, token_client, sf, seed_agent, agent_store):
    await _seed(seed_agent, agent_store, "news")
    headers = await _agent_token(sf, "news")
    cid = await _channel_id(sf, "general")
    project = (await admin_client.post("/api/projects", json={
        "slug": "newsroom", "name": "Newsroom", "agents": ["news"]})).json()
    assert project["slug"] == "newsroom"
    group = (await admin_client.post("/api/relay/channels", json={
        "kind": "group", "participants": ["user:admin"]})).json()
    for room in (cid, group["id"]):
        scoped = await admin_client.put(f"/api/relay/channels/{room}/scope",
                                         json={"project_slug": "newsroom"})
        assert scoped.status_code == 200, scoped.text
        await admin_client.post(f"/api/relay/channels/{room}/messages",
                                json={"body": "newsroom budget"})
    mine = (await admin_client.get("/api/relay/search", params={
        "q": "budget", "project": "newsroom"})).json()
    assert {m["channel_id"] for m in mine} == {cid, group["id"]}
    theirs = (await token_client.get("/api/relay/search", params={
        "q": "budget", "project": "newsroom"}, headers=headers)).json()
    assert [m["channel_id"] for m in theirs] == [cid]


async def test_search_takes_a_channel_by_name_as_well_as_by_id(admin_client, sf):
    """`channel=#general` answered `[]` — indistinguishable from "nothing
    matched" — while the same room by id answered fine. A channel reference is
    one thing across the platform: the sigil form, the bare name, or the id."""
    cid = await _channel_id(sf, "general")
    await admin_client.post(f"/api/relay/channels/{cid}/messages",
                            json={"body": "the Kafka lag is back to zero"})

    async def found(channel):
        r = await admin_client.get("/api/relay/search",
                                   params={"q": "kafka", "channel": channel})
        assert r.status_code == 200, r.text
        return [m["channel_id"] for m in r.json()]

    assert await found("#general") == [cid]
    assert await found("general") == [cid]
    assert await found(cid) == [cid]
    r = await admin_client.get("/api/relay/search",
                               params={"q": "kafka", "channel": "nowhere"})
    assert r.status_code == 404, r.text


async def test_presence_reports_thinking_with_its_channel(admin_client, sf,
                                                          seed_agent, agent_store):
    await _seed(seed_agent, agent_store, "news")
    await _seed(seed_agent, agent_store, "sleepy", enabled=False)
    cid = await _channel_id(sf, "general")
    async with sf() as s:
        s.add(Run(agent="news", trigger="mention", requested_by="user:admin",
                  prompt="ctx", state=RunState.RUNNING, conversation_id=cid))
        await s.commit()

    by_agent = {p["agent"]: p for p in (await admin_client.get("/api/relay/presence")).json()}
    assert by_agent["news"]["state"] == "thinking"
    assert by_agent["news"]["thinking_in"] == [cid]
    assert by_agent["news"]["face"]["emoji"]
    assert by_agent["sleepy"]["state"] == "disabled"
    assert by_agent["hello-world"]["state"] == "idle"


async def test_presence_prefers_the_agents_own_icon(admin_client, seed_agent, agent_store):
    await _seed(seed_agent, agent_store, "news", icon="📰")
    by_agent = {p["agent"]: p for p in (await admin_client.get("/api/relay/presence")).json()}
    assert by_agent["news"]["face"]["emoji"] == "📰"
    assert by_agent["news"]["face"]["image_url"] is None


async def test_a_face_carries_the_agents_picture(admin_client, sf, token_client,
                                                 seed_agent, agent_store):
    """One `faces_for` serves every consumer (docs/design/23): the picture set
    through the agents API shows up on the room's roster, on presence and on
    the message, with no per-consumer change."""
    from agentplatform.relay_store import faces_for

    from .test_artifact_store import png_bytes
    from .test_artifacts_api import upload
    await _seed(seed_agent, agent_store, "news", icon="📰")
    art = await upload(admin_client, png_bytes(8, 8))
    r = await admin_client.put("/api/agents/news/image", json={"artifact_id": art["id"]})
    assert r.status_code == 200, r.text
    thumb = f"/api/artifacts/{art['id']}/thumb"

    async with sf() as s:
        faces = await faces_for(s, {"news", "hello-world", "nobody"})
    assert faces["news"]["emoji"] == "📰" and faces["news"]["image_url"] == thumb
    assert faces["hello-world"]["image_url"] is None and faces["nobody"]["image_url"] is None

    # A room's roster is its explicit members; a DM with the agent has one.
    dm = (await admin_client.post("/api/relay/dm", json={"with": "agent:news"})).json()
    detail = (await admin_client.get(f"/api/relay/channels/{dm['id']}")).json()
    assert detail["faces"]["news"]["image_url"] == thumb
    cid = await _channel_id(sf, "general")
    by_agent = {p["agent"]: p for p in (await admin_client.get("/api/relay/presence")).json()}
    assert by_agent["news"]["face"]["image_url"] == thumb
    m = (await token_client.post(f"/api/relay/channels/{cid}/messages", json={"body": "hi"},
                                 headers=await _agent_token(sf, "news"))).json()
    assert m["face"] == {"emoji": "📰", "hue": faces["news"]["hue"], "image_url": thumb}


async def test_stats_counts_the_last_day(admin_client, sf, token_client,
                                         seed_agent, agent_store):
    await _seed(seed_agent, agent_store, "news")
    cid = await _channel_id(sf, "general")
    headers = await _agent_token(sf, "news")
    # The seeds already wrote today (#art's welcome row): count from there.
    seeded = (await admin_client.get("/api/relay/stats")).json()["messages_24h"]
    await admin_client.post(f"/api/relay/channels/{cid}/messages", json={"body": "hi"})
    await token_client.post(f"/api/relay/channels/{cid}/messages",
                            json={"body": "hi back"}, headers=headers)
    async with sf() as s:
        s.add(RelayInvocation(channel_id=cid, message_id="m1", agent="news",
                              decision="invoked", hop=0))
        s.add(RelayInvocation(channel_id=cid, message_id="m2", agent="news",
                              decision="suppressed", reason="budget", hop=1))
        s.add(RelayInvocation(channel_id=cid, message_id="m0", agent="news",
                              decision="invoked", hop=0,
                              created_at=utcnow() - timedelta(days=3)))
        await s.commit()

    st = (await admin_client.get("/api/relay/stats")).json()
    assert st["messages_24h"] == seeded + 2
    assert st["agent_messages_24h"] == 1
    # Invoked only: the tile says "invocations", so a refusal must not raise it.
    assert st["invocations_24h"] == 1
    assert st["suppressed_24h"] == 1
    assert st["budget"] == {"channel_per_hour": 30, "global_per_hour": 120,
                            "global_used_last_hour": 1}


async def test_suppressed_counts_only_the_refusals(admin_client, sf):
    """A coalesced wake and a DM turn the facade owns are suppressions the way
    a green build is a failure: recorded, not wrong. Counting them as trouble
    is what had the dashboard reporting a problem in a healthy room — so the
    headline number is the three refusals, and the breakdown carries the rest."""
    cid = await _channel_id(sf, "general")
    async with sf() as s:
        for i, reason in enumerate(["hop_limit", "budget", "not_member",
                                    "coalesced", "coalesced", "facade_owns_turn"]):
            s.add(RelayInvocation(channel_id=cid, message_id=f"m{i}", agent="news",
                                  decision="suppressed", reason=reason, hop=1))
        # Yesterday's refusal is not today's, and an invoked row is nobody's
        # suppression.
        s.add(RelayInvocation(channel_id=cid, message_id="old", agent="news",
                              decision="suppressed", reason="budget", hop=1,
                              created_at=utcnow() - timedelta(days=3)))
        s.add(RelayInvocation(channel_id=cid, message_id="ok", agent="news",
                              decision="invoked", reason="mention", hop=0))
        await s.commit()
    st = (await admin_client.get("/api/relay/stats")).json()
    assert st["suppressed_24h"] == 3
    assert st["suppressed_by_reason"] == {"hop_limit": 1, "budget": 1, "not_member": 1,
                                          "coalesced": 2, "facade_owns_turn": 1}


async def test_every_known_reason_is_reported_even_at_zero(admin_client):
    """Zero-filled, so a reader can tell "nothing was refused for that reason"
    from "that reason no longer exists"."""
    st = (await admin_client.get("/api/relay/stats")).json()
    assert st["suppressed_24h"] == 0
    assert st["suppressed_by_reason"] == {"hop_limit": 0, "budget": 0, "not_member": 0,
                                          "coalesced": 0, "facade_owns_turn": 0}


async def test_archived_channel_stops_taking_messages(admin_client, sf):
    cid = await _channel_id(sf, "ops")
    assert (await admin_client.patch(f"/api/relay/channels/{cid}",
                                     json={"topic": "quiet now", "archived": True})).status_code == 200
    r = await admin_client.post(f"/api/relay/channels/{cid}/messages", json={"body": "hello?"})
    assert r.status_code == 404
    assert cid not in {c["id"] for c in (await admin_client.get("/api/relay/channels")).json()}
    assert (await admin_client.get(f"/api/relay/channels/{cid}")).json()["archived_at"]


async def test_channel_can_show_agent_replies_as_a_linear_stream(admin_client):
    made = (await admin_client.post("/api/relay/channels", json={
        "kind": "channel", "name": "live-table"})).json()
    cid = made["id"]
    assert made["reply_mode"] == "threaded"
    changed = await admin_client.patch(f"/api/relay/channels/{cid}",
                                       json={"reply_mode": "linear"})
    assert changed.status_code == 200
    assert changed.json()["reply_mode"] == "linear"
    assert (await admin_client.get(f"/api/relay/channels/{cid}")) \
        .json()["reply_mode"] == "linear"
    assert (await admin_client.patch(f"/api/relay/channels/{cid}",
                                     json={"reply_mode": "buried"})).status_code == 422


async def test_seeded_channels_are_not_deletable(admin_client, sf):
    assert (await admin_client.delete(
        f"/api/relay/channels/{await _channel_id(sf, 'general')}")).status_code == 409
    made = (await admin_client.post("/api/relay/channels",
                                    json={"kind": "channel", "name": "scratch"})).json()
    assert (await admin_client.delete(f"/api/relay/channels/{made['id']}")).status_code == 200
    async with sf() as s:
        assert (await s.get(Conversation, made["id"])).archived_at is not None


async def test_channel_list_ordering_unread_and_last_message(admin_client, token_client,
                                                             sf, seed_agent, agent_store):
    await _seed(seed_agent, agent_store, "news")
    headers = await _agent_token(sf, "news")
    ops = await _channel_id(sf, "ops")
    dm = (await admin_client.post("/api/relay/dm", json={"with": "agent:news"})).json()
    group = (await admin_client.post("/api/relay/channels", json={
        "kind": "group", "participants": ["user:admin", "agent:news"]})).json()
    await admin_client.post(f"/api/relay/channels/{group['id']}/messages", json={"body": "g"})
    await admin_client.post(f"/api/relay/channels/{dm['id']}/messages", json={"body": "d"})

    await admin_client.post(f"/api/relay/channels/{ops}/messages", json={"body": "mine"})
    await token_client.post(f"/api/relay/channels/{ops}/messages",
                            json={"body": "theirs"}, headers=headers)
    await token_client.post(f"/api/relay/channels/{ops}/messages",
                            json={"body": "also theirs"}, headers=headers)

    listed = (await admin_client.get("/api/relay/channels")).json()
    # Channels by name, then the private rooms by last activity (the DM spoke last).
    assert [c["name"] for c in listed[:7]] == ["art", "eng", "general", "ops",
                                               "qa", "standup", "wiki"]
    assert [c["id"] for c in listed[7:]] == [dm["id"], group["id"]]

    by_id = {c["id"]: c for c in listed}
    assert by_id[ops]["unread"] == 2
    assert by_id[ops]["message_count"] == 3
    assert by_id[ops]["last_message"]["body"] == "also theirs"
    assert by_id[ops]["last_message"]["author"] == "agent:news"
    # A room the caller never spoke in reports nothing unread, not everything.
    assert by_id[await _channel_id(sf, "general")]["unread"] == 0
    assert by_id[group["id"]]["participants"] == ["agent:news", "user:admin"]


async def test_messages_page_newest_first(admin_client, sf):
    cid = await _channel_id(sf, "general")
    ids = [(await admin_client.post(f"/api/relay/channels/{cid}/messages",
                                    json={"body": f"m{i}"})).json()["id"] for i in range(5)]
    page = (await admin_client.get(f"/api/relay/channels/{cid}/messages",
                                   params={"limit": 2})).json()
    assert [m["id"] for m in page] == list(reversed(ids))[:2]
    older = (await admin_client.get(f"/api/relay/channels/{cid}/messages",
                                    params={"limit": 2, "before": page[-1]["id"]})).json()
    assert [m["id"] for m in older] == list(reversed(ids))[2:4]


async def test_messages_after_a_cursor_page_oldest_first(admin_client, sf):
    """The catch-up direction: a client whose stream was down asks for what it
    missed and gets it in the order it happened, from its own cursor — and the
    cap keeps the OLDEST of those, so the next page continues rather than
    leaving a hole in the middle of the room."""
    cid = await _channel_id(sf, "general")
    ids = [(await admin_client.post(f"/api/relay/channels/{cid}/messages",
                                    json={"body": f"m{i}"})).json()["id"] for i in range(5)]
    page = (await admin_client.get(f"/api/relay/channels/{cid}/messages",
                                   params={"after": ids[1]})).json()
    assert [m["id"] for m in page] == ids[2:]
    assert [m["body"] for m in page] == ["m2", "m3", "m4"]
    capped = (await admin_client.get(f"/api/relay/channels/{cid}/messages",
                                     params={"after": ids[1], "limit": 2})).json()
    assert [m["id"] for m in capped] == ids[2:4]
    # Caught up: nothing newer than the newest.
    assert (await admin_client.get(f"/api/relay/channels/{cid}/messages",
                                   params={"after": ids[-1]})).json() == []


async def test_after_and_before_together_are_refused(admin_client, sf):
    """A range is a different contract; honouring one of the two quietly would
    hand a paging client a gap it cannot see."""
    cid = await _channel_id(sf, "general")
    m = (await admin_client.post(f"/api/relay/channels/{cid}/messages",
                                 json={"body": "only one"})).json()
    r = await admin_client.get(f"/api/relay/channels/{cid}/messages",
                               params={"after": m["id"], "before": m["id"]})
    assert r.status_code == 422


async def test_an_unplaceable_after_cursor_replays_nothing(admin_client, sf):
    """`before` 422s (the reader asked for a page that cannot exist), but
    `after` returns nothing, exactly as the SSE replay does: a pruned or
    foreign cursor answered with a page would duplicate what the client is
    already showing."""
    cid = await _channel_id(sf, "general")
    other = await _channel_id(sf, "ops")
    stray = (await admin_client.post(f"/api/relay/channels/{other}/messages",
                                     json={"body": "elsewhere"})).json()
    await admin_client.post(f"/api/relay/channels/{cid}/messages", json={"body": "here"})
    for cursor in (stray["id"], "nope"):
        r = await admin_client.get(f"/api/relay/channels/{cid}/messages",
                                   params={"after": cursor})
        assert r.status_code == 200 and r.json() == []


async def test_after_and_thread_narrow_together(admin_client, sf):
    cid = await _channel_id(sf, "general")
    root = (await admin_client.post(f"/api/relay/channels/{cid}/messages",
                                    json={"body": "root"})).json()
    first = (await admin_client.post(f"/api/relay/channels/{cid}/messages",
                                     json={"body": "in thread", "reply_to": root["id"]})).json()
    await admin_client.post(f"/api/relay/channels/{cid}/messages",
                            json={"body": "elsewhere in the room"})
    second = (await admin_client.post(f"/api/relay/channels/{cid}/messages",
                                      json={"body": "later in thread",
                                            "reply_to": root["id"]})).json()
    page = (await admin_client.get(f"/api/relay/channels/{cid}/messages",
                                   params={"after": first["id"],
                                           "thread": root["id"]})).json()
    assert [m["id"] for m in page] == [second["id"]]


async def test_paging_cursor_must_be_a_message_in_this_channel(admin_client, sf):
    cid = await _channel_id(sf, "general")
    other = await _channel_id(sf, "ops")
    stray = (await admin_client.post(f"/api/relay/channels/{other}/messages",
                                     json={"body": "elsewhere"})).json()
    assert (await admin_client.get(f"/api/relay/channels/{cid}/messages",
                                   params={"before": stray["id"]})).status_code == 422
    assert (await admin_client.get(f"/api/relay/channels/{cid}/messages",
                                   params={"before": "nope"})).status_code == 422


async def test_deleted_messages_stay_out_of_the_page(admin_client, sf):
    cid = await _channel_id(sf, "general")
    m = (await admin_client.post(f"/api/relay/channels/{cid}/messages",
                                 json={"body": "oops"})).json()
    async with sf() as s:
        row = await s.get(RelayMessage, m["id"])
        row.deleted_at = utcnow()
        await s.commit()
    assert (await admin_client.get(f"/api/relay/channels/{cid}/messages")).json() == []


async def test_relay_needs_a_caller(client, sf):
    cid = await _channel_id(sf, "general")
    assert (await client.get("/api/relay/channels")).status_code == 401
    assert (await client.post(f"/api/relay/channels/{cid}/messages",
                              json={"body": "anon"})).status_code == 401


async def test_agents_cannot_administer_channels(admin_client, token_client, sf,
                                                 seed_agent, agent_store):
    await _seed(seed_agent, agent_store, "news")
    headers = await _agent_token(sf, "news")
    made = (await admin_client.post("/api/relay/channels",
                                    json={"kind": "channel", "name": "design"})).json()
    assert (await token_client.patch(f"/api/relay/channels/{made['id']}",
                                     json={"topic": "mine now"},
                                     headers=headers)).status_code == 403
    assert (await token_client.delete(f"/api/relay/channels/{made['id']}",
                                      headers=headers)).status_code == 403


async def test_group_membership_is_explicit(admin_client, token_client, sf,
                                            seed_agent, agent_store):
    await _seed(seed_agent, agent_store, "news")
    headers = await _agent_token(sf, "news")
    group = (await admin_client.post("/api/relay/channels", json={
        "kind": "group", "participants": ["user:admin", "agent:news"]})).json()
    r = await token_client.post(f"/api/relay/channels/{group['id']}/messages",
                                json={"body": "in the room"}, headers=headers)
    assert r.status_code == 200, r.text
    async with sf() as s:
        parts = (await s.execute(select(RelayParticipant.participant).where(
            RelayParticipant.channel_id == group["id"]))).scalars().all()
    assert set(parts) == {"user:admin", "agent:news"}


@pytest.mark.parametrize("role,want", [
    ("relay", 200),         # T5's role for a grant-holding agent
    ("annotator", 200),     # what a system agent already carries
    ("operator", 200),
    ("tools", 403),         # reaches nothing but /api/whoami, by design
    ("session", 403),       # exists only to move a resume blob
    ("reader", 403),        # a human scope; naming an agent does not earn a voice
])
async def test_which_agent_token_roles_reach_relay(client, token_client, sf, seed_agent,
                                                   agent_store, role, want):
    await _seed(seed_agent, agent_store, "news")
    cid = await _channel_id(sf, "general")
    headers = await _agent_token(sf, "news", role=role)
    r = await token_client.post(f"/api/relay/channels/{cid}/messages",
                                json={"body": "hi"}, headers=headers)
    assert r.status_code == want, r.text


async def test_a_tools_token_reaches_nothing_in_relay(admin_client, token_client, sf,
                                                      seed_agent, agent_store):
    await _seed(seed_agent, agent_store, "news")
    cid = await _channel_id(sf, "general")
    m = (await admin_client.post(f"/api/relay/channels/{cid}/messages",
                                 json={"body": "hi"})).json()
    headers = await _agent_token(sf, "news", role="tools")
    calls = [
        ("get", "/api/relay/channels", None),
        ("get", f"/api/relay/channels/{cid}", None),
        ("get", f"/api/relay/channels/{cid}/messages", None),
        ("get", "/api/relay/search?q=hi", None),
        ("post", "/api/relay/channels", {"kind": "group"}),
        ("post", f"/api/relay/channels/{cid}/messages", {"body": "x"}),
        ("post", f"/api/relay/messages/{m['id']}/reactions", {"emoji": "👍"}),
        ("post", "/api/relay/dm", {"with": "user:admin"}),
    ]
    for method, path, body in calls:
        kwargs = {"headers": headers} | ({"json": body} if body is not None else {})
        r = await getattr(token_client, method)(path, **kwargs)
        assert r.status_code == 403, (path, r.status_code)


async def test_a_reader_may_look_but_not_speak(admin_client, token_client, sf):
    cid = await _channel_id(sf, "general")
    m = (await admin_client.post(f"/api/relay/channels/{cid}/messages",
                                 json={"body": "hello"})).json()
    headers = await _human_token(sf, "kyle", "reader")
    for path in ("/api/relay/channels", f"/api/relay/channels/{cid}",
                 f"/api/relay/channels/{cid}/messages", "/api/relay/search?q=hello"):
        assert (await token_client.get(path, headers=headers)).status_code == 200, path
    writes = [
        (f"/api/relay/channels/{cid}/messages", {"body": "x"}),
        (f"/api/relay/messages/{m['id']}/reactions", {"emoji": "👍"}),
        ("/api/relay/dm", {"with": "agent:hello-world"}),
        ("/api/relay/channels", {"kind": "channel", "name": "reader-made"}),
    ]
    for path, body in writes:
        assert (await token_client.post(path, json=body,
                                        headers=headers)).status_code == 403, path


async def test_a_revoked_token_is_shut_out(token_client, sf, seed_agent, agent_store):
    await _seed(seed_agent, agent_store, "news")
    headers = await _agent_token(sf, "news")
    assert (await token_client.get("/api/relay/channels", headers=headers)).status_code == 200
    await _revoke(sf, "run:news")
    assert (await token_client.get("/api/relay/channels", headers=headers)).status_code == 401


async def test_a_non_member_agent_cannot_read_or_react(admin_client, token_client, sf,
                                                       seed_agent, agent_store):
    await _seed(seed_agent, agent_store, "news")
    headers = await _agent_token(sf, "news")
    group = (await admin_client.post("/api/relay/channels", json={
        "kind": "group", "participants": ["user:admin"]})).json()
    m = (await admin_client.post(f"/api/relay/channels/{group['id']}/messages",
                                 json={"body": "private"})).json()
    assert (await token_client.get(f"/api/relay/channels/{group['id']}",
                                   headers=headers)).status_code == 403
    assert (await token_client.get(f"/api/relay/channels/{group['id']}/messages",
                                   headers=headers)).status_code == 403
    assert (await token_client.post(f"/api/relay/messages/{m['id']}/reactions",
                                    json={"emoji": "👍"}, headers=headers)).status_code == 403


async def test_a_mention_cannot_reach_outside_a_closed_room(admin_client, sf,
                                                            seed_agent, agent_store):
    """`@news` in a group news is not in stays text: summoning it there would
    hand it the messages its absence was meant to withhold."""
    await _seed(seed_agent, agent_store, "news")
    group = (await admin_client.post("/api/relay/channels", json={
        "kind": "group", "participants": ["user:admin", "agent:hello-world"]})).json()
    m = (await admin_client.post(f"/api/relay/channels/{group['id']}/messages",
                                 json={"body": "@news @hello-world take a look"})).json()
    assert m["mentions"] == ["hello-world"]
    # The same words in an open channel do summon it.
    cid = await _channel_id(sf, "general")
    live = (await admin_client.post(f"/api/relay/channels/{cid}/messages",
                                    json={"body": "@news take a look"})).json()
    assert live["mentions"] == ["news"]


async def test_a_disabled_member_is_not_a_member(admin_client, token_client, sf,
                                                 seed_agent, agent_store):
    await _seed(seed_agent, agent_store, "news")
    headers = await _agent_token(sf, "news")
    group = (await admin_client.post("/api/relay/channels", json={
        "kind": "group", "participants": ["user:admin", "agent:news"]})).json()
    assert (await token_client.post(f"/api/relay/channels/{group['id']}/messages",
                                    json={"body": "in"}, headers=headers)).status_code == 200
    await _seed(seed_agent, agent_store, "news", enabled=False)
    assert (await token_client.post(f"/api/relay/channels/{group['id']}/messages",
                                    json={"body": "still in?"},
                                    headers=headers)).status_code == 403
    # And it stops being mentionable there.
    m = (await admin_client.post(f"/api/relay/channels/{group['id']}/messages",
                                 json={"body": "@news?"})).json()
    assert m["mentions"] == []


async def test_dm_with_a_human(admin_client, token_client, sf, seed_agent, agent_store):
    await _seed(seed_agent, agent_store, "news")
    headers = await _agent_token(sf, "news")
    theirs = await token_client.post("/api/relay/dm", json={"with": "user:kyle"},
                                     headers=headers)
    assert theirs.status_code == 200, theirs.text
    assert set(theirs.json()["participants"]) == {"agent:news", "user:kyle"}
    mine = await admin_client.post("/api/relay/dm", json={"with": "user:kyle"})
    assert set(mine.json()["participants"]) == {"user:admin", "user:kyle"}
    assert mine.json()["id"] != theirs.json()["id"]
    async with sf() as s:
        # The agent's own DM keeps the legacy single-agent column; two humans
        # have no agent at all.
        assert (await s.get(Conversation, theirs.json()["id"])).agent == "news"
        assert (await s.get(Conversation, mine.json()["id"])).agent is None
    assert (await admin_client.post("/api/relay/dm",
                                    json={"with": "user:kyle"})).json()["id"] == mine.json()["id"]


async def test_every_dm_carries_its_key(admin_client, sf, seed_agent, agent_store):
    await _seed(seed_agent, agent_store, "news")
    dm = (await admin_client.post("/api/relay/dm", json={"with": "agent:news"})).json()
    async with sf() as s:
        assert (await s.get(Conversation, dm["id"])).dm_key == "agent:news|user:admin"


async def test_agent_to_agent_dms_are_not_legacy_conversations(admin_client, token_client,
                                                               sf, seed_agent, agent_store):
    """The /api/conversations facade posts down the single-agent path with no
    membership check, so an agent-to-agent room must not be reachable there."""
    await _seed(seed_agent, agent_store, "news")
    headers = await _agent_token(sf, "news")
    theirs = (await token_client.post("/api/relay/dm", json={"with": "agent:hello-world"},
                                      headers=headers)).json()
    assert (await admin_client.get(f"/api/conversations/{theirs['id']}")).status_code == 404
    assert (await admin_client.patch(f"/api/conversations/{theirs['id']}",
                                     json={"title": "mine"})).status_code == 404
    assert (await admin_client.delete(f"/api/conversations/{theirs['id']}")).status_code == 404
    assert (await admin_client.post(f"/api/conversations/{theirs['id']}/messages",
                                    json={"text": "speak"})).status_code == 404
    listed = {c["id"] for c in (await admin_client.get("/api/conversations")).json()}
    assert theirs["id"] not in listed
    # A human's own DM with an agent stays a conversation.
    mine = (await admin_client.post("/api/relay/dm", json={"with": "agent:news"})).json()
    assert (await admin_client.get(f"/api/conversations/{mine['id']}")).status_code == 200
    assert mine["id"] in {c["id"] for c in (await admin_client.get("/api/conversations")).json()}


async def test_a_reply_to_a_deleted_message_is_a_miss(admin_client, sf):
    cid = await _channel_id(sf, "general")
    gone = (await admin_client.post(f"/api/relay/channels/{cid}/messages",
                                    json={"body": "oops"})).json()
    elsewhere = (await admin_client.post(
        f"/api/relay/channels/{await _channel_id(sf, 'ops')}/messages",
        json={"body": "other room"})).json()
    async with sf() as s:
        row = await s.get(RelayMessage, gone["id"])
        row.deleted_at = utcnow()
        await s.commit()
    for target in (gone["id"], elsewhere["id"], "nope"):
        r = await admin_client.post(f"/api/relay/channels/{cid}/messages",
                                    json={"body": "re:", "reply_to": target})
        assert r.status_code == 404, target


async def test_stats_reports_the_guard_settings(admin_client):
    """The loop guards and the default grant are env-only settings, so stats is
    where an operator can see what the running platform is actually enforcing."""
    st = (await admin_client.get("/api/relay/stats")).json()
    assert st["settings"] == {"default_grant": True, "max_hops": 4,
                              "channel_per_hour": 30, "global_per_hour": 120,
                              "cooldown_seconds": 20, "context_messages": 30}


# --- bindings (docs/design/19 T10) -------------------------------------------
# A binding is what makes a room two-sided: the Discord channel on the other
# end of it is the SAME room. Which is why these routes are human-only — an
# agent that could bind a channel could choose its own audience.


async def test_a_binding_is_created_listed_and_deleted(admin_client, sf):
    cid = await _channel_id(sf, "general")
    assert (await admin_client.get(f"/api/relay/channels/{cid}/bindings")).json() == []
    r = await admin_client.post(f"/api/relay/channels/{cid}/bindings",
                                json={"connector": "discord", "external_ref": "4242",
                                      "config": {"guild": "g1"}})
    assert r.status_code == 201, r.text
    binding = r.json()
    assert (binding["connector"], binding["external_ref"]) == ("discord", "4242")
    assert binding["config"] == {"guild": "g1"}
    assert [b["id"] for b in
            (await admin_client.get(f"/api/relay/channels/{cid}/bindings")).json()] == [
        binding["id"]]
    # The detail view carries them, so one fetch tells the UI a room is bridged.
    detail = (await admin_client.get(f"/api/relay/channels/{cid}")).json()
    assert [b["external_ref"] for b in detail["bindings"]] == ["4242"]

    gone = await admin_client.delete(f"/api/relay/channels/{cid}/bindings/{binding['id']}")
    assert gone.status_code == 200 and gone.json() == {"ok": True, "id": binding["id"]}
    assert (await admin_client.get(f"/api/relay/channels/{cid}/bindings")).json() == []
    assert (await admin_client.delete(
        f"/api/relay/channels/{cid}/bindings/{binding['id']}")).status_code == 404


async def test_a_ref_already_bound_is_a_conflict(admin_client, sf):
    """One Discord channel, one Relay channel: the whole point of the unique
    (connector, external_ref) is that an inbound message resolves to one room."""
    first = await _channel_id(sf, "general")
    second = await _channel_id(sf, "ops")
    body = {"connector": "discord", "external_ref": "77"}
    assert (await admin_client.post(f"/api/relay/channels/{first}/bindings",
                                    json=body)).status_code == 201
    assert (await admin_client.post(f"/api/relay/channels/{second}/bindings",
                                    json=body)).status_code == 409
    # ...including a second bind of the same room to the same ref.
    assert (await admin_client.post(f"/api/relay/channels/{first}/bindings",
                                    json=body)).status_code == 409
    # Another network is another room on the other side, so it binds fine.
    assert (await admin_client.post(f"/api/relay/channels/{first}/bindings",
                                    json={"connector": "slack",
                                          "external_ref": "77"})).status_code == 201


async def test_a_dm_cannot_be_bound(admin_client, sf, seed_agent, agent_store):
    """A DM already has a bridge — the ingestor writes one for the Discord
    thread the moment it speaks — and the connector runs that flow itself. A
    second binding here would only be a room it then mirrors twice, through a
    webhook a thread cannot have."""
    await _seed(seed_agent, agent_store, "news")
    dm = (await admin_client.post("/api/relay/dm", json={"with": "agent:news"})).json()
    r = await admin_client.post(f"/api/relay/channels/{dm['id']}/bindings",
                                json={"connector": "discord", "external_ref": "9"})
    assert r.status_code == 409 and "thread flow" in r.json()["detail"]


async def test_binding_input_is_validated(admin_client, sf):
    cid = await _channel_id(sf, "general")
    assert (await admin_client.post(f"/api/relay/channels/{cid}/bindings",
                                    json={"connector": "irc",
                                          "external_ref": "1"})).status_code == 422
    assert (await admin_client.post(f"/api/relay/channels/{cid}/bindings",
                                    json={"connector": "discord",
                                          "external_ref": "  "})).status_code == 422
    assert (await admin_client.post("/api/relay/channels/nope/bindings",
                                    json={"connector": "discord",
                                          "external_ref": "1"})).status_code == 404


async def test_the_cross_channel_list_is_what_a_connector_reads(admin_client, sf):
    """The connector asks the platform which rooms it mirrors, rather than
    being told in its environment — a binding made in the UI has to reach it
    without a redeploy."""
    general, ops = await _channel_id(sf, "general"), await _channel_id(sf, "ops")
    await admin_client.post(f"/api/relay/channels/{general}/bindings",
                            json={"connector": "discord", "external_ref": "111",
                                  "config": {"guild": "g"}})
    await admin_client.post(f"/api/relay/channels/{ops}/bindings",
                            json={"connector": "slack", "external_ref": "222"})
    rows = (await admin_client.get("/api/relay/bindings?connector=discord")).json()
    assert rows == [{"channel_id": general, "external_ref": "111",
                     "external_kind": "channel", "parent_external_ref": None,
                     "display_name": "", "external_url": "", "status": "active",
                     "config": {"guild": "g"}}]
    assert [r["external_ref"] for r in
            (await admin_client.get("/api/relay/bindings?connector=slack")).json()] == ["222"]


async def test_bindings_are_human_only(admin_client, token_client, sf, seed_agent,
                                       agent_store):
    """Never an agent token: a bridge is a decision about who can read the room,
    and an agent holding the `relay` grant must not be able to make it."""
    await _seed(seed_agent, agent_store, "news")
    headers = await _agent_token(sf, "news")
    cid = await _channel_id(sf, "general")
    created = (await admin_client.post(f"/api/relay/channels/{cid}/bindings",
                                       json={"connector": "discord",
                                             "external_ref": "9"})).json()
    assert (await token_client.get(f"/api/relay/channels/{cid}/bindings",
                                   headers=headers)).status_code == 403
    assert (await token_client.post(f"/api/relay/channels/{cid}/bindings",
                                    headers=headers,
                                    json={"connector": "discord",
                                          "external_ref": "10"})).status_code == 403
    assert (await token_client.delete(
        f"/api/relay/channels/{cid}/bindings/{created['id']}",
        headers=headers)).status_code == 403
    assert (await token_client.get("/api/relay/bindings?connector=discord",
                                   headers=headers)).status_code == 403


async def test_a_reader_may_list_bindings_but_not_make_one(admin_client, token_client, sf):
    headers = await _human_token(sf, "watcher", "reader")
    cid = await _channel_id(sf, "general")
    assert (await token_client.get(f"/api/relay/channels/{cid}/bindings",
                                   headers=headers)).status_code == 200
    assert (await token_client.get("/api/relay/bindings?connector=discord",
                                   headers=headers)).status_code == 200
    assert (await token_client.post(f"/api/relay/channels/{cid}/bindings", headers=headers,
                                    json={"connector": "discord",
                                          "external_ref": "1"})).status_code == 403


async def test_a_channel_carries_its_title(admin_client, sf):
    """The rail shows a room's name; a dm and a group have only a title."""
    cid = await _channel_id(sf, "general")
    assert (await admin_client.get(f"/api/relay/channels/{cid}")).json()["title"] == "#general"
    group = (await admin_client.post("/api/relay/channels",
                                     json={"kind": "group", "name": "Launch plan"})).json()
    assert group["title"] == "Launch plan" and group["bindings"] == []
    listed = {c["id"]: c for c in (await admin_client.get("/api/relay/channels")).json()}
    assert listed[group["id"]]["title"] == "Launch plan"


# --- POST /api/relay/notify: a system row from an app key (design/25) -----------

async def test_an_app_key_notifies_a_room_as_a_system_row(admin_client, token_client, sf,
                                                          producer, seed_agent, agent_store):
    """The tcms app announces a recorded run into `#qa` with its `app:tcms` key
    (role annotator, no agent). That key cannot post a message — posting is a
    participant's act and an app is not in any room — so it posts an EVENT row
    the way the platform's own cards are posted: system-authored, no mentions,
    no summons, no membership check. The author is the key's principal, so the
    row says who really wrote it."""
    await _seed(seed_agent, agent_store, "engineer")
    # The seeded #qa (docs/design/25): the room the app announces into is the
    # one init_db ships, so nothing here has to make it.
    qa = next(c for c in (await admin_client.get("/api/relay/channels")).json()
              if c["name"] == "qa")
    headers = await _key(sf, name="app:tcms", role="annotator")
    r = await token_client.post("/api/relay/notify", headers=headers, json={
        "channel": "#qa", "text": "🧪 test run 4f2e… on a1b2c3d · 912 pass\n@engineer look"})
    assert r.status_code == 201, r.text
    m = r.json()
    assert m["author"] == "app:tcms" and m["kind"] == "event"
    assert m["mentions"] == [] and m["run_id"] is None
    # One line in the room; the words survive, the address does not — the row
    # stores no mention, and the mention list is what the router routes on.
    assert "\n" not in m["body"] and "engineer look" in m["body"]
    rows = (await admin_client.get(f"/api/relay/channels/{qa['id']}/messages")).json()
    # Newest first, above the seeded welcome row.
    assert [x["id"] for x in rows][0] == m["id"]
    # Published like every other row, and the router has nothing to summon.
    envs = [e for e in producer.envelopes if e["type"] == "relay.message"]
    assert envs[-1]["data"]["id"] == m["id"] and envs[-1]["data"]["mentions"] == []
    router = RelayRouter(Settings(), sf, producer, agent_store)
    await router.handle(producer.published[-1][2])
    assert await _all_runs(sf) == []
    async with sf() as s:
        assert (await s.execute(select(RelayInvocation))).scalars().all() == []
    # The bare name and the id resolve the same room; a room that is not there
    # is a 404, not a silent drop.
    assert (await token_client.post("/api/relay/notify", headers=headers, json={
        "channel": qa["id"], "text": "by id"})).status_code == 201
    assert (await token_client.post("/api/relay/notify", headers=headers, json={
        "channel": "nowhere", "text": "x"})).status_code == 404
    assert (await token_client.post("/api/relay/notify", headers=headers, json={
        "channel": "qa", "text": "x" * 2001})).status_code == 422


async def test_notify_is_refused_to_a_reader(admin_client, token_client, sf):
    await admin_client.post("/api/relay/channels", json={"kind": "channel", "name": "qa"})
    headers = await _human_token(sf, "viewer", "reader")
    r = await token_client.post("/api/relay/notify", headers=headers,
                                json={"channel": "qa", "text": "hi"})
    assert r.status_code == 403
    # A human with a voice may use it too, and is named as themselves.
    r = await admin_client.post("/api/relay/notify", json={"channel": "qa", "text": "hi"})
    assert r.status_code == 201 and r.json()["author"] == "user:admin"


async def test_notify_reaches_channels_only(admin_client, token_client, sf, seed_agent,
                                            agent_store):
    """A DM or a group is a closed room: its id must not let a non-member put
    the platform's voice inside it. `channel_by_ref` accepts any conversation
    id, so the route itself insists on a channel."""
    await _seed(seed_agent, agent_store, "news")
    dm = (await admin_client.post("/api/relay/dm", json={"with": "agent:news"})).json()
    group = (await admin_client.post("/api/relay/channels", json={
        "kind": "group", "participants": ["user:admin", "agent:news"]})).json()
    headers = await _key(sf, name="app:tcms", role="annotator")
    for cid in (dm["id"], group["id"]):
        r = await token_client.post("/api/relay/notify", headers=headers,
                                    json={"channel": cid, "text": "psst"})
        assert r.status_code == 404, (cid, r.text)
    async with sf() as s:
        rows = (await s.execute(select(RelayMessage).where(
            RelayMessage.channel_id.in_([dm["id"], group["id"]])))).scalars().all()
    assert rows == []


async def test_notify_is_capped_per_principal_per_hour(admin_client, token_client, sf):
    """An app in a loop must not bury a room: the hourly cap is counted from
    the route's own rows, so it survives a restart, and is per principal, so
    one noisy key does not silence another."""
    admin_client._transport.app.state.settings.relay_notify_per_hour = 3
    await admin_client.post("/api/relay/channels", json={"kind": "channel", "name": "qa"})
    tcms = await _key(sf, name="app:tcms", role="annotator")
    other = await _key(sf, name="app:running", role="annotator")
    for i in range(3):
        assert (await token_client.post("/api/relay/notify", headers=tcms, json={
            "channel": "qa", "text": f"run {i}"})).status_code == 201
    r = await token_client.post("/api/relay/notify", headers=tcms,
                                json={"channel": "qa", "text": "run 3"})
    assert r.status_code == 429 and "3/hour" in r.json()["detail"]
    assert (await token_client.post("/api/relay/notify", headers=other, json={
        "channel": "qa", "text": "mine"})).status_code == 201
    # A refused post left nothing behind; the room holds exactly the four.
    async with sf() as s:
        bodies = [m.body for m in (await s.execute(select(RelayMessage).where(
            RelayMessage.kind == "event").order_by(RelayMessage.created_at))).scalars()]
    assert bodies == ["run 0", "run 1", "run 2", "mine"]
