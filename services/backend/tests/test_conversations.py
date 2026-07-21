"""Conversations: CRUD, continue-turn prompt building, connector ingest, and the
recorder's outbound projector."""
from sqlalchemy import select

from agentplatform.conversation import build_prompt
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


async def test_create_list_get_close(admin_client):
    r = await admin_client.post("/api/conversations", json={"connector": "web", "agent": "hello-world"})
    assert r.status_code == 201
    cid = r.json()["id"]
    assert r.json()["connector"] == "web" and r.json()["status"] == "active"
    assert any(c["id"] == cid for c in (await admin_client.get("/api/conversations")).json())
    got = (await admin_client.get(f"/api/conversations/{cid}")).json()
    assert got["turns"] == []
    assert (await admin_client.delete(f"/api/conversations/{cid}")).status_code == 200
    assert (await admin_client.get(f"/api/conversations/{cid}")).json()["status"] == "closed"


async def test_unimplemented_connector_422(admin_client):
    r = await admin_client.post("/api/conversations", json={"connector": "slack", "agent": "hello-world"})
    assert r.status_code == 422


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


async def test_continue_closed_conversation_409(admin_client):
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
