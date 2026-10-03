# 40 — Token-efficiency plan

Status: **proposed** (2026-10-03). Applies to building design 40 and, through
the `token-efficient-orchestration` skill
(`plugins/agent-platform-coding/skills/`), to all later work on this repo.
Kyle's priority: **P0**. Token waste has repeatedly exhausted the weekly
quota (design 24/25 went 42%→98% and stopped unfinished;
`kyle-quota-efficiency` memory).

## Evidence

**Local, last 30 days** (`cc-usage` plus a jsonl script, at list price; not
billing):

- **Total**: ~$4.4k.
- **By model**: Opus family ~72% of dollars, Fable ~21%, Sonnet+Haiku <8%.
- **By token type**: cache reads **~97%** of tokens, cache writes ~3%,
  output ~0.2%.
- **Where**: the agent-platform main checkout is ~65% of cache reads
  (13.3k requests), at ~200k tokens of context per Opus request on average.
- **Older models**: a lot of the usage was the previous generation (Opus 5,
  Sonnet 5), not 5.5.

**Verified against code.claude.com/docs on 2026-10-03** (local CLI 2.1.287):

- `promptCacheTtl` and `subagentPromptCacheTtl` (`5m`|`1h`). Subscription
  default: main conversation 1h; subagents, forks, compaction and workflows
  5m.
- A subagent's first request never reads the parent's cache. Forks and
  resumed agents do.
- Switching model means a separate cache, so a full uncached re-read.
  Changing effort is cache-safe on Opus 5.5, Sonnet 5.5 and Fable 5.1.
  Thinking can't be turned off on those models; effort is the only dial.
- Subagent model resolution order: invocation `model`, then frontmatter,
  then `CLAUDE_CODE_SUBAGENT_MODEL`, then the parent's model.
- `/usage` attributes spend by skill, subagent, plugin and MCP, and shows a
  `Prompt cache (main)` line with hit rate and miss causes.
- The cache is scoped to machine + directory, so every worktree builds its
  own.
- Scheduled tasks, cross-session messages and goal check-ins each resend the
  full context while idle.
- **Unverified:** how plan quota weights cache reads. Treat them as
  counting; the docs say a one-line turn late in a long session "still draws
  usage for the whole conversation".

## Cost model

```
spend ≈ Σ_requests (context_tokens × model_rate) + output
```

Every tool call is a request that resends the whole context. So
**requests × average context × model rate** is the term that matters, and
output is noise. The levers, in order:

1. **Fewer requests**: batched shell commands, no polling, no re-running
   suites.
2. **Smaller contexts**: short tool results, verbose work isolated in
   throwaway subagents, compaction at phase boundaries.
3. **Cheaper model per re-read**: Sonnet by default.
4. **Cache hits**: no model switches, no cold gaps.

A subagent only saves tokens when the bulk it absorbs would otherwise stay
in the orchestrator's context for the rest of the session. A spawn pays an
uncached prefix of ~20k tokens.

## Execution plan for design 40

**Preconditions**:
- Kyle starts a fresh session in this worktree; model and effort are set
  once.
- No `/loop` and no goal check-ins running; at most 2 open sessions.
- `/usage` weekly headroom ≥ 40%.

**Implementation is Claude-only** (Kyle). Codex is used for one read-only
review, a separate quota pool. Swap it to a Sonnet reviewer if Kyle wants
zero Codex.

| Phase | Who | Brief → return | Tests | Est. weekly % |
|---|---|---|---|---|
| Prep | orchestrator | append per-task briefs to this file | — | 0.5 |
| A1–2 models, DDL, sessions, login/logout | Sonnet implementer, `maxTurns` 60 | ≤200 words + §Data model / §Sessions line ranges → ≤15 lines | `pytest tests/test_accounts.py -k "session or login" -q --tb=short \| tail -40` | 1 |
| A3 fence, stream revalidation, route walk, the pre-existing holes | **Opus** implementer (security seam) | §The fence, §Tests → ≤15 lines **including the line proving the walk fails without the fence** | `-k route_walk` | 2 |
| A4–5 register, me, password, users/groups/registration | Sonnet implementer; starts once A3 *reports* | §API + A3's `USER_PATHS` → ≤15 lines | `-k "register or me or users or groups"` | 1 |
| Review A | Sonnet reviewer, read-only tools | worst cases: session fixation, replay after revoke, IDOR on `users/{id}`, admin key on people routes, dangling group, login-vs-reset race → ≤10 findings | — | 0.75 |
| Fix A | same implementers, via SendMessage (warm cache) | findings verbatim → ≤5 lines | same `-k` | 0.25 |
| A6 facade, SDK regen, full backend suite **once** | orchestrator, background | — | `pytest -q --tb=short \| tail -40` | 0.5 |
| B7–9 web | **one** Sonnet implementer (`App.tsx` is shared) | §Web, §Tests; screenshots written by a spec, dark mode, 1280px → ≤15 lines + 2 PNG paths | `npx playwright test tests/accounts.spec.ts \| tail -40` | 2 |
| Review B, whole diff | Codex, read-only | defects only, same worst-case list + web gate bypass → ≤10 findings | — | 0 Claude |
| Fix B | B implementer, via SendMessage | — | the spec file | 0.25 |
| C ship | orchestrator | merge, deploy, run `scripts/live-check-accounts.sh` (written in A4–5), evidence table into design 40 | script output ≤30 lines | 0.5 |
| Orchestrator overhead | — | briefs, commits, ≤2 compactions | — | 2 |
| **Total** | 5 Claude agents + 1 Codex | | | **≈11% (cap 18%)** |

The design-24/25 rate was ~1.9% per task, which would put 9 tasks at ~17%.

**Stop-and-ask**: before each phase, check `/usage`. Stop, write a
"Handoff to Kyle" section here, and send a push notification if either:
- weekly headroom is below 25%, or
- cumulative spend is more than 1.5× the running estimate.

## Recommended settings (Kyle applies; not changed by this branch)

Project `.claude/settings.json` additions, keys verified:

```json
{
  "promptCacheTtl": "1h",
  "subagentPromptCacheTtl": "1h",
  "bashOutputMaxChars": 15000,
  "crossSessionInbound": "hold",
  "env": {
    "CLAUDE_CODE_SUBAGENT_MODEL": "sonnet",
    "CLAUDE_CODE_GOAL_CHECKIN_MINUTES": "0"
  }
}
```

- **Test-output hook**: a PreToolUse hook on Bash that appends
  `2>&1 | tail -n 60` to bare `pytest` / `playwright test` commands. This is
  the docs' own pattern. Don't use `grep`: it would hide the summary line.
- **Trial, not default**: `enabledPlugins` at project scope, to drop plugins
  this repo never uses and shrink every subagent's prefix. Project-scope
  behavior is unverified; try it in one session first.
- **Caveat on the 1h subagent TTL**: it costs more per cache write. It pays
  off only when subagents idle for more than 5 minutes (suites, builds),
  which is the norm here. Check the cache-write share after the build.

## Measuring it

After the build, record these here:
- `/usage` weekly-bar delta (target ≤ 12%).
- Opus + Fable share of tokens (≤ 30%).
- `Prompt cache (main)`: ≥ 90% hits, ≤ 3 misses, none caused by a model
  change.
- `cc-usage` 7-day: cache-write share ≤ 3%.
- Transcript counts: ≤ 7 agents, exactly 2 full-suite runs, 0
  sleep/poll loops.

## Execution log (build session, 2026-10-03)

Kyle: Claude-only today (Review B = Sonnet, not Codex); build in the design
session (Opus 5.5 orchestrator), compact as needed. Weekly quota at start:
31% used (69% headroom). Gate: stop at <25% headroom or >1.5× estimate.

| Unit | Who | Scope | Test command | Est. % |
|---|---|---|---|---|
| U1 | opus — seam: sessions + fence | `login_sessions`, `resolve_session`, login lock/re-check, logout, change-password, fence, setup-state/tail/live_invocations/projects/help, stream revalidation, `SessionPruner`, route walk | `pytest tests/test_accounts.py -q --tb=short \| tail -40` | 3 |
| U2 | sonnet | groups, `group_id` + FK DDL, `platform_settings`, register, me, me/password, users, groups, registration routes, API-key prefix rule, facade exclusions, SDK regen | same file `-k "not fence"` + `services/mcp-facade` tests | 2 |
| RA | sonnet reviewer (read-only) | backend diff, worst-case list | — | 0.75 |
| U3 | sonnet | web: api.ts, Gate, /login, /profile, sidebar, Users, Groups, specs + screenshots | `npx playwright test tests/accounts.spec.ts \| tail -40` | 2 |
| RB | sonnet reviewer (read-only) | whole diff + cleanup | — | 0.75 |
| main | orchestrator | briefs, full suites once per phase, commits, merge, deploy, live script | — | 2.5 |

### Results (2026-10-03)

| Unit | Model | Subagent tokens | Notes |
|---|---|---|---|
| U1 sessions + fence | opus | 192k | 111 tool calls; route walk proven failing both ways |
| U2 accounts API (+ fix round) | sonnet | 132k | fix round resumed the same agent |
| U3 web (+ 2 fix rounds) | sonnet | 129k | screenshots from the spec, not MCP |
| Review (backend + web merged into one) | sonnet | 131k | 0 critical/high, 12 med/low, all fixed |
| **Build total** | | **~585k** | 4 agents (plan: 5); full suites: backend ×2 (one before fixes), web ×1 |

Design-phase spend for comparison: four design reviews ~260k Claude + 2 Codex,
token research 87k, Fable token pass 113k, skill RED/GREEN/refactor tests 7×~45k.

- The orchestrator merged RA and RB into one whole-diff review because the
  backend and web finished together. That saved one agent.
- `/api/quota` read 31% weekly before and after: it only refreshes when
  platform agents run, so it doesn't measure this session. **Kyle: check
  `/usage` for the real weekly delta** (estimate: 11%).
- **Blocked by the auto-mode classifier, left for Kyle:**
  - pushing to `main` (PR #39 opened instead; deploy held until merge, so
    prod never runs code that `main` lacks);
  - writing the project `.claude/settings.json`.
