from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select

from agentplatform.api.auth import require_admin
from agentplatform.db import Schedule

from agentplatform.api import schemas as S
router = APIRouter(dependencies=[Depends(require_admin)])


@router.get("/api/schedules", response_model=list[S.ScheduleRow])
async def list_schedules(request: Request):
    """Agents with cron triggers declared in their entrypoints, joined with
    their runtime state."""
    store = request.app.state.agent_store
    await store.reload()
    async with request.app.state.session_factory() as s:
        rows = {r.agent: r for r in (await s.execute(select(Schedule))).scalars()}
    out = []
    for info in store.list():
        crons = info.crons()
        if not crons:
            continue
        r = rows.get(info.name)
        out.append({"agent": info.name, "cron": ", ".join(crons),
                    "enabled": r.enabled if r else True,
                    "last_fire": r.last_fire if r else None,
                    "next_fire": r.next_fire if r else None})
    return out


@router.post("/api/schedules/{agent}/{action}", response_model=S.ScheduleToggle)
async def set_enabled(request: Request, agent: str, action: str):
    if action not in ("enable", "disable"):
        raise HTTPException(404, "unknown action")
    store = request.app.state.agent_store
    await store.reload()
    info = store.get(agent)
    if info is None or not info.crons():
        raise HTTPException(404, "agent has no schedule")
    async with request.app.state.session_factory() as s:
        row = await s.get(Schedule, agent) or Schedule(agent=agent)
        row.enabled = action == "enable"
        s.add(row)
        await s.commit()
    return {"agent": agent, "enabled": action == "enable"}
