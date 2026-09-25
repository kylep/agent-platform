# Apps

**What:** a database-owned collection of pages and actions. An App can also
have a reviewed domain service with its own API, UI, and data
([design 33](../design/33-capabilities-plugins-and-live-apps.md)). The news app is the
reference: it consumes the news agent's digests, owns the archive + dedup
and the freshness gates (`docs/design/18-news-freshness.md` — undated,
stale, hub-URL and re-worded-repeat stories are rejected as
`app.news.item.rejected` events, never posted), posts the Discord digest,
writes the daily-news report, and serves a browser at `/apps/news/`. The **stockmarket** app is the second: it owns the price
archive, charts the indexes and your watchlist at `/apps/stockmarket/`, and
ingests the weekday market brief the same way.

Stockmarket also shows the shape an app takes when it needs *third-party*
data. App pods hold no outbound internet egress — the tool-executor is the
platform's single egress point — so the app never fetches a price. Its
`prices` tool binds the app's own DB secret and writes bars directly, the
loader agent calls that tool, and a watchlist add spends the app's operator
key on a run rather than reaching for the network itself.

**Lives in:** the App collection and versioned live pages are database rows.
`apps/<name>/` holds a domain backend/frontend and `app.yaml` infrastructure
manifest where specialized behavior exists. Domain services ship like platform
services (build image → import → enable in helm). They are deliberately
**separable from platform code**: an app service may depend
only on public contracts — the HTTP API + SDK, Kafka topics, `@ap/ui` — never
`agentplatform` internals. (Kyle intends to split workloads into their own
repo eventually; an app must survive a `git mv`.)

## The manifest (`apps/<name>/app.yaml`)

```yaml
name: news
description: Browse gathered news by calendar and topic.
icon: 🗞️
ui: true                  # serves a UI at /apps/<name>/
api: true                 # serves an API at /apps/<name>/api/
needs:
  postgres: true          # schema app_<name> + role, secret app-<name>-db
  kafka_topics:           # must be namespaced app.<name>.*
    - app.news.inbound
  redis: false            # reserved — the chart grows redis when first true
agent_key:
  role: operator          # platform key app:<name> (reader|annotator|operator)
```

## Declarative provisioning

The dispatcher's **AppProvisioner** heartbeat reconciles every declared app:
pg role + schema (creds → k8s secret `app-<name>-db`, env-ready keys like
`APP_DB_URL`), a single-owner `app:<name>` API key (→ secret
`app-<name>-key`, predecessors revoked, reminted if the secret vanishes),
and missing Kafka topics. It converges and never tears down — deleting an
app's data is a human act.

## Runtime contract

- **Deploy**: list the name in `.Values.apps.enabled`; the chart's generic
  template runs `agent-platform-app-<name>:tag` (hardened pod, the two
  secrets envFrom'd, `AP_API_URL`/`AP_KAFKA_BOOTSTRAP` injected).
- **Routing/auth**: web nginx proxies `/apps/<name>/` → the app's Service
  with `auth_request` against `GET /api/auth-check`. The app never sees
  credentials — it receives trusted `X-AP-User` / `X-AP-Role` headers (its
  API should refuse requests without them). NetworkPolicy makes nginx the
  ONLY ingress to an app pod, so the guard can't be bypassed in-cluster.
- **Agent output in**: an agent manifest's `result_topic:` feeds successful
  run results to the app's inbound topic via the recorder — the agent itself
  stays credential-free.
- **Actions out**: the app uses its `app:<name>` key against the platform
  API (save reports, trigger runs) and produces to Kafka (e.g.
  `discord.channel.post`).
- **UI**: app frontends are npm workspace members importing `@ap/ui` — the
  same tokens/primitives as the console (no-raw-hex gate scans them too).

## Registry

`GET /api/apps` + the `/apps` page: what's declared, what each app needs,
whether its Deployment is ready, and the available live pages. A legacy
manifest is imported into the collection once; subsequent collection edits
are DB-owned. The manifest still declares infrastructure needs.

## Live pages

An admin can create a page from the Apps list, edit its typed JSON draft,
preview text and controls, save, publish, and restore an earlier published
version. A draft edit has no effect on the live page until published. The
renderer supports headings, paragraphs, metrics, bounded tables and Relay
conversation cards, trusted Ticket and internal Relay actions, and a link to
that App's specialized interface. It never runs authored
HTML, CSS, JavaScript, arbitrary URLs or arbitrary Tool calls.

Running, News, Stockmarket and TCMS have bounded summary read bindings. Each
binding exposes only documented scalar fields from the existing domain
projection; page loads do not call third-party services. The TTRPG collection
has a database page showing ten recent messages from a fixed Relay room and
linking to its dedicated player/spectator interface. Membership is rechecked
on every chat read, and chat cannot be saved into a snapshot. A live page can
refresh data; eligible private snapshots are authenticated MCP Resources.

Two action contracts are admitted: `tickets.create@1` and
`relay.channel.post@1`. An admin grants an operation to a principal for an
App in the page editor's **Action access** section. The page requests a
short-lived intent, the person reviews the target
and text, and the server rechecks the grant and target at dispatch. Relay
posts require current room membership, remain in an internal unbridged
channel, and cannot contain mentions, so the action cannot silently summon
an agent or send to Discord. An idempotent receipt records the outcome.
Further Tools need a reviewed operation contract and an explicit grant before
a page may invoke them. The Apps directory opens published pages first and
keeps the detailed domain screens linked for specialized controls.

An admin can inspect the last 1–30 days of durable action receipts at
`GET /api/live-actions/observation?days=7`. It returns status counts, up to ten
unresolved receipt IDs, and the current number of revoked action grants.
Arguments, destination IDs and user content stay out of this aggregate. This
is action evidence only; page-read latency/errors still require the gateway
and API metrics/logs during a canary.
