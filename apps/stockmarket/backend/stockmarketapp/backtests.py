"""Backtest experiments: notice handling and the read-API queries the app
answers over rows the `backtest` TOOL already committed (docs/design/35).

The tool writes the five backtest_* tables in one transaction, before it
publishes `backtest.completed` — this module never writes an experiment's
spec, results, series or events, only `report_id` (in report.py) once the
app has rendered its own report from those rows. Everything returned here
is bounded: the design caps `/backtests/{id}` at 8 KB, so the unbounded
per-day pick timeline never leaves `metrics`, the raw spec never leaves it
either, and caveats/assumed are truncated — the full versions are read
through `/backtests/{id}/events`, `/backtests/{id}/spec` and
`/backtests/{id}/metrics` instead.
"""
from __future__ import annotations

import json
import re

from sqlalchemy import select

from stockmarketapp.db import (BacktestEvent, BacktestExperiment,
                               BacktestResult, BacktestSeries)

EXPERIMENT_ID_RE = re.compile(r"^[0-9a-f]{32}$")
EVENTS_PAGE_SIZE = 100

# `/backtests/{id}` (docs/design/35) must stay agent-context-sized regardless
# of the spec's worst case (8 strategies, 25-symbol universes, long labels
# and a caveat per concentrated strategy): measured at 20.5 KB uncapped.
DETAIL_CAP = 8192
CAVEAT_CHARS = 160        # matches tools/backtest/run.py's own summary cap
ASSUMED_VALUE_CHARS = 120

# Metric keys T5 fills that are unbounded in size (one entry per rebalance
# day, per symbol, for the life of the run) — never returned by the read
# API; the same information is available in full from /events.
_UNBOUNDED_METRIC_KEYS = ("pick_timeline",)

# The per-strategy subset `/backtests/{id}` carries — mirrors
# tools/backtest/run.py's SUMMARY_METRICS (the same numbers the tool's own
# summary reports), plus `profit`. Full metrics live at .../metrics.
DETAIL_METRIC_KEYS = ("final_value", "contributed", "profit", "xirr", "twr_annualized",
                      "max_drawdown_depth", "volatility", "sharpe", "trades", "costs")


def like_literal(q: str) -> str:
    """`q` as a literal LIKE pattern: the wildcards and the escape character
    itself are escaped, so a name containing `%` or `_` cannot widen the
    match into a scan."""
    return q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def slim_metrics(metrics: dict) -> dict:
    return {k: v for k, v in (metrics or {}).items() if k not in _UNBOUNDED_METRIC_KEYS}


def _short(text: str, n: int) -> str:
    return text if len(text) <= n else text[:max(1, n - 1)] + "…"


def _headline_metrics(metrics: dict) -> dict:
    """Flatten `max_drawdown.depth` into `max_drawdown_depth` (the engine
    nests it; the tool's own headline/summary keys are flat) and keep only
    DETAIL_METRIC_KEYS — bounded per strategy regardless of how many other
    keys T5 adds."""
    m = dict(metrics or {})
    m["max_drawdown_depth"] = (m.get("max_drawdown") or {}).get("depth")
    return {k: m.get(k) for k in DETAIL_METRIC_KEYS}


def _bounded_caveats(caveats: list) -> list:
    return [{"code": c.get("code"), "text": _short(str(c.get("text", "")), CAVEAT_CHARS)}
           for c in (caveats or []) if isinstance(c, dict)]


def _bounded_assumed(assumed: list) -> list:
    out = []
    for a in (assumed or []):
        if not isinstance(a, dict):
            continue
        value = a.get("value")
        if isinstance(value, str):
            value = _short(value, ASSUMED_VALUE_CHARS)
        elif not isinstance(value, (int, float, bool)) and value is not None:
            value = _short(json.dumps(value, default=str), ASSUMED_VALUE_CHARS)
        out.append({"path": a.get("path"), "value": value})
    return out


def _size(out: dict) -> int:
    return len(json.dumps(out, default=str, separators=(",", ":")).encode())


def _fit_detail_cap(out: dict) -> dict:
    """Cascading trim, same idea as tools/backtest/run.py's `summary()`: the
    initial per-field caps handle the ordinary case, but nothing bounds their
    *count* (strategies, caveats, assumed entries), so this is the actual
    guarantee. Cheapest-to-lose information goes first; never raises, never
    drops a strategy or a number, only shortens text."""
    if _size(out) <= DETAIL_CAP:
        return out
    for c in out["caveats"]:
        c["text"] = _short(c["text"], 60)
    if _size(out) <= DETAIL_CAP:
        return out
    out["caveats"] = sorted({c["code"] for c in out["caveats"]})
    if _size(out) <= DETAIL_CAP:
        return out
    out["assumed"] = [a["path"] for a in out["assumed"]]
    if _size(out) <= DETAIL_CAP:
        return out
    for s in out["strategies"]:
        s["label"] = _short(s["label"], 24)
    return out


def thin(points: list, cap: int = 500) -> list:
    """Downsample a (day, value, contributed) series to at most `cap` points,
    always keeping the last one, mirroring api.stride for chart-sized series."""
    if len(points) <= cap:
        return points
    step = len(points) // cap + 1
    kept = list(points[::step])
    if kept[-1] != points[-1]:
        kept.append(points[-1])
    return kept


def monthly(points: list) -> list:
    """One point per calendar month — the last day on record for that
    month — so a 30-year daily series reads as ~360 points for an agent
    instead of ~7,500."""
    out: list = []
    for row in points:
        month = row[0][:7]
        if out and out[-1][0][:7] == month:
            out[-1] = row
        else:
            out.append(row)
    return out


async def ingest_notice(sf, data: dict) -> str | None:
    """Validate a `backtest.completed` notice and confirm the experiment it
    names is actually in the tables. The tool commits before it publishes,
    so this should always be true; a malformed id, or one from a replayed
    notice after the row was since purged, is logged by the caller and
    skipped here — never raised, since the tool's rows are already durable
    regardless of what this consumer does with them."""
    experiment_id = data.get("experiment_id") if isinstance(data, dict) else None
    if not isinstance(experiment_id, str) or not EXPERIMENT_ID_RE.match(experiment_id):
        return None
    async with sf() as s:
        exists = await s.get(BacktestExperiment, experiment_id)
    return experiment_id if exists is not None else None


async def list_experiments(sf, q: str | None, limit: int) -> list[dict]:
    stmt = select(BacktestExperiment).order_by(BacktestExperiment.created_at.desc())
    if q:
        stmt = stmt.where(BacktestExperiment.name.ilike(
            f"%{like_literal(q)}%", escape="\\"))
    stmt = stmt.limit(max(1, min(limit, 100)))
    async with sf() as s:
        rows = (await s.execute(stmt)).scalars().all()
        ids = [r.id for r in rows]
        results = [] if not ids else (await s.execute(
            select(BacktestResult).where(BacktestResult.experiment_id.in_(ids)))
        ).scalars().all()
    by_exp: dict[str, list] = {}
    for r in results:
        m = r.metrics or {}
        by_exp.setdefault(r.experiment_id, []).append(
            {"id": r.strategy_id, "label": r.label,
             "final_value": m.get("final_value"), "xirr": m.get("xirr")})
    return [{"id": e.id, "name": e.name, "created_at": e.created_at.isoformat(),
            "strategies": by_exp.get(e.id, [])} for e in rows]


async def get_detail(sf, experiment_id: str) -> dict | None:
    """The <= 8 KB agent-facing summary: description, assumed (truncated),
    per-strategy headline metrics, caveats (truncated), and an exclusions
    summary — a count plus a bounded sample of symbols, never the full list,
    however many thousand days a wide universe excludes. NEVER the raw spec
    (/backtests/{id}/spec) or the full metrics dict (.../metrics) — at the
    spec's worst case (8 strategies, ~19 caveats) those alone measured over
    20 KB uncapped."""
    async with sf() as s:
        exp = await s.get(BacktestExperiment, experiment_id)
        if exp is None:
            return None
        results = (await s.execute(
            select(BacktestResult).where(BacktestResult.experiment_id == experiment_id)
            .order_by(BacktestResult.strategy_id))).scalars().all()
    exclusions = exp.exclusions or []
    symbols = sorted({x.get("symbol") for x in exclusions if isinstance(x, dict) and x.get("symbol")})
    out = {
        "id": exp.id, "name": exp.name, "description": exp.description,
        "assumed": _bounded_assumed(exp.assumed), "caveats": _bounded_caveats(exp.caveats),
        "exclusions_summary": {"count": len(exclusions), "symbols": symbols[:10]},
        "report_id": exp.report_id, "created_at": exp.created_at.isoformat(),
        "strategies": [{"id": r.strategy_id, "label": r.label,
                        "metrics": _headline_metrics(r.metrics)} for r in results],
    }
    return _fit_detail_cap(out)


async def get_metrics(sf, experiment_id: str, strategy: str | None) -> dict | None:
    """The full metrics dict (minus the unbounded pick timeline) for one
    strategy — what the bounded `/backtests/{id}` response can't afford to
    carry for every strategy at once. Raises ValueError when `strategy` is
    required (more than one strategy ran) and was left out."""
    async with sf() as s:
        exp = await s.get(BacktestExperiment, experiment_id)
        if exp is None:
            return None
        results = (await s.execute(
            select(BacktestResult).where(BacktestResult.experiment_id == experiment_id)
            .order_by(BacktestResult.strategy_id))).scalars().all()
    if strategy is None:
        if len(results) != 1:
            raise ValueError("strategy is required: this experiment ran more than one")
        row = results[0]
    else:
        row = next((r for r in results if r.strategy_id == strategy), None)
    return {"strategy_id": strategy if row is None else row.strategy_id,
            "metrics": slim_metrics(row.metrics) if row else {}}


async def get_spec(sf, experiment_id: str) -> dict | None:
    """The stored spec, verbatim — split out of `/backtests/{id}` because an
    8-strategy, 25-symbol spec is itself several KB, and the UI's spec panel
    is the only reader that needs it whole."""
    async with sf() as s:
        exp = await s.get(BacktestExperiment, experiment_id)
    if exp is None:
        return None
    return {"id": exp.id, "spec": exp.spec or {}}


async def get_events(sf, experiment_id: str, strategy: str | None,
                     page: int, page_size: int = EVENTS_PAGE_SIZE) -> dict | None:
    async with sf() as s:
        exp = await s.get(BacktestExperiment, experiment_id)
        if exp is None:
            return None
        stmt = select(BacktestEvent).where(BacktestEvent.experiment_id == experiment_id)
        if strategy:
            stmt = stmt.where(BacktestEvent.strategy_id == strategy)
        stmt = stmt.order_by(BacktestEvent.day, BacktestEvent.id)
        page = max(1, page)
        rows = (await s.execute(
            stmt.offset((page - 1) * page_size).limit(page_size + 1))).scalars().all()
    has_more = len(rows) > page_size
    rows = rows[:page_size]
    return {"page": page, "page_size": page_size, "has_more": has_more,
            "events": [{"day": r.day, "strategy_id": r.strategy_id, "kind": r.kind,
                       "symbol": r.symbol, "detail": r.detail} for r in rows]}


async def get_series(sf, experiment_id: str, strategy: str | None,
                     sample: str = "monthly") -> dict | None:
    """Raises ValueError when `strategy` is required (more than one strategy
    ran) and was left out — the caller (api.py) turns that into a 422."""
    async with sf() as s:
        exp = await s.get(BacktestExperiment, experiment_id)
        if exp is None:
            return None
        if strategy is None:
            only = (await s.execute(
                select(BacktestResult.strategy_id)
                .where(BacktestResult.experiment_id == experiment_id))).scalars().all()
            if len(only) != 1:
                raise ValueError(
                    "strategy is required: this experiment ran more than one")
            strategy = only[0]
        rows = (await s.execute(
            select(BacktestSeries.day, BacktestSeries.value, BacktestSeries.contributed)
            .where(BacktestSeries.experiment_id == experiment_id,
                   BacktestSeries.strategy_id == strategy)
            .order_by(BacktestSeries.day))).all()
    points = monthly(rows) if sample != "daily" else thin(rows)
    return {"strategy_id": strategy, "sample": sample,
            "points": [{"day": d, "value": v, "contributed": c} for d, v, c in points]}
