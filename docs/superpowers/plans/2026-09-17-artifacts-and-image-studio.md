# Plan — Artifacts, image generation, and the Image Studio (design 23)

Design: `docs/design/23-artifacts-and-image-studio.md` (read it first; the
sections named in each task are pasted into that task's implementer prompt).
Predecessors built the same way: `2026-09-13-wiki-shared-knowledge.md` and
`2026-09-14-quota-usage-bars.md` (their "Live verification" and "Handoff"
sections show the deploy mechanics that worked).

Kyle's ask, verbatim: (1) "Artifacts: files that agents are able to save…
probably just blobs in postgres"; (2) "an Artist agent… the real work is
generate_image skill(s) in the platform… Gemini, openai, bfl… and
associated secrets"; (3) "agent images: profile pics… a nice optional grid
view as the new default (toggle back to old option)… upload an image (saves
as an artifact) for the agent, or generate one… with a prompt"; (4) "a
deterministic image studio app… pick the model that is set up correctly,
write a prompt, and generate it… browse image artifacts… pass an image into
the query… very basic markup abilities… Delight me." Plus: "mid-build you're
going to need the secrets added to the site, open my browser to where I paste
them in and I'll save them, keep working in the meantime."

Acceptance criteria (each task names the ones it satisfies):

- **AC-1** An agent saves a file and gets it back; a human uploads a file; both
  appear on `/artifacts` with provenance; bytes are served safely (nosniff,
  inline only for raster images); caps and soft delete work.
- **AC-2** `POST /api/artifacts/generate` produces an image artifact from each
  configured provider (OpenAI, Gemini, BFL) with cost recorded, a `#art` card,
  and an `artifacts.events` envelope; budget and daily cap refuse loudly.
- **AC-3** `@artist` in Relay answers a brief with a `[[artifact:<id>]]` card
  that renders as an image, and can iterate with the previous result as the
  reference.
- **AC-4** An agent has a profile image (uploaded, chosen, or generated from a
  prompt); every `Face` shows it; `/agents` is a card grid by default with a
  remembered toggle back to the table.
- **AC-5** `/studio` lists only configured models, generates, shows provenance,
  accepts a reference image, sets an agent image from a result, iterates, and
  saves a marked-up (pen/arrow/rect/text/crop) derived artifact.
- **AC-6** Deployed on pai (helm rev recorded), all suites green, live evidence
  recorded below.

## Loop protocol (read this every tick, follow it exactly)

You are the **orchestrator**. You do not write product code yourself. You
dispatch subagents, verify their evidence, commit, and update this file.

1. Re-read this plan top to bottom. Find the first `- [ ]` task in "Tasks".
   If there is none, run "Definition of done"; if it passes, stop the loop
   (ScheduleWakeup `stop: true`) after a PushNotification with the one-line
   outcome; if it fails, add a task under "Tasks → Repairs" and continue.
2. Dispatch **one opus implementer** (Agent tool, `model: "opus"`,
   `subagent_type: "general-purpose"`) with: the task text verbatim, the
   "Ground rules for implementers" block below verbatim, the design sections
   the task names pasted in full (not linked — the subagent has no
   conversation context), and the file paths from the task. Require it to
   work TDD: failing test → implementation → green, and to report the exact
   test commands it ran with their final summary lines. Tasks marked
   `[parallel with Tn]` may be dispatched in the same tick as that task, one
   implementer each; tell each implementer which paths the other owns and
   that it must not touch them. Tasks marked `[after Tn reports]` start the
   moment Tn's implementer reports (before Tn's review finishes), because
   they build on Tn's files.
3. When it reports, **verify the evidence yourself**: run the test commands
   it named (backend: `cd services/backend && .venv/bin/python -m pytest -q
   <paths>`; web: `cd services/web && npm run -s lint && npm run -s
   check:tokens && npm run -s build && npx playwright test --reporter=line`;
   broker: `cd services/mcp-broker && ../backend/.venv/bin/python -m pytest
   -q`; executor: `cd services/tool-executor && ../backend/.venv/bin/python
   -m pytest -q` — the `test_env_minimalism_canary` FAILS ON macOS because
   Apple's `/usr/bin/python3` shim injects `CPATH`/`SDKROOT`/`LIBRARY_PATH`/
   `MANPATH`/`__CF_USER_TEXT_ENCODING`; that one failure is a host artifact,
   CI runs it on Linux — every other executor test must pass; tools: `cd
   tools/image_gen && ../../services/backend/.venv/bin/python -m pytest -q
   test_run.py`). Do not trust a claim of green without output in your own
   transcript. Run git and every repo-relative command from the repo root,
   never from a `cd` that persisted (a diff saved from the wrong directory is
   empty).
4. Dispatch **one sonnet reviewer** (`model: "sonnet"`) with the task text,
   the design excerpt, and the path of a saved `git diff` of the uncommitted
   changes. Ask for findings ranked by severity, defects only, no style — and
   for every guard, limit, permission check, migration, byte-handling path or
   path built from model text, ask it to state the WORST CASE (a 200 MB
   upload, a PNG that is really HTML, an SVG served inline, a decompression
   bomb, an agent saving as another owner, a prompt with `[[` and markdown
   in it entering a `#art` card, a reference id the caller cannot read, a
   provider key leaking into a log or a tool result, two concurrent
   generations racing the daily cap, a second `init_db` on a live DB). For
   tasks tagged `[ui]`, dispatch additionally a **sonnet visual reviewer**
   that builds the app, serves it against the Playwright mock API
   (`tests/mock-api.ts`), screenshots the affected page(s) at 1280×800 and
   390×844 in both themes with a throwaway spec it deletes afterwards, READS
   the PNGs, and reports what a picky human would notice. Never use
   `model: "fable"` or the default model for subagents.
5. Findings of severity high or critical go back to the same implementer via
   SendMessage (it keeps its context). Low/medium: fix if cheap, otherwise
   note under "Deferred" with file:line and move on. Loop at most twice per
   task; if a task still fails after two repair rounds, mark it `- [!]` with a
   one-paragraph note and continue to the next task — do not stall the build.
6. **Hold every commit until no implementer is editing the tree**: the
   pre-commit hook stashes and restores unstaged tracked files and would race
   a live editor. Commit on `main` (Kyle's workflow: single branch, direct
   commits, `git add` each file by name, never `-A`; never add files that may
   hold secrets — `exports.sh`, anything under `secrets/*/` other than
   `secret.yaml`/`verify_*.py`). Message: `feat(artifacts): <task title>`
   plus a body, and the attribution trailer the session was given. Then edit
   this file: `- [x] **Tn …** (commit `<hash>`; <one line of what review
   changed>)`. Two `[ui]` tasks that share App.tsx/app.css/mock-api land as
   one commit.
7. Schedule the next wakeup with `delaySeconds: 60`, `noop: false`, and the
   sentinel prompt the loop skill prescribes. One task (or one parallel set)
   per tick keeps each turn's context small.
8. Recovery. A quota 429 kills a subagent instantly; it keeps its transcript:
   after the reset, SendMessage "quota is restored; check git diff for what
   landed, then resume from where you stopped" — if that fails, relaunch with
   the same prompt. A "stalled: no progress" agent likewise. Terminal.app
   `do script` sometimes DROPS THE FIRST CHARACTER of the command: always
   prefix with `true; true; ` and use absolute paths, and read the `.out`
   file within 30 s of launching. Terminal.app stops executing after ~20
   stale windows: quit and relaunch it. A Playwright spec that fails only
   under the full suite or under host load: rerun that file alone before
   calling it red. Under host load (Kyle's game and the Rancher VM: load
   average > 10) the full backend suite takes 15–20 min instead of 3; do
   NOT kill a long run — `pytest-timeout` is installed in the venv, so run
   with `--timeout=120` and a true hang fails by test name. If sonnet
   subagents stall repeatedly ("no progress for 600s" at launch, the
   classifier reporting sonnet unavailable), the endpoint is degraded: run
   that one review on opus rather than losing hours — note it in the tick.
9. If a Bash action is refused by the auto-mode classifier (kubectl
   apply/delete, helm upgrade), do not retry variants. Write the exact
   commands into "Handoff to Kyle" below, send a PushNotification saying the
   build is blocked on a deploy step, and continue with any task that does not
   depend on it. Reaching the NUC works via Terminal.app:
   `osascript -e 'tell application "Terminal" to do script "true; true; <cmd> > <scratchpad>/x.out 2>&1; echo EXIT=$? >> <scratchpad>/x.out; exit"'`
   then wait for `EXIT=` in the file (plain `ssh pai` from this process gets
   "No route to host" — macOS Local Network permission, see memory
   `claude-code-local-network-tcc-gotcha`). The API is reachable from this
   process only through `ssh -f -N -L 18090:localhost:8090 pai` (started the
   same way; it dies when the ssh session drops — check `curl
   localhost:18090/login` before each live step) at `http://localhost:18090`;
   Playwright screenshots of the live site go through that forward too. The
   deploy script from the Wiki build is the reference (this session's
   scratchpad `t21-deploy.sh`: buildx `--platform linux/amd64
   --provenance=false --load`, `docker save` → `scp` → `sudo k3s ctr -n
   k8s.io images import`, `helm upgrade ap charts/agent-platform -n
   agent-platform -f <stored values> -f charts/agent-platform/values-pai-nuc.yaml`
   — fetch the stored values fresh with `helm get values ap -n agent-platform
   > <scratchpad>/ap-stored-values.yaml`, never `--reuse-values` — then
   rollout restart api/dispatcher/recorder/web/broker/tool-executor, facade
   last). **This build adds the `tool-executor` image** (built from the REPO
   ROOT: `-f services/tool-executor/Dockerfile .`, because it bakes
   `tools/*/requirements.txt`) and the api pod grows a sidecar, so the api
   rolls on the helm upgrade.
10. **The secrets hand-off (T1).** Secret declarations reach the site only
    through the sync of `main` from GitHub. After T1's commit: `git push
    origin main` (this also pushes the earlier unpushed commits on main —
    Kyle's branch, Kyle's workflow), then poll `GET /api/secrets` through the
    forward (log in first: `POST /api/login` with the password from
    `exports.sh` `AGENT_PLATFORM_ADMIN`; the sync runs every few minutes)
    until `openai-api-key`, `gemini-api-key`, `bfl-api-key` are listed as
    `missing`. Then run `open "http://pai:8090/secrets"` (Kyle's browser, on
    the LAN, reaches pai directly) and send a PushNotification: "Paste the
    three image-gen keys at http://pai:8090/secrets (OpenAI, Gemini, BFL —
    they are in ~/gh/claude-ttrpg/exports.sh); the build continues
    meanwhile." Do NOT read or paste the key values yourself. Continue with
    T2. Do not push again until T15 (the executor's `internal:` field is
    unknown to the live registry until the backend image ships).

### Ground rules for implementers (paste verbatim into every implementer prompt)

- Repo: `/Users/kp/gh/agent-platform`. Backend is FastAPI + SQLAlchemy async
  in `services/backend/agentplatform` (tests in `services/backend/tests`,
  run with `cd services/backend && .venv/bin/python -m pytest -q`). Web is
  React 19 + Vite + `@ap/ui` (`packages/ui`) in `services/web`
  (`npm run -s lint`, `npm run -s check:tokens`, `npm run -s build`,
  `npx playwright test`). The MCP broker is `services/mcp-broker/broker.py`
  (tests `cd services/mcp-broker && ../backend/.venv/bin/python -m pytest -q`).
  The tool-executor is `services/tool-executor/executor.py` (tests in the
  same dir with the backend venv; its env-minimalism canary fails on macOS
  because of Apple's python3 shim — extend its allow-list for new keys and
  make everything else green). Prod Python is 3.12 (dev venv is 3.14): no
  3.13+/3.14-only syntax, no annotation tricks that only pass locally. New
  backend deps go in `services/backend/pyproject.toml` `dependencies`
  (Pillow, python-multipart are already installed in the venv).
- TDD: write the failing test first, show it fail, make it pass, keep the
  suite green. Backend tests use the `admin_client` / `sf` fixtures in
  `tests/conftest.py` and sqlite; keep every new table portable to sqlite
  (JSON columns, `LargeBinary` for bytes, no Postgres-only DDL outside
  `dialect == "postgresql"` guards, as `db.py:_ensure_wiki_ddl` does).
- Migrations are additive and live in `db.py`: `Base.metadata.create_all`
  makes new tables, `_ensure_columns` adds new columns to existing ones (it
  cannot apply an ORM default to existing rows — backfill explicitly), and
  one-off seeds/backfills go in a new `_ensure_*` function called from
  `init_db`, idempotent, gated by a `schema_marks` row (`_ensure_wiki_seed`
  and `_grant_to_every_agent` with `QUOTA_GRANT_MARK` are the two shapes:
  seed, and grant-sweep).
- Relay is the substrate. A `#art` card, a budget notice, any system row:
  written through `relay_store.post_relay_message` + `publish_relay_message`
  (or `summon_channel` for a scheduled post) — never a bare insert, never a
  second Kafka producer; cards carry `mentions=[]`. `ticket_store.py` /
  `wiki_store.py` are the reference for "the ONE place a thing changes": one
  commit per public call, then publish (system row, then the domain event),
  the actor↔run invariant (`api/tickets.py::_run_of`: an agent token must
  carry its own run, else 403), `TicketRuleError`/`TicketBudgetError`-style
  exception shapes mapped to 4xx in the route.
- Kafka: topics are constants in `agentplatform/events.py` (`ALL_TOPICS` must
  include them) AND `charts/agent-platform/values.yaml` `topics.specs`
  (retentionMs quoted as a string). Use `make_envelope` / `Producer` /
  `FakeProducer` / `consume_forever` from `events.py`; never a bare
  aiokafka client. Tests inject `FakeProducer`; nothing in a test may open a
  real Kafka connection (see how `test_wiki_api.py` injects the feed). An SSE
  feed follows `api/wiki.py`'s feed + `wiki_events_consumer_factory` in
  `api/app.py` / `api_main.py`.
- Roles: `api/auth.py` `ROLES`/`READ_ROLES`/`INVOKE_ROLES`; the per-run
  participant role (`relay`) and `require_relay_access` in `api/relay.py` are
  the pattern for "as the agent"; `agentspec.PLATFORM_MCP_RELAY_TOOLS` is the
  grant list that yields it (add the `artifacts` and `image_gen` grants there,
  never to `PLATFORM_MCP_TOOLS`); every grantable tool needs a `TOOL_HELP`
  entry (the lockstep test enforces broker ↔ agentspec agreement). Every
  untrusted string an agent could plant (names, tags, prompts) is escaped
  where it enters a prompt or tool result and flattened/capped where it
  enters a system row (`tickets.one_line`).
- Bytes: never trust a client mime — sniff magic bytes; Pillow only for
  images with `Image.MAX_IMAGE_PIXELS` set; never log or echo bytes or keys;
  every byte-serving response carries `X-Content-Type-Options: nosniff`.
- Follow the file's existing comment style: comments explain *why*, not what;
  no narration, no "added by" notes, no TODOs without an owner.
- Web pages: register the route in `services/web/src/App.tsx`, the nav in
  `packages/ui/src/sidenav.tsx` `buildPlatformNav`, add fixtures to
  `services/web/tests/mock-api.ts` (unmatched GETs fail tests on purpose;
  multipart POSTs must be matched by path before the JSON default because
  `postDataJSON()` throws on multipart), a row in `tests/smoke.spec.ts`
  `PAGES`, and the page in `tests/a11y.spec.ts`'s lists (desktop and the
  390 list). Use `@ap/ui` subpath imports like `Tickets.tsx` does; reuse
  `components/relay/Face.tsx`, `components/relay/Message.tsx`'s chip rewrite
  (extend it for `[[artifact:id]]`, never a second rewriter), the markdown
  renderer in `packages/ui/src/markdown.tsx`, the dialogs in
  `@ap/ui/dialog`, `lib/title.ts` for the tab title. The `api()` wrapper in
  `src/api.ts` hardcodes JSON headers: for multipart pass `headers: {}` so
  the browser sets the boundary, and never call it for 204s (it parses
  JSON). No raw hex colours (per-identity colour is `hsl(var(--face-hue) …)`;
  markup colours come from `var(--ds-chart-N)`). Mobile (<560px) must not
  break the shell; never `display: contents` on a landmark; the a11y spec
  runs at 390 too. `.sr-only` exists in app.css. Card grids copy
  `.report-type-grid` / `.app-card` in `app.css`.
- Never widen scope, never commit, never push; report the exact test
  commands you ran with their final summary lines and the list of files you
  touched.

## Tasks

### Phase 0 — the secrets first, so Kyle can paste keys while the rest is built

- [x] **T1 Secret declarations for the three providers.** (AC-2 precondition) (commit `467e327`; review moved the Gemini key from `?key=` to the `x-goog-api-key` header and made the BFL script fail closed on 5xx/429)
  Design sections: "`tools/image_gen` — the port" (the last paragraph on
  secret blocks), "Trust boundaries and guards" (Keys).
  Files: new `secrets/openai-api-key/secret.yaml`,
  `secrets/gemini-api-key/secret.yaml`, `secrets/bfl-api-key/secret.yaml` +
  `secrets/bfl-api-key/verify_bfl.py`; `docs/building-blocks/secrets.md` if
  it lists the declared blocks. Follow `secrets/linear-api-key/secret.yaml`
  (shape) and `secrets/github-token/secret.yaml` (probe). Hints must tell
  Kyle where each key comes from (platform.openai.com → API keys;
  aistudio.google.com → Get API key; dashboard.bfl.ai → API keys). The BFL
  script mirrors `secrets/strava/verify_strava.py`'s contract (only that
  block's keys in env, exit 0 = valid, one stdout line): GET
  `https://api.bfl.ai/v1/get_result?id=00000000-0000-0000-0000-000000000000`
  with `x-key`; 401/403 → invalid; 404/422/200 → valid ("key accepted").
  None `required: true`. Tests: `services/backend/tests/test_secretregistry*`
  style — the three specs load, the BFL script maps statuses (monkeypatch
  urllib). Acceptance: `SecretRegistry` loads all three without error; the
  probe URLs interpolate; the script's status mapping is tested.
  After commit: protocol step 10 (push, poll, `open`, PushNotification).

### Phase 1 — the block's backend (T2 ∥ T4; T3 after T2 reports)

- [x] **T2 Artifact store, tables and API.** `[parallel with T4]` (AC-1) (commit `ef00b81`; review added the PATCH ownership check, a pre-parse body bound (direct callers bypass nginx), LIKE escaping, Pillow off the loop)
  Design sections: "Data model", "Trust boundaries and guards", "API"
  (everything except `generate`, `models`, `events`, `stats`' spend fields,
  and the agent image route), "Naming", "Kafka" (the constant and the
  publish hook only).
  Files: `db.py` (`Artifact`, `ArtifactBlob`, indexes; a pg-only
  `_ensure_artifacts_ddl` if any), new `artifact_store.py` (create from
  bytes: sniff → kind/mime/size/sha256/width/height/thumb; list with
  filters and keyset paging on `created_at,id`; get; content; patch; soft
  delete; `usage()` for count/bytes; the caps as settings in `config.py`:
  `artifacts_max_bytes` 8 MiB, `artifacts_total_max_bytes` 2 GiB; store
  raises `ArtifactRuleError(status, message)`), new `api/artifacts.py`
  (routes in the design's table minus the three named above; multipart via
  `UploadFile` AND the JSON shape; owner from the token — user principal or
  `agent:<name>` with `_run_of`; READ_ROLES read; delete = owner /
  `agents_edit` holder / admin), `api/app.py` router registration (Edit,
  never rewrite — T3 and T4 edit neighbouring files concurrently),
  `events.py` (`TOPIC_ARTIFACTS_EVENTS = "artifacts.events"`, in
  `ALL_TOPICS`), `charts/agent-platform/values.yaml` (`{name:
  artifacts.events, partitions: 3, retentionMs: "2592000000"}`),
  `artifact_store.publish_artifact_event(producer, *, event: str,
  artifact: dict, agent: str | None = None)` — best-effort post-commit
  publish in the `wiki_store._finish` shape, type `artifacts.event`, called
  for `created` and `deleted`; `GET /api/artifacts/stats` with `count`,
  `bytes`, `total_cap` (T6 adds the spend fields),
  `pyproject.toml` (Pillow, python-multipart), `services/web/nginx.conf`
  (`client_max_body_size 16m` on `/api/`; keep the `/mcp` blocks as they
  are). Serving exactly per "Trust boundaries": nosniff, immutable cache,
  inline only for png/jpeg/webp/gif, `attachment` otherwise (a filename
  flattened to `[A-Za-z0-9._ -]`), `/thumb` 404 for non-images. Thumb: ≤ 512
  px longest side, JPEG q80 (PNG when alpha), ≤ 150 KiB; the store
  records `width/height` from Pillow. `Image.MAX_IMAGE_PIXELS = 50_000_000`
  → a bomb is a 413. A claimed text mime (`text/plain|markdown|csv`,
  `application/json`) is accepted only when the bytes decode as UTF-8.
  Tests (`tests/test_artifacts_api.py`, `tests/test_artifact_store.py`): a
  PNG upload gets kind image, dims, thumb, sha256; an HTML file claiming
  `image/png` is stored as octet-stream and served as attachment; SVG is
  attachment; 8 MiB + 1 → 413; total cap → 507; an agent token with no run →
  403; owner is the token's participant not the body's; soft delete hides
  from list and content 404s; paging; patch name/tags with the caps;
  headers on `/content` and `/thumb`; a 1×1 PNG and a 40 MP-claimed PNG
  header (bomb) → 413. Acceptance: all routes in the table exist with those
  semantics; sqlite suite green; `nginx.conf` change present.

- [x] **T3 Grants, feed, prune.** `[after T2 reports; parallel with T4]` (AC-1) (commit `ef00b81`; also DEFAULT_GRANTS + `artifacts` knob on create, `help.py` hides internal tools, SDK regenerated in python:3.12-slim — the CI-equivalent; a 3.12 uv venv produced a different generator form)
  Design sections: "Data model" (Seeds — the grant sweep), "Kafka", "Broker
  tools" (the grant lists only), "API" (`events`). T2 already owns the topic
  constant, the values.yaml spec, `publish_artifact_event` and `stats`;
  build on its uncommitted files, do not rewrite them.
  Files: `agentspec.py` (`TOOL_ARTIFACTS = "mcp__platform__artifacts"`,
  `TOOL_IMAGE_GEN = "mcp__platform__image_gen"`, both appended to
  `PLATFORM_MCP_RELAY_TOOLS`, `TOOL_HELP` entries with display names
  "Artifacts" and "Image generation"), `db.py`
  (`ARTIFACTS_GRANT_MARK = "artifacts-default-grant-v1"` sweep via
  `_grant_to_every_agent`; the wiki-agent seed's grant list gains
  `TOOL_ARTIFACTS`), the `events` SSE route in a separate module
  `api/artifacts_feed.py` mounted under the same prefix (T2 owns
  `api/artifacts.py`; do not edit it), `api/app.py` (Edit only) +
  `api_main.py` (`artifacts_events_consumer_factory`, the wiki shape),
  `pruning.py` (an `ArtifactPruner` deleting rows with `deleted_at <
  now - artifacts_prune_days`, run beside the transcript pruner in
  `dispatcher_main.py`). Tests: the sweep grants `artifacts` to every agent
  once and appends a `migration` version; SSE replays a created event injected
  through `FakeProducer`; the pruner deletes only expired soft-deleted rows;
  the TOOL_HELP lockstep test passes with the two new names (the broker
  side lands in T7 — if the lockstep test compares against `broker.py`,
  add the two names to the broker's allow-list constant now with a
  placeholder tool that T7 replaces, and say so in the report).
  Acceptance: suites green; `ALL_TOPICS` and `values.yaml` agree.

- [x] **T4 Executor file sink, `internal` tools, timeout ceiling, chart wiring.**
  `[parallel with T2]` (AC-2 precondition) (commit `b624dbd`; review found a symlinked sidecar read, pre-decode b64 cap, killpg — a forked grandchild hung proc.wait() —, broker timeout clamp, internal tools excluded from mcp_names, tunnel startupProbe)
  Design sections: "The executor's file sink".
  Files: `services/tool-executor/executor.py` (per-call `tempfile.mkdtemp`
  under `/tmp` with `in/` and `out/`; `TOOL_IN_DIR`/`TOOL_OUT_DIR` in
  `build_env`; `/run` body gains `files_in: [{name, mime, b64}]` ≤ 4 × 8
  MiB, names flattened to a basename; after exit collect `out/*` ≤ 8 files
  × 8 MiB (bigger → that file skipped and an `error`-free note in
  `warnings`), sniff mime by magic bytes, merge and delete a
  `<name>.meta.json` sidecar into `meta`; response gains `files:
  [{name, mime, b64, meta}]`; the temp dir is removed in `finally`; timeout
  ceiling `min(manifest.timeout_seconds, 300)`), `test_executor.py` (canary
  allow-list gains the two dirs; new tests: files in are readable by the
  tool, files out come back with sniffed mime and merged sidecar, caps,
  cleanup, a tool that writes nothing returns `files: []`),
  `services/backend/agentplatform/toolregistry.py` (`internal: bool =
  False`; `timeout_seconds` 1..300) + its test, `services/mcp-broker/
  broker.py` (`_scan_custom_tools` skips `internal: true`; `CustomTool`
  httpx timeout = manifest timeout + 30) + a broker test that an internal
  tool is not registered, `charts/agent-platform/templates/
  networkpolicy.yaml` (`allow-tool-executor` ingress adds component `api`),
  `charts/agent-platform/templates/api.yaml` (env `AP_EXECUTOR_URL`:
  `http://127.0.0.1:8301` under `spire.enabled`, else
  `http://{{ .Release.Name }}-tool-executor:8000` — check the broker's
  template for the exact service name; under `spire.enabled` an
  `executor-tunnel` ghostunnel client sidecar copied from
  `mcp-broker.yaml` with the `spiffe-workload-api` CSI volume),
  `charts/agent-platform/templates/tool-executor.yaml` (mtls-server
  `--allow-uri` gains `sa/{{ .Release.Name }}-api` — ghostunnel accepts
  repeated `--allow-uri`), `docs/building-blocks/tools.md` (the sink, the
  `internal` flag, the 300 s ceiling — three short paragraphs). `helm
  template charts/agent-platform -f charts/agent-platform/values-pai-nuc.yaml
  --set spire.enabled=true` must render (run it; the chart's lint test if
  one exists). Acceptance: executor tests (minus the macOS canary) green;
  registry + broker tests green; chart renders in both spire modes.

### Phase 2 — generation (T5 alone, then T6; T7 and T8 parallel after T6 reports)

- [x] **T5 `tools/image_gen`: the port.** `[after T4 reports]` (AC-2) (commit `05adb09`; sidecar is `image.<ext>.meta.json`; BFL ids are the real paths `flux-kontext-pro/max`, `flux-pro-1.1`; review added no-redirect opener, polling-host check, a single 165 s budget, read cap, clean catch-all; a quota 429 killed the implementer once — resumed with context)
  Design sections: "`tools/image_gen` — the port", "Trust boundaries and
  guards" (Keys, Budget — the tool does no budgeting; it only reports cost).
  Source to port: `/Users/kp/gh/claude-ttrpg/tools/imagegen.py` and its
  `test_imagegen.py` (read both fully; stdlib `urllib` only; keep
  `_request`'s status mapping, `parse_size`/`nearest_aspect`, the model
  registry idea, `call_openai`/`call_gemini`/`call_bfl`; drop the CLI,
  ledger, env-file loading and Imagen). Before writing `models.json`, verify
  the current model ids and request shapes with WebFetch against
  `https://developers.openai.com/api/docs/guides/image-generation` (models
  `gpt-image-2.5-flare`, `gpt-image-2.5-sunburst`, `gpt-image-2`,
  `gpt-image-1-mini`; `/v1/images/edits` multipart `image[]`,
  `input_fidelity`), `https://ai.google.dev/gemini-api/docs/image-generation`
  (`gemini-3.1-flash-image`, `gemini-3.1-flash-lite-image`,
  `gemini-3-pro-image`, `gemini-2.5-flash-image`; keep the
  `generateContent` + `responseModalities` shape Kyle's code uses — it is
  the one proven with his key — and add a reference as an `inlineData`
  part), and `https://docs.bfl.ml/llms.txt` → the FLUX.2 text-to-image and
  image-editing pages (`flux-2-pro`, `flux-2-max`, `flux-2-flex`,
  `flux-2-klein-9b`, `flux-2-klein-4b`, `flux-1-kontext-pro`,
  `flux-1-kontext-max`, `flux-1.1-pro`; `input_image` base64 for edits;
  `polling_url`; `result.sample`). Record in the report which ids/fields
  you confirmed and which you could not (leave those entries out rather
  than guess).
  Files: new `tools/image_gen/tool.yaml` (`internal: true`; params per the
  design; `infra.secrets: [openai-api-key, gemini-api-key, bfl-api-key]`;
  `timeout_seconds: 180`), `run.py`, `models.json`, `requirements.txt`
  (empty file, stdlib), `test_run.py` (monkeypatched `urllib` for every
  provider path incl. edits, the size↔aspect bridge, the error mapping,
  the sink files + sidecar, the `models` action's `configured` flags, a
  moderation refusal → clean exit 1 message). `run.py` writes
  `TOOL_OUT_DIR/image.<ext>` + `image.meta.json` `{provider, model, seed,
  cost_usd, duration_ms, params}` and prints one JSON line
  `{"ok": true, "model": …, "cost_usd": …}`; references are read from
  `TOOL_IN_DIR` by the names in `args.references`. A missing provider key →
  exit 1 "provider <x> is not configured (add the <block> secret)".
  Also `docs/building-blocks/tools.md` gets one line naming `image_gen` as
  the first internal tool. Acceptance: `cd tools/image_gen && ../../services/
  backend/.venv/bin/python -m pytest -q test_run.py` green; `ToolRegistry`
  loads it (a backend test in `tests/test_toolregistry*` that scans the real
  `tools/` dir passes with `internal: true`); the CI `tools` job loop picks it
  up unchanged.

- [x] **T6 `POST /api/artifacts/generate`, `GET /api/artifacts/models`, budget, `#art`.**
  `[after T2 and T3 are committed and T5 reports]` (AC-2) (commit `f978d32`; review: card kind=event (text rows get their @mentions re-parsed by the router), prompt cap before spend, reservation ledger for the concurrent-cap race, image_gen grant required on the route, `local_timezone` setting added)
  Design sections: "API" (`generate`, `models`, `stats` spend fields), "Trust
  boundaries and guards" (Budget and spend, Third parties, Untrusted text),
  "Relay" (the `#art` card text), "Data model" (Seeds — `#art`; settings
  `image_gen_agent_per_hour`, `image_gen_daily_usd`).
  Files: new `image_gen_service.py` (the ONE place: budget → daily cap →
  reference fetch through the store with the caller's scope, images only,
  ≤ 4 → `POST {AP_EXECUTOR_URL}/run` via httpx with timeout
  `manifest.timeout_seconds + 30`, body `{tool: "image_gen", args, files_in,
  caller: {agent, run_id}}` → first returned file → `artifact_store.create`
  with `source: generated`, `meta` = sidecar + `prompt`, `params`,
  `reference_ids`, `tool: "image_gen"` → `#art` card via
  `relay_store.post_relay_message` + publish (`[[artifact:<id>]]` newline
  `by <owner> · <model> · "<prompt flattened ≤ 120>"`, `mentions=[]`) →
  event; executor `ok: false` → `ImageGenError(502, message)`), `api/
  artifacts.py` (`generate` and `models` routes; `models` = registry from
  `tools/image_gen/models.json` read through `st.tool_registry` × the
  secret status from `secrets_meta`/the k8s store — `configured` true when
  the block's status is `valid` or `unprobed`; unknown model → 422),
  `config.py` (the two settings + `executor_url`), `db.py`
  (`ART_CHANNEL_MARK` seed of the `#art` open channel with topic "every
  generated image, as a card" and a welcome row "Summon @artist with a
  brief, or make images yourself in the Studio" — the artist row itself is
  T9), `stats` gains `generated_this_month`, `spend_this_month_usd`,
  `spend_today_usd`, `daily_cap_usd`. The budget counts `source=generated`
  rows by owner in the last hour (agents only) → 429 with the wait; the
  daily cap sums `meta.cost_usd` since local midnight (settings timezone
  as the scheduler uses) → 402, and one system row per day in `#art`
  (mark the day in `schema_marks` or a `#art` lookup — the tickets budget
  notice pattern). Tests (`tests/test_image_gen.py` with a fake executor
  via a monkeypatched `httpx.AsyncClient`, the `test_apps.py:133` shape, and a `FakeProducer`): happy path stores
  the file with meta and dims, posts the card, publishes the event; the
  card body flattens a hostile prompt (newlines, `[[`, `@artist`, 5 KB) to
  one line ≤ 120; a reference the caller cannot read → 404 before any
  executor call; 5 references → 422; budget → 429; daily cap → 402 and one
  notice; executor error text → 502 body verbatim; unknown model → 422;
  `models` reflects secret status. Acceptance: suite green; design's
  `generate` paragraph satisfied line by line.

- [x] **T7 Broker core tools `artifacts` and `image_gen`.** `[after T6 reports; parallel with T8]` (AC-1, AC-3) (commit `f2a1cfa`; review: generate waits 240 s (was the 20 s default — a double-spend path), byte/mime bounds on attached pictures, capped error bodies; facade KEEP 94 / 155 graded)
  Design sections: "Broker tools", "Relay" (the card syntax the tool
  results teach).
  Files: `services/mcp-broker/broker.py` (two core tools in the wiki shape:
  `artifacts` with actions `list/get/save/delete` — `get` returns text
  metadata plus `ImageContent` (`mcp.types.ImageContent`, the thumb fetched
  from `/api/artifacts/{id}/thumb` by default, the original when
  `full=true` and ≤ 1 MiB) — `save` accepts `text` or `content_b64` ≤ 256
  KiB and posts JSON to `/api/artifacts`; `image_gen` with `generate` →
  `POST /api/artifacts/generate` and `models` → `GET /api/artifacts/models`;
  every result ends with "reference it in Relay as `[[artifact:<id>]]`";
  `generate` also attaches the thumb as `ImageContent` so the artist sees
  its work; both tools forward the caller's token exactly as `wiki` does,
  never a broker credential; the audit row records sizes not bytes), the
  broker's allow-list/help constants, `services/mcp-facade` exclusion list
  (the `content` and `thumb` routes are excluded; note the new tier counts
  in the report for T14), `agentspec.TOOL_HELP` wording if T3's stub needs
  it. Tests (`services/mcp-broker/test_*.py` with the fake API the wiki
  tests use): each action's request shape and token forwarding; `get`
  yields a `ToolResult` whose content includes an `ImageContent` with the
  right mime; a 429/402/502 from the API becomes a plain error string the
  model can act on; `full=true` on a 2 MiB image falls back to the thumb
  with a note; the facade tier test with the new exclusions. Acceptance:
  broker + facade suites green; the lockstep test passes.

- [x] **T8 Agent image: column, route, faces.** `[after T6 reports; parallel with T7]` (AC-4) (commit `f978d32`; review (on opus — sonnet stalled 3×): delete/prune undress agents + publish clears, feed passes clears, grant parity on the self path, `token_client` into conftest)
  Design sections: "Agent images and the Agents page" (backend paragraphs),
  "API" (the `PUT /api/agents/{name}/image` line), "Kafka" (`agent_image`).
  Files: `db.py` (`AgentDef.image_artifact_id`, `_ensure_columns` picks it
  up; NOT in `DEF_FIELDS`), `api/agents.py` (`PUT /api/agents/{name}/image`
  `{artifact_id | null}`: artifact must exist, be `kind=image`, not deleted;
  admin, an `agents_edit` holder, or the agent itself (`_run_of` + name
  match); publishes `agent_image` on `artifacts.events`), `api/schemas.py`
  (`RelayFace.image_url: str | None`; `AgentSummary`/`AgentDefOut` gain
  `image_artifact_id` and `face`), `relay_store.faces_for` (fills
  `image_url = /api/artifacts/<id>/thumb`), `api/agents.py` list/get
  (attach `face`), `agentdefs.py` untouched (rollback must not clear the
  image — test it). Tests: set/clear/forbidden (a different agent's run →
  403; a non-image artifact → 422; a deleted artifact → 404); `faces_for`
  returns `image_url`; a version rollback keeps the image; the Relay
  channel view's `faces` carries it. Acceptance: suite green; the wiki/
  tickets/relay face tests unchanged.

- [x] **T9 The artist.** `[after T7 is committed]` (AC-3) (commit `52343c3`; review added a step 0 so the daily #standup @all never makes it generate)
  Design sections: "The artist", "Relay".
  Files: `db.py` (`ARTIST_SEED_MARK = "artist-seed-v1"`, `_ensure_artist_seed`
  in the `_ensure_wiki_seed` shape: name `artist`, `model: sonnet`,
  `system: false`, `can_invoke: false`, `platform_tools: [TOOL_IMAGE_GEN,
  TOOL_ARTIFACTS, TOOL_RELAY]`, description and prompt exactly as the design
  section specifies — write the prompt in full, with the three house styles
  quoted, the model guidance, the process, the never-claim rule and the
  budget rule; `changed_via="seed"`), `docs/building-blocks/agents.md` (a
  line in the seeded-agents list if one exists). Tests: the seed is
  idempotent (second `init_db` adds no version), the row has the grants and
  the prompt contains the never-claim sentence; the artist is summonable
  (`@artist` in a channel resolves to a wake — the relay summon test shape).
  Acceptance: suite green.

### Phase 3 — the web (T10 → T11 → T12 → T13, each starting when the previous reports)

- [x] **T10 `[ui]` Face images, artifact cards, the `/artifacts` page, nav.** (AC-1, AC-3, AC-4) (commit `68ce735`; review: one shared per-tab SSE feed + card-cache invalidation (a 404 was cached for the session), byte-URL scheme guard, nav label without the emoji, lightbox floor for small images)
  Design sections: "Relay", "Agent images and the Agents page" (the
  `Face.tsx` paragraph), "The Studio" (the `/artifacts` paragraph and the
  nav line), "Naming".
  Files: `services/web/src/api.ts` (`Artifact`, `ArtifactStats`,
  `ImageModel` types; `RelayFace.image_url`; `AgentSummary.image_artifact_id`
  + `face`; `listArtifacts/getArtifact/uploadArtifact(FormData)/patch/
  delete/artifactStats/artifactModels/generateArtifact/setAgentImage`
  helpers), `components/relay/Face.tsx` (`<img src={image_url}
  alt="" loading="lazy">` inside the disc, `object-fit: cover`, emoji
  fallback on `onError`; keep `.relay-face` and `aria-hidden`), new
  `components/artifacts/{ArtifactCard.tsx, ArtifactGrid.tsx,
  Lightbox.tsx, useArtifacts.ts (list + SSE upsert, the `useTickets`
  shape), Provenance.tsx}`, `components/relay/Message.tsx` (the chip
  rewrite gains `[[artifact:<32 hex>]]` → `<ArtifactCard>`; an unknown id
  → muted "artifact not found" chip; the card is NOT rewritten inside code
  spans or links — the existing guards), new `pages/Artifacts.tsx`
  (`/artifacts` and `/artifacts/:id`: thumb grid, filters kind/source/
  owner/tag/search in the URL, lightbox with provenance + delete (confirm)
  + "open in Studio" link, the total-bytes bar from `stats` against the
  cap, empty state "Nothing here yet — try the Studio"), `App.tsx` routes
  (`/artifacts`, `/artifacts/:id`, and a placeholder `/studio` route that
  T12 fills — a page with the heading "Studio" and "coming in the next
  task" so smoke passes now), `packages/ui/src/sidenav.tsx` (top-level
  `Studio 🎨` → `/studio` with child `Artifacts` → `/artifacts`, between
  Wiki and Reporting), `app.css` (`.artifact-grid` on the
  `.report-type-grid` pattern, `.artifact-card`, `.artifact-lightbox`, the
  `.relay-face img` rule), `tests/mock-api.ts` (fixtures: `/api/artifacts`
  list with 6 items incl. one file and one generated image with full meta,
  `/api/artifacts/<id>`, `/api/artifacts/stats`, `/api/artifacts/events`
  (a fulfilled empty SSE like the tickets feed), `/api/artifacts/<id>/thumb`
  and `/content` returning a real 1×1 PNG body with `image/png`, a face with
  `image_url` on the `news` agent), `tests/smoke.spec.ts` + `tests/a11y.spec.ts`
  (`/artifacts`, `/artifacts/<id>`, `/studio` in both lists), new
  `tests/artifacts.spec.ts` (grid renders thumbs and a file tile; filter
  in URL; lightbox opens on click and on `/artifacts/<id>`; delete confirm
  calls DELETE; the Relay page renders `[[artifact:<id>]]` as a card with
  an `<img>` and an unknown id as the muted chip; a `[[artifact:…]]` inside
  a code span is untouched; the `news` face renders an `<img>`), and the
  existing `relay.spec.ts` face-count assertion still passes.
  Acceptance: web suite green (lint, tokens, build, playwright); the visual
  reviewer's screenshots show the grid at 1280 and 390 in both themes with
  no overflow.

- [x] **T11 `[ui]` Agents card grid + Profile image section.** `[after T10 reports]` (AC-4) (commit `68ce735`; review: the section owns every write/error (a dialog closed mid-flight swallowed failures), drop zone gated against double-fire, client-side type/size checks, 422-array fold; visual: section divider, picker scroll fade, a real drop zone)
  Design sections: "Agent images and the Agents page" (all).
  Files: `pages/Agents.tsx` (a `ViewToggle` segmented control in the page
  header — two `<Button>`s with `aria-pressed`, labels "Grid" and "Table";
  `localStorage["agents.view"]`, default `grid`, wrapped in try/catch; grid
  = `<AgentCard>` per agent: `<Face size={96}>` (image or emoji), name as
  the link, description two-line clamp, the same status `Chip` logic as
  the table row (the smoke probe for "blocked" must still hit), the
  schedule/jobs line; system agents keep their own section; the table is
  the existing markup untouched), new `components/agents/{AgentCard.tsx,
  ProfileImage.tsx}`, `pages/AgentDetail.tsx` (header shows `<Face
  size={64}>`; the Config tab gets `<h2>Profile image</h2>` +
  `<ProfileImage>` BEFORE the Prompt section, outside the draft/PUT:
  current image or the emoji face; **Upload** = `<input type="file"
  accept="image/*">` behind a `<Button>` plus a drop zone using the tickets
  board's `dataTransfer` pattern with `e.dataTransfer.files[0]`; **Choose
  from artifacts** = `FormDialog` with `<ArtifactGrid kind="image">`;
  **Generate** = `FormDialog`: model `Select` of configured models
  (unconfigured disabled with "add key in Secrets"), prompt `Textarea`
  prefilled `Portrait of "<name>": <description>. flat, friendly avatar,
  square, centred, no text` (the house style the artist's prompt names),
  Generate → shimmer → preview → "Use as profile image"; **Remove**;
  every action calls `setAgentImage` then refetches the agent; errors via
  `Banner`), `app.css` (`.agent-grid`, `.agent-card`, `.profile-image`,
  `.drop-zone[data-over]`), `tests/mock-api.ts` (multipart `POST
  /api/artifacts` matched by path BEFORE the JSON default, returning a
  fixture artifact; `PUT /api/agents/*/image` → `{ok: true}`;
  `POST /api/artifacts/generate` → the generated fixture; `/api/artifacts/
  models` with two configured and one unconfigured provider), new
  `tests/agents-grid.spec.ts` (grid is default; toggle to table persists
  across reload; card shows the blocked chip for `news`; a card shows an
  `<img>` for the agent with a face image; on the detail page Upload sends
  multipart and then `PUT …/image` with the returned id; Generate shows the
  preview then sets the image; Remove sends `null`; a disabled model option
  is not selectable), `tests/smoke.spec.ts` if the `/agents` probe needs
  the grid's markup. Acceptance: web suite green; screenshots at 1280/390
  both themes: the grid wraps to one column at 390 without horizontal
  scroll; the segmented control is keyboard-operable.

- [x] **T12 `[ui]` The Studio.** `[after T11 reports]` (AC-5) (commit `68ce735`, committed with T10/T11 because it re-pointed ProfileImage at the shared studio helpers; code + visual review findings land as a follow-up commit)
  Design sections: "The Studio" (all but Markup), "API" (`generate`,
  `models`, `stats`), "Naming".
  Files: new `pages/Studio.tsx` (replaces T10's placeholder; the two-column
  layout from the design: Compose — model `Select` (configured only
  enabled; each option label `<label> · $<price>`; the design's default
  model preselected), prompt `Textarea` (⌘/Ctrl+Enter generates), size or
  aspect `Select` and quality `Select` driven by the chosen model's entry
  (hidden when the model has none), seed `Input` (optional), the reference
  strip (drop zone + "Pick from artifacts" dialog + the "Use result" chip
  — enabled only when the model has `edits`; ≤ 4; each shows a thumb with
  a remove ×), Generate `Button` with the price beside it and the elapsed
  seconds while pending, `StatRow`: images this month · spend this month ·
  spend today / cap; Stage — shimmer while pending; the result `<img>`
  from `/content` with `max-height: 70vh`; `<Provenance>`; actions Use as
  agent image (dialog with an agent `Select` from `/api/agents` →
  `setAgentImage`), Iterate (moves the result into the reference strip,
  keeps the prompt, focuses it), Mark up (T13 — render a disabled button
  with a title "next task" now), Download (`<a href=content download>`),
  Delete (confirm); Recent — the last 24 image artifacts via
  `useArtifacts({kind: "image", limit: 24})` + SSE, click → stage; "Browse
  all" → `/artifacts`; an error from `generate` (429/402/502) shows as a
  `Banner` with the server's message verbatim; when NO model is configured
  the compose panel shows one `Banner` linking to `/secrets`), new
  `components/studio/{Compose.tsx, Stage.tsx, ReferenceStrip.tsx,
  RecentStrip.tsx}`, `app.css` (`.studio` grid ≥ 900 px two columns,
  stacked below; `.studio-shimmer` keyframes; `.reference-strip`), `App.tsx`
  (`/studio` and `/studio/:id` to open a result), `tests/mock-api.ts`
  (already has `models`/`generate`; add a `/api/agents` shape check),
  new `tests/studio.spec.ts` (models: unconfigured disabled; generate posts
  the right body incl. `reference_ids` and shows the result with
  provenance; Iterate moves the result to the strip; "Use as agent image"
  calls `PUT`; a 402 body renders in the banner; recent strip shows 24 and
  clicking one fills the stage; keyboard: ⌘Enter generates; the page at 390
  stacks). Acceptance: web suite green; screenshots both breakpoints/themes;
  the visual reviewer specifically judges whether the empty, pending and
  result states each look intentional.

- [ ] **T13 `[ui]` Markup: pen, arrow, rectangle, text, crop → derived artifact.** `[after T12 reports]` (AC-5)
  Design sections: "The Studio" (Markup paragraph), "Data model" (`meta`
  derived shape).
  Files: new `components/studio/Markup.tsx` (a `<canvas>` sized to the
  image's natural pixels, CSS-scaled to the stage; tools as a toolbar of
  `<Button aria-pressed>`: Pen (freehand path), Arrow (line with head),
  Rect (outline), Text (click → an `Input` overlay → drawn on commit), Crop
  (drag a rectangle; Save crops to it); colour swatches from
  `var(--ds-chart-1..6)` resolved with `getComputedStyle`; stroke width 3
  and 6; Undo (operation stack, redraw from the source image + ops); Cancel;
  Save → `canvas.toBlob("image/png")` → `uploadArtifact` multipart with
  `name = <parent name>-markup.png`, `meta = {parent_id, operation:
  "markup" | "crop"}`, `source` inferred by the server from `meta.parent_id`
  (T2's store must set `source: derived` when `meta.parent_id` is present
  and the parent exists — if T2 did not, add that to `artifact_store` here
  with a backend test); the new artifact becomes the stage result;
  keyboard: Esc cancels, ⌘Z undoes; the canvas has `role="img"` and an
  `aria-label` naming the tool in use; pointer events only (no mouse-only
  handlers) so touch works at 390), `pages/Studio.tsx` (enable Mark up;
  `Stage` swaps to `<Markup>` in place), `app.css` (`.markup-toolbar`,
  `.markup-canvas`), `tests/studio.spec.ts` (enter markup, draw a rect via
  pointer events, Undo removes it — assert on a pixel via `canvas.
  getContext` in `page.evaluate`, Save posts multipart with the
  `parent_id` meta and the stage shows the new id; Crop changes the
  uploaded blob's dimensions; Esc cancels without a POST). Backend: `tests/
  test_artifact_store.py` gains the derived-source test if added here.
  Acceptance: web suite green (+ backend if touched); screenshots of the
  markup mode at both breakpoints; the visual reviewer tries the tools by
  hand through Playwright and reports whether the arrow head and text look
  right.

### Phase 4 — ship it

- [ ] **T14 Docs.** `[after T13 is committed]` (all ACs)
  Files: `docs/design/23-artifacts-and-image-studio.md` (Status → shipped
  pending T15; fill "AS BUILT" from the commits and the implementer
  reports: the executor sink shape, the model ids T5 confirmed, anything
  the implementers decided that the design did not pin down), new
  `docs/building-blocks/artifacts.md` (what an artifact is, the routes,
  the tool and when an agent should use it, the card syntax, the caps and
  settings, provenance, the Studio, how to add a provider — a `models.json`
  entry + a secret block), `docs/building-blocks/README.md` (index row),
  `docs/building-blocks/glossary.md` (Artifact, Artifact card, Derived,
  Studio, Artist, `#art`; the tool-executor row gains "and a file sink";
  `image_gen` under tools), `docs/building-blocks/tools.md` (already
  touched by T4/T5 — check it reads as one piece), `docs/building-blocks/
  secrets.md` (the three blocks if it lists blocks), `docs/building-blocks/
  agents.md` (profile image; the artist among seeded agents), `docs/
  building-blocks/relay.md` (the artifact card), `docs/design/00-overview.md`
  (row 23 in the series table; the data-model list gains `artifacts`;
  the tool-executor row mentions the sink), `docs/design/12-executable-
  capabilities.md` (a short "AS BUILT addendum 2026-09-17": file sink,
  `internal`, 300 s, api as a second caller), `docs/design/17-*` (facade
  counts + the two exclusions from T7's report), `README.md` if it lists
  building blocks or platform tools, `docs/security.md` (Layer 2: the api
  now also dials the executor through a client tunnel; the byte-serving
  rules under the relevant layer). No implementer review needed beyond a
  sonnet doc reviewer checking every claim against the code (paths, names,
  counts, settings). Acceptance: every path/name/count in the docs exists
  in the tree; the Help page lists Artifacts (the API serves
  `docs/building-blocks/*` directly).

- [ ] **T15 Deploy and live verification.** `[after T14]` (AC-6, and the live
  proof of AC-1…AC-5)
  The orchestrator does this task itself with the Terminal.app mechanics
  from protocol step 9 (no implementer). First `git push origin main` (the
  sync must carry `tools/image_gen`, the secret blocks and the docs). Build
  and import backend, web, mcp-broker, mcp-facade AND tool-executor
  (from the repo root); fetch the stored values fresh; `helm upgrade` with
  stored values + the pai overlay; confirm the api pod rolled with its new
  sidecar (`kubectl get pod -l app.kubernetes.io/component=api -o
  jsonpath='{.items[*].spec.containers[*].name}'` shows `executor-tunnel`),
  the executor pod rolled, `kubectl get pods` all Running, the topics job
  created `artifacts.events`, the dispatcher log shows the grant sweep and
  the `#art` + artist seeds ran once. Wait for a sync tick; `GET
  /api/tools` lists `image_gen` as internal and `GET /api/secrets` lists the
  three blocks with their status. Then, through the forward, record in
  "Live verification" below:
  1. **Upload (AC-1).** `POST /api/artifacts` multipart with a small PNG as
     admin; `GET /content` headers (nosniff, inline, immutable); `/thumb`
     is a JPEG/PNG ≤ 150 KiB; a `.html` renamed `.png` is served as
     attachment octet-stream. Quote the headers.
  2. **Agent save (AC-1).** In Relay: `@pai save a note called
     "hello.md" with the text "artifacts work" as an artifact and post its
     card` — pai holds `artifacts` after the sweep; quote the reply and the
     rendered card (screenshot).
  3. **Generate (AC-2)** — only for providers whose secret is `valid`
     (Kyle pastes them during the build; if none is valid yet, record that
     and put the three `curl` generate calls in "Handoff to Kyle" — do NOT
     wait for keys): `POST /api/artifacts/generate` once per configured
     provider with the same prompt ("a friendly robot librarian, flat
     vector icon, dark charcoal background"), the smallest/cheapest model
     each; record model, duration, cost, artifact id; confirm each
     produced a `#art` card and an `artifacts.event` envelope (kafka
     console consumer, last 3); then one generation with a reference
     (the first result) on a model with `edits`; then a budget probe is
     NOT run live (it would spend) — cite T6's test.
  4. **Artist (AC-3).** In `#art`: `@artist make me a small icon of a
     compass, flat style` — quote the reply, confirm the card renders and
     the artifact's owner is `agent:artist` with `run_id` set; then
     `@artist make it blue` in the thread and confirm the second
     artifact's `meta.reference_ids` names the first.
  5. **Agent image (AC-4).** Set `pai`'s image to a generated result via
     `PUT /api/agents/pai/image`; screenshot `/agents` (grid, both themes,
     1280 and 390), the toggle to the table, `/relay` showing pai's message
     with the image face, and `/agents/pai`'s Profile image section; then
     upload a file through the UI with Playwright and confirm the PUT.
  6. **Studio (AC-5).** Playwright through the forward: `/studio` lists
     configured models only; generate on the cheapest configured model
     (skip if none); Iterate; Mark up: draw a rect, Save, confirm a derived
     artifact with `parent_id` exists via the API; screenshots of compose,
     pending, result and markup states.
  7. **Caps.** `GET /api/artifacts/stats` before/after; `helm history`
     revision; `kubectl top pod` for the api after the thumbnails (memory
     stays under its limit).
  Then set the design's Status to shipped with the helm revision, finish
  the AS BUILT section, and commit the docs. Acceptance: items 1–7 recorded
  with commands, timestamps and screenshot paths (or an explicit "no valid
  key yet" for 3/4/6's generation steps with the exact commands handed
  off); the "Definition of done" passes.

### Repairs

(added by the loop when the definition of done fails)

### Deferred

(low/medium findings the loop chose not to fix, with file:line)

- (T2 review, low) `artifact_store.py` sets `Image.MAX_IMAGE_PIXELS` process-wide at import; fine while nothing else in the process uses Pillow.
- (T2 review, low) `PATCH /api/artifacts/{id}` publishes no event; the design lists only created/deleted/agent_image.
- (T4 review, low) `TOOL_SCRATCH_DIR` is not validated at executor boot (a misconfigured dir → 500 per call instead of a boot failure).
- (T4 review, low) the broker suite stubs `fastmcp`; a real-fastmcp incompatibility in `CustomTool` fields would only surface at deploy (the reviewer verified 3.4.7 by hand).
- (T6 review, low) reference fetch holds up to 4 × 8 MiB blobs base64'd in memory per generate request (~43 MB); bounded per request, compounds only with concurrency — the reservation ledger bounds concurrency by budget, not memory.
- (T6 review, medium, pre-existing) `secrets.py::K8sSecretStore.get` is a synchronous kubernetes call inside async routes (`api/secrets.py` too); the models route now wraps it in `to_thread` + a cache, the secrets page still does not.
- (T8 review, low) `PUT /api/agents/{name}/image` publishes an `agent_image` event even when the value is unchanged (noise only).
- (T8 review, low) the regenerated SDK's `AgentDefOut.from_dict` pops `face` unconditionally — against a pre-T8 server it KeyErrors; restart the facade after deploy (T15).
- (T10 review, low) `/artifacts` owner/tag dropdown options are derived from the currently filtered rows, so picking `kind=file` hides owners with no files until filters clear.
- (T10 visual, low) a system-posted `#art` card renders without an author line, consistent with every other `kind: event` card (ticket cards too).
- (T11 visual, pre-existing) `/agents/<name>` at 390 overflows by 1–2 px from the `.tabs` strip (seven tab buttons, no wrap) — present before this build.
- (T1 review, medium, pre-existing) `services/backend/agentplatform/secretverify.py:50-75` — declarative probes run `urlopen(timeout=8)` but DNS resolution (`getaddrinfo`) is not bounded by it and `verifierloop.verify_all` awaits probes sequentially, so a hung resolver stalls the whole heartbeat pass; scripts are safe (`subprocess.run(timeout=20)`). Fix later: wrap each `verify_one` in `asyncio.wait_for`.

## Definition of done

All T1–T15 are `[x]` (a `[!]` task counts as not done until it has a
Repairs entry that is `[x]`); `cd services/backend && .venv/bin/python -m
pytest -q` is green; `cd services/mcp-broker && ../backend/.venv/bin/python
-m pytest -q` is green; `cd services/tool-executor && ../backend/.venv/bin/
python -m pytest -q` is green except the macOS canary; `cd tools/image_gen &&
../../services/backend/.venv/bin/python -m pytest -q test_run.py` is green;
the facade suite is green under its 3.12 venv; `cd services/web && npm run -s
lint && npm run -s check:tokens && npm run -s build && npx playwright test`
is green; `helm template` renders with `spire.enabled` true and false; the
NUC runs the new images (`kubectl get pods` all Running, the api pod has the
`executor-tunnel` container, `GET /api/artifacts/stats` answers through the
forward, `GET /api/artifacts/models` lists the registry with `configured`
flags, `/studio` and `/artifacts` load); "Live verification" below records
items 1–7 with the screenshots — the generation items may read "no valid
key at build time" with the handed-off commands, and that still passes.

## Live verification

(filled by T15)

## Handoff to Kyle

(filled by the loop: the secrets link, anything classifier-blocked, the
generation commands if no key was valid at build time)
