# Apps

**What:** an App is something agents build and maintain for Kyle: structured
records, the views that query them, and the pages that show them. Apps are
**state, not code** (`docs/design/39-agent-built-apps.md`). An App is rows
in the platform's own store, built by an agent through the `apps` tool and
filled through the `app_data` tool. It has no image, schema, deploy or key,
so a fresh install has no Apps, and restoring the database brings every App
back. Code lives only in tools: connectors that bring outside data in, App
tools that compute for an App, and platform tools.

The six Apps that predate this (news, running, stockmarket, TCMS, judgment
and TTRPG) are **coded Apps, being migrated**: services with their own
schemas and images, described at the end of this page until each one is
rebuilt as state and its code is deleted.

**Lives in:** Postgres, in the `app_data_*` tables. [App data](app-data.md)
is the reference: definitions, access, records, views, batch writes,
artifacts, quotas, tools, routes and error codes.

## When an App is the answer

A one-off answer belongs in chat, durable knowledge in the [wiki](wiki.md), a
private note in [memory](memories.md), and a piece of work in a
[ticket](tickets.md). An App is for structured records used repeatedly,
read by Kyle on a page, and kept correct by an agent over time. It is either
a **report** (views and a page over records an agent writes on a schedule)
or a **tool** (records an agent changes as Kyle asks, guarded by rules).

## Who builds

- A **builder** is any agent Kyle grants the `apps` tool. It creates Apps
  it owns, drafts definitions, validates and previews them, publishes,
  rolls back and retires. Records go through `app_data`, which builders
  hold too.
- The **maintainer** is the App's owning agent. It keeps the App healthy,
  writes its records, and keeps its **build notes**: a runbook of at most 4 KB
  an agent that has never seen the App can follow.
- **Kyle** grants the tools: `apps` and `app_data` are Kyle-only grants
  that only his browser session can add or remove, and an agent holding one
  can be edited only by Kyle or by itself ([security.md](security.md)). He
  reads every App from the console: `/apps` lists them, `/apps/state/<id>`
  shows an App's definitions, drafts, notes and health, and
  `/apps/state/<id>/pages/<page>` renders its pages.

The `app-building` skill in the reviewed
[coding plugin](../agent-platform-coding-plugin.md) teaches builders the
procedure. Assign it with the two grants.

## How an App is built

1. **Inspect** existing Apps and their notes, so a build resumes rather than
   duplicates.
2. **Write the build notes** before any definition: the goal, the plan, the
   step.
3. **Draft** each collection, view and page.
4. **Validate** the whole App: language errors with JSON paths and fixes,
   stored records the change would break, data it would drop, and the
   authority it would grant.
5. **Preview** views and pages with sample records, as Kyle sees them.
6. **Publish**, a compare-and-swap on the App's approved version.
7. **Seed and verify** real records through `app_data`, then **update the
   notes**.

Every write takes a `request_id`, so a retried call returns its first
receipt instead of acting twice.

## Authority

Building is not authority. The platform computes an App's **authority
facts** from its definitions (who can read or write which field, delete
reach, rules, retention, links, templates, tools) and a publish
**self-publishes** only when nothing widens: every fact is already approved,
or narrower. A new App starts with nothing approved, so it is visible to its
owner and Kyle only. New fields start private, and adding a rule always
self-publishes.

Widening (sharing with another agent, retention, delete reach, links,
tool-only collections, page action templates) is a **proposal** Kyle
approves from his browser session. `apps propose` freezes the selected drafts,
a rollback, or an ownership transfer with a digest and the current approved
version. `apps get` shows open proposals; `apps proposal` reads or withdraws
one. Kyle reviews the frozen change and current diff, then approves by sending
the shown digest or declines with a reason. A changed App or authority delta
stales the proposal instead of silently publishing a different change. Every
decision has a `request_id` for safe retries. The full list is in
[App data](app-data.md#access-and-authority).

## The App Builder request path

When a builder needs a primitive the kit lacks (a component, field type,
rule, view feature or connector), it searches open `app-builder` tickets and
comments on a match, or files one in `#eng`:

```text
Need: <the primitive>
For: <App id and what its reader is trying to do>
Tried: <the closest existing primitive and why it falls short>
Shape: <what the definition would look like if it existed>
```

It records the ticket key in the App's build notes and checks
`apps schema`'s capabilities version before relying on anything new. It
never ships a weaker App to work around a missing guard.

## Releases

Release 1a (the store, definitions, the lifecycle, records, views, batch
and artifacts in the engine, quotas, and `table`, `detail`, `metric` and
`text` pages) is being built. Proposals, sharing, page actions and tool
views are Release 1b; history, `versioned` collections and tool actions on
pages are Release 2; charts and richer components are Release 3.
[App data](app-data.md#not-built-yet) lists them.

## Coded Apps (being migrated)

Each coded App moves to state in turn: judgment (M1) and TCMS (M2) after
Release 2, then running, news and stockmarket after Release 3, then TTRPG.
For each one, the definitions are published through Kyle's approval, the data
is copied with parity checks, readers cut over, the maintainer passes a
takeover gate run from the build notes alone, and then the service, image,
schema, secrets, key, topics, `query_app` adapter and its `app.yaml` are
deleted. What follows describes them as they run until then.

A coded App is a database-owned collection of pages and actions, usually
backed by a reviewed domain service with its own API, UI, and data
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

**Backtests** (`docs/design/35-backtest-lab.md`) is a tab of the same app, at
`/apps/stockmarket/backtests`. It runs a declarative strategy spec — a
period, money in, costs, one to eight strategies — through a deterministic
engine on a pinned copy of the price archive, and stores the full result
(experiment, dataset, metrics, series, events) directly in the app's own
tables. Ask for one in a Relay DM or channel with `stockmarket-data`, in
plain words ("what if I'd put $500/month into QQQ since 2018?"); it drafts
the spec, tells you which defaults it assumed (base currency, what happens
to new money), runs it, and replies with a link to the experiment page —
description, a stat row per strategy, value-vs-contributed and drawdown
charts, the pick timeline, and the caveats that print on every report
(hindsight, concentration, taxes, data provenance). Reruns reuse the pinned
dataset by default, so an October check of a March experiment reproduces the
same numbers; ask for current data explicitly to redo it on a fresh pull. A
question the grammar cannot express (shorting, options, leverage) gets a
plain "can't do that yet" and a ticket, never a guessed answer.

**Coded Apps live in:** the App collection and versioned live pages are
database rows. `apps/<name>/` holds a domain backend/frontend and `app.yaml` infrastructure
manifest where specialized behavior exists. Domain services ship like platform
services (build image → import → enable in helm). They are deliberately
**separable from platform code**: an app service may depend
only on public contracts — the HTTP API + SDK, Kafka topics, `@ap/ui` — never
`agentplatform` internals. (Kyle intends to split workloads into their own
repo eventually; an app must survive a `git mv`.)

### The manifest (`apps/<name>/app.yaml`)

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

### Declarative provisioning

The dispatcher's **AppProvisioner** heartbeat reconciles every declared app:
pg role + schema (creds → k8s secret `app-<name>-db`, env-ready keys like
`APP_DB_URL`), a single-owner `app:<name>` API key (→ secret
`app-<name>-key`, predecessors revoked, reminted if the secret vanishes),
and missing Kafka topics. It converges and never tears down — deleting an
app's data is a human act.

### Runtime contract

- **Deploy**: list the name in `.Values.apps.enabled`; the chart's generic
  template runs `agent-platform-app-<name>:tag` (hardened pod, the two
  secrets envFrom'd, `AP_API_URL`/`AP_KAFKA_BOOTSTRAP` injected).
- **Routing/auth**: web nginx proxies `/apps/<name>/` → the app's Service
  with `auth_request` against `GET /api/auth-check`. The app never sees
  credentials — it receives trusted `X-AP-User` / `X-AP-Role` headers (its
  API should refuse requests without them), plus `X-AP-Auth` saying how the
  caller authenticated: `session` (login cookie), `key` (`ap_` API key) or
  `workload` (ServiceAccount token). A key's principal is its name, so an
  App that must admit only a person's login checks `X-AP-Auth: session`.
  `query_app` calls carry no `X-AP-Auth`. NetworkPolicy makes nginx the
  ONLY ingress to an app pod, so the guard can't be bypassed in-cluster.
- **Agent output in**: an agent manifest's `result_topic:` feeds successful
  run results to the app's inbound topic via the recorder — the agent itself
  stays credential-free.
- **Actions out**: the app uses its `app:<name>` key against the platform
  API (save reports, trigger runs) and produces to Kafka (e.g.
  `discord.channel.post`).
- **UI**: app frontends are npm workspace members importing `@ap/ui` — the
  same tokens/primitives as the console (no-raw-hex gate scans them too).

### Registry

`GET /api/apps` + the `/apps` page: what's declared, what each app needs,
whether its Deployment is ready, and the available live pages. A legacy
manifest is imported into the collection once; subsequent collection edits
are DB-owned. The manifest still declares infrastructure needs.

### Live pages (`typed/v1`)

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
an agent or send to Discord. An idempotent receipt records the outcome. At
dispatch, the server serializes calls by App and allows at most 30 dispatched
actions per person and page, and 120 per App, in a rolling hour. An exhausted
budget produces a `denied_at_dispatch` receipt without performing the action.
For the two admitted local actions, the Ticket or Relay row and its success
receipt commit in one database transaction. If a later event publication
fails, the browser still sees the committed success rather than an uncertain
outcome and does not retry the action.
Further Tools need a reviewed operation contract and an explicit grant before
a page may invoke them. The Apps directory opens published pages first and
keeps the detailed domain screens linked for specialized controls.

An admin can inspect the last 1–30 days of durable action receipts at
`GET /api/live-actions/observation?days=7`. It returns status counts, up to ten
unresolved receipt IDs, and the current number of revoked action grants.
Arguments, destination IDs and user content stay out of this aggregate. This
is action evidence only. Page-data reads have a separate admin view at
`GET /api/live-reads/observation?days=7`, optionally filtered by `view_id`.
It reports HTTP status counts and p95 server latency from up to 50,000 recent
samples. Each observation stores only view ID, status, duration and time;
it records after the response and is retained for 30 days. A `truncated`
flag means the result is a recent sample rather than the full period. Browser
paint and network time still need a browser canary before retiring a
specialized screen.
