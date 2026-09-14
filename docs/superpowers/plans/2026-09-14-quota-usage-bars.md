# Plan — Quota, the account's usage windows in the sidebar (design 22)

Design: `docs/design/22-quota-usage-bars.md` (read it first; the sections
named in each task are pasted into that task's implementer prompt).
Predecessors built the same way: `2026-09-12-tickets-agent-work-tracker.md`
and `2026-09-13-wiki-shared-knowledge.md` (their "Live verification" and
"Handoff" sections show the deploy mechanics that worked).

The ask, verbatim, is the design's "The ask" section; the acceptance
criteria are AC-1…AC-5 there. Every task below names the AC it satisfies.

## Loop protocol (read this every tick, follow it exactly)

You are the **orchestrator**. You do not write product code yourself. You
dispatch subagents, verify their evidence, commit, and update this file.

1. Re-read this plan top to bottom. Find the first `- [ ]` task in "Tasks".
   If there is none, run "Definition of done"; if it passes, stop the loop
   (ScheduleWakeup `stop: true`) after a PushNotification with the one-line
   outcome; if it fails, add a task under "Tasks → Repairs" and continue.
2. Dispatch **one opus implementer** (Agent tool, `model: "opus"`,
   `subagent_type: "general-purpose"`) with: the task text verbatim, the
   "Ground rules for implementers" block below verbatim, the relevant design
   sections pasted in (not linked — the subagent has no conversation context),
   and the file paths from the task. Require it to work TDD: failing test →
   implementation → green, and to report the exact test commands it ran with
   their final summary lines. Tasks marked `[parallel with Tn]` may be
   dispatched in the same tick as that task, one implementer each; tell each
   implementer which paths the other owns and that it must not touch them.
   Tasks marked `[after Tn reports]` start the moment Tn's implementer
   reports (before Tn's review finishes), because they build on Tn's files.
3. When it reports, **verify the evidence yourself**: run the test commands it
   named (backend: `cd services/backend && .venv/bin/python -m pytest -q
   <paths>`; web: `cd services/web && npm run -s lint && npm run -s
   check:tokens && npm run -s build && npx playwright test --reporter=line`;
   broker: `cd services/mcp-broker && ../backend/.venv/bin/python -m pytest
   -q`; proxy: `cd services/claude-proxy && ../backend/.venv/bin/python -m
   pytest -q` with the Docker daemon running). Do not trust a claim of green
   without output in your own transcript. Run git and every repo-relative
   command from the repo root, never from a `cd` that persisted (a diff saved
   from the wrong directory is empty).
4. Dispatch **one sonnet reviewer** (`model: "sonnet"`) with the task text, the
   design excerpt, and the path of a saved `git diff` of the uncommitted
   changes. Ask for findings ranked by severity, defects only, no style — and
   for every guard, limit, permission check, secret compare, header parse or
   njs handler, ask it to state the WORST CASE (a hostile header value, a
   looping agent calling the tool, the API down while the proxy posts, two
   concurrent refreshes, a second `init_db` on a live DB, a response with no
   usage headers). For tasks tagged `[ui]`, dispatch additionally a **sonnet
   visual reviewer** that builds the app, serves it against the Playwright
   mock API (`tests/mock-api.ts`), screenshots the affected page(s) at
   1280×800 and 390×844 in both themes with a throwaway spec it deletes
   afterwards, READS the PNGs, and reports what a picky human would notice
   (for T5: is the label legible at 0%, 50%, 100%; does the inversion line up
   with the fill edge; do the bars sit directly under the brand at the
   sidebar's inner width; does the light theme still show a track; at 390
   the nav reflows into a full-width top bar — are the bars still directly
   under the brand, full width, and not wrapped between nav entries).
   Never use `model: "fable"` or the default model for subagents.
5. Findings of severity high or critical go back to the same implementer via
   SendMessage (it keeps its context). Low/medium: fix if cheap, otherwise
   note under "Deferred" with file:line and move on. Loop at most twice per
   task; if a task still fails after two repair rounds, mark it `- [!]` with a
   one-paragraph note and continue to the next task — do not stall the build.
6. **Hold every commit until no implementer is editing the tree**: the
   pre-commit hook stashes and restores unstaged tracked files and would race
   a live editor. Commit on `main` (Kyle's workflow: single branch, direct
   commits, `git add` each file by name, never `-A`; never add files that may
   hold secrets — the stored values file and anything under the scratchpad
   stay out). Message: `feat(quota): <task title>` plus a body, and the
   attribution trailer the session was given. Then edit this file:
   `- [x] **Tn …** (commit `<hash>`; <one line of what review changed>)`.
7. Schedule the next wakeup with `delaySeconds: 60`, `noop: false`, and the
   sentinel prompt the loop skill prescribes. One task (or one parallel pair)
   per tick keeps each turn's context small.
8. Recovery. A quota 429 kills a subagent instantly; it keeps its transcript:
   after the reset, SendMessage "quota is restored; check git diff for what
   landed, then resume from where you stopped" — if that fails, relaunch with
   the same prompt. A "stalled: no progress" agent likewise. Terminal.app
   `do script` sometimes DROPS THE FIRST CHARACTER of the command: always
   prefix with `true; true; ` and use absolute paths, and read the `.out`
   file within 30 s of launching. Terminal.app stops executing after ~20
   stale windows: quit and relaunch it. A Playwright spec that fails only
   under the full suite or under host load (`relay.spec` "replies stay out of
   the room" is a known one): rerun that file alone before calling it red.
9. If a Bash action is refused by the auto-mode classifier (kubectl
   apply/delete, helm upgrade, reading a credentials file), do not retry
   variants. Write the exact commands into "Handoff to Kyle" below, send a
   PushNotification saying the build is blocked on that step, and continue
   with any task that does not depend on it. Reaching the NUC works via
   Terminal.app:
   `osascript -e 'tell application "Terminal" to do script "true; true; <cmd> > <scratchpad>/x.out 2>&1; echo EXIT=$? >> <scratchpad>/x.out; exit"'`
   then wait for `EXIT=` in the file (plain `ssh pai` from this process gets
   "No route to host" — macOS Local Network permission, see memory
   `claude-code-local-network-tcc-gotcha`). The API is reachable from this
   process only through `ssh -f -N -L 18090:localhost:8090 pai` (started the
   same way; it dies when the ssh session drops — check `curl
   localhost:18090/login` before each live step) at `http://localhost:18090`;
   Playwright screenshots of the live site go through that forward too. The
   deploy script from the Wiki build is the reference (session scratchpad
   `t21-deploy.sh`: buildx `--platform linux/amd64 --provenance=false --load`,
   `docker save` → `scp` → `sudo k3s ctr -n k8s.io images import`, `helm
   upgrade ap charts/agent-platform -n agent-platform -f <stored values> -f
   charts/agent-platform/values-pai-nuc.yaml` — never `--reuse-values` — then
   rollout restart api/dispatcher/recorder/web/broker, facade last). This
   build adds one required value: `env.AP_INTERNAL_SECRET` must be appended
   to the stored values file (scratchpad `ap-stored-values.yaml`, never
   committed) before the first `helm upgrade`, generated with
   `python3 -c 'import secrets;print(secrets.token_urlsafe(36))'`.
   The claude-proxy Deployment must roll too (its ConfigMap changed; add
   the checksum annotation if the template lacks one, or restart it).

### Ground rules for implementers (paste verbatim into every implementer prompt)

- Repo: `/Users/kp/gh/agent-platform`. Backend is FastAPI + SQLAlchemy async
  in `services/backend/agentplatform` (tests in `services/backend/tests`,
  run with `cd services/backend && .venv/bin/python -m pytest -q`). Settings
  are `config.py` `Settings` (pydantic, `env_prefix="AP_"`; `claude_proxy_url`
  already exists). Web is React 19 + Vite + `@ap/ui` (`packages/ui`) in
  `services/web` (`npm run -s lint`, `npm run -s check:tokens`, `npm run -s
  build`, `npx playwright test`). The MCP broker is
  `services/mcp-broker/broker.py` (tests `cd services/mcp-broker &&
  ../backend/.venv/bin/python -m pytest -q`). The facade is
  `services/mcp-facade/facade.py` (tests need a Python 3.12 venv:
  scratchpad `facade-venv`). Prod Python is 3.12 (dev venv is 3.14): no
  3.13+/3.14-only syntax, no annotation tricks that only pass locally.
- TDD: write the failing test first, show it fail, make it pass, keep the
  suite green. Backend tests use the `admin_client` / `sf` fixtures in
  `tests/conftest.py` and sqlite; keep every new table portable to sqlite
  (JSON columns, no Postgres-only DDL outside `dialect == "postgresql"`
  guards, as `db.py:_ensure_relay_ddl` and `_ensure_tickets_ddl` do).
- Migrations are additive and live in `db.py`: `Base.metadata.create_all`
  makes new tables, `_ensure_columns` adds new columns to existing ones (it
  cannot apply an ORM default to existing rows — backfill explicitly), and
  one-off seeds/backfills go in a new `_ensure_*` function called from
  `init_db`, idempotent, gated by a `schema_marks` row (see
  `_ensure_wiki_default_grant` and `_grant_to_every_agent` for the grant
  sweep shape).
- Kafka: topics are constants in `agentplatform/events.py` (`ALL_TOPICS` must
  include them) AND `charts/agent-platform/values.yaml` `topics.specs`
  (retentionMs quoted as a string). Use `make_envelope` / `Producer` /
  `FakeProducer` / `consume_forever` from `events.py`; never a bare
  aiokafka client. SSE fan-out is `relay_feed.TopicFeed` (`api/wiki.py`
  `wiki_feed()` and `GET /api/wiki/events`, plus `api/app.py`
  `wiki_events_consumer_factory`, are the three pieces to mirror). Tests
  inject `FakeProducer`; nothing in a test may open a real Kafka connection
  (see how `test_relay_sse.py` and `test_tickets_api.py` inject the feeds).
- Roles: `api/auth.py` `ROLES`/`READ_ROLES`/`INVOKE_ROLES`; the per-run
  participant role is `relay`; `agentspec.PLATFORM_MCP_RELAY_TOOLS` is the
  grant list that yields it (add the quota grant there, never to
  `PLATFORM_MCP_TOOLS`). Shared-secret compares use `hmac.compare_digest`
  like `webhooksecrets.verify_secret`; failure is a uniform 401 with no hint
  which part was wrong; a missing server-side secret is 503 (fail closed).
- Every value that arrives from outside (a header string the proxy
  forwarded, a body from a runner) is parsed into numbers/datetimes in
  `quota.py` before it is stored, rendered, or published; unknown headers
  are kept verbatim in `raw` and never rendered into prompts or pages.
- Follow the file's existing comment style: comments explain *why*, not what;
  no narration, no "added by" notes, no TODOs without an owner.
- Web: the shell is `packages/ui/src/sidenav.tsx` (`SideNav`, brand at
  `.nav-brand`, CSS in `sidenav.css`); tokens are `packages/ui/src/tokens.css`
  (the ONLY file allowed to contain hex; `npm run -s check:tokens` enforces
  it; add primitives under `@theme`, semantic `--ds-*` aliases per theme,
  mirror the `--ds-warning`/`--ds-danger` shape). Data fetching lives in
  `services/web` (`src/api.ts` `api()` helper; `components/tickets/
  useTickets.ts` is the SSE + backoff + poll-fallback template). Playwright
  fixtures go in `services/web/tests/mock-api.ts` (unmatched GETs fail tests
  on purpose — add `GET /api/quota`, `POST /api/quota/refresh` and the SSE
  route there); a11y runs at desktop and 390 (`tests/a11y.spec.ts`). No raw
  hex colours. Mobile (<560px) must not break the shell; never `display:
  contents` on a landmark. Storybook stories live next to the component in
  `packages/ui/src/*.stories.tsx`.
- After any change to `services/backend/agentplatform/api/*` run
  `sdk/regenerate.py` with a Python 3.12 venv's `bin` FIRST on `PATH` (the
  session scratchpad has `sdkvenv`; `uv venv --python 3.12` makes another) so
  the SDK drift check stays green; commit the regenerated files with the task.
- Never widen scope. If the task needs something the design didn't specify,
  choose the simplest option consistent with the design and say so in your
  report. Do not touch deploy, the NUC values overlay, or secrets. Never
  commit — the orchestrator commits.
- Report: files changed, test commands with their final summary lines, and
  any decision you made that the task text did not pin down.

## Tasks

### Phase 1 — capture and cache (T1 and T2 are disjoint)

- [ ] **T1 Proxy capture: njs header filter, internal secret, network policy, docker test.** `[parallel with T2]` (AC-1)
  Design sections: "The proxy capture" (with its contract and both
  Alternatives tables), "Identity, trust, and guards".
  Files: `charts/agent-platform/templates/claude-proxy-config.yaml`
  (new njs function + `js_header_filter` on the Anthropic location),
  `charts/agent-platform/templates/claude-proxy.yaml` (mount the
  `{{ .Release.Name }}-internal` Secret read-only at `/secrets/internal`;
  a checksum annotation of the ConfigMaps so config changes roll the pod),
  new `charts/agent-platform/templates/internal-secret.yaml` (one key
  `quota`, value `.Values.env.AP_INTERNAL_SECRET`, `required` like
  `AP_SESSION_SECRET` in `api.yaml`), `charts/agent-platform/templates/
  networkpolicy.yaml` (`allow-api` ingress adds component `claude-proxy`;
  `allow-claude-proxy` ingress adds component `api`), `values.yaml`
  (`claudeProxy.quota.enabled: true`, `claudeProxy.quota.apiUrl` default
  `http://agent-platform-api:8000`; document `env.AP_INTERNAL_SECRET` in
  the `env:` comment block, no default), new `services/claude-proxy/tests/`
  with `test_proxy_quota.py` and a `conftest.py`.
  The njs handler: on every upstream response, collect every response
  header whose name starts with `anthropic-ratelimit-unified-`; if none,
  return; else `ngx.fetch(<apiUrl>/api/internal/quota, {method: "POST",
  headers: {"Content-Type": "application/json", "X-AP-Internal-Secret":
  <file /secrets/internal/quota, trimmed>}, body: JSON})` without awaiting,
  `.catch` → `r.warn(...)`. Body per the design's contract (`headers`,
  `status`, `observed_at` as ISO-8601 UTC). It must never alter the
  response or its timing and must tolerate the secret file being absent
  (log once per request, post nothing). Render the ConfigMap so the api URL
  and the enabled flag come from values (envsubst already renders the
  `.conf.template`; keep secrets out of nginx variables).
  The docker test (skips with a clear reason when the Docker daemon is
  unreachable): render the two ConfigMaps with `helm template` (set
  `env.AP_SESSION_SECRET` and `env.AP_INTERNAL_SECRET` to dummies), write
  nginx.conf/claude.js/the conf.template into a temp dir, run the
  `nginxinc/nginx-unprivileged:1.31.3` image with those mounted, a dummy
  token file and a dummy secret file, `DNS_RESOLVER` pointing at a resolver
  that works in the container, and the upstream overridden to a Python
  fake (`http.server` in a thread on the host, reachable as
  `host.docker.internal`) that answers `/v1/messages` with the five headers
  from the design's contract. A second fake on the host receives the
  internal POST. Assert: the client got the upstream body and status
  untouched; the receiver got exactly one POST with the right secret header
  and a body whose `headers` map contains the five names with their
  verbatim values; a response without the headers produces no POST; a
  receiver that is down does not change what the client gets. Because
  `proxy_pass $upstream` and `proxy_ssl_*` are hard-wired for Anthropic,
  add one values switch used only by the test (`claudeProxy.upstream`,
  default `https://api.anthropic.com`, and `claudeProxy.upstreamTls`
  default `true`) so the fake can be plain HTTP; do not weaken the
  production defaults. If `ngx.fetch` proves unusable from
  `js_header_filter` in 1.31.3's njs, implement the design's fallback
  (`js_shared_dict_zone` written by the filter, `js_periodic` every 2 s
  posting when the dict changed) with the same test, and say so in the
  report. Also add a `helm template` lint test (no docker) asserting the
  Secret, the mount, the annotation and the two network-policy changes
  render. Run `helm lint charts/agent-platform` with the two dummy secrets.
  Acceptance: both tests green (docker one actually ran, not skipped, on
  this machine); `helm lint` clean; the production template still verifies
  Anthropic's TLS and still blanks `x-api-key`.

- [ ] **T2 Model, pure library, store, topic.** `[parallel with T1]` (AC-2)
  Design sections: "Data model", "Events", "Naming", "Identity, trust, and
  guards".
  Files: `services/backend/agentplatform/db.py` (`QuotaSnapshot` model per
  the design table; `id` PK; JSON `raw`; tz-aware datetimes as the other
  models do), `events.py` (`TOPIC_QUOTA_EVENTS = "quota.events"` in
  `ALL_TOPICS`), `charts/agent-platform/values.yaml` `topics.specs` (1
  partition, `retentionMs: "604800000"`), new `quota.py` (pure: header
  names as constants; `parse_observation(headers: Mapping[str,str],
  observed_at, source) -> Observation` that accepts utilization as a
  fraction `0.22` or a percent `22`/`22.5` (rule: values > 1 are percents;
  clamp to 0..1; unparsable → None), reset as epoch seconds or ISO-8601
  (→ tz-aware datetime or None), keeps every `anthropic-ratelimit-unified-*`
  header verbatim in `raw`, and returns `None` when no known header is
  present; `is_stale(snapshot, now)` (no row, or `now` past the earliest
  non-null reset); `changed(prev, obs)` (any utilization or reset differs);
  `render_text(snapshot, now)` producing the tool's text from the design,
  with the >90% advisory sentence; `humanize_delta`), new `quota_store.py`
  (`observe(session, producer, obs) -> QuotaSnapshot`: upsert the singleton
  under a row lock on postgres, always bump `observed_at`/`source`/`raw`/
  `updated_at`, publish `quota.event` (key `"quota"`, data = the serialised
  snapshot) only when `changed`; `latest(session)`; `serialize(snapshot,
  now)` → the API shape with `stale` and `age_seconds`; `quota_feed()` =
  `TopicFeed(TOPIC_QUOTA_EVENTS, event="quota", frame_of=...)` with one
  stream key), tests `tests/test_quota.py` (parsing edge cases: fraction vs
  percent, `1.0`, `100`, negative, garbage, epoch vs ISO, no known header,
  staleness boundaries, render_text at 0/50/91/100 and with nulls) and
  `tests/test_quota_store.py` (first observe creates; repeat with same
  values bumps `observed_at` and publishes nothing; changed value publishes
  one envelope with the right type/key/data; concurrent observes leave one
  row; `latest` on an empty table is None; sqlite round-trip keeps tz).
  Acceptance: both test files green; `pytest -q` whole backend green;
  `ALL_TOPICS` and `values.yaml` agree.

### Phase 2 — the API

- [ ] **T3 API: `/api/quota`, refresh probe, internal observe, SSE, facade, SDK.** `[after T2 reports]` (AC-1, AC-3, AC-4)
  Design sections: "API", "Alternatives considered — where the refresh
  runs", "Identity, trust, and guards".
  Files: new `services/backend/agentplatform/api/quota.py` (router with the
  four routes exactly as the design's table), `api/app.py` (include the
  router; `quota_feed` on `app.state`; a `quota_events_consumer_factory`
  mirroring the wiki one; start it where the wiki feed is started),
  `config.py` (`internal_secret: str = ""`, `quota_refresh_min_seconds: int
  = 15`, `quota_probe_model: str = "claude-haiku-4-5-20251001"`,
  `quota_probe_timeout_seconds: float = 20`), `api/schemas.py` (`Quota`,
  `QuotaWindow`, `QuotaObserveIn`), `charts/agent-platform/templates/
  api.yaml` (`AP_INTERNAL_SECRET` from `.Values.env`, `required`;
  `AP_CLAUDE_PROXY_URL` already reaches the api and dispatcher containers
  through the shared `backendEnv` helper in `_helpers.tpl` when
  `claudeProxy.enabled` — verify, do not add it again), `services/mcp-facade/facade.py` +
  `test_facade.py` (`^/api/quota/events$` into `EXCLUDED_PATHS`;
  `/api/internal/` prefix into `EXCLUDED_PATHS`; `POST /api/quota/refresh`
  into `CURATED_OUT` (agents use the tool); `GET /api/quota` stays a tool →
  counts become 85 default / 112 admin, docstring counts updated), `sdk/`
  regenerated, tests `tests/test_quota_api.py`.
  The refresh: an `asyncio.Lock` per app plus the short-circuit from the
  design; the probe uses `httpx.AsyncClient` against
  `settings.claude_proxy_url` with `Authorization: Bearer placeholder`,
  `anthropic-version: 2023-06-01`, `anthropic-beta: oauth-2025-04-20`,
  `content-type: application/json`; step 1 `POST /v1/messages/count_tokens`
  `{"model": probe_model, "messages": [{"role": "user", "content": "."}]}`;
  if the response (any status) carries at least one utilization header →
  observe it (`source="refresh"`) and return; else step 2 `POST /v1/messages`
  with `max_tokens: 1` and the same message; if that too lacks the headers →
  503 `detail="the probe returned no usage headers"`; proxy unreachable /
  timeout → 503 with a plain detail. Record which step answered in the
  response (`probe: "count_tokens" | "message"`) so live verification can
  read it. Inject the HTTP client through `app.state` so tests use a
  `httpx.MockTransport` and never touch the network. Internal observe:
  reads `X-AP-Internal-Secret`, `hmac.compare_digest` against
  `settings.internal_secret`; empty setting → 503; mismatch/missing → 401
  `{"detail": "unauthorized"}`; the route must bypass session/API-key auth
  entirely (it is called by nginx). SSE route mirrors `api/wiki.py`.
  Tests: GET before any observation (200, nulls, `stale: true`); internal
  observe happy path (row + one published envelope), wrong secret 401,
  no secret configured 503, body without known headers 204/`ignored`;
  refresh: count_tokens carries headers → one call, `probe: count_tokens`;
  count_tokens without headers → falls through to `messages` with
  `max_tokens: 1`; neither → 503; two concurrent refreshes → one probe;
  a refresh within `quota_refresh_min_seconds` of a fresh non-stale
  snapshot → no probe; a stale snapshot → probes even if recent; reader
  session may GET and refresh; a `relay`-role run token may GET and
  refresh; an unauthenticated request 401; SSE yields a `quota` frame after
  an observe (use the injected `FakeProducer` + feed as `test_relay_sse.py`
  does); facade counts.
  Acceptance: backend suite green; facade suite green under the 3.12 venv;
  `sdk/regenerate.py` produced a clean diff check; the new routes appear in
  the OpenAPI with tags `quota`.

### Phase 3 — the agents' side and the sidebar (T4 and T5 are disjoint)

- [ ] **T4 Broker tool `get_quota_usage` + default grant.** `[after T3 reports]` `[parallel with T5]` (AC-3)
  Design sections: "The `get_quota_usage` tool", "Identity, trust, and
  guards".
  Files: `services/mcp-broker/broker.py` (a plain `@mcp.tool` +
  `@_metered("quota")` async function `get_quota_usage() -> str`, no
  arguments, docstring written for a model: what it costs, when to call
  it, what the windows mean; `POST /api/quota/refresh` via `_call`; render
  the text client-side from the JSON with the same wording as
  `quota.render_text` (the broker cannot import the backend; keep the
  format identical and add a test that pins the exact string for a fixed
  snapshot); an `error: …` string on any failure; on 503 from the refresh
  fall back to `GET /api/quota` and say the value is cached and how old),
  new `test_quota_tool.py` (import the module-level `broker`, the `Calls`
  class and the `caller` fixture from `test_relay_tool.py` as
  `test_wiki_tool.py` does;
  asserts the `(method, path, params, json)` tuple, the rendered text at
  22%/81%, the >90% advisory, the 503→GET fallback wording, and that the
  audit record names tool `quota`), `services/backend/agentplatform/
  agentspec.py` (`TOOL_QUOTA = "mcp__platform__get_quota_usage"` appended to
  `PLATFORM_MCP_RELAY_TOOLS`; confirm `platform_token_role` and the runner
  `--allowedTools` derivation pick it up with a test in the existing
  agentspec tests), `db.py` (`_ensure_quota_default_grant(conn,
  default_grant=True)` via `_grant_to_every_agent` under mark
  `quota-default-grant-v1`, wired into `init_db` next to the wiki one, with
  an `init_db` kwarg like `wiki_grant`), tests in `tests/test_db*.py` for
  the sweep (idempotent; grants every enabled agent; skips when the kwarg
  is False).
  Acceptance: broker suite green; backend suite green; a runner spec for
  an agent holding the grant lists `mcp__platform__get_quota_usage` in its
  allowed tools.

- [ ] **T5 Sidebar bars: tokens, `QuotaBars`, `useQuota`, wiring.** `[ui]` `[after T3 reports]` `[parallel with T4]` (AC-4, AC-5)
  Design sections: "Web UI (the sidebar)" with its Alternatives table, and
  the AC-4/AC-5 lines of "The ask".
  Files: `packages/ui/src/tokens.css` (primitives `--color-blue-400` and
  `--color-pink-400` chosen to sit with the chip hues in the platform's
  charts — a clear mid blue and a soft magenta-pink — plus `--color-white`;
  semantic `--ds-quota-5h`, `--ds-quota-7d`, `--ds-quota-track` (white in
  dark theme, the raised surface in light theme), `--ds-quota-label-on`
  (white)), new `packages/ui/src/quota.tsx` + `quota.css` +
  `quota.stories.tsx` (`QuotaBars` per the design: two bars, sidebar inner
  width, ~12px tall, `NN%` centred in a monospace ~10px label, the
  two-layer inverted label (base label in the bar colour over the white
  track; a clipped overlay `width: NN%` with the fill colour and a white
  label positioned identically), `role="meter"`, `aria-valuenow/min/max`,
  `aria-label` and `title` with the window name, percent, reset countdown
  and observed age; stale → fill at 50% opacity; no data → renders
  `null`; stories for 0/22/81/100, stale, light and dark),
  `packages/ui/src/sidenav.tsx` + `sidenav.css` (new optional prop
  `belowBrand?: ReactNode` rendered immediately after `.nav-brand` with the
  brand's horizontal padding; nothing else moves), `packages/ui/src/
  index.ts` (export), new `services/web/src/components/quota/useQuota.ts`
  (mount: `GET /api/quota`; if `stale` → `POST /api/quota/refresh`
  fire-and-forget, merge its result; `EventSource("/api/quota/events")`
  with `quota` frames merged, `overflow` → re-GET, `onerror` → close, poll
  every 60 s, reconnect with the `useTickets` backoff; `visibilitychange`
  to visible → re-GET; never throws — a failed GET leaves the bars hidden),
  `services/web/src/Layout.tsx` (where `SideNav` is rendered — `App.tsx`
  only owns routes; `belowBrand={<QuotaBars {...quota} />}`),
  `services/web/tests/mock-api.ts` (`GET /api/quota` fixture at 22%/81%
  fresh by default, with a way for a spec to serve a stale one and to
  record the refresh POST; the SSE route answering a never-ending
  heartbeat like the other feeds), new `services/web/tests/quota.spec.ts`
  (bars render under the brand on `/` with labels `22%` and `81%` and the
  right `aria-label`s; the fill widths match; stale fixture → exactly one
  refresh POST is made and the bars update to the refreshed values; no
  data → no bars and no layout shift of the nav; both themes render a
  visible track — assert computed background differs from the nav
  background; at 390×844 the bars sit under the brand and span the
  reflowed top bar), `tests/a11y.spec.ts` untouched unless it fails (the
  bars are on every page already covered).
  Acceptance: `npm run -s lint && npm run -s check:tokens && npm run -s
  build && npx playwright test` green; the Storybook story builds
  (`npm run -s build-storybook` if that script exists, else skip and say
  so); the visual reviewer's screenshots show the two bars exactly under
  "Agent Platform", the label inverted across the fill edge at 22% and
  81%, and a visible track in light theme.

### Phase 4 — ship it

- [ ] **T6 Docs.** (all ACs) `[after T4 and T5 are committed]`
  Files: `docs/design/22-quota-usage-bars.md` (Status → shipped pending
  T7; fill "AS BUILT" from the commits: the njs approach that worked, the
  probe step that answered if T7 already ran — otherwise leave that line
  for T7; anything the implementers decided that the design did not pin
  down, from their reports in this plan's commit messages), new
  `docs/building-blocks/quota.md` (what the bars mean, where the numbers
  come from, the tool and when an agent should call it, the two settings,
  what "stale" means, how to rotate `AP_INTERNAL_SECRET`),
  `docs/building-blocks/README.md` (index row), `docs/building-blocks/
  glossary.md` (claude-proxy row gains "and reports the usage headers";
  vocabulary entries "Usage window", "Snapshot"), `docs/design/
  00-overview.md` (row 22 in the series table, same shape as row 21),
  `docs/design/09-token-brokering.md` (a short "AS BUILT addendum
  2026-09-14" noting the proxy now also observes usage headers and posts to
  the API with the internal secret, and the two network-policy additions),
  `docs/design/17-*` (facade counts 85/112 and the two new exclusions),
  `README.md` if it lists the platform tools or building blocks.
  No implementer review needed beyond a sonnet doc reviewer checking every
  claim against the code (paths, names, counts, settings).
  Acceptance: every path/name/count in the docs exists in the tree; the
  Help page lists Quota (the API serves `docs/building-blocks/*` directly).

- [ ] **T7 Deploy and live verification.** (all ACs) `[after T6]`
  The orchestrator does this task itself with the Terminal.app mechanics
  from protocol step 9 (no implementer): build and import backend, web,
  broker, facade images (the proxy is a stock image; only its ConfigMap
  and Secret change); append `env.AP_INTERNAL_SECRET` to the stored values
  file; `helm upgrade` with stored values + the pai overlay; confirm the
  claude-proxy pod rolled (new ConfigMap checksum) and `kubectl get pods`
  all Running; the topics job created `quota.events`. Then, through the
  forward, record in "Live verification" below:
  1. `GET /api/quota` right after deploy (expect nulls/stale, or a value
     if a run has already happened) with its timestamp.
  2. Trigger any cheap run (Run Now on an existing job, or `@pai` in
     Relay) and show `GET /api/quota` afterwards with `source: proxy` and
     an `observed_at` inside the run's window: AC-1 proven by the passive
     path. Quote the proxy pod's log line for the fetch, and confirm the
     API log shows the internal POST (200).
  3. In Relay, ask `@pai what is our quota usage right now?` and quote the
     agent's reply with the two windows: AC-3 proven; then `GET /api/quota`
     shows `source: refresh` and the `probe` field the refresh returned.
     Record which probe step answered and what the raw header values
     looked like (`raw`), so the design's AS BUILT can say fraction vs
     percent and epoch vs ISO.
  4. Load the web app through the forward with Playwright (a copy of
     `t21-shot.mjs`), screenshot the sidebar at 1280×800 and 390×844 in
     both themes, and confirm from the API log that a page load against a
     stale snapshot issued exactly one refresh (make the snapshot stale by
     waiting for a reset if one is close, or by asserting the code path
     with the network tab: the POST fires when `stale` is true — if no
     stale state is reachable live, say so and rely on T5's test for
     AC-4).
  5. Compare the platform's percentages with Claude Code's status line on
     Kyle's laptop at the same minute (the HUD screenshot in this session
     showed 22% / 81% at 15:0x local; read the current one from a fresh
     `claude` status line if reachable, else note the platform's values and
     leave the comparison to Kyle in "Handoff").
  Then set the design's Status to shipped with the helm revision, fill
  the AS BUILT line for the probe, and commit the docs.
  Acceptance: the five items recorded with commands, timestamps and
  screenshot paths; the "Definition of done" passes.

### Repairs

_(added by the loop when the definition of done fails)_

### Deferred

_(low/medium findings the loop chose not to fix, with file:line)_

## Definition of done

All T1–T7 are `[x]`; `cd services/backend && .venv/bin/python -m pytest -q`
is green; `cd services/mcp-broker && ../backend/.venv/bin/python -m pytest -q`
is green; the facade suite is green under the 3.12 venv; `cd
services/claude-proxy && ../backend/.venv/bin/python -m pytest -q` is green
with the docker test actually executed; `cd services/web && npm run -s lint
&& npm run -s check:tokens && npm run -s build && npx playwright test` is
green; the NUC runs the new images and the rolled proxy (`kubectl get pods`
all Running, `GET /api/quota` answers through the forward with
`source: proxy` after a run and `source: refresh` after the tool call);
"Live verification" below records items 1–5 with the sidebar screenshots.

## Live verification

_(filled by T7)_

## Handoff to Kyle

_(commands the loop could not run itself, and anything only Kyle can check)_
