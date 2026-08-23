"""Conversations: CRUD, continue-turn prompt building, connector ingest, and the
recorder's outbound projector."""
from datetime import timedelta

from sqlalchemy import select

from agentplatform.conversation import _history, build_prompt
from agentplatform.conversation_ingest import ConversationIngestor
from agentplatform.config import Settings
from agentplatform.db import Conversation, Run, RunState, utcnow
from agentplatform.events import (FakeProducer, TOPIC_CONVERSATION_OUTBOUND,
                                  TOPIC_RUN_REQUESTS)
from agentplatform.recorder import Recorder


def test_build_prompt():
    assert build_prompt([], "hi") == "hi"   # no history → just the message
    p = build_prompt([("hello", "hi there")], "how are you?")
    assert "User: hello" in p and "Assistant: hi there" in p and "User: how are you?" in p


async def test_connectors_registry(admin_client):
    rows = (await admin_client.get("/api/connectors")).json()
    by = {c["name"]: c for c in rows}
    assert by["web"]["implemented"] and by["discord"]["implemented"]
    assert by["slack"]["implemented"] is False


async def test_create_list_get_delete(admin_client):
    r = await admin_client.post("/api/conversations", json={"connector": "web", "agent": "hello-world"})
    assert r.status_code == 201
    cid = r.json()["id"]
    assert r.json()["connector"] == "web" and r.json()["status"] == "active"
    assert any(c["id"] == cid for c in (await admin_client.get("/api/conversations")).json())
    got = (await admin_client.get(f"/api/conversations/{cid}")).json()
    assert got["turns"] == []
    # Delete is a hard delete for web conversations — the row is gone (404).
    assert (await admin_client.delete(f"/api/conversations/{cid}")).status_code == 200
    assert (await admin_client.get(f"/api/conversations/{cid}")).status_code == 404


async def test_delete_web_detaches_turns_keeps_runs(admin_client, sf):
    from agentplatform.db import Run, RunState
    from sqlalchemy import select
    cid = (await admin_client.post("/api/conversations",
           json={"connector": "web", "agent": "hello-world"})).json()["id"]
    async with sf() as s:
        run = Run(agent="hello-world", trigger="conversation", requested_by="admin",
                  prompt="p", conversation_id=cid, user_message="hi", state=RunState.SUCCEEDED)
        s.add(run); await s.commit(); rid = run.id
    assert (await admin_client.delete(f"/api/conversations/{cid}")).status_code == 200
    async with sf() as s:
        kept = await s.get(Run, rid)
        assert kept is not None and kept.conversation_id is None  # run survives, detached


async def test_delete_discord_conversation_409(admin_client, sf):
    from agentplatform.db import Conversation
    async with sf() as s:
        conv = Conversation(connector="discord", external_ref="t-del", agent="hello-world")
        s.add(conv); await s.commit(); cid = conv.id
    r = await admin_client.delete(f"/api/conversations/{cid}")
    assert r.status_code == 409


async def test_turn_sender_surfaced(admin_client, sf):
    from agentplatform.db import Conversation, Run, RunState
    async with sf() as s:
        conv = Conversation(connector="discord", external_ref="t-snd", agent="hello-world")
        s.add(conv); await s.commit(); cid = conv.id
        s.add(Run(agent="hello-world", trigger="conversation",
                  requested_by="connector:discord:kyle", prompt="p", conversation_id=cid,
                  user_message="hey", state=RunState.SUCCEEDED))
        await s.commit()
    d = (await admin_client.get(f"/api/conversations/{cid}")).json()
    assert d["turns"][0]["sender"] == "kyle"


async def test_unimplemented_connector_422(admin_client):
    r = await admin_client.post("/api/conversations", json={"connector": "slack", "agent": "hello-world"})
    assert r.status_code == 422


async def test_history_token_budget(sf):
    # 100 turns of ~1.5k estimated tokens each: only the newest ~20 fit the 30k
    # budget. The NEWEST must survive and the OLDEST fall off (not the reverse).
    async with sf() as s:
        conv = Conversation(connector="web", agent="hello-world", title="t")
        s.add(conv); await s.flush(); cid = conv.id
        base = utcnow()
        for i in range(100):
            s.add(Run(agent="hello-world", trigger="conversation", requested_by="t",
                      prompt="x", state=RunState.SUCCEEDED, conversation_id=cid,
                      created_at=base + timedelta(seconds=i),
                      user_message=f"m{i} " + "x" * 3000, result="r" * 3000))
        await s.commit()
    hist = await _history_of(sf, cid)
    assert hist[-1][0].startswith("m99")           # newest kept
    assert not any(u.startswith("m0 ") for u, _ in hist)   # oldest dropped
    assert sum(len(u) + len(r) for u, r in hist) // 4 <= 30_000
    assert 0 < len(hist) < 100


async def _history_of(sf, cid):
    async with sf() as s:
        return await _history(s, cid)


async def test_continue_creates_turn_with_history(admin_client, sf, producer):
    cid = (await admin_client.post("/api/conversations",
           json={"connector": "web", "agent": "hello-world"})).json()["id"]
    # seed a completed prior turn so history is non-empty
    async with sf() as s:
        s.add(Run(agent="hello-world", trigger="conversation", requested_by="u", prompt="built",
                  conversation_id=cid, user_message="first question", result="first answer",
                  state=RunState.SUCCEEDED))
        await s.commit()
    r = await admin_client.post(f"/api/conversations/{cid}/messages", json={"text": "second question"})
    assert r.status_code == 200
    run_id = r.json()["run_id"]
    async with sf() as s:
        run = await s.get(Run, run_id)
    assert run.conversation_id == cid and run.user_message == "second question"
    assert run.trigger == "conversation"
    assert "first question" in run.prompt and "first answer" in run.prompt and "second question" in run.prompt
    assert any(t == TOPIC_RUN_REQUESTS and k == run_id for t, k, _ in producer.published)


async def test_continue_deleted_conversation_409(admin_client):
    cid = (await admin_client.post("/api/conversations",
           json={"connector": "web", "agent": "hello-world"})).json()["id"]
    await admin_client.delete(f"/api/conversations/{cid}")
    r = await admin_client.post(f"/api/conversations/{cid}/messages", json={"text": "hi"})
    assert r.status_code == 409


async def test_connector_ingest_maps_ref_to_conversation(sf):
    producer = FakeProducer()
    ing = ConversationIngestor(Settings(), sf, producer)
    ev = {"connector": "discord", "external_ref": "thread-1", "external_user": "kyle",
          "text": "hey pai", "agent": "hello-world"}
    await ing.handle(ev)
    await ing.handle({**ev, "text": "you there?"})   # same ref → same conversation
    async with sf() as s:
        convs = (await s.execute(select(Conversation))).scalars().all()
        runs = (await s.execute(select(Run))).scalars().all()
    assert len(convs) == 1 and convs[0].external_ref == "thread-1" and convs[0].connector == "discord"
    # first turn created a run; the second is blocked (a turn is still in flight)
    assert len(runs) == 1 and runs[0].conversation_id == convs[0].id


async def test_recorder_emits_outbound_on_terminal(sf):
    producer = FakeProducer()
    async with sf() as s:
        conv = Conversation(connector="discord", external_ref="t9", agent="hello-world")
        s.add(conv); await s.flush()
        run = Run(agent="hello-world", trigger="conversation", requested_by="u", prompt="p",
                  conversation_id=conv.id, user_message="hi", result="the reply",
                  state=RunState.RUNNING)
        s.add(run); await s.commit()
        rid, cid = run.id, conv.id
    rec = Recorder(sf, producer)
    await rec._handle_state(rid, {"state": RunState.SUCCEEDED, "exit_code": 0})
    outbound = [p for p in producer.published if p[0] == TOPIC_CONVERSATION_OUTBOUND]
    assert len(outbound) == 1
    _, key, data = outbound[0]
    assert key == cid and data["connector"] == "discord" and data["external_ref"] == "t9"
    assert data["text"] == "the reply"


async def test_rename_conversation_any_type(admin_client, sf):
    from agentplatform.db import Conversation
    # web
    cid = (await admin_client.post("/api/conversations",
           json={"connector": "web", "agent": "hello-world"})).json()["id"]
    r = await admin_client.patch(f"/api/conversations/{cid}", json={"title": "  Roadmap chat  "})
    assert r.status_code == 200 and r.json()["title"] == "Roadmap chat"
    # discord (a local label — renaming is allowed and doesn't touch the channel)
    async with sf() as s:
        conv = Conversation(connector="discord", external_ref="t-ren", agent="hello-world",
                            title="discord:t-ren")
        s.add(conv); await s.commit(); did = conv.id
    r = await admin_client.patch(f"/api/conversations/{did}", json={"title": "Kyle in Discord"})
    assert r.status_code == 200 and r.json()["title"] == "Kyle in Discord"


async def test_rename_empty_title_422(admin_client):
    cid = (await admin_client.post("/api/conversations",
           json={"connector": "web", "agent": "hello-world"})).json()["id"]
    assert (await admin_client.patch(f"/api/conversations/{cid}", json={"title": ""})).status_code == 422
