# 18 — News freshness (why the digest kept posting old news, and the gates)

Status: **shipped 2026-08-28**.

One of the numbered design records under `docs/design/`. The series index is
`docs/design/00-overview.md`; component names are defined in
`docs/building-blocks/glossary.md`, and Kyle is the project owner.

## The problem

The morning digest (design [08](08-news-and-injection-hardening.md) →
[11](11-apps-and-reports.md)) kept re-posting stale stories as today's news:
a CloudNativePG CVE fixed on May 8 appeared on Aug 19 and 22; Claude Design
(Apr 17) was "launched" on Aug 28; Anthropic's Sept 2025 Series F was Aug 28
news; the August Patch Tuesday zero-day was posted four times in ten days and
attempted nine; the PostgreSQL minor release four times.

## The investigation (what the transcripts showed)

Six scheduled runs' transcripts, read end to end:

1. **The gatherer never reads articles.** 9–14 `WebSearch` calls per run and
   at most one `WebFetch` — the Environment Canada page. Claude Code's
   `WebSearch` returns `{title, url}` plus an LLM-written prose summary with
   **no publication dates**, and the summaries say things like "Anthropic
   just launched Claude Design" about a four-month-old page. The agent never
   sees a date for anything it reports.
2. **Month-scoped queries return roundup pages.** `"CVE actively exploited
   August 2026"`, `"data breach August 2026"`, `"Kubernetes PostgreSQL Redis
   release August 2026"` land on aggregators — `releasebot.io/updates/*`,
   "top breaches of August (so far)", `postgresql.org/support/security`,
   `cloudnative-pg.io/releases`, the Wikipedia election page — which list a
   whole month's (or all-time top) stories. The agent lifts the most
   prominent entries and stamps them today.
3. **No per-item date existed anywhere.** The contract had one digest-level
   `date` (= today, dutifully filled) and no per-item date; the app assigned
   every item `day = digest.date`. There was no point at which a stale item
   *could* be rejected, and the archive could not say afterwards which items
   had been stale.
4. **Dedup was URL-only and the agent is blind to history.** Same story,
   different URL each day (securityweek → helpnetsecurity → thehackernews →
   securityaffairs) sails through; and many cited URLs were site hubs
   (`openai.com/news/`, `anthropic.com/news`, `cp24.com`) — one story "uses
   up" a hub forever, after which anything else cited to it is silently
   dropped.

The search index itself was fine — the same result sets contained genuine
Aug 26–28 items. The failure was entirely "no date signal + queries that pull
roundups + a pipeline that trusts the agent's single date stamp".

## The decision: gate on the trusted side, instruct on the untrusted side

Consistent with design-08, the correctness controls live in deterministic
code in the news app, not in the prompt; the prompt is the second line.

### Digest contract v2

Each item gains a required `published` (`YYYY-MM-DD`) — the story's own
publication date as the gatherer verified it by fetching the page. The `url`
must be the article, not a hub.

### Freshness gates (`apps/news/backend/newsapp/{digest,ingest}.py`)

Applied per item on ingest, in this order; the first failing gate names the
reason:

| reason | rule |
|---|---|
| `duplicate-url` | canonical URL already archived (the original dedup) |
| `undated` | `published` missing or not strict ISO |
| `stale` | `published` more than `NEWS_MAX_AGE_DAYS` (default 2) before the digest date |
| `hub-url` | URL is a site section/landing page (root, or ≤2 plain-word path segments — real articles carry a slug or a date/id) or on the aggregator host list |
| `duplicate-story` | headline is the same story as one archived in the last 7 days: shares a CVE id, or ≥2 identity tokens covering ≥50% of the shorter headline (overlap coefficient — robust to one side being wordier, which Jaccard is not; English function words and headline verbs like *launches/patches/confirms* are stripped first) |

A rejection is data, not a failure: every one becomes an
`app.news.item.rejected` Kafka event (`{day, headline, url, published,
reason, run_id}`), the Discord digest carries a subtext footer
(`-# filtered: 3 stale · 1 duplicate-story`) so a thin digest is visibly the
filter's doing, and accepted rows store `published` (additive column; the
app's `init_db` now backfills missing columns the way the platform's does).
A digest with nothing surviving posts nothing but still emits its
rejections.

### Prompt (agent `news`, edited through the platform — design-15 change log)

Search **day-scoped** (today's date in the query, never `"… August 2026"`);
**fetch every candidate** and read its date; drop anything not within the
last 2 days regardless of how prominent the search result made it; article
URLs only; recognise a story covered days ago as not-news unless there is a
new development; a short verified list over a padded one. The prompt tells
the agent the platform rejects the same things, so cutting corners yields an
empty digest rather than a stale one. Timeout raised 600→900 s for the extra
fetches.

## Platform extension: `params` on the app query proxy

Verifying this through the external MCP facade (design-17) surfaced a gap:
`GET /api/apps/{name}/query/{path}` forwarded whatever loose query string
the broker sent, but an OpenAPI-derived client can only express parameters
the schema names — so the facade's `query_app` tool could not filter the
archive at all (`items?topic=security` came back as everything). The endpoint
now also accepts an explicit `params` query argument holding a JSON object of
scalar values, merged into the upstream query. The broker's shape keeps
working; the facade picks the new argument up from the spec on restart.

## Not done (deliberately)

- **Giving the gatherer a view of what has been posted** (so it could avoid
  re-finding stories) would mean handing it a token or a prompt-injection
  channel from the archive; design-08's trifecta break says no. The
  server-side story dedup plus the 2-day age gate make the blind spot cheap:
  a repeat costs a digest slot, not a stale post.
- **Semantic dedup** (embeddings) — the token-overlap rule caught every real
  repeat in the archive; revisit if it starts missing.
