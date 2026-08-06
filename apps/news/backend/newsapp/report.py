"""The daily-news report: deterministic report-kit HTML rendered from the
archive and saved through the platform's reports API with the app's own key
(app:news — reports/daily-news/report.yaml names it as generator).

Deterministic on purpose: the report renders sanitized DATA the app already
holds, so an injected digest can't write markup — only rows, which render as
text. Charts come from the platform's server-side SVG endpoint."""
from __future__ import annotations

import html
import logging
import os
from datetime import date as date_cls, timedelta

import httpx
from sqlalchemy import func, select

from newsapp.db import Item, Topic

log = logging.getLogger("news-report")


def _esc(s: str) -> str:
    return html.escape(str(s), quote=True)


async def _sparkline(client: httpx.AsyncClient, headers: dict, counts: list[int]) -> str:
    try:
        r = await client.post("/api/report-kit/chart", headers=headers, json={
            "kind": "sparkline", "series": [{"values": counts or [0]}],
            "width": 140, "height": 32})
        r.raise_for_status()
        return r.json()["svg"]
    except Exception:
        return ""


async def render_daily(sf, day: str, client: httpx.AsyncClient, headers: dict) -> tuple[str, dict]:
    """(html fragment, meta) for one day's report."""
    async with sf() as s:
        rows = (await s.execute(
            select(Item, Topic).join(Topic, Item.topic_id == Topic.id)
            .where(Item.day == day).order_by(Topic.slug, Item.ingested_at))).all()
        gather_run = (await s.execute(
            select(Item.run_id).where(Item.day == day, Item.run_id.isnot(None))
            .order_by(Item.ingested_at.desc()).limit(1))).scalar()
        start = (date_cls.fromisoformat(day) - timedelta(days=13)).isoformat()
        trend = dict((await s.execute(
            select(Item.day, func.count()).where(Item.day >= start, Item.day <= day)
            .group_by(Item.day))).all())
    days = [(date_cls.fromisoformat(day) - timedelta(days=13 - i)).isoformat()
            for i in range(14)]
    counts = [int(trend.get(d, 0)) for d in days]
    spark = await _sparkline(client, headers, counts)

    by_topic: dict[str, list] = {}
    labels: dict[str, str] = {}
    for item, topic in rows:
        by_topic.setdefault(topic.slug, []).append(item)
        labels[topic.slug] = topic.label
    parts = [
        '<header class="rk-header">',
        f'<h1 class="rk-title">Daily news — {_esc(day)}</h1>',
        f'<p class="rk-meta">{len(rows)} items · {len(by_topic)} topics · gathered by the news agent</p>',
        "</header>",
        '<div class="rk-stat-row">',
        f'<div class="rk-stat"><span class="rk-stat-value">{len(rows)}</span><span class="rk-stat-label">items</span></div>',
        f'<div class="rk-stat"><span class="rk-stat-value">{len(by_topic)}</span><span class="rk-stat-label">topics</span></div>',
        (f'<div class="rk-stat"><span class="rk-stat-value">{spark}</span>'
         '<span class="rk-stat-label">14-day volume</span></div>' if spark else ""),
        "</div>",
    ]
    for slug, items in by_topic.items():
        parts.append('<section class="rk-section">')
        parts.append(f'<h2>{_esc(labels[slug])} <span class="rk-chip">{len(items)} '
                     f'{"item" if len(items) == 1 else "items"}</span></h2>')
        for it in items:
            title = _esc(it.title)
            link = (f'<a href="{_esc(it.url)}">{title}</a>'
                    if it.url.startswith("https://") else title)
            parts.append(
                '<div class="rk-item">'
                f'<span class="rk-item-title">{link}</span>'
                + (f'<span class="rk-item-src">{_esc(it.source)}</span>' if it.source else "")
                + (f'<p class="rk-item-sum">{_esc(it.summary)}</p>' if it.summary else "")
                + "</div>")
        parts.append("</section>")
    parts.append(f'<footer class="rk-footer">news app · daily-news · {_esc(day)}</footer>')
    return "".join(parts), {"items": len(rows), "topics": len(by_topic),
                            "run_id": gather_run}


async def write_daily_report(sf, day: str) -> None:
    """Render and upsert the daily-news report via the platform API. No-op
    (with a log line) when the app key isn't provisioned yet."""
    token = os.environ.get("AP_API_TOKEN", "")
    base = os.environ.get("AP_API_URL", "")
    if not token or not base:
        log.warning("no AP_API_TOKEN/AP_API_URL — skipping daily report")
        return
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(base_url=base, timeout=20) as client:
        body, meta = await render_daily(sf, day, client, headers)
        r = await client.post("/api/reports", headers=headers, json={
            "type": "daily-news", "date": day,
            "title": f"Daily news — {day}", "meta": meta, "html": body,
            "run_id": meta.pop("run_id", None)})
        r.raise_for_status()
        log.info("daily-news report saved for %s (replaced=%s)",
                 day, r.json().get("replaced"))
