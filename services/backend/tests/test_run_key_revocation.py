"""Per-run tokens must die with their run: once a run terminates, its scoped
API key is revoked so a finished run's operator token can't invoke agents."""
from sqlalchemy import select

from agentplatform.apikeys import generate_token, hash_token, token_prefix
from agentplatform.db import ApiKey, Run, RunState
from agentplatform.events import FakeProducer
from agentplatform.recorder import Recorder


async def _run_with_key(sf, state=RunState.RUNNING):
    token = generate_token()
    async with sf() as s:
        run = Run(agent="hello-world", trigger="manual", requested_by="t", prompt="x", state=state)
        s.add(run)
        await s.flush()
        s.add(ApiKey(name="invoke:hello-world", role="operator", agent="hello-world",
                     run_id=run.id, key_hash=hash_token(token), prefix=token_prefix(token)))
        await s.commit()
        return run.id


async def _key_revoked(sf, run_id) -> bool:
    async with sf() as s:
        k = (await s.execute(select(ApiKey).where(ApiKey.run_id == run_id))).scalar_one()
        return k.revoked_at is not None


async def test_recorder_terminal_revokes_run_key(sf):
    rid = await _run_with_key(sf)
    rec = Recorder(sf)
    await rec._handle_state(rid, {"state": RunState.SUCCEEDED, "exit_code": 0})
    assert await _key_revoked(sf, rid)


async def test_recorder_active_transition_keeps_key(sf):
    rid = await _run_with_key(sf, state=RunState.DISPATCHED)
    rec = Recorder(sf)
    await rec._handle_state(rid, {"state": RunState.RUNNING})
    assert not await _key_revoked(sf, rid)


async def test_recorder_dlq_revokes_run_key(sf):
    rid = await _run_with_key(sf)
    rec = Recorder(sf)
    await rec._handle_dlq(rid, {"error": "boom"})
    assert await _key_revoked(sf, rid)
