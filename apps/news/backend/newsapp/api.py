"""The browse API, served under /apps/news/api/ (nginx strips nothing — the
app sees full paths). Auth: nginx's auth_request has already vetted the
session/key and stamps X-AP-User/X-AP-Role; we require the header as a
defense-in-depth marker that the request came through the guarded route."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, or_, select

from newsapp.db import Item, Topic

router = APIRouter(prefix="/apps/news/api")


def require_gateway(x_ap_user: str = Header(default="")) -> str:
    if not x_ap_user:
        raise HTTPException(401, "missing gateway identity")
    return x_ap_user


class TopicView(BaseModel):
    slug: str
    label: str
    color: int
    count: int
    spark: list[int]      # last 14 days of volume, oldest first


class ItemView(BaseModel):
    id: str
    title: str
    url: str
    source: str
    summary: str
    topic: str
    topic_label: str
    color: int
    day: str
    published: str | None
    run_id: str | None


class CalendarDay(BaseModel):
    total: int
    by_topic: dict[str, int]


class Summary(BaseModel):
    today: int
    week: int
    total: int
    topics: int
    latest_day: str | None


def _sf(request):
    return request.app.state.sf


from fastapi import Request  # noqa: E402


@router.get("/summary", response_model=Summary, dependencies=[Depends(require_gateway)])
async def summary(request: Request):
    from datetime import date, timedelta
    today = date.today().isoformat()
    week = (date.today() - timedelta(days=6)).isoformat()
    async with _sf(request)() as s:
        total = (await s.execute(select(func.count()).select_from(Item))).scalar()
        n_today = (await s.execute(select(func.count()).where(Item.day == today))).scalar()
        n_week = (await s.execute(select(func.count()).where(Item.day >= week))).scalar()
        topics = (await s.execute(select(func.count()).select_from(Topic))).scalar()
        latest = (await s.execute(select(func.max(Item.day)))).scalar()
    return {"today": n_today, "week": n_week, "total": total,
            "topics": topics, "latest_day": latest}


@router.get("/topics", response_model=list[TopicView], dependencies=[Depends(require_gateway)])
async def topics(request: Request):
    from datetime import date, timedelta
    days = [(date.today() - timedelta(days=13 - i)).isoformat() for i in range(14)]
    async with _sf(request)() as s:
        rows = (await s.execute(
            select(Topic, func.count(Item.id))
            .join(Item, Item.topic_id == Topic.id, isouter=True)
            .group_by(Topic.id).order_by(func.count(Item.id).desc()))).all()
        recent = (await s.execute(
            select(Item.topic_id, Item.day, func.count())
            .where(Item.day >= days[0]).group_by(Item.topic_id, Item.day))).all()
    per = {}
    for tid, day, n in recent:
        per.setdefault(tid, {})[day] = n
    return [{"slug": t.slug, "label": t.label, "color": t.color, "count": c,
             "spark": [int(per.get(t.id, {}).get(d, 0)) for d in days]}
            for t, c in rows]


@router.get("/calendar", response_model=dict[str, CalendarDay],
            dependencies=[Depends(require_gateway)])
async def calendar(request: Request, month: str):
    if len(month) != 7 or month[4] != "-":
        raise HTTPException(422, "month must be YYYY-MM")
    async with _sf(request)() as s:
        rows = (await s.execute(
            select(Item.day, Topic.slug, func.count())
            .join(Topic, Item.topic_id == Topic.id)
            .where(Item.day >= f"{month}-01", Item.day <= f"{month}-31")
            .group_by(Item.day, Topic.slug))).all()
    out: dict[str, dict] = {}
    for day, slug, n in rows:
        d = out.setdefault(day, {"total": 0, "by_topic": {}})
        d["total"] += n
        d["by_topic"][slug] = n
    return out


@router.get("/items", response_model=list[ItemView], dependencies=[Depends(require_gateway)])
async def items(request: Request, day: str | None = None, topic: str | None = None,
                q: str | None = None, day_from: str | None = None,
                day_to: str | None = None, limit: int = 100, offset: int = 0):
    stmt = (select(Item, Topic).join(Topic, Item.topic_id == Topic.id)
            .order_by(Item.day.desc(), Item.ingested_at.desc()))
    if day:
        stmt = stmt.where(Item.day == day)
    if day_from:
        stmt = stmt.where(Item.day >= day_from)
    if day_to:
        stmt = stmt.where(Item.day <= day_to)
    if topic:
        stmt = stmt.where(Topic.slug == topic)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Item.title.ilike(like), Item.summary.ilike(like)))
    async with _sf(request)() as s:
        rows = (await s.execute(stmt.limit(min(limit, 500)).offset(offset))).all()
    return [{"id": i.id, "title": i.title, "url": i.url, "source": i.source,
             "summary": i.summary, "topic": t.slug, "topic_label": t.label,
             "color": t.color, "day": i.day, "published": i.published,
             "run_id": i.run_id}
            for i, t in rows]
