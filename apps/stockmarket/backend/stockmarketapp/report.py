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

from stockmarketapp.db import Brief

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
