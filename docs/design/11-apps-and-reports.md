# 11 — Apps & Reports: the agentic application platform

**Status: PROPOSED — talking through with Kyle before execution.** This doc is
the execution reference; the conversation happens at a higher level.

The platform today runs agents. This milestone extends it into an **agentic
application platform**: agents produce durable, browsable artifacts
(**Reports**) and feed full web applications (**Apps**) that own their own
data, expose their own APIs/UIs, and close the loop by triggering agents. The
proving case is **News**: today a Discord-posting agent, tomorrow a tagged
relational archive with a calendar browser and a daily HTML report.

---

## Entity map

```
ReportType (git building block)      App (monorepo service)
    │ 1:N                                │ owns
    ▼                                    ▼
Report (pg row + HTML blob)          pg schema · kafka topics · redis (later)
    identity: type/YYYY-MM-DD[/HH-MM]    │ talks to
                                         ▼
                                     agents (scoped API key, SDK)
```

Two new pages: **/reports** and **/apps**. Two new nouns in the vocabulary.
Reports are *artifacts* (immutable, dated, rendered). Apps are *services*
(long-running, stateful, interactive).

---

## Decision: where report HTML lives

**Postgres blobs, behind a `ReportStore` interface.** (Judgment call — debate
welcome.)

- ① **Postgres (chosen).** Reports are small HTML text fragments (10s–100s of
  KB, compresses ~5:1 with pg's TOAST). Metadata and content stay
  transactional, calendar queries are one indexed SELECT, retention pruning
  reuses the existing transcript-pruning pattern, and backup is the existing
  pg story. Zero new operational components.
- ② **MinIO on a PVC** — the honest self-hosted S3. The right move *if* reports
  grow binary payloads (images, PDFs) or volume gets serious. `ReportStore` is
  an interface from day one so this slots in without touching callers.
- ③ **Rook/Ceph — rejected.** Ceph wants 3+ OSD nodes and several GB of RAM
  for its daemons; on a single NUC it's distributed-storage cosplay. If we
  ever have three nodes, revisit.
- ④ **Git — rejected.** Generated artifacts in the config repo pollutes
  history and the change loop; wrong tool.

Stored form: the **body fragment only** (sanitized at ingest). The viewer
wraps it in the report-kit shell (current CSS) at render time — artifacts stay
stable while styling upgrades retroactively.

## Reports

### ReportType — the 4th git building block

`reports/<type>/report.yaml`:

```yaml
name: daily-news
description: Morning digest of gathered news, grouped by topic.
icon: 📰
generator: news            # the agent expected to produce it
cadence: daily             # daily | intraday | adhoc — display + validation hint
retention_days: 365        # pruning window (0 = keep forever)
```

- Registered like skills/secrets (a `ReportTypeRegistry` mirroring
  `SecretRegistry`), edited through the change loop on `coder/report-<name>`
  (runner `_BLOCK_KINDS` gains `reports:`; classify_change_path too).
- **Scheduling stays where it lives today**: a ReportType does not schedule
  anything. The generator agent's `entrypoints.yaml` cron (or a Job) fires the
  run; the run saves the report. One scheduler, no new firing mechanism.
- Broken yaml → the type shows errored in the UI; saving reports against an
  undeclared type is a 422 (declare first — same discipline as secrets).

### Report rows

```
reports table: id, type, date (DATE), time (TIME null), html (text),
               meta (jsonb: title, summary, tags), run_id, created_at
unique (type, date, time)    -- type/YYYY-MM-DD[/HH-MM] identity, as specced
```

### API + SDK

- `POST /api/reports` `{type, date, time?, html, meta}` — agent-scoped keys
  allowed (`annotator`-style narrow role: may write reports, read nothing
  else). Rejects undeclared types; **sanitizes HTML at ingest** (python `nh3`,
  allowlist = report-kit tags/classes + inline SVG minus scriptable attrs).
  Idempotent upsert on the identity triple (a re-run replaces its report).
- `GET /api/reports?type=&from=&to=` — calendar/list metadata (no HTML).
- `GET /api/reports/{id}/html` — the wrapped, rendered document.
- SDK grows `save_report(...)`; a **`reports` skill** teaches agents the
  contract and the blessed markup.

### Report-kit: storybook-first authoring

Kyle's rule: **Reports and Apps use the design system exclusively. Missing
element → add it to storybook first.**

- `packages/report-kit/` builds `report.css` from `tokens.css` + a set of
  blessed, documented composite classes (`rk-page`, `rk-section`, `rk-stat`,
  `rk-table`, `rk-chip`, `rk-callout`…), each with a Storybook story
  (`ReportKit/*`) — storybook remains the single component home.
- **Charts without JavaScript**: reports are static; the viewer iframe is
  `sandbox` with no `allow-scripts`. Charts are **server-rendered inline
  SVG**: `POST /api/report-kit/chart {spec}` returns themed SVG (bar/line/
  donut/sparkline, `--ds-chart-*` palette); agents embed the SVG. Chart
  varieties mirror storybook chart components; new chart = storybook story +
  server renderer together.
- The `reports` skill documents: fragment-only HTML, blessed classes, the
  chart endpoint, and "if the element you want doesn't exist, STOP and say
  so" (backpressure by instruction; the sanitizer enforces it mechanically by
  stripping unknown classes/tags).

### Reports UI (/reports)

- Type grid (icon, description, cadence, latest date, count).
- Type view: **month calendar** (ds-tokens grid) with dot-marked dates;
  intraday types expand a date into its HH-MM list.
- Report view: sandboxed `<iframe srcdoc>` (no scripts, CSP `default-src
  'none'; style-src 'unsafe-inline'; img-src data:`), print-friendly, prev/next
  date navigation, "generated by run …" provenance link.

### Security posture

Report HTML is **agent-generated from untrusted inputs** (news!). Three
layers: ingest sanitization (nh3 allowlist), script-free sandboxed iframe,
narrow write-only API role. The news injection-hardening posture
(docs/design/08) is unchanged — the gatherer still never holds posting or
reporting credentials; the projector writes the report.

## Apps

### Runtime model — monorepo services (judgment call)

- ① **Apps as monorepo services (chosen).** `apps/<name>/` in this repo:
  FastAPI backend + optional React frontend, own Dockerfile, deployed by the
  chart's generic per-app template. Same build/import/deploy pipeline as every
  other service (there is no registry and CI can't reach the NUC — dynamic
  image building isn't real here). Apps are **platform code**, not
  change-loop blocks: they ship like services/backend does.
- ② *In-process plugins (rejected):* mounting app routers inside the platform
  API is cheap but couples app crashes/deps/migrations to the control plane —
  "full web server apps" deserve their own process.
- ③ *Separate repos/images per app (rejected for now):* honest microservices,
  but per-app CI and image logistics on a registry-less NUC is pure friction.
  The app.yaml contract keeps this door open.

### The app contract — `apps/<name>/app.yaml`

```yaml
name: news
description: Browse gathered news by calendar and topic.
icon: 🗞️
ui: true                   # serves a UI at /apps/news/
api: true                  # serves an API at /apps/news/api/
needs:
  postgres: true           # schema app_news + role, secret app-news-db
  kafka_topics: [app.news.item.ingested]
  redis: false             # shared redis chart dep added when first true
agent_key:                 # scoped platform key minted for this app
  role: operator           # may trigger runs / read run output
```

- **Postgres**: one database, schema-per-app (`app_news`), dedicated role with
  rights only on its schema; credentials in k8s secret `app-<name>-db`,
  provisioned by an idempotent init job. Apps own their migrations (alembic
  in-app).
- **Kafka**: topics namespaced `app.<name>.*`, declared in app.yaml, created
  by the existing topics-job.
- **Redis**: not installed today; the chart grows a shared redis dependency
  the first time an app sets `redis: true` (per-app key prefix).
- **Agent access**: each app gets a single-owner platform API key (the
  `system:<agent>` minting pattern → `app:<name>`), scoped by role. Apps
  trigger agents and read run output through the public SDK like anyone else.
- **Routing/auth**: web nginx proxies `/apps/<name>/` to the app's Service.
  App routes are session-guarded by **nginx `auth_request`** against the
  platform API — apps never see credentials, they receive trusted headers
  (`X-AP-User`, `X-AP-Role`).
- **UI**: app frontends import the shared component library. This forces the
  overdue extraction: **`packages/ui/`** (npm workspace: `src/ui` primitives +
  `tokens.css` + cn/cva utils) consumed by `services/web` and every app
  frontend; storybook builds from the package. One design system, enforced by
  the same no-raw-hex + Playwright/axe gates (app smoke tests join CI).

### Apps UI (/apps)

Registry page listing each app (from app.yaml discovery in the synced
checkout): icon, description, deployment health (replicas ready), its
resources (schema, topics, key), and an **Open** link into `/apps/<name>/`.
Platform chrome stays minimal — the app owns its interior UX.

## News: the proving case

News today: privilege-separated gatherer → projector → discord connector,
dedup via the `shared_news` table. It becomes **an App + a daily ReportType**,
and the pieces couple through the app's database.

### The news app (`apps/news/`)

Schema `app_news`:

```
items:  id, title, url, source, summary, topic (fk), published_at,
        ingested_at, run_id, dedup_hash (unique), raw jsonb
topics: id, slug (ai, business, …), label, color_token
```

- **Ingestion API** `POST /apps/news/api/items` (bulk): validates, tags,
  dedups on `dedup_hash` — **this replaces `shared_news`** as the single
  dedup authority (one-time backfill migration, then drop).
- Emits `app.news.item.ingested` to Kafka per accepted item — the event spine
  later consumers (connector, report trigger, whatever) hang off.
- **Browser UI**: month calendar × topic chips; a day×topic drill-down lists
  items (title→source link, summary); topics manageable in-app; links out to
  that day's `daily-news` report and to the gathering run.

### The coupled flow

```
cron → news gatherer (untrusted web) ──news-db skill──▶ POST items (tagged)
                                                            │ kafka: item.ingested
projector (reads app db, trusted) ──reports skill──▶ POST /api/reports (daily-news)
                                   └─────────────▶ discord post (existing, + report link)
```

- **`news-db` skill**: teaches agents the ingestion API (endpoint, item shape,
  topic slugs, "tag every item; propose a new topic rather than forcing a bad
  fit"). The gatherer tags at ingest — it already reads the untrusted
  content, so tagging adds no new exposure.
- The projector renders the **daily-news report** with report-kit (topic
  sections, stat row, a 14-day volume sparkline via the chart endpoint) and
  keeps the Discord post (now linking the report).
- Injection posture unchanged: gatherer writes *data* to the app API (its
  key can ONLY ingest items); the projector, which holds report/discord
  reach, consumes structured rows, never raw pages.

## Phasing (each phase ships + live-verifies before the next)

1. **Reports foundation** — ReportStore (pg), ReportType registry + block
   wiring (branch kinds, change loop, impact digest), APIs, SDK regen,
   `reports` skill, report-kit package + chart endpoint + stories,
   /reports UI (grid, calendar, sandboxed viewer), retention pruning.
2. **Apps foundation** — `packages/ui` extraction (web + storybook consume
   it; CI gates follow), app.yaml contract + registry API, chart generic
   app template + nginx auth_request routing, pg schema provisioning job,
   `app:<name>` key minting, /apps UI.
3. **News app** — apps/news backend (schema, migrations, ingestion+browse
   APIs, kafka emit), frontend (calendar × topics), shared_news backfill +
   retirement, deploy.
4. **Coupling** — `news-db` skill, gatherer tags+posts items, projector
   writes the daily-news report, discord post links it, first real report
   generated on schedule.
5. **Docs + memory** — building-blocks docs gain reports.md + apps.md,
   README map, memory files.

## Judgment calls to debate

- Pg blobs over MinIO/Ceph (interface keeps the exit).
- Apps are monorepo platform code, NOT change-loop building blocks;
  ReportTypes ARE building blocks.
- ReportTypes don't schedule; generation rides existing entrypoints/jobs.
- Reports store sanitized fragments; kit shell injected at render.
- Charts = server-rendered SVG, never JS in reports.
- `packages/ui` extraction lands inside this milestone (Phase 2) — it's the
  enforcement mechanism for "storybook components exclusively".
- shared_news dies; the news app db is the dedup authority.
- Redis deferred until an app actually needs it.
