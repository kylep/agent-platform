"""The wiki's REST surface (docs/design/21 T4): the pages, the writes an agent
makes as itself, and the live stream behind both.

The parts worth holding down are the ones that decide what the platform
BELIEVES. Authorship is the token's, so a page always says who wrote it; an
agent writes only from its own run, or the provenance line is a guess; a stale
`base_version` loses loudly rather than erasing the winner; the hourly budget
answers a looping agent with a 429 AND a line in `#wiki`; and an archived page
keeps its history while dropping out of everything else.
"""
from datetime import timedelta

from sqlalchemy import select

from agentplatform import wiki_store
from agentplatform.api import relay as relay_api
from agentplatform.api.wiki import STREAM
from agentplatform.db import (AgentDef, Conversation, Memory, RelayMessage,
                              RelayParticipant, Run, RunState, WikiPage, utcnow)
from agentplatform.events import TOPIC_WIKI_EVENTS
from agentplatform.wiki import BUDGET_PREFIX

from .test_relay_api import (_agent_token, _channel_id, _human_token, _seed,  # noqa: F401
                             token_client)
from .test_relay_sse import StubConsumer, _msg, sse  # noqa: F401

RELAY_GRANT = "mcp__platform__relay"
TICKETS_GRANT = "mcp__platform__tickets"
WIKI_GRANT = "mcp__platform__wiki"


async def _run_id(sf, agent: str, *, depth: int = 0) -> str:
    async with sf() as s:
        run = Run(agent=agent, trigger="relay", requested_by=f"agent:{agent}",
                  depth=depth, state=RunState.RUNNING, prompt="p")
        s.add(run)
        await s.commit()
        return run.id


async def _agent_headers(sf, seed_agent, agent_store, name: str,
                         grants=(RELAY_GRANT, TICKETS_GRANT, WIKI_GRANT)) -> dict:
    """A seeded agent with a per-run token to write from. No membership — a page
    is not a room — but the wiki grant is load-bearing: the wiki's door asks for
    it directly, because the participant ROLE the token carries is shared with
    Relay and Tickets and says nothing about the pages."""
    await _seed(seed_agent, agent_store, name, platform_tools=list(grants))
    return await _agent_token(sf, name, run_id=await _run_id(sf, name))


async def _create(client, slug="deploying", **body) -> dict:
    r = await client.post("/api/wiki/pages",
                          json={"slug": slug, "title": "Deploying",
                                "body": "How a change reaches the NUC.", **body})
    assert r.status_code == 201, r.text
    return r.json()


async def _say(sf, channel: str, body: str, *, kind: str = "text",
               author: str = "user:admin") -> str:
    async with sf() as s:
        msg = RelayMessage(channel_id=await _channel_id(sf, channel), author=author,
                           kind=kind, body=body, mentions=[], hop=0)
        s.add(msg)
        await s.commit()
        return msg.id


# --- the happy paths ---------------------------------------------------------

async def test_write_a_page_and_read_it_back(admin_client, sf, producer):
    page = await _create(admin_client, tags=["ops"], reason="first pass")
    assert (page["slug"], page["version"], page["tags"]) == ("deploying", 1, ["ops"])
    assert page["created_by"] == "user:admin" and page["summary"]
    assert page["updated_by_face"] is None          # humans have no face

    r = await admin_client.get("/api/wiki/pages/deploying")
    assert r.status_code == 200, r.text
    detail = r.json()
    assert detail["page"]["id"] == page["id"]
    # `home` ships with a [[deploying]] link, so the seed is a backlink the
    # moment the page exists.
    assert [b["slug"] for b in detail["backlinks"]] == ["home"]
    assert detail["cited_in"] == {"count": 0, "count_capped": False, "last": []}
    assert [e["type"] for e in producer.envelopes if e["type"] == "wiki.event"]

    # The diff card landed in #wiki as an ordinary message.
    async with sf() as s:
        cards = list((await s.execute(select(RelayMessage).where(
            RelayMessage.channel_id == await _channel_id(sf, "wiki")))).scalars())
    assert [c.kind for c in cards] == ["event"] and cards[0].card["slug"] == "deploying"


async def test_an_edit_names_the_version_it_read(admin_client):
    page = await _create(admin_client)
    r = await admin_client.put("/api/wiki/pages/deploying",
                               json={"body": "Line one.\nLine two.", "reason": "detail",
                                     "base_version": page["version"]})
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 2 and r.json()["body"].startswith("Line one")

    # A write that does not say what it read is a 422 at the door.
    r = await admin_client.put("/api/wiki/pages/deploying",
                               json={"body": "x", "reason": "y"})
    assert r.status_code == 422, r.text

    history = (await admin_client.get("/api/wiki/pages/deploying/history")).json()
    assert [h["version"] for h in history] == [2, 1]
    assert history[0]["reason"] == "detail" and history[0]["author"] == "user:admin"
    assert (history[0]["added"], history[0]["removed"]) == (2, 1)
    assert history[-1]["removed"] == 0          # before v1 there was nothing

    r = await admin_client.get("/api/wiki/pages/deploying/versions/2")
    assert r.status_code == 200, r.text
    diff = r.json()
    assert diff["version"]["body"] == "Line one.\nLine two."
    assert "+Line one." in diff["diff"] and (diff["added"], diff["removed"]) == (2, 1)
    assert (await admin_client.get("/api/wiki/pages/deploying/versions/9")).status_code == 404


async def test_append_creates_the_page_and_never_conflicts(admin_client):
    r = await admin_client.post("/api/wiki/pages/standup/append",
                                json={"body": "We meet at 09:00.", "reason": "note"})
    assert r.status_code == 200, r.text
    assert (r.json()["slug"], r.json()["version"]) == ("standup", 1)
    r = await admin_client.post("/api/wiki/pages/standup/append",
                                json={"body": "In #standup.", "reason": "where"})
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 2 and "In #standup." in r.json()["body"]


async def test_the_list_searches_and_filters(admin_client, sf):
    await _create(admin_client, tags=["ops"])
    await _create(admin_client, slug="weather", title="Weather", tags=["news"],
                  body="The forecast is a daily item.")

    async def slugs(**params):
        r = await admin_client.get("/api/wiki/pages", params=params)
        assert r.status_code == 200, r.text
        return [p["slug"] for p in r.json()]

    assert set(await slugs()) == {"home", "deploying", "weather"}
    assert await slugs(q="forecast") == ["weather"]
    assert await slugs(tag="ops") == ["deploying"]
    assert await slugs(tag="nobody-uses-this") == []
    assert len(await slugs(limit=1)) == 1
    assert await slugs(changed_since=(utcnow() + timedelta(days=1)).isoformat()) == []
    assert "deploying" in await slugs(
        changed_since=(utcnow() - timedelta(days=1)).isoformat())
    # Promotion's badge query: which pages came from a memory.
    assert await slugs(source_memory_id="nothing") == []


async def test_wanted_pages_are_the_red_links(admin_client):
    r = await admin_client.get("/api/wiki/wanted")
    assert r.status_code == 200, r.text
    # The seeded home page links two pages nobody has written yet.
    assert {w["slug"]: w["linked_from"] for w in r.json()} == {"standup": ["home"],
                                                              "deploying": ["home"]}
    await _create(admin_client)
    assert [w["slug"] for w in (await admin_client.get("/api/wiki/wanted")).json()] \
        == ["standup"]


async def test_archiving_keeps_the_history_and_hides_the_page(admin_client):
    await _create(admin_client)
    r = await admin_client.delete("/api/wiki/pages/deploying")
    assert r.status_code == 200, r.text
    assert r.json()["archived_at"] is not None
    assert (await admin_client.get("/api/wiki/pages/deploying")).status_code == 404
    assert "deploying" not in [p["slug"] for p in
                               (await admin_client.get("/api/wiki/pages")).json()]
    # ...but the record is all still there.
    assert (await admin_client.get("/api/wiki/pages/deploying/history")).status_code == 200
    assert (await admin_client.get(
        "/api/wiki/pages/deploying/versions/1")).status_code == 200
    # Archiving twice is a conflict, not a second archive.
    assert (await admin_client.delete("/api/wiki/pages/deploying")).status_code == 409
    assert (await admin_client.delete("/api/wiki/pages/nothing-here")).status_code == 404

    r = await admin_client.post("/api/wiki/pages/deploying/restore", json={})
    assert r.status_code == 200, r.text
    assert r.json()["archived_at"] is None and r.json()["version"] == 1


async def test_restoring_a_version_is_a_new_version(admin_client):
    page = await _create(admin_client)
    await admin_client.put("/api/wiki/pages/deploying",
                           json={"body": "rewritten", "reason": "oops",
                                 "base_version": page["version"]})
    r = await admin_client.post("/api/wiki/pages/deploying/restore", json={"version": 1})
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 3 and r.json()["body"] == page["body"]


async def test_a_slug_that_is_not_a_slug_is_refused(admin_client):
    assert (await admin_client.get("/api/wiki/pages/Not A Slug")).status_code == 400
    r = await admin_client.post("/api/wiki/pages",
                                json={"slug": "Not A Slug", "title": "x"})
    assert r.status_code == 400, r.text
    r = await admin_client.post("/api/wiki/pages", json={"slug": "home", "title": "x"})
    assert r.status_code == 409, r.text


# --- who may do what ---------------------------------------------------------

async def test_a_reader_may_read_but_not_write(admin_client, token_client, sf):
    await _create(admin_client)
    headers = await _human_token(sf, "watcher", "reader")
    assert (await token_client.get("/api/wiki/pages", headers=headers)).status_code == 200
    assert (await token_client.get("/api/wiki/pages/deploying",
                                   headers=headers)).status_code == 200
    writes = [("post", "/api/wiki/pages", {"slug": "x", "title": "x"}),
              ("put", "/api/wiki/pages/deploying",
               {"body": "b", "reason": "r", "base_version": 1}),
              ("post", "/api/wiki/pages/deploying/append", {"body": "b", "reason": "r"}),
              ("post", "/api/wiki/pages/deploying/restore", {}),
              ("post", "/api/wiki/promote", {"memory_id": "x"}),
              ("delete", "/api/wiki/pages/deploying", None)]
    for method, path, payload in writes:
        kw = {} if payload is None else {"json": payload}
        r = await getattr(token_client, method)(path, headers=headers, **kw)
        assert r.status_code == 403, (method, path, r.status_code, r.text)


async def test_an_agent_writes_as_itself_from_its_own_run(token_client, sf, seed_agent,
                                                          agent_store):
    headers = await _agent_headers(sf, seed_agent, agent_store, "news")
    r = await token_client.post("/api/wiki/pages",
                                json={"slug": "weather-dedup", "title": "Weather dedup",
                                      "body": "The forecast is a daily item.",
                                      "reason": "what I learned"}, headers=headers)
    assert r.status_code == 201, r.text
    assert r.json()["created_by"] == "agent:news"
    assert r.json()["updated_by_face"]["emoji"]
    # The version carries the run, which is the provenance the wiki promises.
    history = (await token_client.get("/api/wiki/pages/weather-dedup/history",
                                      headers=headers)).json()
    assert history[0]["run_id"] and history[0]["author_face"]["emoji"]


async def test_an_agent_token_with_no_run_may_not_write(token_client, sf, seed_agent,
                                                        agent_store):
    """The store refuses an agent actor with no run — its card would post at hop
    0 and its version would trace to nothing. That is a 403, never a 500."""
    await _seed(seed_agent, agent_store, "news", platform_tools=[WIKI_GRANT])
    headers = await _agent_token(sf, "news")          # no run_id
    r = await token_client.post("/api/wiki/pages", json={"slug": "x", "title": "x"},
                                headers=headers)
    assert r.status_code == 403, r.text
    # Reading is still fine: a stale key may still read the wiki.
    assert (await token_client.get("/api/wiki/pages", headers=headers)).status_code == 200


async def test_an_agent_without_the_wiki_grant_is_refused(token_client, sf, seed_agent,
                                                          agent_store, admin_client):
    """The three participant grants share ONE role, and for an agent caller the
    role tuple is not what is judged — Relay and Tickets fence an agent by room
    membership instead, and the wiki has no room to match. So `mcp__platform__
    wiki` is asked for at the door, on reads as well as writes: the tool is the
    sanctioned way in, and holding the messenger is not holding the wiki."""
    await _create(admin_client)
    headers = await _agent_headers(sf, seed_agent, agent_store, "news",
                                   grants=(RELAY_GRANT, TICKETS_GRANT))
    reads = ["/api/wiki/pages", "/api/wiki/pages/deploying", "/api/wiki/wanted",
             "/api/wiki/stats", "/api/wiki/pages/deploying/history",
             "/api/wiki/pages/deploying/versions/1"]
    for path in reads:
        r = await token_client.get(path, headers=headers)
        assert r.status_code == 403, (path, r.status_code, r.text)
        assert "wiki tool" in r.json()["detail"]
    r = await token_client.post("/api/wiki/pages/deploying/append",
                                json={"body": "b", "reason": "r"}, headers=headers)
    assert r.status_code == 403, r.text

    # Granted, the same token reads and writes.
    granted = await _agent_headers(sf, seed_agent, agent_store, "news")
    for path in reads:
        assert (await token_client.get(path, headers=granted)).status_code == 200, path
    assert (await token_client.post("/api/wiki/pages/deploying/append",
                                    json={"body": "b", "reason": "r"},
                                    headers=granted)).status_code == 200

    # ...and an agent somebody switched off is refused whatever it holds.
    async with sf() as s:
        (await s.get(AgentDef, "news")).enabled = False
        await s.commit()
    await agent_store.reload()
    r = await token_client.get("/api/wiki/pages", headers=granted)
    assert r.status_code == 403 and "disabled" in r.json()["detail"]


async def test_a_lost_create_race_is_a_conflict_not_a_bad_request(admin_client,
                                                                  monkeypatch):
    """The store's courteous lookup and the unique index behind it are the same
    answer — somebody got there first — so the API must not tell them apart: a
    create that merely lost a race is a 409, never a 400."""
    await _create(admin_client)
    assert (await admin_client.post(
        "/api/wiki/pages", json={"slug": "deploying", "title": "x"})).status_code == 409

    async def _no_page(*args, **kwargs):
        return None                      # the other writer commits after we look

    monkeypatch.setattr(wiki_store, "_page_by_slug", _no_page)
    r = await admin_client.post("/api/wiki/pages",
                                json={"slug": "deploying", "title": "x"})
    assert r.status_code == 409, r.text
    assert "already exists" in r.json()["detail"]


async def test_a_stale_base_version_is_a_conflict_that_can_be_merged(admin_client):
    page = await _create(admin_client)
    await admin_client.put("/api/wiki/pages/deploying",
                           json={"body": "somebody else got here first",
                                 "reason": "theirs", "base_version": page["version"]})
    r = await admin_client.put("/api/wiki/pages/deploying",
                               json={"body": "mine", "reason": "ours",
                                     "base_version": page["version"]})
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["current_version"] == 2
    assert body["current_summary"] == "somebody else got here first"
    assert "v2" in body["detail"]
    # The loser wrote nothing at all.
    assert (await admin_client.get("/api/wiki/pages/deploying")).json()["page"]["body"] \
        == "somebody else got here first"


async def test_the_hourly_write_budget_is_a_429_and_a_line_in_the_room(
        token_client, sf, seed_agent, agent_store):
    headers = await _agent_headers(sf, seed_agent, agent_store, "news")
    limit = 30
    for i in range(limit):
        r = await token_client.post("/api/wiki/pages/notes/append",
                                    json={"body": f"note {i}", "reason": "n"},
                                    headers=headers)
        assert r.status_code == 200, r.text
    r = await token_client.post("/api/wiki/pages/notes/append",
                                json={"body": "one too many", "reason": "n"},
                                headers=headers)
    assert r.status_code == 429, r.text
    assert BUDGET_PREFIX in r.json()["detail"]
    wiki_room = await _channel_id(sf, "wiki")
    async with sf() as s:
        said = list((await s.execute(select(RelayMessage).where(
            RelayMessage.channel_id == wiki_room, RelayMessage.kind == "system",
            RelayMessage.body.like(BUDGET_PREFIX + "%")))).scalars())
    assert len(said) == 1
    # A second refusal in the same hour does not say it twice.
    assert (await token_client.post("/api/wiki/pages/notes/append",
                                    json={"body": "again", "reason": "n"},
                                    headers=headers)).status_code == 429
    async with sf() as s:
        again = list((await s.execute(select(RelayMessage).where(
            RelayMessage.channel_id == wiki_room, RelayMessage.kind == "system",
            RelayMessage.body.like(BUDGET_PREFIX + "%")))).scalars())
    assert len(again) == 1
    budget = (await token_client.get("/api/wiki/stats", headers=headers)).json()["budget"]
    assert budget["limit"] == limit
    assert [(a["agent"], a["used"], a["left"]) for a in budget["agents"]] == [
        ("news", limit, 0)]


# --- citations ----------------------------------------------------------------

async def test_cited_in_counts_the_rooms_pointing_at_the_page(admin_client, sf):
    await _create(admin_client)
    cited = await _say(sf, "general", "see [[deploying]] for the helm trap")
    await _say(sf, "ops", "the syntax is `[[deploying]]`")        # code, not a link
    await _say(sf, "ops", "nothing to do with it")
    r = await admin_client.get("/api/wiki/pages/deploying")
    assert r.status_code == 200, r.text
    citations = r.json()["cited_in"]
    assert citations["count"] == 1
    assert [c["message_id"] for c in citations["last"]] == [cited]
    assert citations["last"][0]["author"] == "user:admin"


async def test_citations_stop_at_the_rooms_the_reader_may_see(
        admin_client, token_client, sf, seed_agent, agent_store):
    """The page is the platform's; the conversation about it is Relay's. A
    citation in a closed room an agent is not in must not reach it — not the
    message, not the channel id, and not the count, which on its own says that
    somewhere private is talking about this."""
    await _create(admin_client)
    async with sf() as s:
        s.add(private := Conversation(connector="web", kind="channel", open=False,
                                      name="war-room", topic="", title="#war-room"))
        await s.flush()
        s.add(RelayParticipant(channel_id=private.id, participant="user:admin"))
        s.add(RelayMessage(channel_id=private.id, author="user:admin", kind="text",
                           body="[[deploying]] is how we do it", mentions=[], hop=0))
        await s.commit()
    open_citation = await _say(sf, "general", "see [[deploying]]")
    headers = await _agent_headers(sf, seed_agent, agent_store, "news")

    human = (await admin_client.get("/api/wiki/pages/deploying")).json()["cited_in"]
    assert human["count"] == 2                      # a human reads the platform
    agent = (await token_client.get("/api/wiki/pages/deploying",
                                    headers=headers)).json()["cited_in"]
    assert agent["count"] == 1
    assert [c["message_id"] for c in agent["last"]] == [open_citation]
    assert private.id not in [c["channel_id"] for c in agent["last"]]
    assert human["count_capped"] is False and agent["count_capped"] is False


# --- promotion ----------------------------------------------------------------

async def _memory(sf, agent: str, key: str, content: str) -> str:
    async with sf() as s:
        m = Memory(agent=agent, key=key, content=content, tags=[])
        s.add(m)
        await s.commit()
        return m.id


async def test_a_human_promotes_any_memory(admin_client, sf):
    memory_id = await _memory(sf, "pai", "Location", "Kyle lives in Whitby.")
    r = await admin_client.post("/api/wiki/promote", json={"memory_id": memory_id})
    assert r.status_code == 200, r.text
    page = r.json()
    assert page["slug"] == "location" and page["title"] == "Location"
    assert "Kyle lives in Whitby." in page["body"]
    assert "Promoted from pai's memory" in page["body"]
    assert page["source_memory_id"] == memory_id and page["tags"] == ["memory", "pai"]
    # The badge query the Memories page asks.
    listed = (await admin_client.get("/api/wiki/pages",
                                     params={"source_memory_id": memory_id})).json()
    assert [p["slug"] for p in listed] == ["location"]
    # The memory itself is untouched: promotion is not a move.
    async with sf() as s:
        assert (await s.get(Memory, memory_id)).content == "Kyle lives in Whitby."
    assert (await admin_client.post("/api/wiki/promote",
                                    json={"memory_id": "nope"})).status_code == 404


async def test_an_agent_promotes_only_its_own_memory(token_client, sf, seed_agent,
                                                     agent_store):
    headers = await _agent_headers(sf, seed_agent, agent_store, "news")
    mine = await _memory(sf, "news", "Dedup", "The forecast is a daily item.")
    theirs = await _memory(sf, "pai", "Location", "Kyle lives in Whitby.")
    r = await token_client.post("/api/wiki/promote",
                                json={"memory_id": mine, "slug": "weather-dedup"},
                                headers=headers)
    assert r.status_code == 201 or r.status_code == 200, r.text
    assert r.json()["created_by"] == "agent:news"
    r = await token_client.post("/api/wiki/promote", json={"memory_id": theirs},
                                headers=headers)
    assert r.status_code == 403, r.text


async def test_an_agent_promotes_by_key_from_its_own_namespace(token_client, sf,
                                                               seed_agent, agent_store):
    """An agent holds the key it remembered under, not the id, and its
    participant token cannot list `/api/memories` to trade one for the other —
    so the key is resolved here, in the caller's own namespace. The same key in
    somebody else's namespace is not a memory this caller has."""
    headers = await _agent_headers(sf, seed_agent, agent_store, "news")
    await _memory(sf, "news", "Dedup", "The forecast is a daily item.")
    await _memory(sf, "pai", "Dedup", "Something else entirely.")
    r = await token_client.post("/api/wiki/promote",
                                json={"key": "Dedup", "slug": "weather-dedup"},
                                headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["created_by"] == "agent:news"
    assert "The forecast is a daily item." in r.json()["body"]

    # Naming another namespace is the cross-namespace refusal, not a promotion.
    r = await token_client.post("/api/wiki/promote",
                                json={"key": "Dedup", "agent": "pai"}, headers=headers)
    assert r.status_code == 403, r.text
    r = await token_client.post("/api/wiki/promote", json={"key": "nope"},
                                headers=headers)
    assert r.status_code == 404, r.text


async def test_a_human_promoting_by_key_names_the_namespace(admin_client, sf):
    """A key is only unique inside a namespace, and a human has none of their
    own — so a human promoting by key says whose memory it is."""
    await _memory(sf, "pai", "Location", "Kyle lives in Whitby.")
    assert (await admin_client.post("/api/wiki/promote",
                                    json={"key": "Location"})).status_code == 400
    r = await admin_client.post("/api/wiki/promote",
                                json={"key": "Location", "agent": "pai"})
    assert r.status_code == 200, r.text
    assert r.json()["slug"] == "location"
    r = await admin_client.post("/api/wiki/promote",
                                json={"key": "nope", "agent": "pai"})
    assert r.status_code == 404, r.text


async def test_promote_names_the_memory_exactly_one_way(admin_client, sf):
    """Neither argument is nothing to promote; both is two answers to which
    memory, and a 422 at the door is cheaper than guessing."""
    memory_id = await _memory(sf, "pai", "Location", "Kyle lives in Whitby.")
    assert (await admin_client.post("/api/wiki/promote", json={})).status_code == 422
    r = await admin_client.post("/api/wiki/promote",
                                json={"memory_id": memory_id, "key": "Location",
                                      "agent": "pai"})
    assert r.status_code == 422, r.text


# --- stats and live -----------------------------------------------------------

async def test_stats_report_the_garden(admin_client, sf):
    await _create(admin_client)
    async with sf() as s:
        # A page nobody has touched for longer than the setting.
        (await s.execute(select(WikiPage).where(WikiPage.slug == "home"))) \
            .scalars().one().updated_at = utcnow() - timedelta(days=40)
        await s.commit()
    r = await admin_client.get("/api/wiki/stats")
    assert r.status_code == 200, r.text
    stats = r.json()
    assert stats["pages"] == 2 and stats["wanted"] == 1 and stats["stale"] == 1
    # The seed's own v1 is an edit like any other — it just was not a person's.
    assert dict((e["author"], e["count"]) for e in stats["edits_24h"]) == {
        "user:admin": 1, "system:wiki": 1}
    assert all(e["face"] is None for e in stats["edits_24h"])
    assert stats["budget"] == {"limit": 30, "agents": []}


async def test_a_capped_citation_count_says_so(admin_client, sf, monkeypatch):
    """The count is read off the messages themselves (a `[[slug]]` in a code
    fence is not a citation), so the scan has a limit — and a client showing
    "cited in 2 messages" when the real number is a hundred is wrong in a way
    nobody can see. The cap is reported instead."""
    from agentplatform.api import wiki as wiki_api
    monkeypatch.setattr(wiki_api, "CITED_SCAN", 2)
    await _create(admin_client)
    for i in range(3):
        await _say(sf, "general", f"{i}: see [[deploying]]")
    cited = (await admin_client.get("/api/wiki/pages/deploying")).json()["cited_in"]
    assert cited["count"] == 2 and cited["count_capped"] is True


async def test_the_stream_carries_a_published_page_event(admin_client, sf):
    """The wiki is Kafka-fed: a write from ANY pod reaches this one's streams
    off `wiki.events`, and the frame carries what recent-changes draws."""
    page = await _create(admin_client)
    feed = admin_client._transport.app.state.wiki_feed
    async with sse(admin_client, "/api/wiki/events") as (resp, stream):
        assert resp["status"] == 200
        assert resp["headers"]["content-type"].startswith("text/event-stream")
        payload = {"event": "edited", "page": {**page, "version": 2},
                   "version": 2, "author": "agent:news", "run_id": "r1",
                   "reason": "the helm trap", "added": 3, "removed": 1}
        await feed.run(StubConsumer([_msg(TOPIC_WIKI_EVENTS, page["id"],
                                          "wiki.event", payload)]))
        event, data, _ = await stream.event()
    assert event == "page"
    assert (data["page"]["slug"], data["added"]) == ("deploying", 3)


async def test_the_stream_beats_for_an_agent_too(admin_client, token_client, sf,
                                                 seed_agent, agent_store, monkeypatch):
    """Pages are not room-scoped, so an agent's stream is the same stream a
    human gets — every page, and a heartbeat in between."""
    monkeypatch.setattr(relay_api, "HEARTBEAT_SECONDS", 0.05)
    page = await _create(admin_client)
    headers = await _agent_headers(sf, seed_agent, agent_store, "news")
    feed = admin_client._transport.app.state.wiki_feed
    async with sse(token_client, "/api/wiki/events", headers=headers) as (_, stream):
        assert (await stream.frame()) == ": heartbeat"
        feed.publish(STREAM, "page", {"event": "edited", "page": page})
        event, data, _ = await stream.event()
    assert (event, data["page"]["slug"]) == ("page", "deploying")


# --- the default grant --------------------------------------------------------

async def test_a_new_agent_holds_all_three_participant_grants(admin_client, sf):
    r = await admin_client.post("/api/agents", json={"name": "newbie",
                                                     "description": "test",
                                                     "prompt": "# newbie"})
    assert r.status_code == 201, r.text
    assert r.json()["platform_tools"] == [RELAY_GRANT, TICKETS_GRANT, WIKI_GRANT]
    async with sf() as s:
        assert (await s.get(AgentDef, "newbie")).platform_tools == [
            RELAY_GRANT, TICKETS_GRANT, WIKI_GRANT]


async def test_the_wiki_default_can_be_turned_off_platform_wide(admin_client, sf):
    admin_client._transport.app.state.settings.wiki_default_grant = False
    r = await admin_client.post("/api/agents", json={"name": "quiet",
                                                     "description": "test",
                                                     "prompt": "# quiet"})
    assert r.status_code == 201, r.text
    assert r.json()["platform_tools"] == [RELAY_GRANT, TICKETS_GRANT]
