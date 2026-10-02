"""Platform maintenance mode (design 39, Lifecycle -> Restore).

A restore is a database point in time, not a platform point in time, so a
restored platform starts with automation paused and Kyle resumes it. What
pauses: crons, schedules, jobs, Tasks and App-data materialization. What
doesn't: runs and conversations Kyle starts himself.

Two gates for code that lands later: R2 tool actions and M6 service
principals call `require_running`; the materializer (B21) calls
`materialization_allowed`.
"""
from datetime import datetime

from agentplatform.db import PlatformMaintenance, utcnow

RUNNING = "running"
RESTORE = "restore"
_ROW = 1


class MaintenanceActive(Exception):
    """Raised by `require_running` while the platform is in maintenance."""


async def _row(s) -> PlatformMaintenance | None:
    return await s.get(PlatformMaintenance, _ROW)


async def status(s) -> dict:
    row = await _row(s)
    if row is None:
        return {"mode": RUNNING, "reason": "", "entered_at": None, "resumed_by": None}
    entered: datetime | None = row.entered_at
    return {"mode": row.mode, "reason": row.reason,
            "entered_at": entered.isoformat() if entered else None,
            "resumed_by": row.resumed_by}


async def is_paused(s) -> bool:
    row = await _row(s)
    return row is not None and row.mode != RUNNING


async def require_running(s) -> None:
    if await is_paused(s):
        raise MaintenanceActive("the platform is in maintenance mode; automation is paused")


async def materialization_allowed(s) -> bool:
    return not await is_paused(s)


async def enter_restore(s, reason: str) -> None:
    """Caller commits."""
    row = await _row(s)
    if row is None:
        row = PlatformMaintenance(id=_ROW)
        s.add(row)
    row.mode, row.reason, row.entered_at, row.resumed_by = RESTORE, reason, utcnow(), None


async def resume(s, by: str) -> bool:
    """Leave maintenance. False if the platform was already running. Caller commits."""
    row = await _row(s)
    if row is None or row.mode == RUNNING:
        return False
    row.mode, row.resumed_by = RUNNING, by
    return True
