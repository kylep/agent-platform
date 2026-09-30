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
from agentplatform.db import (ApiKey, AuthorizedRelaySession, Conversation, RelayBinding,
                              RelayMessage, RelayParticipant, Run, RunState, utcnow)
from agentplatform.events import (TOPIC_CONVERSATION_OUTBOUND, TOPIC_RELAY_MESSAGES,
                                  TOPIC_RUN_TRANSCRIPT)
from agentplatform.recorder import Recorder
from agentplatform.relay_store import outbound_for_message, post_relay_message


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


async def test_connector_turn_is_authored_by_the_external_user(sf, producer, agent_store):
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


async def test_a_racing_first_turn_is_retried_not_a_500(sf, producer, monkeypatch,
                                                      agent_store):
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


async def test_dm_reply_is_top_level_not_threaded(sf, producer):
    """QA-16: a DM is one conversation, so the agent's answer sits in the
    transcript beside the human's message. Threading it under the trigger — right
    for a room running several conversations — hid every DM reply behind a
    thread the DM pane never opens."""
    cid = await _dm(sf)
    async with sf() as s:
        conv = await s.get(Conversation, cid)
        trig = RelayMessage(channel_id=cid, author="user:admin", body="hello?", hop=0)
        s.add(trig); await s.flush()
        run = Run(agent="hello-world", trigger="conversation", requested_by="user:admin",
                  prompt="p", conversation_id=cid, state=RunState.RUNNING,
                  trigger_message_id=trig.id)
        s.add(run); await s.commit()
        assert conv.kind == "dm"
        rid, trig_id = run.id, trig.id
    rec = Recorder(sf, producer)
    await rec.handle(TOPIC_RUN_TRANSCRIPT, rid, {"seq": 1, "type": "result",
                                                 "result": "hi there"})
    reply = (await _rows(sf, cid))[-1]
    assert (reply.reply_to, reply.thread_root) == (None, None)
    # Provenance survives: the reply still knows which message it answers.
    assert reply.trigger_message_id == trig_id and reply.author == "agent:hello-world"


async def test_no_reply_reaches_a_legacy_bridge(sf, producer):
    """docs/design/34 retired generic Relay mirroring: external effects need a
    persona-owned delivery request, so neither a web DM, a legacy Discord-shaped
    room nor a bound room produces `conversation.outbound`."""
    rec = Recorder(sf, producer)
    rid, _ = await _turn(sf)
    await rec.handle(TOPIC_RUN_TRANSCRIPT, rid, {"seq": 1, "type": "result", "result": "a"})
    rid, _ = await _turn(sf, connector="discord", external_ref="thread-1")
    await rec.handle(TOPIC_RUN_TRANSCRIPT, rid, {"seq": 1, "type": "result", "result": "b"})
    rid, cid = await _turn(sf)
    async with sf() as s:
        s.add(RelayBinding(channel_id=cid, connector="discord", external_ref="thread-2"))
        await s.commit()
    await rec.handle(TOPIC_RUN_TRANSCRIPT, rid, {"seq": 1, "type": "result", "result": "c"})
    # The room still gets its message; only the bridge is gone.
    assert [m.body for m in await _rows(sf, cid)] == ["c"]
    assert _outbound(producer) == []


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
    # No legacy bridge copy (docs/design/34).
    assert len(_messages(producer)) == 1 and _outbound(producer) == []


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
    # The legacy bridge no longer mirrors it (docs/design/34).
    assert _outbound(producer) == []


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
        # Resumes are keyed by the run's frozen authorization generation
        # (docs/design/34), so an old-generation session cannot be resumed.
        row = await s.get(AuthorizedRelaySession, {"channel_id": cid, "agent": "hello-world",
                                                   "authorization_generation": 0})
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
        if fired or not any(isinstance(o, AuthorizedRelaySession) for o in session.new):
            return
        fired.append(1)
        session.add(AuthorizedRelaySession(channel_id=cid, agent="hello-world",
                                           authorization_generation=0))

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
        rows = (await s.execute(select(AuthorizedRelaySession))).scalars().all()
    assert len(rows) == 1 and rows[0].claude_session_id == "sid-2"


# --- ingest ------------------------------------------------------------------


@pytest.fixture
def ingestor(sf, producer):
    return ConversationIngestor(Settings(), sf, producer)


async def _bound_channel(sf, *, ref: str) -> str:
    async with sf() as s:
        conv = Conversation(connector="web", kind="channel", name="bridged", open=True,
                            agent=None, title="#bridged")
        s.add(conv); await s.flush()
        s.add(RelayBinding(channel_id=conv.id, connector="discord", external_ref=ref))
        await s.commit()
        return conv.id


async def test_retired_discord_kafka_ingress_changes_nothing(ingestor, sf, producer):
    """docs/design/34: Discord observations arrive through the authenticated
    external-chat API (tests/test_external_chat.py). A legacy
    `conversation.inbound` payload is ignored, for a new ref and for one a
    legacy binding already names: no room, binding, message, participant, run
    or publish."""
    cid = await _bound_channel(sf, ref="c-1")
    before = await _rows(sf, cid)
    for ref in ("t-new", "c-1"):
        await ingestor.handle({"connector": "discord", "external_ref": ref,
                               "external_message_id": "9911", "external_user": "55",
                               "display_name": "Kyle", "text": "@hello-world hey",
                               "agent": "hello-world"})
    async with sf() as s:
        assert [b.external_ref for b in (await s.execute(select(RelayBinding))).scalars()] == ["c-1"]
        assert (await s.execute(select(Conversation).where(
            Conversation.external_ref == "t-new"))).scalars().all() == []
        assert (await s.execute(select(Run))).scalars().all() == []
        assert (await s.execute(select(RelayParticipant).where(
            RelayParticipant.participant == "discord:55"))).scalars().all() == []
    assert await _rows(sf, cid) == before
    assert _messages(producer) == []


# --- the retired bound-room bridge (docs/design/19 T10, docs/design/34) ------


async def _general(sf) -> str:
    async with sf() as s:
        return (await s.execute(select(Conversation.id).where(
            Conversation.kind == "channel", Conversation.name == "general"))).scalar_one()


async def _bind(sf, channel_id: str, *, external_ref: str,
                connector: str = "discord") -> None:
    async with sf() as s:
        s.add(RelayBinding(channel_id=channel_id, connector=connector,
                           external_ref=external_ref))
        await s.commit()


async def test_a_bound_room_mirrors_nothing(admin_client, sf, producer):
    """docs/design/34 retired the design-19 T10 bridge: a binding is no longer
    permission to send. Human posts, messages from either side, multi-bridge
    rooms and failed-run notices all stay in Relay."""
    cid = await _general(sf)
    await _bind(sf, cid, external_ref="chan-d")
    await _bind(sf, cid, external_ref="chan-s", connector="slack")
    assert (await admin_client.post(f"/api/relay/channels/{cid}/messages",
                                    json={"body": "morning"})).status_code == 200
    async with sf() as s:
        conv = await s.get(Conversation, cid)
        theirs = await post_relay_message(s, conv, author="discord:123", body="hello")
        mine = await post_relay_message(s, conv, author="user:admin", body="hi back")
        await s.commit()
        assert await outbound_for_message(s, conv, theirs) == []
        assert await outbound_for_message(s, conv, mine) == []
    rid, dm = await _turn(sf)
    await _bind(sf, dm, external_ref="chan-fail")
    await Recorder(sf, producer)._handle_state(rid, {"state": RunState.FAILED,
                                                     "exit_code": 1})
    assert (await _rows(sf, dm))[-1].kind == "system"
    assert _outbound(producer) == []


async def test_an_unbound_channel_mirrors_nothing(admin_client, sf, producer):
    cid = await _general(sf)
    assert (await admin_client.post(f"/api/relay/channels/{cid}/messages",
                                    json={"body": "just us"})).status_code == 200
    assert _outbound(producer) == []

