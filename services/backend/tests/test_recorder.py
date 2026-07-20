from agentplatform.db import Run, RunState, SecretMeta, TranscriptEvent
from agentplatform.events import TOPIC_RUN_EVENTS, TOPIC_RUN_TRANSCRIPT
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
