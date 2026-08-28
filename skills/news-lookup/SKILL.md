---
name: news-lookup
description: Query the news app's archive (topics, dates, keyword search) with the mcp__platform__query_app tool. Use when asked what was in the news, to find past stories, or to summarize coverage of a topic or period — instead of searching the web.
icon: 🗞️
---
# news-lookup

Fast, structured lookup against the platform's news archive (the news app —
every story the news agent has gathered, tagged by topic and dated). Answer
"what happened with X?" questions from HERE, not from the web: it's instant,
deduplicated, and already curated.

You have **no shell**. Query with your `mcp__platform__query_app` tool:
`query_app(app="news", path=<endpoint>, params={...})`. Endpoints:

| path | params | returns |
|---|---|---|
| `summary` | — | totals + `latest_day` (start here for relative dates) |
| `topics` | — | topics with counts + 14-day trend |
| `items` | `day` \| `topic` \| `q` \| `day_from`+`day_to` \| `limit`/`offset` | stories: title, url, source, summary, topic, day, published (the story's own date; null on rows archived before the freshness gates) |
| `calendar` | `month` = `YYYY-MM` | per-day volume for a month |

Examples:

- One day: `query_app(app="news", path="items", params={"day": "2026-08-06"})`
- A topic over a range: `query_app(app="news", path="items", params={"topic": "ai-industry", "day_from": "2026-07-30", "day_to": "2026-08-06"})`
- Keyword search: `query_app(app="news", path="items", params={"q": "kubernetes", "limit": 50})`

## Answering style

- Resolve relative dates ("last week") to explicit `day_from`/`day_to` before
  querying; use `summary` to learn the latest gathered day.
- Cite stories by title + source, link the URL, and mention the day when the
  question spans a range.
- If the archive has nothing, say so plainly — do NOT fall back to inventing
  coverage; suggest the topic may predate the archive.
- Point at the browsable views when useful: a day lives at
  `/apps/news/day/<YYYY-MM-DD>`, a topic at `/apps/news/topic/<slug>`, and
  each day's rendered digest at `/reports/daily-news/<YYYY-MM-DD>`.
