"""Relay's REST surface (docs/design/19): channels, messages, DMs, reactions,
search, presence and stats. The authorship rules are the load-bearing part —
who a message is from is decided by the token, never by the payload."""
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from agentplatform.apikeys import generate_token, hash_token, token_prefix
from agentplatform.db import (ApiKey, Conversation, RelayInvocation, RelayMessage,
                              RelayParticipant, Run, RunState, utcnow)
from agentplatform.events import TOPIC_RELAY_MESSAGES


@pytest.fixture
async def token_client(client):
    """A second client over the same app carrying no session cookie:
    `authenticate` tries the cookie before the bearer, so a bearer token is
    only really under test on a request that has nothing else."""
    async with httpx.AsyncClient(transport=client._transport, base_url="http://t") as c:
        yield c


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


async def test_stats_counts_the_last_day(admin_client, sf, token_client,
                                         seed_agent, agent_store):
    await _seed(seed_agent, agent_store, "news")
    cid = await _channel_id(sf, "general")
    headers = await _agent_token(sf, "news")
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
    assert st["messages_24h"] == 2
    assert st["agent_messages_24h"] == 1
    assert st["invocations_24h"] == 2
    assert st["suppressed_24h"] == 1
    assert st["budget"] == {"channel_per_hour": 30, "global_per_hour": 120,
                            "global_used_last_hour": 1}


async def test_archived_channel_stops_taking_messages(admin_client, sf):
    cid = await _channel_id(sf, "ops")
    assert (await admin_client.patch(f"/api/relay/channels/{cid}",
                                     json={"topic": "quiet now", "archived": True})).status_code == 200
    r = await admin_client.post(f"/api/relay/channels/{cid}/messages", json={"body": "hello?"})
    assert r.status_code == 404
    assert cid not in {c["id"] for c in (await admin_client.get("/api/relay/channels")).json()}
    assert (await admin_client.get(f"/api/relay/channels/{cid}")).json()["archived_at"]


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
    assert [c["name"] for c in listed[:3]] == ["general", "ops", "standup"]
    assert [c["id"] for c in listed[3:]] == [dm["id"], group["id"]]

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
