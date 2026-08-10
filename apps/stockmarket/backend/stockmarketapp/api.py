"""The browse API, served under /apps/stockmarket/api/.

Auth: nginx's auth_request has already vetted the session/key and stamps
X-AP-User/X-AP-Role; we require the user header as a defense-in-depth marker
that the request came through the guarded route. Watchlists are keyed on that
header, so identity is not optional here — it is the primary key.

Reads are open to any authenticated identity (including agents, which reach
this through the platform's query_app proxy as `reader`). Watchlist mutations
require better than `reader`, so a compromised agent cannot enqueue backfill
runs.
"""
from __future__ import annotations

import logging
import os
from datetime import date, timedelta

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import delete, func, select

from stockmarketapp.brief import SYMBOL_RE, TAGS
from stockmarketapp.db import Bar, Brief, Symbol, Watch

log = logging.getLogger("stockmarket-api")
router = APIRouter(prefix="/apps/stockmarket/api")

# Range → how far back from the anchor day. Google's selector minus 1D: the
# archive holds daily bars only, so an intraday range would be a single point
# pretending to be a line. The latest session's move is served separately, as
# a number, by /summary.
RANGES: dict[str, str] = {
    "5D": "days:7",       # 7 calendar days ≈ the last 5 sessions
    "1M": "days:31",
    "6M": "days:183",
    "YTD": "ytd",
    "1Y": "days:366",
    "5Y": "days:1827",
}
# Points per series, after which the response strides instead of growing. A
# 900px chart cannot show 1,250 daily closes distinctly, and a fifteen-symbol
# watchlist at 5Y would otherwise ship a third of a megabyte.
MAX_POINTS = 400
MAX_WATCHLIST = 20


def require_gateway(x_ap_user: str = Header(default="")) -> str:
    if not x_ap_user:
        raise HTTPException(401, "missing gateway identity")
    return x_ap_user


def require_writer(x_ap_user: str = Header(default=""),
                   x_ap_role: str = Header(default="")) -> str:
    if not x_ap_user:
        raise HTTPException(401, "missing gateway identity")
    if x_ap_role.lower() in ("", "reader"):
        raise HTTPException(403, "watchlist changes need more than read access")
    return x_ap_user


def _sf(request: Request):
    return request.app.state.sf


class SymbolView(BaseModel):
    symbol: str
    label: str
    kind: str
    status: str
    error: str
    latest_day: str | None
    latest_close: float | None
    change_pct: float | None      # the latest session's move


class SeriesView(BaseModel):
    symbol: str
    points: list[tuple[str, float]]     # (day, close), oldest first
    downsampled: bool


class BriefView(BaseModel):
    day: str
    body: str
    tags: list
    indexes: list
    movers: list
    run_id: str | None


class Summary(BaseModel):
    indexes: list[SymbolView]
    watchlist: list[SymbolView]
    latest_day: str | None
    latest_brief_day: str | None
    tags: list[str]


class WatchIn(BaseModel):
    symbol: str


def window_start(anchor: str, rng: str) -> str:
    """First day to include, given the anchor (latest session in the archive).

    Anchoring on the archive rather than on today keeps the chart stable on a
    weekend: "the last five days" of a market that has been shut since Friday
    means five sessions, not two.
    """
    spec = RANGES[rng]
    anchor_date = date.fromisoformat(anchor)
    if spec == "ytd":
        return date(anchor_date.year, 1, 1).isoformat()
    return (anchor_date - timedelta(days=int(spec.split(":")[1]))).isoformat()


def stride(points: list, cap: int = MAX_POINTS) -> tuple[list, bool]:
    """Thin a series to at most `cap` points, always keeping the last one —
    the newest close is the number people actually read off the chart."""
    if len(points) <= cap:
        return points, False
    step = len(points) // cap + 1
    kept = points[::step]
    if kept[-1] != points[-1]:
        kept.append(points[-1])
    return kept, True


async def _latest_per_symbol(s, symbols: list[str]) -> dict[str, tuple]:
    """symbol → (day, close, change_pct) for the most recent session, computed
    from the last two bars we hold."""
    if not symbols:
        return {}
    rows = (await s.execute(
        select(Bar.symbol, Bar.day, Bar.close)
        .where(Bar.symbol.in_(symbols))
        .order_by(Bar.symbol, Bar.day.desc()))).all()
    seen: dict[str, list] = {}
    for sym, day, close in rows:
        bucket = seen.setdefault(sym, [])
        if len(bucket) < 2:
            bucket.append((day, close))
    out = {}
    for sym, bars in seen.items():
        (day, close) = bars[0]
        change = None
        if len(bars) == 2 and bars[1][1]:
            change = round((close / bars[1][1] - 1) * 100, 2)
        out[sym] = (day, close, change)
    return out


def _symbol_view(row: Symbol, latest: dict) -> dict:
    day, close, change = latest.get(row.symbol, (None, None, None))
    return {"symbol": row.symbol, "label": row.label, "kind": row.kind,
            "status": row.status, "error": row.error, "latest_day": day,
            "latest_close": close, "change_pct": change}


@router.get("/summary", response_model=Summary)
async def summary(request: Request, user: str = Depends(require_gateway)):
    """Everything the page needs before it draws: the pinned indexes, this
    user's watchlist, and where the archive currently ends."""
    async with _sf(request)() as s:
        indexes = (await s.execute(
            select(Symbol).where(Symbol.kind == "index").order_by(Symbol.symbol))).scalars().all()
        mine = (await s.execute(
            select(Symbol).join(Watch, Watch.symbol == Symbol.symbol)
            .where(Watch.user == user).order_by(Symbol.symbol))).scalars().all()
        latest = await _latest_per_symbol(
            s, [r.symbol for r in [*indexes, *mine]])
        latest_day = (await s.execute(select(func.max(Bar.day)))).scalar()
        latest_brief = (await s.execute(select(func.max(Brief.day)))).scalar()
    return {"indexes": [_symbol_view(r, latest) for r in indexes],
            "watchlist": [_symbol_view(r, latest) for r in mine],
            "latest_day": latest_day, "latest_brief_day": latest_brief,
            "tags": TAGS}


@router.get("/series", response_model=list[SeriesView],
            dependencies=[Depends(require_gateway)])
async def series(request: Request, symbols: str, range: str = "1M"):
    """Closing prices per symbol over a range. Absolute closes, not percent
    changes — the client normalizes for the overlay (three indexes priced
    $38 to $560 cannot share a linear axis) but wants the real number for
    tooltips."""
    rng = range.upper()
    if rng not in RANGES:
        raise HTTPException(422, f"range must be one of {', '.join(RANGES)}")
    wanted = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    wanted = [s for s in dict.fromkeys(wanted) if SYMBOL_RE.match(s)][:25]
    if not wanted:
        return []
    async with _sf(request)() as s:
        anchor = (await s.execute(
            select(func.max(Bar.day)).where(Bar.symbol.in_(wanted)))).scalar()
        if not anchor:
            return []
        start = window_start(anchor, rng)
        rows = (await s.execute(
            select(Bar.symbol, Bar.day, Bar.close)
            .where(Bar.symbol.in_(wanted), Bar.day >= start)
            .order_by(Bar.symbol, Bar.day))).all()
    per: dict[str, list] = {s: [] for s in wanted}
    for sym, day, close in rows:
        per[sym].append((day, close))
    out = []
    for sym in wanted:
        points, thinned = stride(per[sym])
        out.append({"symbol": sym, "points": points, "downsampled": thinned})
    return out


@router.get("/briefs", response_model=list[BriefView],
            dependencies=[Depends(require_gateway)])
async def briefs(request: Request, day: str | None = None, tag: str | None = None,
                 limit: int = 30):
    stmt = select(Brief).order_by(Brief.day.desc()).limit(min(limit, 200))
    if day:
        stmt = select(Brief).where(Brief.day == day)
    async with _sf(request)() as s:
        rows = (await s.execute(stmt)).scalars().all()
    out = [{"day": b.day, "body": b.body, "tags": b.tags or [],
            "indexes": b.indexes or [], "movers": b.movers or [],
            "run_id": b.run_id} for b in rows]
    if tag:
        out = [b for b in out if tag in b["tags"]]
    return out


async def request_backfill(symbol: str) -> None:
    """Ask the loader agent to backfill a newly watchlisted ticker.

    The app cannot fetch prices itself — app pods hold no third-party egress —
    so it spends its operator key on a run instead. Best-effort by design: if
    the API is unreachable the symbol simply stays `pending` and the next
    weekday sync picks it up, because that sync backfills anything never
    loaded. A watchlist add must not fail because a run could not start.
    """
    token = os.environ.get("AP_API_TOKEN", "")
    base = os.environ.get("AP_API_URL", "")
    if not token or not base:
        log.warning("no AP_API_TOKEN/AP_API_URL — %s waits for the daily sync",
                    symbol)
        return
    try:
        async with httpx.AsyncClient(base_url=base, timeout=20) as client:
            r = await client.post("/api/runs",
                                  headers={"Authorization": f"Bearer {token}"},
                                  json={"agent": "stockmarket-data",
                                        "prompt": f"Backfill {symbol} with five "
                                                  f"years of daily bars."})
            r.raise_for_status()
            log.info("backfill run %s requested for %s", r.json().get("id"), symbol)
    except Exception:
        log.exception("backfill request failed for %s; the daily sync will "
                      "cover it", symbol)


@router.post("/watchlist", response_model=SymbolView, status_code=201)
async def add_watch(request: Request, body: WatchIn,
                    user: str = Depends(require_writer)):
    symbol = body.symbol.strip().upper()
    if not SYMBOL_RE.match(symbol):
        raise HTTPException(422, "not a ticker (Yahoo conventions; TSX ends in .TO)")
    async with _sf(request)() as s:
        n = (await s.execute(select(func.count()).select_from(Watch)
                             .where(Watch.user == user))).scalar()
        if n >= MAX_WATCHLIST:
            raise HTTPException(409, f"watchlist is full ({MAX_WATCHLIST} symbols)")
        row = await s.get(Symbol, symbol)
        fresh = row is None
        if fresh:
            # Optimistic: the app can't validate a ticker without egress, so
            # it records the intent and lets the tool be the judge. A bad
            # ticker comes back `invalid` with Yahoo's own reason.
            row = Symbol(symbol=symbol, label="", kind="watch", status="pending")
            s.add(row)
        already = (await s.execute(select(Watch).where(
            Watch.user == user, Watch.symbol == symbol))).scalar_one_or_none()
        if already is None:
            s.add(Watch(user=user, symbol=symbol))
        await s.commit()
        latest = await _latest_per_symbol(s, [symbol])
        view = _symbol_view(row, latest)
    if fresh:
        await request_backfill(symbol)
    return view


@router.delete("/watchlist/{symbol}", status_code=204)
async def remove_watch(request: Request, symbol: str,
                       user: str = Depends(require_writer)):
    """Drop it from this user's list. The symbol row and its bars stay: some
    other user may watch it, and re-adding it should be instant rather than
    another five-year backfill."""
    async with _sf(request)() as s:
        await s.execute(delete(Watch).where(Watch.user == user,
                                            Watch.symbol == symbol.strip().upper()))
        await s.commit()
