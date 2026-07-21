"""Run-metrics rollups: per-agent and platform-wide aggregates computed on
read from the runs table. Portable (Python aggregation, no engine-specific
date math) and cheap at this scale; a bounded recent window caps the work."""
from collections import Counter, defaultdict
from datetime import timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select

from agentplatform.api.auth import READ_ROLES, require_role
from agentplatform.db import ACTIVE_STATES, Run, RunState, utcnow
from agentplatform.scheduler import as_utc

router = APIRouter()

# Cap how many recent runs a rollup scans, so metrics stay cheap as history grows.
_WINDOW = 5000
_SUCCESS = RunState.SUCCEEDED


def _duration(run: Run) -> float | None:
    s, f = as_utc(run.started_at), as_utc(run.finished_at)
    if s and f:
        return max(0.0, (f - s).total_seconds())
    return None


def _agg(runs: list[Run]) -> dict:
    by_state = Counter(r.state for r in runs)
    terminal = [r for r in runs if r.state not in ACTIVE_STATES]
    succeeded = by_state.get(_SUCCESS, 0)
    durations = [d for d in (_duration(r) for r in terminal) if d is not None]
    last = max((as_utc(r.created_at) for r in runs if r.created_at), default=None)
    return {
        "total": len(runs),
        "by_state": dict(by_state),
        "active": sum(by_state.get(s, 0) for s in ACTIVE_STATES),
        "succeeded": succeeded,
        "success_rate": round(succeeded / len(terminal), 4) if terminal else None,
        "tokens_in": sum(r.tokens_in or 0 for r in runs),
        "tokens_out": sum(r.tokens_out or 0 for r in runs),
        "tool_calls": sum(r.tool_calls or 0 for r in runs),
        "avg_duration_seconds": round(sum(durations) / len(durations), 2) if durations else None,
        "max_duration_seconds": round(max(durations), 2) if durations else None,
        "last_run_at": last.isoformat() if last else None,
    }


def _failure_streak(runs: list[Run]) -> int:
    """Consecutive most-recent terminal runs that did not succeed. Feeds the
    alerting hook (a run of failures on one agent)."""
    streak = 0
    for r in sorted(runs, key=lambda r: as_utc(r.created_at) or utcnow(), reverse=True):
        if r.state in ACTIVE_STATES:
            continue
        if r.state == _SUCCESS:
            break
        streak += 1
    return streak


async def _recent_runs(request: Request) -> list[Run]:
    async with request.app.state.session_factory() as s:
        return list((await s.execute(
            select(Run).order_by(Run.created_at.desc()).limit(_WINDOW))).scalars())


@router.get("/api/metrics/overview", dependencies=[Depends(require_role(*READ_ROLES))])
async def overview(request: Request):
    runs = await _recent_runs(request)
    now = utcnow()
    day = [r for r in runs if r.created_at and as_utc(r.created_at) >= now - timedelta(days=1)]
    week = [r for r in runs if r.created_at and as_utc(r.created_at) >= now - timedelta(days=7)]
    out = _agg(runs)
    out["runs_24h"] = len(day)
    out["runs_7d"] = len(week)
    out["dlq"] = out["by_state"].get(RunState.DLQ, 0)
    out["window"] = _WINDOW
    return out


@router.get("/api/metrics/agents", dependencies=[Depends(require_role(*READ_ROLES))])
async def per_agent(request: Request):
    runs = await _recent_runs(request)
    buckets: dict[str, list[Run]] = defaultdict(list)
    for r in runs:
        buckets[r.agent].append(r)
    rows = []
    for agent, agent_runs in buckets.items():
        row = _agg(agent_runs)
        row["agent"] = agent
        row["failure_streak"] = _failure_streak(agent_runs)
        rows.append(row)
    rows.sort(key=lambda r: r["total"], reverse=True)
    return rows
