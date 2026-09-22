"""The browse API, served under /apps/running/api/.

Auth mirrors stockmarket: nginx's auth_request has vetted the session/key and
stamps X-AP-User; we require it as a defense-in-depth marker that the request
came through the guarded route. Reads are open to any authenticated identity —
the `running-coach` agent reaches /summary through the platform's query_app proxy as
`reader` to learn its `sync_after` cue. There are no write endpoints here: the
only writer is the Kafka ingest path.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select

from runningapp import brief as bf
from runningapp import stats as st
from runningapp.db import Activity, Brief

router = APIRouter(prefix="/apps/running/api")

# How far back a first (empty-archive) sync reaches, and the overlap re-pulled
# each run to catch edited/renamed activities. Kept modest so the tool's direct
# Kafka sync stays bounded.
BACKFILL_DAYS = 90
OVERLAP_DAYS = 3


def require_gateway(x_ap_user: str = Header(default="")) -> str:
    if not x_ap_user:
        raise HTTPException(401, "missing gateway identity")
    return x_ap_user


def _sf(request: Request):
    return request.app.state.sf


def _row_dict(r: Activity) -> dict:
    return {"day": r.day, "type": r.type, "distance_m": r.distance_m,
            "moving_time_s": r.moving_time_s, "elevation_m": r.elevation_m,
            "avg_hr": r.avg_hr, "max_hr": r.max_hr}


async def _all_activities(s) -> list[dict]:
    rows = (await s.execute(select(Activity).order_by(Activity.day))).scalars().all()
    return [_row_dict(r) for r in rows]


class Summary(BaseModel):
    totals: dict
    latest_day: str | None
    latest_brief_week: str | None
    sync_after: str          # the date the agent should pull activities after
    today: str
    tags: list[str]


@router.get("/summary", response_model=Summary)
async def summary(request: Request, user: str = Depends(require_gateway)):
    today = date.today()
    async with _sf(request)() as s:
        acts = await _all_activities(s)
        latest_day = (await s.execute(select(func.max(Activity.day)))).scalar()
        latest_brief = (await s.execute(select(func.max(Brief.week_start)))).scalar()
    floor = (today - timedelta(days=BACKFILL_DAYS)).isoformat()
    if latest_day:
        overlap = (date.fromisoformat(latest_day) - timedelta(days=OVERLAP_DAYS)).isoformat()
        sync_after = max(overlap, floor)
    else:
        sync_after = floor
    return {"totals": st.totals(acts), "latest_day": latest_day,
            "latest_brief_week": latest_brief, "sync_after": sync_after,
            "today": today.isoformat(), "tags": bf.TAGS}


@router.get("/health")
async def health(request: Request, user: str = Depends(require_gateway)):
    async with _sf(request)() as s:
        activity_count = (await s.execute(select(func.count(Activity.id)))).scalar() or 0
        brief_count = (await s.execute(select(func.count(Brief.week_start)))).scalar() or 0
        latest_day = (await s.execute(select(func.max(Activity.day)))).scalar()
    ingest = getattr(request.app.state, "ingest", None)
    pipeline = dict(ingest.status) if ingest else {
        "consumer_started": False, "consumer_assigned": False,
        "last_message_at": None, "last_activity_sync_at": None,
        "last_error": "ingest loop is not running"}
    return {"ok": bool(pipeline.get("consumer_assigned")),
            "activity_count": activity_count, "brief_count": brief_count,
            "latest_activity_day": latest_day, "pipeline": pipeline}


@router.get("/coach-context")
async def coach_context(request: Request, user: str = Depends(require_gateway)):
    """Small deterministic context for the weekly coach. Raw Strava history
    stays in the app; the model receives trends and recent runs, not a backfill."""
    today = datetime.now(ZoneInfo("America/Toronto")).date()
    week_start = st.completed_week_start(today)
    week_end = week_start + timedelta(days=6)
    async with _sf(request)() as s:
        acts = await _all_activities(s)
        recent_rows = (await s.execute(select(Activity).order_by(
            Activity.day.desc(), Activity.id.desc()).limit(12))).scalars().all()
        week_runs = (await s.execute(select(Activity).where(
            Activity.day >= week_start.isoformat(),
            Activity.day <= week_end.isoformat(),
            Activity.type.in_(st.RUN_TYPES)).order_by(
                Activity.day.desc(), Activity.id.desc()).limit(30))).scalars().all()
    week_acts = [a for a in acts if week_start.isoformat()
                 <= a["day"] <= week_end.isoformat() and a["type"] in st.RUN_TYPES]
    return {
        "today": today.isoformat(),
        "completed_week": {
            "week_start": week_start.isoformat(),
            "week_end": week_end.isoformat(),
            "totals": st.totals(week_acts),
            "runs": [
                {"day": r.day, "name": r.name, "type": r.type,
                 "distance_km": round(r.distance_m / 1000, 2),
                 "moving_time_s": r.moving_time_s, "avg_hr": r.avg_hr}
                for r in week_runs
            ],
        },
        "totals": st.totals(acts),
        "weeks": st.weekly(acts, today, 8),
        "records": st.prs(acts, today),
        "recent_runs": [
            {"day": r.day, "name": r.name, "type": r.type,
             "distance_km": round(r.distance_m / 1000, 2),
             "moving_time_s": r.moving_time_s, "avg_hr": r.avg_hr,
             "elevation_m": r.elevation_m}
            for r in recent_rows if r.type in st.RUN_TYPES
        ],
        "allowed_tags": bf.TAGS,
    }


@router.get("/calendar")
async def calendar(request: Request, weeks: int = 26,
                   user: str = Depends(require_gateway)):
    weeks = max(4, min(weeks, 53))
    async with _sf(request)() as s:
        acts = await _all_activities(s)
    return {"weeks": weeks, "days": st.heatmap(acts, date.today(), weeks)}


@router.get("/weekly")
async def weekly(request: Request, weeks: int = 12,
                 user: str = Depends(require_gateway)):
    weeks = max(4, min(weeks, 53))
    async with _sf(request)() as s:
        acts = await _all_activities(s)
    return {"weeks": st.weekly(acts, date.today(), weeks)}


@router.get("/prs")
async def prs(request: Request, user: str = Depends(require_gateway)):
    async with _sf(request)() as s:
        acts = await _all_activities(s)
    return st.prs(acts, date.today())


class BriefView(BaseModel):
    week_start: str
    body: str
    highlights: list
    tags: list
    distance_km: float
    runs: int
    run_id: str | None


@router.get("/briefs", response_model=list[BriefView])
async def briefs(request: Request, limit: int = 12,
                 user: str = Depends(require_gateway)):
    async with _sf(request)() as s:
        rows = (await s.execute(select(Brief).order_by(Brief.week_start.desc())
                                .limit(min(limit, 60)))).scalars().all()
    return [{"week_start": b.week_start, "body": b.body,
             "highlights": b.highlights or [], "tags": b.tags or [],
             "distance_km": round((b.distance_m or 0) / 1000, 1),
             "runs": b.runs or 0, "run_id": b.run_id} for b in rows]


class ActivityView(BaseModel):
    id: int
    day: str
    name: str
    type: str
    distance_km: float
    moving_time_s: int
    pace: str | None
    elevation_m: float | None
    avg_hr: float | None


@router.get("/activities", response_model=list[ActivityView])
async def activities(request: Request, limit: int = 20,
                     user: str = Depends(require_gateway)):
    async with _sf(request)() as s:
        rows = (await s.execute(select(Activity).order_by(Activity.day.desc(),
                Activity.id.desc()).limit(min(limit, 100)))).scalars().all()
    out = []
    for r in rows:
        pace = None
        if r.type in st.RUN_TYPES and r.distance_m > 0 and r.moving_time_s > 0:
            pace = st.fmt_pace(r.moving_time_s / (r.distance_m / 1000))
        out.append({"id": r.id, "day": r.day, "name": r.name, "type": r.type,
                    "distance_km": round(r.distance_m / 1000, 2),
                    "moving_time_s": r.moving_time_s, "pace": pace,
                    "elevation_m": r.elevation_m, "avg_hr": r.avg_hr})
    return out
