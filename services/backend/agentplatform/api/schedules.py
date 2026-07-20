from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select

from agentplatform.api.auth import require_admin
from agentplatform.db import Schedule
from agentplatform.scheduler import is_valid_cron

router = APIRouter(dependencies=[Depends(require_admin)])


def _cron(info) -> str:
    return info.manifest.schedule if info and info.manifest else ""


@router.get("/api/schedules")
async def list_schedules(request: Request):
    """Agents with a valid cron schedule, joined with their runtime state."""
    store = request.app.state.agent_store
    store.reload()
    async with request.app.state.session_factory() as s:
        rows = {r.agent: r for r in (await s.execute(select(Schedule))).scalars()}
    out = []
    for info in store.list():
        cron = _cron(info)
        if not is_valid_cron(cron):
            continue
        r = rows.get(info.name)
        out.append({"agent": info.name, "cron": cron,
                    "enabled": r.enabled if r else True,
                    "last_fire": r.last_fire if r else None,
                    "next_fire": r.next_fire if r else None})
    return out


@router.post("/api/schedules/{agent}/{action}")
async def set_enabled(request: Request, agent: str, action: str):
    if action not in ("enable", "disable"):
        raise HTTPException(404, "unknown action")
    store = request.app.state.agent_store
    store.reload()
    if not is_valid_cron(_cron(store.get(agent))):
        raise HTTPException(404, "agent has no schedule")
    async with request.app.state.session_factory() as s:
        row = await s.get(Schedule, agent) or Schedule(agent=agent)
        row.enabled = action == "enable"
        s.add(row)
        await s.commit()
    return {"agent": agent, "enabled": action == "enable"}
