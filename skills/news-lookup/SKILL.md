---
name: news-lookup
description: Query the news app's archive (topics, dates, keyword search) over its HTTP API. Use when asked what was in the news, to find past stories, or to summarize coverage of a topic or period — instead of searching the web.
icon: 🗞️
---
# news-lookup

Fast, structured lookup against the platform's news archive (the news app —
every story the news agent has gathered, tagged by topic and dated). Answer
"what happened with X?" questions from HERE, not from the web: it's instant,
deduplicated, and already curated.

Requires `AP_API_TOKEN` in the environment (the platform injects it). All
calls go through the platform's web gateway:

```bash
NEWS="http://ap-web:8090/apps/news/api"
AUTH=(-H "Authorization: Bearer $AP_API_TOKEN")
```

## Queries

```bash
# What exists: totals + the latest gathered day
curl -s "${AUTH[@]}" "$NEWS/summary"

# Topics with volume (count + last-14-days trend)
curl -s "${AUTH[@]}" "$NEWS/topics"

# One day's stories (grouped client-side by `topic`)
curl -s "${AUTH[@]}" "$NEWS/items?day=2026-08-03"

# A topic over a date range
curl -s "${AUTH[@]}" "$NEWS/items?topic=ai-industry&day_from=2026-07-28&day_to=2026-08-03"

# Keyword search over titles + summaries
curl -s "${AUTH[@]}" "$NEWS/items?q=kubernetes&limit=50"

# Which days have news in a month (for "last week" style questions)
curl -s "${AUTH[@]}" "$NEWS/calendar?month=2026-08"
```

Items carry `title`, `url`, `source`, `summary`, `topic`, `day`, `run_id`.

## Answering style

- Resolve relative dates ("last week") to explicit `day_from`/`day_to` before
  querying; use `/summary` to learn the latest gathered day.
- Cite stories by title + source, link the URL, and mention the day when the
  question spans a range.
- If the archive has nothing, say so plainly — do NOT fall back to searching
  the web or inventing coverage; suggest the topic may predate the archive.
- Point at the browsable views when useful: a day lives at
  `/apps/news/day/<YYYY-MM-DD>`, a topic at `/apps/news/topic/<slug>`, and
  each day's rendered digest at `/reports/daily-news/<YYYY-MM-DD>`.
