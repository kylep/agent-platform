import json

from fastapi import APIRouter, WebSocket
from sqlalchemy import select

from agentplatform.db import TranscriptEvent

router = APIRouter()


@router.websocket("/api/runs/{run_id}/tail")
async def tail(ws: WebSocket, run_id: str):
    await ws.accept()
    async with ws.app.state.session_factory() as s:
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
    async for key, value in factory():
        if key != run_id:
            continue
        await ws.send_text(json.dumps(value))
        if value.get("terminal"):
            break
    await ws.close()
