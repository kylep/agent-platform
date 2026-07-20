# Milestone 01 Handoff — read me first

Briefing for a fresh Claude session resuming milestone 01 (walking
skeleton) at the deploy/verification stage. The previous session ran from
`~/gh/multi` and executed the entire implementation plan
(`docs/plans/2026-07-17-milestone-01-walking-skeleton.md`) via
subagent-driven development; only Task 19 (deploy + verification
checklist) remains in progress.

## State right now

- **Branch:** `milestone-01`, pushed. All work is committed — nothing
  uncommitted matters. CI on GitHub is green (backend, runner, web, helm,
  subscription-guard).
- **Cluster:** pai NUC k3s (`ssh pai`, kubeconfig `~/.kube/pai-nuc.yaml`),
  namespace `agent-platform`, helm release `ap` in **deployed** status.
  All 7 pods Running: api, dispatcher, recorder, web, agents-sync,
  ap-postgresql-0, ap-kafka-controller-0. UI at **http://pai:8090**.
- **Auth state:** admin account exists (password in Kyle's password
  manager — Bitwarden is retired, never use it). The claude-credentials
  secret currently holds a STALE session snapshot; the plan is to replace
  it with a `claude setup-token` value (see below).
- **Local images** (Rancher Desktop, tags `:dev`, linux/amd64):
  `agent-platform-backend` (shipped to pai), `agent-platform-runner` and
  `agent-platform-web` (rebuilt with latest fixes, **NOT yet shipped** —
  networking died mid-ship; Kyle has since fixed it).
- **Session ledger** with full per-task history:
  `.superpowers/sdd/progress.md` (gitignored, on disk in this repo).
- **The agents-sync tracks `milestone-01`** (helm value
  `agents.gitRef=milestone-01`, set at install) so the cluster sees
  `agents/hello-world` before merge. After merging to main, flip back.

## Exact next steps (in order)

1. **Ship the two rebuilt images to pai:**
   ```bash
   for img in agent-platform-runner agent-platform-web; do
     docker save $img:dev | ssh pai 'sudo k3s ctr images import -'
   done
   export KUBECONFIG=~/.kube/pai-nuc.yaml
   kubectl -n agent-platform rollout restart deploy/ap-web
   ```
2. **Kyle runs `claude setup-token`** in his own terminal and pastes the
   token into http://pai:8090/secrets (the page stores non-JSON pastes
   under the secret's `token` key; the runner exports it as
   `CLAUDE_CODE_OAUTH_TOKEN`). This replaces the stale snapshot.
3. **Requeue the smoke run** (id `2cde4874…`, currently `failed` from the
   stale token; full pipeline verified otherwise):
   ```bash
   kubectl -n agent-platform delete job run-2cde48744ca1 --ignore-not-found
   # psql via ap-postgresql-0 (password from secret ap-postgresql):
   #   DELETE FROM run_transcript_events WHERE run_id LIKE '2cde%';
   #   UPDATE runs SET state='queued', started_at=NULL, finished_at=NULL,
   #     exit_code=NULL, error=NULL WHERE id LIKE '2cde%';
   ```
   The dispatcher's 15s sweep picks it up. Expect `succeeded`, exit 0,
   transcript ending in `OK`.
4. **Finish the verification checklist** in
   `docs/design/01-walking-skeleton.md`: live transcript renders in the
   run page (Playwright: http://pai:8090/runs/<id>); kill button on a
   longer run; concurrency (4 requests vs global cap 3 → one queued);
   kafka-down → runs queue + sweep drains (already proven live once —
   see ledger); tick boxes + record findings, commit.
5. **Final whole-branch review** (superpowers:requesting-code-review, most
   capable model, package = `git merge-base main HEAD`..HEAD) — feed it
   the deferred-findings list below. Then
   superpowers:finishing-a-development-branch → PR `milestone-01` → main.

## Deferred findings for the final review

Collected by per-task reviewers, intentionally not blocking:

- Dispatcher cancel path: unguarded `_set_state` can clobber a terminal
  state (watcher side is guarded; dispatcher side is not).
- K8s secret write TOCTOU (replace→create race) — single-admin, theoretical.
- K8sSecretStore has no fake-client unit test.
- Session cookie: no expiry/secure flag (M6 hardening owns this).
- Tail websocket: one Kafka consumer per socket + replay/live gap (M5).
- RunDetail transcript state unbounded (M5).
- Runner merges stderr into transcript (deliberate: capture-everything).
- Runner >1MB stdout line would exceed Kafka message cap (note only).
- Topics hook vs consumer startup ordering (auto-create races the hook;
  see also __consumer_offsets note below).
- api.ts 401-redirect: fixed for login/setup; other flows still hard-redirect.

## Hard-won operational knowledge (do not relearn)

- **Bitnami images:** versioned tags moved off `docker.io/bitnami` →
  chart pins `bitnamilegacy/kafka`. Postgres image still pulls.
- **Kafka on this chart:** client listener forced PLAINTEXT (M1 posture).
  On a FRESH kafka PVC, `__consumer_offsets` auto-creation loops forever
  (KRaft combined-mode quirk) → consumers get
  GroupCoordinatorNotAvailable. Fix: create it explicitly —
  `kafka-topics.sh --create --if-not-exists --topic __consumer_offsets
  --partitions 50 --replication-factor 1 --config cleanup.policy=compact`.
  Worth automating in the topics hook later.
- **Helm on this cluster:** always `-n agent-platform` (forgetting it
  yields "no deployed releases"). If an install/upgrade times out before
  hooks finish, the release records as failed even though resources run —
  recover by capturing `ap-postgresql` password + api's AP_SESSION_SECRET
  and reinstalling with both `--set` so admin login and DB survive.
  Uninstall keeps PVCs: a kept postgres PVC + regenerated password =
  InvalidPasswordError crashloop; a kept kafka PVC across reinstall =
  wedged coordinator. Wipe PVCs or carry the password, deliberately.
- **Cross-arch builds from the M2 Mac:** backend/runner build fine via
  buildx; the web image's `npm run build` SIGILLs under QEMU — build
  dist natively, then `services/web/Dockerfile.prebuilt`.
- **claude CLI integration (verified real):** `--agent <name>` resolves
  from `~/.claude/agents/`; runner installs the synced `agent.md` there.
  stream-json frames land in postgres exactly as the UI expects.
  Session-credential snapshots die fast (the exporting Mac's own claude
  rotates the refresh token) — hence setup-token.
- **This Mac's LAN to pai is flaky** (WiFi; NUC is WiFi-only). Symptoms:
  "no route to host" while other devices reach it. A stray
  `192.168.2.0/24` route via bridge100 caused it once
  (`sudo route delete -net 192.168.2 -interface bridge100`).
- **Subagent reports in the prior session repeatedly claimed commits and
  test runs that did not exist** (also: completion notifications race
  their final writes). Verify every subagent claim against `git log` and
  real command output before acting on it.

## Key file map

- Design: `docs/design/00-overview.md` + numbered milestone docs
- Plan being executed: `docs/plans/2026-07-17-milestone-01-walking-skeleton.md`
- Setup/ops guide: `docs/setup.md` (includes admin-password reset)
- Services: `services/{backend,runner,web}`; chart: `charts/agent-platform`
- Backend tests: `cd services/backend && .venv/bin/pytest tests/` (32 green)
- Runner tests: `cd services/runner && ../backend/.venv/bin/python -m pytest test_runner.py`
