"""The daily-market report: deterministic report-kit HTML rendered from the
stored brief and saved through the platform's reports API with the app's own
key (app:stockmarket — reports/daily-market/report.yaml names it as generator).

Deterministic on purpose: it renders the sanitized DATA the app already stored
(the agent's brief, already clamped by brief.py), so an injected brief can't
write markup — only rows, which render as text. No inline styles: the
report-kit sanitizer keeps only rk-*/ds-* classes, and a leading +/- sign
carries direction without color.
"""
from __future__ import annotations

import html
import logging
import os

import httpx
from sqlalchemy import select

from stockmarketapp.backtests import thin
from stockmarketapp.db import (BacktestEvent, BacktestExperiment,
                               BacktestResult, BacktestSeries, Brief)

log = logging.getLogger("stockmarket-report")


def _esc(s: str) -> str:
    return html.escape(str(s), quote=True)


def _pct(v) -> str:
    if v is None:
        return "—"
    return f"{'+' if v > 0 else ''}{v:.2f}%"


def render_daily_market(brief: Brief) -> tuple[str, dict]:
    """(html fragment, meta) for one session's report. The unified `body` is the
    headline summary; per-index notes and movers give the breakdown."""
    indexes = brief.indexes or []
    movers = brief.movers or []
    tags = brief.tags or []

    parts = [
        '<header class="rk-header">',
        f'<h1 class="rk-title">Market brief — {_esc(brief.day)}</h1>',
        f'<p class="rk-meta">{_esc(" · ".join(tags)) or "market brief"}'
        ' · by the stockmarket agent</p>',
        "</header>",
    ]

    # The three indexes as stat tiles — the at-a-glance line.
    if indexes:
        parts.append('<div class="rk-stat-row">')
        for i in indexes:
            parts.append(
                '<div class="rk-stat">'
                f'<span class="rk-stat-value">{_esc(_pct(i.get("return_pct")))}</span>'
                f'<span class="rk-stat-label">{_esc(i.get("symbol", ""))}</span>'
                "</div>")
        parts.append("</div>")

    # The unified summary — the thing that also goes to #news.
    if brief.body:
        parts.append('<section class="rk-section"><h2>Summary</h2>'
                     f'<p>{_esc(brief.body)}</p></section>')

    # One row per index — its own move and its own driver.
    if any(i.get("note") for i in indexes):
        parts.append('<section class="rk-section"><h2>By index</h2>')
        for i in indexes:
            parts.append(
                '<div class="rk-item">'
                f'<span class="rk-item-title">{_esc(i.get("symbol", ""))} '
                f'{_esc(_pct(i.get("return_pct")))}</span>'
                + (f'<p class="rk-item-sum">{_esc(i["note"])}</p>' if i.get("note") else "")
                + "</div>")
        parts.append("</section>")

    # The movers that earned a mention, with contribution in basis points.
    if movers:
        parts.append('<section class="rk-section"><h2>Movers</h2>')
        for m in movers:
            bps = m.get("contrib_bps")
            head = _esc(m.get("symbol", ""))
            if m.get("index"):
                head += f' in {_esc(m["index"])}'
            if bps is not None:
                head += f' · {"+" if bps > 0 else ""}{bps:.0f}bp'
            parts.append(
                '<div class="rk-item">'
                f'<span class="rk-item-title">{head}</span>'
                + (f'<p class="rk-item-sum">{_esc(m["note"])}</p>' if m.get("note") else "")
                + "</div>")
        parts.append("</section>")

    parts.append(f'<footer class="rk-footer">stockmarket app · daily-market · '
                 f'{_esc(brief.day)}</footer>')
    return "".join(parts), {"indexes": len(indexes), "movers": len(movers),
                            "run_id": brief.run_id}


async def write_daily_market_report(sf, day: str) -> None:
    """Render and upsert the daily-market report via the platform API. No-op
    (with a log line) when the app key isn't provisioned yet."""
    token = os.environ.get("AP_API_TOKEN", "")
    base = os.environ.get("AP_API_URL", "")
    if not token or not base:
        log.warning("no AP_API_TOKEN/AP_API_URL — skipping daily-market report")
        return
    async with sf() as s:
        brief = await s.get(Brief, day)
    if brief is None:
        return
    body, meta = render_daily_market(brief)
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(base_url=base, timeout=20) as client:
        r = await client.post("/api/reports", headers=headers, json={
            "type": "daily-market", "date": day,
            "title": f"Market brief — {day}", "meta": meta, "html": body,
            "run_id": meta.pop("run_id", None)})
        r.raise_for_status()
        log.info("daily-market report saved for %s (replaced=%s)",
                 day, r.json().get("replaced"))


# --- backtest report (docs/design/35) ----------------------------------------

_MAX_SLOT_ATTEMPTS = 200   # bounded: a stuck slot search must not spin forever
_PICK_EVENT_LIMIT = 200    # bounds the timeline table's contribution to the 2MB cap


def _fmt_money(v) -> str:
    if v is None:
        return "—"
    return f"{float(v):,.2f}"


def _fmt_pct(v) -> str:
    if v is None:
        return "—"
    return f"{float(v) * 100:.2f}%"


async def _chart(client: httpx.AsyncClient, headers: dict, kind: str, series: list,
                 labels: list, title: str, height: int = 220) -> str:
    try:
        r = await client.post("/api/report-kit/chart", headers=headers, json={
            "kind": kind, "series": series, "labels": labels, "title": title,
            "width": 640, "height": height})
        r.raise_for_status()
        return r.json()["svg"]
    except Exception:
        # A chart-rendering hiccup should not sink the whole report — the
        # metrics table still carries the numbers.
        return ""


async def render_backtest(sf, experiment_id: str, client: httpx.AsyncClient,
                          headers: dict) -> tuple[str, dict] | None:
    """(html fragment, meta) for one experiment, or None if it vanished
    between the notice and the render. `meta.experiment_id` is how a later
    render for the same day tells "my slot" from "someone else's"."""
    async with sf() as s:
        exp = await s.get(BacktestExperiment, experiment_id)
        if exp is None:
            return None
        results = (await s.execute(
            select(BacktestResult).where(BacktestResult.experiment_id == experiment_id)
            .order_by(BacktestResult.strategy_id))).scalars().all()
        series_rows = (await s.execute(
            select(BacktestSeries.strategy_id, BacktestSeries.day, BacktestSeries.value,
                  BacktestSeries.contributed)
            .where(BacktestSeries.experiment_id == experiment_id)
            .order_by(BacktestSeries.strategy_id, BacktestSeries.day))).all()
        events = (await s.execute(
            select(BacktestEvent).where(
                BacktestEvent.experiment_id == experiment_id,
                BacktestEvent.kind.in_(("buy", "sell")))
            .order_by(BacktestEvent.day).limit(_PICK_EVENT_LIMIT))).scalars().all()

    by_strategy: dict[str, list] = {}
    for sid, day, value, contributed in series_rows:
        by_strategy.setdefault(sid, []).append((day, value, contributed))

    parts = [
        '<header class="rk-header">',
        f'<h1 class="rk-title">{_esc(exp.name or exp.id)}</h1>',
        f'<p class="rk-meta">backtest · {_esc(exp.id)} · dataset '
        f'{_esc((exp.dataset_sha or "")[:12])}…</p>',
        "</header>",
        f'<section class="rk-section"><p>{_esc(exp.description)}</p></section>',
    ]

    if results:
        parts.append('<section class="rk-section"><h2>Results</h2>'
                     "<table><thead><tr><th>Strategy</th><th>Final value</th>"
                     "<th>Contributed</th><th>XIRR</th><th>TWR (ann.)</th>"
                     "<th>Max drawdown</th><th>Trades</th></tr></thead><tbody>")
        for r in results:
            m = r.metrics or {}
            dd = m.get("max_drawdown") or {}
            parts.append(
                "<tr>"
                f"<td>{_esc(r.label or r.strategy_id)}</td>"
                f"<td>{_esc(_fmt_money(m.get('final_value')))}</td>"
                f"<td>{_esc(_fmt_money(m.get('contributed')))}</td>"
                f"<td>{_esc(_fmt_pct(m.get('xirr')))}</td>"
                f"<td>{_esc(_fmt_pct(m.get('twr_annualized')))}</td>"
                f"<td>{_esc(_fmt_pct(dd.get('depth')))}</td>"
                f"<td>{_esc(str(m.get('trades', '—')))}</td>"
                "</tr>")
        parts.append("</tbody></table></section>")

    # One value-vs-contributed + drawdown pair per strategy. Drawdown isn't
    # stored (only value/contributed are), so it's derived here from the
    # thinned series' own running peak — cheap, and exact enough for a chart.
    for r in results:
        pts = thin(by_strategy.get(r.strategy_id, []))
        if not pts:
            continue
        days = [p[0] for p in pts]
        values = [float(p[1]) for p in pts]
        contributed = [float(p[2]) for p in pts]
        value_svg = await _chart(
            client, headers, "line",
            [{"label": "Value", "values": values},
             {"label": "Contributed", "values": contributed}],
            days, f"{r.label or r.strategy_id}: value vs contributed")
        peak = float("-inf")
        drawdown = []
        for v in values:
            peak = max(peak, v)
            drawdown.append((v - peak) / peak * 100 if peak > 0 else 0.0)
        drawdown_svg = await _chart(
            client, headers, "line", [{"label": "Drawdown %", "values": drawdown}],
            days, f"{r.label or r.strategy_id}: drawdown")
        if value_svg or drawdown_svg:
            parts.append(f'<section class="rk-section"><h2>{_esc(r.label or r.strategy_id)}</h2>')
            parts.append(value_svg)
            parts.append(drawdown_svg)
            parts.append("</section>")

    if events:
        parts.append('<section class="rk-section"><h2>Pick timeline</h2>'
                     "<table><thead><tr><th>Day</th><th>Strategy</th>"
                     "<th>Action</th><th>Symbol</th></tr></thead><tbody>")
        for e in events:
            parts.append(
                "<tr>"
                f"<td>{_esc(e.day)}</td><td>{_esc(e.strategy_id)}</td>"
                f"<td>{_esc(e.kind)}</td><td>{_esc(e.symbol or '')}</td>"
                "</tr>")
        parts.append("</tbody></table></section>")

    if exp.caveats:
        parts.append('<section class="rk-section"><h2>Caveats</h2>')
        for c in exp.caveats:
            text = c.get("text", "") if isinstance(c, dict) else str(c)
            parts.append(f'<div class="rk-callout rk-callout-warning">{_esc(text)}</div>')
        parts.append("</section>")

    if exp.exclusions:
        parts.append(
            '<section class="rk-section"><h2>Exclusions</h2>'
            f"<p>{len(exp.exclusions)} exclusion(s) recorded across the run — "
            "see the experiment page for the full list.</p></section>")

    parts.append(
        f'<footer class="rk-footer"><a href="/apps/stockmarket/#/backtests/{_esc(exp.id)}">'
        "Open the experiment</a></footer>")
    return "".join(parts), {"experiment_id": exp.id}


def _next_slot(t: str) -> str:
    h, m = int(t[:2]), int(t[3:5])
    m += 1
    if m == 60:
        m, h = 0, (h + 1) % 24
    return f"{h:02d}-{m:02d}"


async def _pick_slot(client: httpx.AsyncClient, headers: dict, day: str,
                     experiment_id: str) -> str | None:
    """The first HH-MM slot on `day` that is free, or already this
    experiment's own slot (a re-delivered notice re-renders in place rather
    than drifting to a new minute every time). Bounded: a runaway day full
    of experiments gives up rather than looping forever."""
    r = await client.get("/api/reports", headers=headers, params={
        "type": "backtest", "date_from": day, "date_to": day, "limit": 1000})
    r.raise_for_status()
    taken = {(row.get("time") or "00-00"): (row.get("meta") or {}).get("experiment_id")
            for row in r.json()}
    t = "00-00"
    for _ in range(_MAX_SLOT_ATTEMPTS):
        owner = taken.get(t)
        if owner is None or owner == experiment_id:
            return t
        t = _next_slot(t)
    return None


async def write_backtest_report(sf, experiment_id: str) -> None:
    """Render and upsert the backtest report, then record `report_id` on the
    experiment row — the one field the app writes onto a table the tool
    owns. Best-effort like the daily-market report: an API hiccup here must
    not poison the ingest loop or the tool's already-durable rows."""
    token = os.environ.get("AP_API_TOKEN", "")
    base = os.environ.get("AP_API_URL", "")
    if not token or not base:
        log.warning("no AP_API_TOKEN/AP_API_URL — skipping backtest report for %s",
                   experiment_id)
        return
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(base_url=base, timeout=20) as client:
        rendered = await render_backtest(sf, experiment_id, client, headers)
        if rendered is None:
            log.warning("experiment %s vanished before its report could render",
                       experiment_id)
            return
        body, meta = rendered
        async with sf() as s:
            exp = await s.get(BacktestExperiment, experiment_id)
        day = exp.created_at.date().isoformat()
        slot = await _pick_slot(client, headers, day, experiment_id)
        if slot is None:
            log.error("no free backtest report slot on %s for %s after %d minutes",
                     day, experiment_id, _MAX_SLOT_ATTEMPTS)
            return
        r = await client.post("/api/reports", headers=headers, json={
            "type": "backtest", "date": day, "time": slot,
            "title": exp.name or experiment_id, "meta": meta, "html": body,
            "run_id": exp.run_id})
        r.raise_for_status()
        report_id = r.json().get("id")
    if report_id:
        async with sf() as s:
            row = await s.get(BacktestExperiment, experiment_id)
            if row is not None:
                row.report_id = report_id
                await s.commit()
    log.info("backtest report saved for %s (slot %s, replaced=%s)",
             experiment_id, slot, bool(report_id))
