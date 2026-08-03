from datetime import timedelta

from agentplatform.db import (Conversation, Run, RunState, SecretMeta, TranscriptEvent,
                             utcnow)
from agentplatform.events import (TOPIC_CONVERSATION_OUTBOUND, TOPIC_RUN_EVENTS,
                                  TOPIC_RUN_TRANSCRIPT)
from agentplatform.recorder import Recorder
from agentplatform.secrets import CLAUDE_CREDENTIAL
from sqlalchemy import select

async def seed(sf) -> str:
    async with sf() as s:
        run = Run(agent="hello-world", trigger="manual", requested_by="t",
                  prompt="x", state=RunState.RUNNING)
        s.add(run); await s.commit(); return run.id

def _assistant_tool_use(seq, *names):
    """A real stream-json assistant frame carrying one tool_use block per name."""
    return {"seq": seq, "type": "assistant",
            "message": {"role": "assistant",
                        "content": [{"type": "tool_use", "name": n, "id": f"t{i}"}
                                    for i, n in enumerate(names)]}}

async def test_transcript_and_metrics(sf):
    rid = await seed(sf); rec = Recorder(sf)
    # Two assistant frames: the first invokes Bash, the second invokes two
    # tools in one turn (each tool_use block counts) → 3 tool calls total.
    await rec.handle(TOPIC_RUN_TRANSCRIPT, rid, _assistant_tool_use(1, "Bash"))
    await rec.handle(TOPIC_RUN_TRANSCRIPT, rid, _assistant_tool_use(1, "Bash"))  # dup seq
    await rec.handle(TOPIC_RUN_TRANSCRIPT, rid, _assistant_tool_use(2, "Read", "Bash"))
    # A text-only assistant frame must not count as a tool call.
    await rec.handle(TOPIC_RUN_TRANSCRIPT, rid,
                     {"seq": 3, "type": "assistant",
                      "message": {"content": [{"type": "text", "text": "OK"}]}})
    await rec.handle(TOPIC_RUN_TRANSCRIPT, rid,
                     {"seq": 4, "type": "result", "usage": {"input_tokens": 10, "output_tokens": 5}})
    async with sf() as s:
        assert len((await s.execute(select(TranscriptEvent))).scalars().all()) == 4
        run = await s.get(Run, rid)
        assert run.tool_calls == 3 and run.tokens_in == 10 and run.tokens_out == 5

async def test_state_event_terminal(sf):
    rid = await seed(sf); rec = Recorder(sf)
    await rec.handle(TOPIC_RUN_EVENTS, rid, {"type": "state", "state": "succeeded", "exit_code": 0})
    await rec.handle(TOPIC_RUN_EVENTS, rid, {"type": "state", "state": "running"})  # no regress
    async with sf() as s:
        run = await s.get(Run, rid)
        assert run.state == "succeeded" and run.finished_at is not None and run.exit_code == 0

async def _cred_status(sf):
    async with sf() as s:
        meta = await s.get(SecretMeta, CLAUDE_CREDENTIAL)
        return meta.status if meta else None

async def test_probe_marks_credential_valid_on_success(sf):
    rid = await seed(sf); rec = Recorder(sf)
    await rec.handle(TOPIC_RUN_EVENTS, rid, {"type": "state", "state": "succeeded", "exit_code": 0})
    assert await _cred_status(sf) == "valid"

async def test_probe_marks_credential_invalid_on_auth_failure(sf):
    rid = await seed(sf); rec = Recorder(sf)
    # The CLI reports auth failures with error=authentication_failed / 401.
    await rec.handle(TOPIC_RUN_TRANSCRIPT, rid,
                     {"seq": 1, "type": "system", "subtype": "api_retry",
                      "error": "authentication_failed", "error_status": 401})
    assert await _cred_status(sf) == "invalid"

async def test_probe_ignores_non_auth_failure(sf):
    rid = await seed(sf); rec = Recorder(sf)
    # A run that fails for a non-auth reason must not invalidate the token.
    await rec.handle(TOPIC_RUN_EVENTS, rid,
                     {"type": "state", "state": "failed", "exit_code": 1})
    assert await _cred_status(sf) is None


async def test_captures_permission_denials(sf):
    rid = await seed(sf); rec = Recorder(sf)
    await rec.handle(TOPIC_RUN_TRANSCRIPT, rid, {
        "seq": 1, "type": "result",
        "permission_denials": [{"tool_name": "Bash", "tool_input": {"command": "cat /secrets"}}]})
    async with sf() as s:
        run = await s.get(Run, rid)
        assert len(run.permission_denials) == 1
        assert run.permission_denials[0]["tool_name"] == "Bash"


async def test_no_denials_leaves_empty_list(sf):
    rid = await seed(sf); rec = Recorder(sf)
    await rec.handle(TOPIC_RUN_TRANSCRIPT, rid, {"seq": 1, "type": "result", "permission_denials": []})
    async with sf() as s:
        assert (await s.get(Run, rid)).permission_denials == []


# --- conversation replies: the two topics race, the thread must get one message


async def _seed_turn(sf, *, state=RunState.RUNNING, result=None, finished=None):
    """A conversation turn (Run) plus its owning Conversation."""
    async with sf() as s:
        conv = Conversation(connector="discord", external_ref="thread-1", agent="pai")
        s.add(conv); await s.flush()
        run = Run(agent="pai", trigger="conversation", requested_by="u", prompt="p",
                  conversation_id=conv.id, user_message="hi", state=state,
                  result=result, finished_at=finished)
        s.add(run); await s.commit()
        return run.id, conv.id


def _replies(producer):
    return [d for t, _, d in producer.published if t == TOPIC_CONVERSATION_OUTBOUND]


async def test_reply_published_from_result_frame(sf, producer):
    """The result frame holds the text, so it publishes — no waiting on the
    state event, which rides a different topic."""
    rid, _ = await _seed_turn(sf)
    rec = Recorder(sf, producer)
    await rec.handle(TOPIC_RUN_TRANSCRIPT, rid, {"seq": 1, "type": "result", "result": "the answer"})
    assert [d["text"] for d in _replies(producer)] == ["the answer"]
    assert _replies(producer)[0]["external_ref"] == "thread-1"


async def test_state_first_then_result_still_replies_once_with_real_text(sf, producer):
    """The regression: the terminal state arriving BEFORE the result frame used
    to publish '(the agent produced no reply)' and permanently lose the answer."""
    rid, _ = await _seed_turn(sf)
    rec = Recorder(sf, producer)
    await rec.handle(TOPIC_RUN_EVENTS, rid, {"type": "state", "state": "succeeded", "exit_code": 0})
    await rec.handle(TOPIC_RUN_TRANSCRIPT, rid, {"seq": 1, "type": "result", "result": "the answer"})
    assert [d["text"] for d in _replies(producer)] == ["the answer"]


async def test_result_first_then_state_replies_once(sf, producer):
    """The other ordering must also yield exactly one reply, not two."""
    rid, _ = await _seed_turn(sf)
    rec = Recorder(sf, producer)
    await rec.handle(TOPIC_RUN_TRANSCRIPT, rid, {"seq": 1, "type": "result", "result": "the answer"})
    await rec.handle(TOPIC_RUN_EVENTS, rid, {"type": "state", "state": "succeeded", "exit_code": 0})
    assert [d["text"] for d in _replies(producer)] == ["the answer"]


async def test_failed_run_with_no_result_still_gets_a_reply(sf, producer):
    """A run that dies without a result frame must not leave the thread silent."""
    rid, _ = await _seed_turn(sf)
    rec = Recorder(sf, producer)
    await rec.handle(TOPIC_RUN_EVENTS, rid, {"type": "state", "state": "failed", "exit_code": 1})
    assert len(_replies(producer)) == 1 and "failed" in _replies(producer)[0]["text"]


async def test_sweep_publishes_when_result_frame_never_arrives(sf, producer):
    """Backstop: a succeeded turn whose result frame was lost gets a reply once
    the grace period has passed, rather than waiting forever."""
    rid, _ = await _seed_turn(sf, state=RunState.SUCCEEDED,
                             finished=utcnow() - timedelta(seconds=300))
    rec = Recorder(sf, producer)
    assert await rec.reconcile_replies(60) == 1
    assert len(_replies(producer)) == 1
    # ...and never twice.
    assert await rec.reconcile_replies(60) == 0


async def test_sweep_leaves_recent_runs_alone(sf, producer):
    """Inside the grace window the normal path still owns the reply."""
    rid, _ = await _seed_turn(sf, state=RunState.SUCCEEDED, finished=utcnow())
    rec = Recorder(sf, producer)
    assert await rec.reconcile_replies(60) == 0
    assert _replies(producer) == []


async def test_sweep_ignores_already_published(sf, producer):
    rid, _ = await _seed_turn(sf)
    rec = Recorder(sf, producer)
    await rec.handle(TOPIC_RUN_TRANSCRIPT, rid, {"seq": 1, "type": "result", "result": "a"})
    async with sf() as s:
        run = await s.get(Run, rid)
        run.state = RunState.SUCCEEDED
        run.finished_at = utcnow() - timedelta(seconds=300)
        await s.commit()
    assert await rec.reconcile_replies(60) == 0
    assert len(_replies(producer)) == 1


async def test_result_topic_publishes_agent_result(sf, producer, tmp_path):
    """A manifest-declared result_topic feeds the agent's successful result to
    its consuming app (docs/design/11) — errors and topic-less agents don't."""
    from agentplatform.agents import AgentStore
    d = tmp_path / "news"; d.mkdir()
    (d / "agent.md").write_text("# news\nGather.")
    (d / "manifest.yaml").write_text("description: n\nresult_topic: app.news.inbound\n")
    store = AgentStore(tmp_path)
    rec = Recorder(sf, producer, agent_store=store)
    async with sf() as s:
        run = Run(agent="news", trigger="schedule", requested_by="t",
                  prompt="x", state=RunState.RUNNING)
        other = Run(agent="hello-world", trigger="manual", requested_by="t",
                    prompt="x", state=RunState.RUNNING)
        s.add(run); s.add(other); await s.commit()
        rid, oid = run.id, other.id
    await rec.handle(TOPIC_RUN_TRANSCRIPT, rid, {"seq": 1, "type": "result",
                                                 "result": '{"items": []}'})
    feed = [(t, d) for t, _, d in producer.published if t == "app.news.inbound"]
    assert feed == [("app.news.inbound",
                     {"run_id": rid, "agent": "news", "result": '{"items": []}'})]
    # an erroring result is NOT fed; an agent without result_topic is NOT fed
    await rec.handle(TOPIC_RUN_TRANSCRIPT, rid, {"seq": 2, "type": "result",
                                                 "result": "boom", "is_error": True})
    await rec.handle(TOPIC_RUN_TRANSCRIPT, oid, {"seq": 1, "type": "result",
                                                 "result": "hi"})
    assert len([1 for t, _, _ in producer.published if t == "app.news.inbound"]) == 1
