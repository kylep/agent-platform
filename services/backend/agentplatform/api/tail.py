import asyncio
import json

from fastapi import APIRouter, WebSocket
from sqlalchemy import select

from agentplatform.api import auth
from agentplatform.api.auth import USER_ROLE, resolve_session, signed_in_now, still_signed_in
from agentplatform.db import Run, TranscriptEvent
from agentplatform.authority import assert_readable_run

router = APIRouter()


@router.websocket("/api/runs/{run_id}/tail")
async def tail(ws: WebSocket, run_id: str):
    await ws.accept()
    cookie = ws.cookies.get("ap_session")
    resolved = await resolve_session(ws.app, cookie)
    if resolved is None:
        await ws.close(code=4401)
        return
    # The no-access tier (docs/design/40) is signed in but reads nothing.
    if resolved[0].role == USER_ROLE:
        await ws.close(code=4403)
        return
    signed_in_now(ws.app, cookie, resolved[0].id)
    async with ws.app.state.session_factory() as s:
        run = await s.get(Run, run_id)
        if run is None:
            await ws.close(code=4404)
            return
        # This endpoint authenticates human session cookies only. Keep the
        # shared history policy here if agent authentication is added later.
        await assert_readable_run(s, ws, run)
        rows = (
            await s.execute(
                select(TranscriptEvent)
                .where(TranscriptEvent.run_id == run_id)
                .order_by(TranscriptEvent.seq)
            )
        ).scalars()
        for e in rows:
            await ws.send_text(json.dumps(e.payload))
    factory = ws.app.state.consumer_factory
    if factory is None:
        await ws.close()
        return
    # A tail can outlive its session: a sign-out, password change or reset
    # closes it. The check runs per event AND on a timer while the run is idle,
    # so a quiet stream does not outlive a revoke. The iterator's next is a
    # task we wait on, never cancel: cancelling would end the generator.
    events = factory().__aiter__()
    nxt = asyncio.ensure_future(events.__anext__())
    try:
        while True:
            done, _ = await asyncio.wait({nxt}, timeout=max(auth.SESSION_RECHECK_SECONDS, 0.5))
            if not await still_signed_in(ws.app, cookie):
                await ws.close(code=4401)
                return
            if not done:
                continue
            try:
                key, value = nxt.result()
            except StopAsyncIteration:
                break
            nxt = asyncio.ensure_future(events.__anext__())
            if key != run_id:
                continue
            await ws.send_text(json.dumps(value))
            if value.get("terminal"):
                break
    finally:
        nxt.cancel()
    await ws.close()
