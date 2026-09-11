"""The compatibility facade (docs/design/19 T4): the DM endpoints, the ingestor
and the recorder all speak relay_messages now. A turn is still a Run, but the
turn's text lives in the channel, so the messenger and the old single-agent
conversation are the same room seen from two sides."""
import base64

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from agentplatform.apikeys import generate_token, hash_token, token_prefix
from agentplatform.config import Settings
from agentplatform import conversation
from agentplatform.conversation import _fold, _history, continue_conversation
from agentplatform.conversation_ingest import ConversationIngestor
from agentplatform.db import (ApiKey, Conversation, RelayBinding, RelayMessage,
                              RelayParticipant, RelaySession, Run, RunState, utcnow)
from agentplatform.events import (TOPIC_CONVERSATION_OUTBOUND, TOPIC_RELAY_MESSAGES,
                                  TOPIC_RUN_TRANSCRIPT)
from agentplatform.recorder import Recorder


def _messages(producer):
    return [d for t, _, d in producer.published if t == TOPIC_RELAY_MESSAGES]


def _outbound(producer):
    return [d for t, _, d in producer.published if t == TOPIC_CONVERSATION_OUTBOUND]


async def _rows(sf, channel_id):
    async with sf() as s:
        return list((await s.execute(select(RelayMessage).where(
            RelayMessage.channel_id == channel_id)
            .order_by(RelayMessage.created_at, RelayMessage.id))).scalars())


async def _dm(sf, **fields) -> str:
    async with sf() as s:
        conv = Conversation(connector="web", agent="hello-world", title="t", **fields)
        s.add(conv); await s.commit()
        return conv.id


# --- the human turn ----------------------------------------------------------


async def test_turn_posts_a_human_message_and_publishes(admin_client, sf, producer):
    cid = (await admin_client.post("/api/conversations",
           json={"connector": "web", "agent": "hello-world"})).json()["id"]
    r = await admin_client.post(f"/api/conversations/{cid}/messages",
                                json={"text": "how are you?"})
    assert r.status_code == 200
    run_id = r.json()["run_id"]

    rows = await _rows(sf, cid)
    assert [(m.author, m.body, m.hop, m.kind) for m in rows] == [
        ("user:admin", "how are you?", 0, "text")]
    async with sf() as s:
        run = await s.get(Run, run_id)
        parts = set((await s.execute(select(RelayParticipant.participant).where(
            RelayParticipant.channel_id == cid))).scalars())
        conv = await s.get(Conversation, cid)
    assert run.trigger_message_id == rows[0].id
    assert parts == {"user:admin", "agent:hello-world"}
    assert conv.dm_key == "agent:hello-world|user:admin"

    published = _messages(producer)
    assert len(published) == 1
    assert published[0]["id"] == rows[0].id and published[0]["author"] == "user:admin"
    assert published[0]["channel_kind"] == "dm"
    assert any(e["type"] == "relay.message" for e in producer.envelopes)


async def test_connector_turn_is_authored_by_the_external_user(sf, producer):
    cid = await _dm(sf)
    await continue_conversation(sf, producer, cid, "hey", "connector:discord:kyle")
    assert [m.author for m in await _rows(sf, cid)] == ["discord:kyle"]


async def test_history_folds_messages_into_turns(sf, producer):
    cid = await _dm(sf)
    async with sf() as s:
        base = utcnow()
        for i, (author, body) in enumerate([("user:admin", "first question"),
                                            ("agent:hello-world", "first answer"),
                                            ("user:admin", "second question")]):
            s.add(RelayMessage(channel_id=cid, author=author, body=body,
                               created_at=base.replace(microsecond=i)))
        # A deleted message and a system notice are not conversation history.
        s.add(RelayMessage(channel_id=cid, author="user:admin", body="oops",
                           created_at=base.replace(microsecond=4), deleted_at=utcnow()))
        s.add(RelayMessage(channel_id=cid, author="system:relay", kind="system",
                           body="😵 boom", created_at=base.replace(microsecond=5)))
        await s.commit()
        assert await _history(s, cid) == [("first question", "first answer"),
                                          ("second question", "")]


async def test_history_falls_back_to_runs_when_the_channel_is_empty(sf):
    cid = await _dm(sf)
    async with sf() as s:
        s.add(Run(agent="hello-world", trigger="conversation", requested_by="u",
                  prompt="p", conversation_id=cid, user_message="q", result="a",
                  state=RunState.SUCCEEDED))
        await s.commit()
        assert await _history(s, cid) == [("q", "a")]


def test_fold_starts_a_pair_even_when_an_agent_speaks_first():
    """A room where the agent spoke first (a wake, a scheduled nudge) still
    replays as turns: the reply belongs to a prompt nobody typed."""
    class _Row:
        def __init__(self, author, body):
            self.author, self.body = author, body
    assert _fold([_Row("agent:hello-world", "morning"),
                  _Row("user:admin", "hi"),
                  _Row("agent:hello-world", "hello")]) == [("", "morning"),
                                                           ("hi", "hello")]


async def test_a_racing_first_turn_is_retried_not_a_500(sf, producer, monkeypatch):
    """Two first turns into a participant-less DM stage the same membership
    rows; the loser must re-read and post, not lose the message the user typed.
    sqlite cannot run the two transactions at once, so the conflicting row is
    staged in the same flush — the same primary key, raised where the real race
    raises it."""
    cid = await _dm(sf)
    real, calls = conversation.post_relay_message, []

    async def racing(session, conv, **kw):
        calls.append(1)
        if len(calls) == 1:
            session.add(RelayParticipant(channel_id=conv.id, participant="user:admin"))
        return await real(session, conv, **kw)

    monkeypatch.setattr(conversation, "post_relay_message", racing)
    run_id = await continue_conversation(sf, producer, cid, "hi", "admin")
    assert run_id is not None and len(calls) == 2
    assert [m.body for m in await _rows(sf, cid)] == ["hi"]
    async with sf() as s:
        parts = (await s.execute(select(RelayParticipant.participant).where(
            RelayParticipant.channel_id == cid))).scalars().all()
    assert sorted(parts) == ["agent:hello-world", "user:admin"]


async def test_continue_refuses_a_channel(sf, producer):
    async with sf() as s:
        cid = (await s.execute(select(Conversation.id).where(
            Conversation.name == "general"))).scalar_one()
    assert await continue_conversation(sf, producer, cid, "hi", "admin") is None


# --- the agent's reply -------------------------------------------------------


async def _turn(sf, *, connector="web", external_ref=None, trigger="conversation",
                trigger_message_id=None, state=RunState.RUNNING, result=None):
    async with sf() as s:
        conv = Conversation(connector=connector, external_ref=external_ref,
                            agent="hello-world", title="t")
        s.add(conv); await s.flush()
        run = Run(agent="hello-world", trigger=trigger, requested_by="u", prompt="p",
                  conversation_id=conv.id, user_message="hi", state=state,
                  result=result, trigger_message_id=trigger_message_id)
        s.add(run); await s.commit()
        return run.id, conv.id


async def test_reply_is_posted_once_however_the_frames_race(sf, producer):
    rid, cid = await _turn(sf)
    rec = Recorder(sf, producer)
    await rec.handle(TOPIC_RUN_TRANSCRIPT, rid,
                     {"seq": 1, "type": "result", "result": "the answer"})
    await rec._handle_state(rid, {"state": RunState.SUCCEEDED, "exit_code": 0})
    rows = await _rows(sf, cid)
    assert [(m.author, m.body, m.run_id, m.kind) for m in rows] == [
        ("agent:hello-world", "the answer", rid, "text")]
    assert [d["id"] for d in _messages(producer)] == [rows[0].id]


async def test_state_first_then_result_posts_the_real_text_once(sf, producer):
    """The other ordering: the terminal state lands before the result frame, and
    the room must still end up with one message carrying the real answer."""
    rid, cid = await _turn(sf)
    rec = Recorder(sf, producer)
    await rec._handle_state(rid, {"state": RunState.SUCCEEDED, "exit_code": 0})
    await rec.handle(TOPIC_RUN_TRANSCRIPT, rid,
                     {"seq": 1, "type": "result", "result": "the answer"})
    rows = await _rows(sf, cid)
    assert [(m.author, m.body, m.kind) for m in rows] == [
        ("agent:hello-world", "the answer", "text")]
    assert [d["id"] for d in _messages(producer)] == [rows[0].id]


async def test_failed_run_posts_a_system_message(sf, producer):
    rid, cid = await _turn(sf)
    async with sf() as s:
        run = await s.get(Run, rid)
        run.error = "pod evicted"
        await s.commit()
    rec = Recorder(sf, producer)
    await rec._handle_state(rid, {"state": RunState.FAILED, "exit_code": 1})
    rows = await _rows(sf, cid)
    assert [(m.author, m.kind) for m in rows] == [("system:relay", "system")]
    assert rows[0].body == "😵 hello-world couldn't answer: pod evicted"
    assert [d["id"] for d in _messages(producer)] == [rows[0].id]


async def test_mention_reply_lands_one_hop_deeper_in_the_thread(sf, producer):
    async with sf() as s:
        conv = Conversation(connector="web", kind="channel", name="room", open=True,
                            agent=None, title="#room")
        s.add(conv); await s.flush()
        root = RelayMessage(channel_id=conv.id, author="user:admin", body="start", hop=1)
        s.add(root); await s.flush()
        trig = RelayMessage(channel_id=conv.id, author="agent:news", hop=2,
                            body="@hello-world what do you think?", thread_root=root.id)
        s.add(trig); await s.flush()
        run = Run(agent="hello-world", trigger="mention", requested_by="agent:news",
                  prompt="p", conversation_id=conv.id, state=RunState.RUNNING,
                  trigger_message_id=trig.id)
        s.add(run); await s.commit()
        rid, cid, root_id, trig_id = run.id, conv.id, root.id, trig.id
    rec = Recorder(sf, producer)
    await rec.handle(TOPIC_RUN_TRANSCRIPT, rid, {"seq": 1, "type": "result",
                                                 "result": "I think so"})
    reply = (await _rows(sf, cid))[-1]
    assert reply.hop == 3 and reply.thread_root == root_id and reply.reply_to == root_id
    assert reply.trigger_message_id == trig_id and reply.author == "agent:hello-world"


async def test_outbound_only_for_bound_or_legacy_channels(sf, producer):
    # A web DM nothing bridges: the message is the delivery, no outbound.
    rid, _ = await _turn(sf)
    rec = Recorder(sf, producer)
    await rec.handle(TOPIC_RUN_TRANSCRIPT, rid, {"seq": 1, "type": "result", "result": "a"})
    assert _outbound(producer) == []
    # The legacy Discord shape (no binding row yet) still reaches the connector.
    rid, _ = await _turn(sf, connector="discord", external_ref="thread-1")
    await rec.handle(TOPIC_RUN_TRANSCRIPT, rid, {"seq": 1, "type": "result", "result": "b"})
    assert [d["external_ref"] for d in _outbound(producer)] == ["thread-1"]
    # A bound channel: the binding is what the bridge is listening to.
    rid, cid = await _turn(sf)
    async with sf() as s:
        s.add(RelayBinding(channel_id=cid, connector="discord", external_ref="thread-2"))
        await s.commit()
    await rec.handle(TOPIC_RUN_TRANSCRIPT, rid, {"seq": 1, "type": "result", "result": "c"})
    last = _outbound(producer)[-1]
    assert last["external_ref"] == "thread-2" and last["connector"] == "discord"
    assert last["author"] == "agent:hello-world"
    assert last["message_id"] == (await _rows(sf, cid))[-1].id


async def test_reconcile_posts_the_message_too(sf, producer):
    from datetime import timedelta
    rid, cid = await _turn(sf, connector="discord", external_ref="t-late",
                           state=RunState.SUCCEEDED, result="late answer")
    async with sf() as s:
        run = await s.get(Run, rid)
        run.finished_at = utcnow() - timedelta(seconds=300)
        await s.commit()
    rec = Recorder(sf, producer)
    assert await rec.reconcile_replies(60) == 1
    assert [m.body for m in await _rows(sf, cid)] == ["late answer"]
    assert len(_messages(producer)) == 1 and len(_outbound(producer)) == 1


async def test_a_lost_reply_does_not_read_as_a_failure(sf, producer):
    """A run that succeeded and whose result frame never arrived: the room is
    told the words are gone, not that the agent could not answer."""
    from datetime import timedelta
    rid, cid = await _turn(sf, connector="discord", external_ref="t-lost",
                           state=RunState.SUCCEEDED)
    async with sf() as s:
        run = await s.get(Run, rid)
        run.finished_at = utcnow() - timedelta(seconds=300)
        await s.commit()
    rec = Recorder(sf, producer)
    assert await rec.reconcile_replies(60) == 1
    rows = await _rows(sf, cid)
    assert rows[0].kind == "system"
    assert rows[0].body == f"😵 hello-world answered, but the reply was lost (run {rid[:8]})"
    # The bridge keeps the placeholder it has always been given.
    assert _outbound(producer)[0]["text"] == "(the run succeeded without a reply)"


# --- sessions ----------------------------------------------------------------


async def test_session_roundtrips_through_relay_sessions(client, sf):
    cid = await _dm(sf)
    async with sf() as s:
        run = Run(agent="hello-world", trigger="conversation", requested_by="t",
                  prompt="x", state=RunState.RUNNING, conversation_id=cid)
        s.add(run); await s.commit()
        rid = run.id
    token = generate_token()
    async with sf() as s:
        s.add(ApiKey(name="session:hello-world", role="session", agent="hello-world",
                     run_id=rid, key_hash=hash_token(token), prefix=token_prefix(token)))
        await s.commit()
    auth = {"Authorization": f"Bearer {token}"}
    blob = base64.b64encode(b"jsonl bytes").decode()
    put = await client.put(f"/api/runs/{rid}/session",
                           json={"session_id": "sid-9", "blob_b64": blob}, headers=auth)
    assert put.status_code == 200 and put.json() == {"ok": True, "reset": False}
    async with sf() as s:
        row = await s.get(RelaySession, {"channel_id": cid, "agent": "hello-world"})
        conv = await s.get(Conversation, cid)
    assert row is not None and row.claude_session_id == "sid-9"
    assert conv.session_blob is None   # the legacy column is no longer written
    got = (await client.get(f"/api/runs/{rid}/session", headers=auth)).json()
    assert got == {"session_id": "sid-9", "blob_b64": blob}


async def test_a_racing_first_put_writes_into_the_winners_row(client, sf):
    """Two runs of the same agent in one channel can PUT their first blob at
    once. sqlite cannot interleave the transactions, so the conflicting row is
    staged in the same flush: the endpoint must re-read rather than 500."""
    cid = await _dm(sf)
    async with sf() as s:
        run = Run(agent="hello-world", trigger="conversation", requested_by="t",
                  prompt="x", state=RunState.RUNNING, conversation_id=cid)
        s.add(run); await s.commit()
        rid = run.id
    token = generate_token()
    async with sf() as s:
        s.add(ApiKey(name="session:hello-world", role="session", agent="hello-world",
                     run_id=rid, key_hash=hash_token(token), prefix=token_prefix(token)))
        await s.commit()
    fired = []

    def race(session, flush_context, instances):
        if fired or not any(isinstance(o, RelaySession) for o in session.new):
            return
        fired.append(1)
        session.add(RelaySession(channel_id=cid, agent="hello-world"))

    event.listen(Session, "before_flush", race)
    try:
        blob = base64.b64encode(b"second writer").decode()
        put = await client.put(f"/api/runs/{rid}/session",
                               json={"session_id": "sid-2", "blob_b64": blob},
                               headers={"Authorization": f"Bearer {token}"})
    finally:
        event.remove(Session, "before_flush", race)
    assert fired and put.status_code == 200 and put.json() == {"ok": True, "reset": False}
    async with sf() as s:
        rows = (await s.execute(select(RelaySession))).scalars().all()
    assert len(rows) == 1 and rows[0].claude_session_id == "sid-2"


# --- ingest ------------------------------------------------------------------


@pytest.fixture
def ingestor(sf, producer):
    return ConversationIngestor(Settings(), sf, producer)


async def test_ingest_binds_a_first_contact_ref(ingestor, sf):
    await ingestor.handle({"connector": "discord", "external_ref": "t-new",
                           "external_user": "kyle", "text": "hey",
                           "agent": "hello-world"})
    async with sf() as s:
        binding = (await s.execute(select(RelayBinding))).scalars().one()
        conv = await s.get(Conversation, binding.channel_id)
        parts = set((await s.execute(select(RelayParticipant.participant).where(
            RelayParticipant.channel_id == binding.channel_id))).scalars())
    assert binding.connector == "discord" and binding.external_ref == "t-new"
    assert conv.external_ref == "t-new" and conv.kind == "dm"
    assert parts == {"discord:kyle", "agent:hello-world"}
    assert [m.author for m in await _rows(sf, conv.id)] == ["discord:kyle"]


async def test_ingest_reopens_a_closed_bound_room(ingestor, sf):
    """The binding is unique, so a closed channel behind it would strand every
    later message from that thread. An inbound message reopens the room."""
    async with sf() as s:
        conv = Conversation(connector="discord", external_ref="t-closed",
                            agent="hello-world", title="t", status="closed")
        s.add(conv); await s.flush()
        s.add(RelayBinding(channel_id=conv.id, connector="discord",
                           external_ref="t-closed"))
        await s.commit()
        cid = conv.id
    await ingestor.handle({"connector": "discord", "external_ref": "t-closed",
                           "external_user": "kyle", "text": "back again",
                           "agent": "hello-world"})
    async with sf() as s:
        convs = (await s.execute(select(Conversation).where(
            Conversation.kind == "dm"))).scalars().all()
        run = (await s.execute(select(Run))).scalars().one()
    assert [(c.id, c.status) for c in convs] == [(cid, "active")]
    assert run.conversation_id == cid


async def test_ingest_resolves_an_existing_binding(ingestor, sf):
    async with sf() as s:
        conv = Conversation(connector="web", agent="hello-world", title="bridged")
        s.add(conv); await s.flush()
        s.add(RelayBinding(channel_id=conv.id, connector="discord",
                           external_ref="t-bound"))
        await s.commit()
        cid = conv.id
    await ingestor.handle({"connector": "discord", "external_ref": "t-bound",
                           "external_user": "kyle", "text": "hey",
                           "agent": "hello-world"})
    async with sf() as s:
        convs = (await s.execute(select(Conversation).where(
            Conversation.kind == "dm"))).scalars().all()
        run = (await s.execute(select(Run))).scalars().one()
    assert [c.id for c in convs] == [cid] and run.conversation_id == cid
