"""The weekly-running report: deterministic report-kit HTML rendered from the
stored weekly brief, saved through the platform reports API with the app's own
key (app:running — reports/weekly-running/report.yaml names it as generator).

Deterministic on purpose: it renders the sanitized DATA the app already stored
(brief.py clamped it), so an injected note can't write markup — only text.
Only rk-*/ds-* classes survive the report-kit sanitizer; no inline styles.
"""
from __future__ import annotations

import html
import logging
import os

import httpx

from runningapp.db import Brief

log = logging.getLogger("running-report")


def _esc(s) -> str:
    return html.escape(str(s), quote=True)


def render_weekly(brief: Brief) -> tuple[str, dict]:
    km = round((brief.distance_m or 0) / 1000, 1)
    parts = [
        '<header class="rk-header">',
        f'<h1 class="rk-title">Running week — {_esc(brief.week_start)}</h1>',
        f'<p class="rk-meta">{_esc(" · ".join(brief.tags or [])) or "weekly recap"}'
        ' · by the running agent</p>',
        "</header>",
        '<div class="rk-stat-row">',
        f'<div class="rk-stat"><span class="rk-stat-value">{km:.1f} km</span>'
        '<span class="rk-stat-label">distance</span></div>',
        f'<div class="rk-stat"><span class="rk-stat-value">{brief.runs or 0}</span>'
        '<span class="rk-stat-label">runs</span></div>',
        "</div>",
    ]
    if brief.body:
        parts.append('<section class="rk-section"><h2>The week</h2>'
                     f'<p>{_esc(brief.body)}</p></section>')
    if brief.highlights:
        parts.append('<section class="rk-section"><h2>Highlights</h2><ul class="rk-list">')
        for h in brief.highlights:
            parts.append(f'<li>{_esc(h)}</li>')
        parts.append("</ul></section>")
    parts.append(f'<footer class="rk-footer">running app · weekly-running · '
                 f'{_esc(brief.week_start)}</footer>')
    return "".join(parts), {"runs": brief.runs or 0, "distance_km": km,
                            "run_id": brief.run_id}


async def write_weekly_report(sf, week_start: str) -> None:
    """Render and upsert the weekly-running report. No-op (with a log line) when
    the app key isn't provisioned yet."""
    token = os.environ.get("AP_API_TOKEN", "")
    base = os.environ.get("AP_API_URL", "")
    if not token or not base:
        log.warning("no AP_API_TOKEN/AP_API_URL — skipping weekly-running report")
        return
    async with sf() as s:
        brief = await s.get(Brief, week_start)
    if brief is None:
        return
    body, meta = render_weekly(brief)
    async with httpx.AsyncClient(base_url=base, timeout=20) as client:
        r = await client.post("/api/reports",
                              headers={"Authorization": f"Bearer {token}"}, json={
                                  "type": "weekly-running", "date": week_start,
                                  "title": f"Running week — {week_start}",
                                  "meta": meta, "html": body,
                                  "run_id": meta.pop("run_id", None)})
        r.raise_for_status()
        log.info("weekly-running report saved for %s (replaced=%s)",
                 week_start, r.json().get("replaced"))
