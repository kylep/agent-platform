"""Tickets' REST surface (docs/design/20 T4): the board's data, the writes an
agent makes as itself, and the live stream behind both.

The load-bearing parts are the ones an agent can reach. Authorship is the
token's — there is no `reporter` on the wire — an agent reads only the rooms it
is in and writes only where it is a member, it must be speaking from its own
run or it cannot write at all, and its hourly creation budget is answered with
a 429 AND a line in the room, so a loop is visible where the work is."""
from datetime import timedelta

from sqlalchemy import select, text

from agentplatform.api import relay as relay_api
from agentplatform.api.tickets import LINKED_RUNS, STREAM
from agentplatform.db import (AgentDef, Conversation, RelayMessage,
                              RelayParticipant, Run, RunState, Ticket,
                              TicketEvent, utcnow)
from agentplatform.events import TOPIC_TICKETS_EVENTS
from agentplatform.tickets import BUDGET_PREFIX

from .test_relay_api import _agent_token, _channel_id, _human_token, _seed
from .test_relay_sse import StubConsumer, _msg, sse  # noqa: F401

RELAY_GRANT = "mcp__platform__relay"
TICKETS_GRANT = "mcp__platform__tickets"
WIKI_GRANT = "mcp__platform__wiki"
QUOTA_GRANT = "mcp__platform__get_quota_usage"
ARTIFACTS_GRANT = "mcp__platform__artifacts"


async def _run_id(sf, agent: str, *, depth: int = 0) -> str:
    """The run an agent speaks from: an agent token with no run may not write."""
    async with sf() as s:
        run = Run(agent=agent, trigger="relay", requested_by=f"agent:{agent}",
                  depth=depth, state=RunState.RUNNING, prompt="p")
        s.add(run)
        await s.commit()
        return run.id


async def _agent_in(sf, seed_agent, agent_store, name: str, *channel_ids: str) -> dict:
    """A seeded agent, made an explicit member of each closed room, with a
    per-run token to speak from."""
    await _seed(seed_agent, agent_store, name)
    async with sf() as s:
        for cid in channel_ids:
            s.add(RelayParticipant(channel_id=cid, participant=f"agent:{name}"))
        await s.commit()
    return await _agent_token(sf, name, run_id=await _run_id(sf, name))


async def _private_project(sf, prefix: str, *participants: str) -> str:
    """A CLOSED channel that is also a project — the room an agent is not in.
    Closed rather than open because an open channel holds every enabled agent
    by definition, so there would be nobody to keep out."""
    async with sf() as s:
        conv = Conversation(connector="web", kind="channel", open=False, name="war-room",
                            topic="", title="#war-room", ticket_prefix=prefix,
                            ticket_seq=0)
        s.add(conv)
        await s.flush()
        for p in participants:
            s.add(RelayParticipant(channel_id=conv.id, participant=p))
        await s.commit()
        return conv.id


async def _open(client, channel: str = "#general", **body) -> dict:
    r = await client.post("/api/tickets", json={"channel": channel,
                                                "title": "Fix stale weather dedup",
                                                **body})
    assert r.status_code == 201, r.text
    return r.json()


# --- the happy paths ---------------------------------------------------------

async def test_open_a_ticket_and_read_it_back(admin_client, sf, producer):
    t = await _open(admin_client, body="the forecast is a daily item")
    assert t["key"] == "GEN-1" and t["state"] == "open" and t["priority"] == "p2"
    assert t["reporter"] == "user:admin" and t["assignee"] is None
    # The card is the thread root, and it is a real Relay message in the room.
    async with sf() as s:
        card = await s.get(RelayMessage, t["root_message_id"])
    assert card.kind == "event" and card.card["key"] == "GEN-1"

    r = await admin_client.get(f"/api/tickets/{t['key']}")
    assert r.status_code == 200, r.text
    detail = r.json()
    assert detail["ticket"]["id"] == t["id"]
    assert [e["kind"] for e in detail["events"]] == ["created"]
    assert detail["root_message_id"] == t["root_message_id"]
    assert detail["runs"] == [] and detail["thinking"] is None
    # An id is as good as a key on every {key} route.
    assert (await admin_client.get(f"/api/tickets/{t['id']}")).status_code == 200
    assert (await admin_client.get("/api/tickets/GEN-404")).status_code == 404
    assert [e["type"] for e in producer.envelopes if e["type"] == "ticket.event"]


async def test_move_assign_comment_and_edit(admin_client, sf, seed_agent, agent_store):
    await _seed(seed_agent, agent_store, "news")
    t = await _open(admin_client)
    r = await admin_client.post(f"/api/tickets/{t['key']}/move",
                                json={"state": "in_progress", "reason": "on it"})
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "in_progress"

    r = await admin_client.post(f"/api/tickets/{t['key']}/assign",
                                json={"to": "agent:news", "reason": "closer to the data"})
    assert r.status_code == 200, r.text
    assert r.json()["assignee"] == "agent:news"
    assert r.json()["assignee_face"]["emoji"]

    r = await admin_client.post(f"/api/tickets/{t['key']}/comments",
                                json={"body": "any luck?"})
    assert r.status_code == 200, r.text
    comment = r.json()
    assert comment["author"] == "user:admin" and comment["body"] == "any luck?"
    assert comment["thread_root"] == t["root_message_id"]

    r = await admin_client.patch(f"/api/tickets/{t['key']}",
                                 json={"title": "Weather dedup", "priority": "p1",
                                       "labels": ["bug"]})
    assert r.status_code == 200, r.text
    assert (r.json()["title"], r.json()["priority"]) == ("Weather dedup", "p1")

    detail = (await admin_client.get(f"/api/tickets/{t['key']}")).json()
    assert [e["kind"] for e in detail["events"]] == [
        "created", "moved", "assigned", "commented", "edited"]
    # The card in the room was rewritten in place rather than re-posted.
    async with sf() as s:
        card = await s.get(RelayMessage, t["root_message_id"])
    assert card.card["state"] == "in_progress" and card.edited_at is not None


async def test_an_illegal_move_is_a_conflict(admin_client):
    t = await _open(admin_client)
    r = await admin_client.post(f"/api/tickets/{t['key']}/move", json={"state": "open"})
    assert r.status_code == 409, r.text
    r = await admin_client.post(f"/api/tickets/{t['key']}/move", json={"state": "sideways"})
    assert r.status_code == 409


async def test_an_edit_that_changes_nothing_is_not_an_event(admin_client):
    t = await _open(admin_client)
    r = await admin_client.patch(f"/api/tickets/{t['key']}", json={"title": t["title"]})
    assert r.status_code == 200, r.text
    detail = (await admin_client.get(f"/api/tickets/{t['key']}")).json()
    assert [e["kind"] for e in detail["events"]] == ["created"]


async def test_the_list_filters_the_board(admin_client, sf, seed_agent, agent_store):
    await _seed(seed_agent, agent_store, "news")
    a = await _open(admin_client, title="alpha", labels=["bug"])
    b = await _open(admin_client, channel="#ops", title="beta", assignee="agent:news",
                    priority="p0")
    await admin_client.post(f"/api/tickets/{b['key']}/move", json={"state": "blocked"})

    async def keys(**params):
        r = await admin_client.get("/api/tickets", params=params)
        assert r.status_code == 200, r.text
        return [t["key"] for t in r.json()]

    assert set(await keys()) == {a["key"], b["key"]}
    assert await keys(channel="#ops") == [b["key"]]
    assert await keys(state="blocked") == [b["key"]]
    assert await keys(assignee="agent:news") == [b["key"]]
    assert await keys(label="bug") == [a["key"]]
    assert await keys(q="alph") == [a["key"]]
    assert await keys(mine=True) == []          # nothing is assigned to user:admin
    rows = (await admin_client.get("/api/tickets", params={"state": "blocked"})).json()
    assert rows[0]["assignee_face"]["emoji"] and rows[0]["reporter_face"] is None


async def test_the_label_filter_survives_the_limit(admin_client):
    """Filtered in python after the cap, a labelled ticket that sorted past the
    page was dropped without a trace — `label=qa&limit=2` answered `[]` while
    three tickets carried the label. The cap now counts matches."""
    for i in range(4):
        await _open(admin_client, title=f"urgent {i}", priority="p0")
    labelled = {(await _open(admin_client, title=f"later {i}", priority="p3",
                             labels=["qa"]))["key"] for i in range(3)}

    async def keys(**params):
        r = await admin_client.get("/api/tickets", params=params)
        assert r.status_code == 200, r.text
        return [t["key"] for t in r.json()]

    assert set(await keys(label="qa")) == labelled
    # The limit is a cap on the answer, not on the rows the filter got to see.
    page = await keys(label="qa", limit=2)
    assert len(page) == 2 and set(page) < labelled
    assert await keys(label="nothing-has-this") == []


async def test_projects_are_the_channels_with_a_prefix(admin_client):
    await _open(admin_client)
    r = await admin_client.get("/api/tickets/projects")
    assert r.status_code == 200, r.text
    rows = {p["name"]: p for p in r.json()}
    assert rows["general"]["prefix"] == "GEN" and rows["general"]["open"] == 1
    assert rows["ops"]["prefix"] == "OPS" and rows["ops"]["open"] == 0
    assert "standup" not in rows          # the ceremony room is not a project


async def test_stats_count_the_board(admin_client, sf, seed_agent, agent_store):
    await _seed(seed_agent, agent_store, "news")
    a = await _open(admin_client, title="alpha")
    b = await _open(admin_client, title="beta")
    c = await _open(admin_client, title="gamma")
    await admin_client.post(f"/api/tickets/{b['key']}/move", json={"state": "in_progress"})
    await admin_client.post(f"/api/tickets/{c['key']}/move", json={"state": "done"})
    async with sf() as s:
        # A ticket in flight that nobody has touched for longer than the setting.
        (await s.get(Ticket, b["id"])).last_activity_at = utcnow() - timedelta(days=9)
        # An assignee nobody can reach: the agent was deleted under the ticket.
        (await s.get(Ticket, a["id"])).assignee = "agent:ghost"
        await s.commit()

    r = await admin_client.get("/api/tickets/stats")
    assert r.status_code == 200, r.text
    stats = r.json()
    assert (stats["open"], stats["in_progress"]) == (1, 1)
    assert stats["done_24h"] == 1 and stats["stale"] == 1 and stats["orphaned"] == 1
    moved = {m["actor"]: m["count"] for m in stats["moved_24h"]}
    assert moved == {"user:admin": 2}
    assert stats["budget"]["creates_per_hour"] == 20


async def test_a_bare_assignee_is_an_agent_or_a_400(admin_client, seed_agent,
                                                    agent_store):
    """What pai actually did to OPS-2: assigned it to `pai`. Stored as typed,
    the board shows an assignee that summons nobody — so a bare name that is an
    agent is one, and a bare name that is not is refused with the shapes that
    are allowed."""
    await _seed(seed_agent, agent_store, "news")
    t = await _open(admin_client)
    r = await admin_client.post(f"/api/tickets/{t['key']}/assign", json={"to": "news"})
    assert r.status_code == 200, r.text
    assert r.json()["assignee"] == "agent:news"

    r = await admin_client.post(f"/api/tickets/{t['key']}/assign", json={"to": "kyle"})
    assert r.status_code == 400, r.text
    assert "agent:<name>" in r.json()["detail"]
    # A create carries the same rule, and a refused create files nothing.
    r = await admin_client.post("/api/tickets", json={"channel": "#general",
                                                      "title": "x", "assignee": "kyle"})
    assert r.status_code == 400, r.text
    assert (await admin_client.post("/api/tickets",
                                    json={"channel": "#general", "title": "y",
                                          "assignee": "news"})).json()["assignee"] \
        == "agent:news"


async def test_a_qualified_assignee_has_to_be_a_participant_string(admin_client,
                                                                   seed_agent,
                                                                   agent_store):
    """`Agent:pai` reached the store as a stranger's name and was filed as one:
    200, an assignee on the board, and no summons. Anything with a colon is now
    held to the grammar Relay uses at its own doors, namespace case included."""
    await _seed(seed_agent, agent_store, "news")
    t = await _open(admin_client)
    for bad in ("Agent:news", "hacker:injected", "slack:x", "user:with space"):
        r = await admin_client.post(f"/api/tickets/{t['key']}/assign", json={"to": bad})
        assert r.status_code == 400, r.text
        assert "agent:<name>" in r.json()["detail"]
    r = await admin_client.post("/api/tickets", json={"channel": "#general",
                                                      "title": "x",
                                                      "assignee": "Agent:news"})
    assert r.status_code == 400, r.text

    for good in ("user:kyle", "discord:123"):
        r = await admin_client.post(f"/api/tickets/{t['key']}/assign", json={"to": good})
        assert r.status_code == 200, r.text
        assert r.json()["assignee"] == good


async def test_a_channel_can_be_named_without_its_hash(admin_client):
    """`ops` is what a model types, and the tool already accepts it — the API
    refusing it made the same word mean two things depending on the door."""
    r = await admin_client.post("/api/tickets", json={"channel": "ops", "title": "x"})
    assert r.status_code == 201, r.text
    assert r.json()["key"] == "OPS-1"
    assert (await admin_client.get("/api/tickets", params={"channel": "ops"})).json()
    r = await admin_client.post("/api/tickets", json={"channel": "nowhere", "title": "x"})
    assert r.status_code == 404, r.text


async def test_a_name_that_looks_like_an_id_is_still_a_name(admin_client, sf):
    """A channel slug may be 32 hex characters — `_NAME_RE` allows it — so
    "looks like an id" is a guess, not a rule. The name is tried first and the
    id is the fallback, which is the only order where both forms always find
    the room they name."""
    async with sf() as s:
        s.add(conv := Conversation(connector="web", kind="channel", open=True,
                                   name="ab" * 16, topic="", title="#hex",
                                   ticket_prefix="HEX", ticket_seq=0))
        await s.commit()
        hex_name, hex_id = conv.name, conv.id
    r = await admin_client.post("/api/tickets", json={"channel": hex_name, "title": "x"})
    assert r.status_code == 201, r.text
    assert r.json()["key"] == "HEX-1"
    # And an id is still an id: the general channel answers to its own.
    gen = await _channel_id(sf, "general")
    assert (await admin_client.post("/api/tickets",
                                    json={"channel": gen, "title": "y"})
            ).json()["key"] == "GEN-1"
    assert hex_id != hex_name


# --- who may do what ---------------------------------------------------------

async def test_a_reader_may_read_but_not_write(admin_client, token_client, sf):
    t = await _open(admin_client)
    headers = await _human_token(sf, "watcher", "reader")
    assert (await token_client.get("/api/tickets", headers=headers)).status_code == 200
    assert (await token_client.get(f"/api/tickets/{t['key']}",
                                   headers=headers)).status_code == 200
    for path, body in ((f"/api/tickets/{t['key']}/move", {"state": "blocked"}),
                       (f"/api/tickets/{t['key']}/assign", {"to": None}),
                       (f"/api/tickets/{t['key']}/comments", {"body": "hi"})):
        assert (await token_client.post(path, json=body,
                                        headers=headers)).status_code == 403
    r = await token_client.post("/api/tickets", json={"channel": "#general", "title": "x"},
                                headers=headers)
    assert r.status_code == 403


async def test_an_agent_sees_only_the_rooms_it_is_in(admin_client, token_client, sf,
                                                     seed_agent, agent_store):
    """#general is open, so every enabled agent is in it. A closed group is the
    other case, and a ticket there must not appear on an outsider's board."""
    mine = await _open(admin_client, title="in the open")
    private = await _private_project(sf, "WAR", "user:admin")
    theirs = await _open(admin_client, channel=private, title="behind a door")
    headers = await _agent_in(sf, seed_agent, agent_store, "news")

    r = await token_client.get("/api/tickets", headers=headers)
    assert r.status_code == 200, r.text
    assert [t["key"] for t in r.json()] == [mine["key"]]
    assert (await token_client.get(f"/api/tickets/{theirs['key']}",
                                   headers=headers)).status_code == 404
    # ...and it cannot write there either. A ticket it may not read answers
    # 404 on every {key} route, write included: that WAR-1 exists at all is
    # part of what the closed room was keeping.
    r = await token_client.post(f"/api/tickets/{theirs['key']}/comments",
                                json={"body": "hello?"}, headers=headers)
    assert r.status_code == 404
    # Naming the room outright is the case that is a 403, exactly as Relay
    # answers a post into a room an agent is not in.
    r = await token_client.post("/api/tickets", json={"channel": private, "title": "x"},
                                headers=headers)
    assert r.status_code == 403
    # The projects listing is scoped the same way: the four open projects
    # (#eng and #qa are seeded as projects, docs/design/24, 25), never the
    # closed WAR room.
    assert {p["prefix"] for p in (await token_client.get(
        "/api/tickets/projects", headers=headers)).json()} == {"ENG", "GEN", "OPS", "QA"}


async def test_an_agent_opens_a_ticket_as_itself(token_client, sf, seed_agent,
                                                 agent_store):
    headers = await _agent_in(sf, seed_agent, agent_store, "news")
    r = await token_client.post("/api/tickets",
                                json={"channel": "#general", "title": "found it"},
                                headers=headers)
    assert r.status_code == 201, r.text
    assert r.json()["reporter"] == "agent:news"
    # There is no reporter on the wire at all: asking for one is a 422, not a
    # quietly ignored field.
    r = await token_client.post("/api/tickets",
                                json={"channel": "#general", "title": "x",
                                      "reporter": "user:admin"}, headers=headers)
    assert r.status_code == 422


async def test_the_detail_links_the_run_that_opened_it(admin_client, token_client, sf,
                                                       seed_agent, agent_store):
    """`Run.ticket_id` is set only when a run was summoned IN a ticket's thread,
    so the run that OPENED the ticket — or moved it with the tool — left three
    Activity rows each linking a run while the fields panel above said "none
    yet". The linked runs are every run the ticket's own history names."""
    headers = await _agent_in(sf, seed_agent, agent_store, "news")
    key = (await token_client.post("/api/tickets",
                                   json={"channel": "#general", "title": "found it"},
                                   headers=headers)).json()["key"]
    await token_client.post(f"/api/tickets/{key}/move",
                            json={"state": "in_progress"}, headers=headers)
    detail = (await admin_client.get(f"/api/tickets/{key}")).json()
    from_events = {e["run_id"] for e in detail["events"] if e["run_id"]}
    assert from_events and {r["id"] for r in detail["runs"]} == from_events
    opener = next(iter(from_events))
    async with sf() as s:
        assert (await s.get(Run, opener)).ticket_id is None
        # ...and a busy thread does not push it back out: the run that opened
        # the ticket is the oldest one, so a newest-first cap over the union is
        # exactly what loses it again.
        for i in range(LINKED_RUNS + 1):
            s.add(Run(agent="news", trigger="mention", requested_by="user:admin",
                      prompt="p", state=RunState.SUCCEEDED,
                      ticket_id=detail["ticket"]["id"],
                      created_at=utcnow() + timedelta(minutes=i + 1)))
        await s.commit()
    runs = (await admin_client.get(f"/api/tickets/{key}")).json()["runs"]
    assert opener in {r["id"] for r in runs}
    assert len(runs) == LINKED_RUNS


async def test_an_agent_token_with_no_run_may_not_write(token_client, sf, seed_agent,
                                                        agent_store):
    """The store refuses an agent actor that brings no run — its posts would
    land at hop 0 and the loop guard would be off. That must be a 403, never a
    500."""
    await _seed(seed_agent, agent_store, "news")
    headers = await _agent_token(sf, "news")          # no run_id
    r = await token_client.post("/api/tickets",
                                json={"channel": "#general", "title": "x"},
                                headers=headers)
    assert r.status_code == 403, r.text
    # Reading is still fine: a stale key can look at the board.
    assert (await token_client.get("/api/tickets", headers=headers)).status_code == 200


async def test_an_explicitly_granted_agents_launcher_token_opens_a_ticket(
        token_client, sf, seed_agent, agent_store):
    """The live failure (docs/design/20): `agent:health-monitor` is told to open
    OPS tickets, and every one of them was a 403 because its granted token
    named no run. The launcher is driven here rather than
    imitated — a per-run key written by hand would pass whatever the launcher
    actually mints."""
    from agentplatform.agents import Manifest
    from agentplatform.config import Settings
    from agentplatform.joblauncher import K8sJobLauncher

    class _FakeBatch:
        def create_namespaced_job(self, ns, job): self.job = job

    await _seed(seed_agent, agent_store, "health-monitor", system=True)
    async with sf() as s:
        s.add(run := Run(agent="health-monitor", trigger="schedule",
                         requested_by="scheduler", state=RunState.RUNNING,
                         prompt="watch the platform"))
        await s.commit()
        run_id = run.id
    batch = _FakeBatch()
    launcher = K8sJobLauncher(batch=batch, settings=Settings(),
                              session_factory=sf)
    async with sf() as s:
        await launcher.launch(await s.get(Run, run_id), Manifest(
            system=True, platform_tools=["mcp__platform__tickets"]))
    env = {e.name: e.value for e in batch.job.spec.template.spec.containers[0].env}
    headers = {"Authorization": f"Bearer {env['AP_API_TOKEN']}"}

    r = await token_client.post("/api/tickets",
                                json={"channel": "#ops", "title": "disk is 91% full"},
                                headers=headers)
    assert r.status_code == 201, r.text
    assert r.json()["reporter"] == "agent:health-monitor"
    async with sf() as s:
        event = (await s.execute(select(TicketEvent).where(
            TicketEvent.ticket_id == r.json()["id"]))).scalar_one()
    assert event.run_id == run_id


async def test_the_hourly_create_budget_is_a_429_and_a_line_in_the_room(
        token_client, sf, seed_agent, agent_store):
    headers = await _agent_in(sf, seed_agent, agent_store, "news")
    limit = 20
    for i in range(limit):
        r = await token_client.post("/api/tickets",
                                    json={"channel": "#general", "title": f"t{i}"},
                                    headers=headers)
        assert r.status_code == 201, r.text
    r = await token_client.post("/api/tickets",
                                json={"channel": "#general", "title": "one too many"},
                                headers=headers)
    assert r.status_code == 429, r.text
    assert BUDGET_PREFIX in r.json()["detail"]
    cid = await _channel_id(sf, "general")
    async with sf() as s:
        said = list((await s.execute(select(RelayMessage).where(
            RelayMessage.channel_id == cid, RelayMessage.kind == "system",
            RelayMessage.body.like(BUDGET_PREFIX + "%")))).scalars())
    assert len(said) == 1
    # A second refusal in the same hour does not say it twice.
    assert (await token_client.post("/api/tickets",
                                    json={"channel": "#general", "title": "again"},
                                    headers=headers)).status_code == 429
    async with sf() as s:
        again = (await s.execute(select(RelayMessage).where(
            RelayMessage.channel_id == cid, RelayMessage.kind == "system",
            RelayMessage.body.like(BUDGET_PREFIX + "%")))).scalars().all()
    assert len(again) == 1
    # The dashboard reports the same spend the refusal was made on.
    budget = (await token_client.get("/api/tickets/stats", headers=headers)).json()["budget"]
    assert budget["creates_per_hour"] == limit and budget["stale_days"] == 3
    assert [(a["agent"], a["used"], a["left"]) for a in budget["agents"]] == [
        ("news", limit, 0)]
    assert budget["agents"][0]["face"]["emoji"]


# --- a channel becomes a project ---------------------------------------------

async def test_a_channels_prefix_is_editable_until_the_first_ticket(admin_client, sf):
    r = await admin_client.post("/api/relay/channels",
                                json={"kind": "channel", "name": "platform"})
    assert r.status_code == 201, r.text
    channel = r.json()
    # A new channel is a project by default, named from its own name.
    assert channel["ticket_prefix"] == "PLA"

    async def patch(**body):
        return await admin_client.patch(f"/api/relay/channels/{channel['id']}", json=body)

    assert (await patch(ticket_prefix="plat")).json()["ticket_prefix"] == "PLAT"
    assert (await patch(ticket_prefix="TOOLONG")).status_code == 422
    assert (await patch(ticket_prefix="P")).status_code == 422
    assert (await patch(ticket_prefix="OPS")).status_code == 409     # #ops has it
    await _open(admin_client, channel=channel["id"], title="first")
    assert (await patch(ticket_prefix="PLATF")).status_code == 409


async def test_a_lost_race_on_a_prefix_is_a_conflict_not_a_crash(admin_client, sf,
                                                                 monkeypatch):
    """Checking for a free prefix and then taking it is two steps, and the room
    between them is real: another patch can claim the prefix in it. The unique
    index is the arbiter that catches that, and it is postgres-only — so sqlite
    is handed the same index here in order to lose the same race."""
    async with sf() as s:
        await s.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_conversations_ticket_prefix "
            "ON conversations (ticket_prefix) WHERE ticket_prefix IS NOT NULL"))
        await s.commit()
    channel = (await admin_client.post("/api/relay/channels",
                                       json={"kind": "channel", "name": "platform"})).json()

    async def _nobody_has_one(*a, **kw):
        return set()            # the other writer commits after we looked

    monkeypatch.setattr(relay_api, "_taken_prefixes", _nobody_has_one)
    r = await admin_client.patch(f"/api/relay/channels/{channel['id']}",
                                 json={"ticket_prefix": "OPS"})
    assert r.status_code == 409, r.text
    assert "OPS" in r.json()["detail"]


async def test_only_a_channel_has_a_prefix(admin_client, sf):
    group = (await admin_client.post("/api/relay/channels",
                                     json={"kind": "group", "name": "huddle"})).json()
    assert group["ticket_prefix"] is None
    r = await admin_client.patch(f"/api/relay/channels/{group['id']}",
                                 json={"ticket_prefix": "GRP"})
    assert r.status_code == 422, r.text


async def test_a_channel_without_a_prefix_is_not_a_project(admin_client, sf):
    cid = await _channel_id(sf, "standup")
    r = await admin_client.post("/api/tickets", json={"channel": cid, "title": "x"})
    assert r.status_code == 400, r.text


async def test_an_archived_project_takes_no_more_ticket_writes(
        admin_client, token_client, sf, seed_agent, agent_store):
    """Archiving is the operator's containment lever — how a room that has gone
    wrong is stopped — and a ticket write IS a Relay write: a card, a system row
    and sometimes a mention. Relay's message route answers 404 for an archived
    room, so every ticket write does too."""
    channel = (await admin_client.post("/api/relay/channels",
                                       json={"kind": "channel", "name": "platform"})).json()
    t = await _open(admin_client, channel=channel["id"], title="before the lights went out")
    headers = await _agent_in(sf, seed_agent, agent_store, "news")
    assert (await admin_client.patch(f"/api/relay/channels/{channel['id']}",
                                     json={"archived": True})).status_code == 200

    writes = [("post", "/api/tickets", {"channel": channel["id"], "title": "x"}),
              ("patch", f"/api/tickets/{t['key']}", {"title": "y"}),
              ("post", f"/api/tickets/{t['key']}/move", {"state": "blocked"}),
              ("post", f"/api/tickets/{t['key']}/assign", {"to": None}),
              ("post", f"/api/tickets/{t['key']}/comments", {"body": "hello?"})]
    for client, extra in ((admin_client, {}), (token_client, {"headers": headers})):
        for method, path, payload in writes:
            r = await getattr(client, method)(path, json=payload, **extra)
            assert r.status_code == 404, (method, path, r.status_code, r.text)

    # Archiving is not deletion: the record stays readable to a human...
    assert (await admin_client.get(f"/api/tickets/{t['key']}")).status_code == 200
    assert t["key"] in [x["key"] for x in (await admin_client.get("/api/tickets")).json()]
    # ...and an agent's read stops at exactly the moment its write does, because
    # both are answered from the rooms `_visible` says it can see.
    assert (await token_client.get(f"/api/tickets/{t['key']}",
                                   headers=headers)).status_code == 404
    assert (await token_client.get("/api/tickets", headers=headers)).json() == []


# --- live --------------------------------------------------------------------

async def test_the_stream_carries_a_published_ticket_event(admin_client, sf):
    """The board is Kafka-fed: a change written by ANY pod reaches this one's
    streams off `tickets.events`."""
    t = await _open(admin_client)
    feed = admin_client._transport.app.state.ticket_feed
    async with sse(admin_client, "/api/tickets/events") as (resp, stream):
        assert resp["status"] == 200
        assert resp["headers"]["content-type"].startswith("text/event-stream")
        payload = {"event": {"id": "e1", "ticket_id": t["id"], "kind": "moved"},
                   "ticket": {**t, "state": "in_progress"}}
        await feed.run(StubConsumer([_msg(TOPIC_TICKETS_EVENTS, t["id"],
                                          "ticket.event", payload)]))
        event, data, _ = await stream.event()
    assert event == "ticket"
    assert (data["id"], data["state"]) == (t["id"], "in_progress")


async def test_the_stream_beats_and_skips_rooms_an_agent_is_not_in(
        admin_client, token_client, sf, seed_agent, agent_store, monkeypatch):
    monkeypatch.setattr(relay_api, "HEARTBEAT_SECONDS", 0.05)
    private = await _private_project(sf, "WAR", "user:admin")
    theirs = await _open(admin_client, channel=private, title="behind a door")
    mine = await _open(admin_client, title="in the open")
    headers = await _agent_in(sf, seed_agent, agent_store, "news")
    feed = admin_client._transport.app.state.ticket_feed
    async with sse(token_client, "/api/tickets/events", headers=headers) as (_, stream):
        assert (await stream.frame()) == ": heartbeat"
        feed.publish(STREAM, "ticket", theirs)
        feed.publish(STREAM, "ticket", mine)
        event, data, _ = await stream.event()
    assert (event, data["key"]) == ("ticket", mine["key"])


# --- the default grant -------------------------------------------------------

async def test_a_new_agent_holds_the_participant_grants(admin_client, sf):
    # The usage grant (docs/design/22) and the artifacts grant (docs/design/23)
    # ride along; they are not this file's subject, and `tests/test_quota_api.py`
    # and `tests/test_artifacts_feed.py` are where they are asserted.
    born = [RELAY_GRANT, TICKETS_GRANT, WIKI_GRANT, QUOTA_GRANT, ARTIFACTS_GRANT]
    r = await admin_client.post("/api/agents", json={"name": "newbie",
                                                     "description": "test",
                                                     "prompt": "# newbie"})
    assert r.status_code == 201, r.text
    assert r.json()["platform_tools"] == born
    async with sf() as s:
        assert (await s.get(AgentDef, "newbie")).platform_tools == born


async def test_the_tickets_default_can_be_turned_off_platform_wide(admin_client, sf):
    admin_client._transport.app.state.settings.tickets_default_grant = False
    r = await admin_client.post("/api/agents", json={"name": "quiet",
                                                     "description": "test",
                                                     "prompt": "# quiet"})
    assert r.status_code == 201, r.text
    assert r.json()["platform_tools"] == [RELAY_GRANT, WIKI_GRANT, QUOTA_GRANT,
                                          ARTIFACTS_GRANT]
