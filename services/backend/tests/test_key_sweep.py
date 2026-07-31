"""Orphaned per-run key revocation (containment sweep)."""
from sqlalchemy import select

from agentplatform.apikeys import generate_token, hash_token, revoke_orphaned_run_keys, token_prefix
from agentplatform.db import ApiKey, Run, RunState


def _key(run_id: str) -> ApiKey:
    tok = generate_token()
    return ApiKey(name=f"system:{run_id[:6]}", role="operator", run_id=run_id,
                  key_hash=hash_token(tok), prefix=token_prefix(tok))


async def test_orphaned_run_keys_revoked(sf):
    async with sf() as s:
        done = Run(agent="a", trigger="manual", requested_by="t", prompt="x",
                   state=RunState.SUCCEEDED)
        live = Run(agent="a", trigger="manual", requested_by="t", prompt="x",
                   state=RunState.RUNNING)
        s.add_all([done, live]); await s.commit()
        done_id, live_id = done.id, live.id
    async with sf() as s:
        s.add_all([_key(done_id), _key(live_id)])
        await s.commit()

    async with sf() as s:
        n = await revoke_orphaned_run_keys(s)
        await s.commit()
    assert n == 1
    async with sf() as s:
        rows = {k.run_id: k.revoked_at for k in (await s.execute(select(ApiKey))).scalars()}
    assert rows[done_id] is not None and rows[live_id] is None

    # idempotent: nothing left to revoke on the next pass
    async with sf() as s:
        assert await revoke_orphaned_run_keys(s) == 0
