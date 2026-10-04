# Agent-built Apps: resume checkpoint (2026-10-03)

Kyle asked to pause the migration because the weekly Codex allowance is early
in its cycle. **Do not resume migration work until he explicitly asks.** On
resumption keep at least 75% weekly quota left, checking a fresh reading at
each App cutover boundary. The full finish line remains all six legacy Apps
operated as database-owned state Apps, with old workloads removed after live
parity and a restore proof. This is a checkpoint, not a descoping decision.

Read these first, in this order: this file;
[execution retro](2026-10-04-app-migration-retro.md);
[live status](2026-10-03-app-migration-live-status.md);
[efficient delivery plan](2026-10-02-agent-built-apps-efficient-delivery.md);
the relevant App's cutover runbook. Consult
[design 39](../../design/39-agent-built-apps.md) only for the boundary you
are editing. The older 2026-10-02 paused checkpoint says 0/6 and is obsolete.
The app-building and platform-change skills are relevant. Check `git status`
before editing: untracked `.claude/`, old HTML reports and
`codex-session-id.tmp` belong to other work and must not be staged.

## What is live

Running is the only completed migration (1/6). State App ID
`ea8fc027b3af45718a5a3d5c87e182af`, owner `running-coach`. The copy
preserved 50 activities and two briefs; the coach subsequently wrote two
activities directly to state storage. Its direct `strava.sync` and
`running.report` worked. Desktop and 390px Playwright journeys showed the
activity rows, briefs, charts and sidebar link without console errors or
horizontal overflow. Helm release `ap` revision 93 removed `ap-app-running`.
The old Running schema remains for rollback. See
[Running cutover](../../building-blocks/running-state-cutover.md).

Encrypted pre- and post-cutover backups were uploaded to Cloud Storage. The
post-cutover archive `ap-recovery-20261003T041032Z.tar.age` had SHA-256
`935636c00c914cf684c07735412b0989e8ab70d8ddd8f021a15cde0eb0d734a4`;
a downloaded copy matched and offline decryption/manifest inspection passed.
This is **not yet a disposable-cluster restore**. That drill is still needed.

Judgment is still the coded App. Read-only preflight counted 41 beliefs,
66 versions, four predictions, one prediction link and six feedback rows;
25 earlier belief snapshots. The state adapter and guarded one-shot copier
are committed, but **have not been used on live data**. The copy refuses a
nonempty destination and compares current and historical content in one
transaction. `tests/test_judgment_migration.py` (five focused cases) passed.
Do not run its `--apply`, switch Kai, or retire its service until Kyle's
review actions work in the state App. See
[Judgment cutover](../../building-blocks/judgment-state-cutover.md) and ENG-10.

News, TCMS, Stockmarket and TTRPG remain on their coded services. No live
copy has begun. The migration order may be changed where a measured dependency
suggests it, but preserve each existing user outcome; minor UI reshaping is
authorized. TTRPG also needs a cross-repo adapter in `~/gh/claude-ttrpg`.

## Next vertical slice

Implement signed, bounded page-intent **tool actions** for Judgment's Kyle
review flow, then cut Judgment over as one milestone. The old Judgment UI
supports confirming/correcting/rejecting beliefs, confirming feedback, and
deleting predictions and versions. Current typed/v2 state pages intentionally
refuse writes to tool-only collections; a direct record-write shortcut would
bypass Kai's guarded tool. Use the design 39 Release 2 contract: fixed
operation and target, Kyle-authenticated intent, dispatch-time access check,
idempotency and durable receipt. Test wrong caller, changed target, replay,
maintenance mode and failed operation. Relevant files:

- `services/backend/agentplatform/appdata/definitions.py` — action templates
- `services/backend/agentplatform/appdata/intents.py` and `credentials.py`
- `services/backend/agentplatform/api/auth.py` — tool-call auth boundary
- `services/tool-executor/executor.py` and `tools/judgment/state.py`
- `apps/judgment/backend/judgmentapp/api.py` — legacy action semantics
- `services/backend/agentplatform/appdata/judgment_cutover.py`

The App sequence after Judgment is TCMS (size its large results and prove a
scratch restore before copying), News (freshness/dedup and notification
separation), Stockmarket (bars/watchlist/briefs/backtests and bounded chart
queries), and TTRPG (cross-repo turn and spectator proof). The detailed
acceptance gates are in the efficient delivery plan. Do not add generic
features without a named App behavior needing them. Run focused local tests
while editing, one relevant suite and one scripted browser journey at each
milestone, then build/deploy in a batch. No routine `gh run` polling.

## Admin API-key change during this pause

Kyle explicitly authorized the Codex and Claude platform API keys to act
with his full admin authority. They already had role `admin`; the sole
remaining limitation was the browser-session-only check for Kyle-only tool
grants and protected agents. The backend now accepts **only explicitly
configured active key IDs** for that check, never a display name or an
agent/run key. Both existing IDs belong in private Helm values under
`env.AP_TRUSTED_ADMIN_KEY_IDS`; no token or key ID is committed. This changes
the human-admin path, not agent tool grants. Helm revision **94** deployed
this change. Both keys (`codex-laptop`, `kyle-claude-code-mcp`) were verified
active with role `admin`, private values held exactly two unique trusted IDs,
and a no-op full-definition update of protected Kai through Codex's MCP key
succeeded without appending a version. All four affected deployments were
ready after rollout. Claude's own bearer was not exercised in this session;
its active admin role and ID were verified. The relevant local tests are
`services/backend/tests/test_kyle_only_tools.py` (30 passed).

## Operational constraints and resume commands

The laptop's APFS data volume had under 1 GiB free on 2026-10-03, although
pai had 338 GiB free. A cached backend image build did succeed without filling
it. Before further Docker work, check `df -h /private/tmp`; do not delete
Claude files, Docker volumes or unrelated images to make room. Use the
existing `services/backend/.venv/bin/python -m pytest` for focused tests;
`uv`'s default cache was inaccessible under the sandbox. Python 3.14 locally
and 3.12 in production differ. Follow `docs/deployment.md` and use
`--provenance=false` for image builds. Stream images to `sudo k3s ctr -n
k8s.io images import -`; restart all three backend deployments plus the MCP
facade after an API image change.

The AP MCP key in ignored `exports.sh` is `codex-laptop`; its bearer value must
never appear in logs, files staged for git, or a final answer. Use the facade
for agent/platform calls. If querying API-key inventory, print only IDs,
names, roles and active state. Never `git add -A`; stage exact paths and
inspect `git diff --cached` and the repository's secret checks before push.
Both Codex and Claude keys were already admin at the pause point.
