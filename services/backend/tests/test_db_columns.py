from agentplatform.db import Conversation, Run, RunModelUsage, RunState


async def test_cache_and_session_columns(sf):
    async with sf() as s:
        run = Run(agent="hello-world", trigger="manual", requested_by="t",
                  prompt="x", state=RunState.RUNNING,
                  tokens_cache_read=100, tokens_cache_creation=7)
        conv = Conversation(connector="web", agent="hello-world", title="t",
                            claude_session_id="abc-123", session_blob=b"\x00jsonl")
        s.add(run); s.add(conv)
        s.add(RunModelUsage(run_id="r1", model="m", agent="a",
                            tokens_in=1, tokens_out=2,
                            tokens_cache_read=3, tokens_cache_creation=4))
        await s.commit()
        rid, cid = run.id, conv.id
    async with sf() as s:
        r = await s.get(Run, rid)
        assert (r.tokens_cache_read, r.tokens_cache_creation) == (100, 7)
        c = await s.get(Conversation, cid)
        assert c.claude_session_id == "abc-123" and c.session_blob == b"\x00jsonl"
