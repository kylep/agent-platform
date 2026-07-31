# Design note — declarative building blocks: secrets as code + readiness

Status: **proposed (spec for review).** Turns the platform's ad-hoc state
model into one coherent shape: every *configuration* building block is a
self-describing folder in git; the database holds only runtime/derived state;
secret **values** are the sole exception and live in k8s (never git). Adds a
readiness contract so a missing/broken dependency blocks an agent visibly
instead of failing it silently.

## Why

State ownership grew inconsistent:

- **Agents, skills** — declarative in git (reviewed via Changes, versioned,
  survive a cluster teardown). Good.
- **Secret metadata** — smeared across code: `REQUIRED_SECRETS`,
  `SECRET_HINTS`, `PROBEABLE_SECRETS`, and an `if name == …` ladder in
  `secret_probe_target`. Adding a secret means editing central Python.
- **Failure mode** — a missing/invalid secret doesn't stop a run. The pod gets
  `optional: true` envFrom, the secret is silently absent, and the run fails at
  runtime with a confusing error instead of "this agent is blocked because
  `discord-bot` isn't verified."
- **DR** — a fresh cluster restores agents/skills from git but nothing *tells*
  you which secrets are missing or how to check them.

The fix: make **secrets a first-class git citizen** like agents and skills, and
**derive + enforce agent readiness** from the dependency graph.

## The model

Four git-declared building blocks, each a self-describing folder:

```
agents/<name>/    manifest.yaml · agent.md · entrypoints.yaml
skills/<name>/    SKILL.md            (declares its secrets + strictness)
secrets/<name>/   secret.yaml         (shape) · verify_*.py (optional)
```

Plus two things that are **not** git, by necessity:

- **Secret values** → k8s Secrets. Can't be in git (leak posture). Set through
  the API/UI. See "exports.sh" below.
- **Runtime/derived state** → Postgres: runs, metrics, memories, conversations,
  the news dedup ledger, the secret-status *cache*, audit, and the **ephemeral
  Jobs** (ad-hoc scheduled runs — history, not config; see 03). Losing the DB
  costs history, never config.

`requirements.yaml` was considered and **dropped**: an agent's dependency set is
derivable (`manifest.skills` → each skill's `secrets:`), so restating it would
only drift. Strictness lives on the skill; readiness is computed.

## Secrets as code (`secrets/<name>/`)

`secret.yaml` becomes the single source of truth for everything *about* a secret
except its value — replacing all four scattered pieces:

```yaml
# secrets/git/secret.yaml
name: github-app
required: false           # platform can't boot without it? (replaces REQUIRED_SECRETS)
description: GitHub App creds so platform-coder can open PRs.
keys:                     # k8s data key -> what env var the skill reads
  - name: GITHUB_APP_ID
    hint: "App ID from github.com/settings/apps/pericakai"
  - name: GITHUB_INSTALL_ID
    hint: "Installation ID"
  - name: GITHUB_APP_PRIVATE_KEY_B64
    hint: "base64 of the app private key .pem"
    format: base64
verify: verify_github_app.py     # optional; else a declarative probe or "verified by run"
```

A `SecretRegistry` loads `secrets/*/secret.yaml` at runtime the way `SkillStore`
loads skills. The secrets API, the UI hints, the "Verify" button, and the
readiness evaluator all read from it. `secrets.py`'s hardcoded dicts disappear.
Adding a secret = add a folder + open a PR — no central-file edit.

### Verification

Two forms, both deterministic (code, not an LLM):

- **Declarative probe** (default, no code execution) — for the common
  "GET this URL with that header, 2xx = valid" case (discord, github today),
  expressed as data in `secret.yaml`.
- **`verify_*.py` script** (escape hatch) — for anything a probe can't express.
  Run in a **sandboxed subprocess** with *only that secret's* data in its env,
  returning pass/fail + detail. Sandboxing (not in-process import) keeps a
  verify script from reaching other secrets or the DB — it fits the containment
  posture, and it's still reviewed repo code.
- Claude's token is the special case: verified **by run success** (a run can't
  reach `succeeded` without authenticating), so its verify is continuous and
  needs no probe.

**Open decision (needs your nod):** declarative-probe-by-default + sandboxed
script-when-needed, vs. everything-is-a-script. Recommendation: the former.

## Readiness (the contract)

An agent is **ready** when every *required* dependency is satisfied:

- Dependencies are **derived**: `manifest.skills` → each skill's declared
  secrets. No restating.
- **Strictness lives on the skill's secret declaration** — DRY, because the
  skill knows how strict it needs to be:
  ```yaml
  # skills/discord/SKILL.md frontmatter
  secrets:
    - name: discord-bot
      state: verified        # present | verified   (default: present)
      severity: required     # required | optional  (default: optional)
  ```
- **Evaluation**: resolve the dep set; a `required` secret that is missing or
  whose status ≠ `verified` → the agent is **blocked**.

### Behavior when blocked

- **Runs are blocked before dispatch.** A trigger (cron / webhook / manual /
  agent / Discord) for a blocked agent does **not** launch a pod. Instead it
  records a **failed Run** with a clear reason — *"blocked: skill `discord`
  disabled — secret `discord-bot` failed verification"* — so it's visible in
  Runs, the agent's history, and the DLQ path, not silently dropped.
- **Try-before-block (transient recovery).** When a required secret looks
  invalid *at the moment it's about to be used*, re-run its verify once first;
  if it passes now (the failure was transient), proceed. This generalizes the
  existing Claude-token circuit-breaker to every secret.
- **Distinct state.** "Blocked — unmet requirement" is its own agent state,
  recoverable by *fixing the secret*, separate from "quarantined — broken
  definition," recoverable by *fixing the agent*. Different problems, different
  fixes; both surfaced, never conflated.

### Secret-validation heartbeat

A platform-internal **verifier loop** (alongside the scheduler in the
dispatcher) runs each secret's declarative probe / verify script on a sane
cadence and writes `SecretMeta.status`. Deterministic. This keeps `verified`
from going stale-green (a token that rotates/expires between uses is caught
within one cadence), and it's what the readiness evaluator and Dashboard read.
Claude's token is exempt (verified by run outcomes).

### Visibility

- **Dashboard "needs attention"** gains broken secrets and blocked agents:
  *"discord-bot: invalid → Secrets"*, *"news blocked — discord-bot not verified."*
- The **Secrets page** shows each secret's live status from the heartbeat.
- The **agent list / agent page** shows the blocked state + the specific unmet
  dependency.

## Entrypoints (`agents/<name>/entrypoints.yaml`)

Consolidates *how an agent gets triggered* into one declarative file per agent —
the agent's **durable, defining** triggers:

```yaml
cron: ["0 13 * * *"]          # replaces the single manifest `schedule:`
webhooks: [{ path: news }]    # inbound POST /api/webhooks/news
kafka: []                     # topic subscriptions (future)
```

This is distinct from DB **Jobs**, which stay as *ad-hoc experiments* you spin
up in the UI and throw away (history, not config). Two different things that
both happen to be "cron." (Separate pass — see sequencing.)

## exports.sh — what it actually is

`exports.sh` (gitignored) is **a development convenience, not a platform
component.** It is how the operator hands secret values to Claude so Claude can
set them via the API while building — a safe channel for "here are the creds to
work with." The running platform never reads it. A fresh cluster gets its secret
*contracts* from git (`secrets/`) and its *values* from whoever sets them
through the API/UI (the operator, or Claude acting from `exports.sh`). This will
be documented explicitly in `exports.sh.sample` and the secrets doc.

## Documentation deliverable

Every first-class building block gets a concise doc under `docs/` and a mention
in `README.md`: **Agents, Skills, Secrets, Entrypoints, Jobs, Conversations,
Memories, Runs, Changes (self-edit).** Each: what it is, where it lives (git vs
DB vs k8s), its shape, and how to add one. README gets the one-paragraph version
of each.

## What is explicitly NOT changing

- Secret **values** never enter git. The folder holds shape + verify only.
- The DB stays the home for runtime/derived state and ephemeral Jobs.
- The k8s value plumbing (`K8sSecretStore`) is untouched.

## Sequencing

1. **`secrets/` registry + verification** — move the 5 existing secrets to
   folders; `SecretRegistry`; secrets API + UI read from it; declarative
   probe + sandboxed verify; the heartbeat loop. (The "gross" fix.)
2. **Readiness gate** — skill strictness; derived requirements; blocked state;
   block-before-dispatch with failed-Run logging; try-before-block; Dashboard +
   Secrets + agent-page wiring. (Highest value — kills the silent-failure mode.)
3. **`entrypoints.yaml`** — fold cron/webhook/kafka triggers into the agent
   definition; keep DB Jobs as ephemeral.
4. **Skill wizard + in-place SKILL.md editor** — on top; the wizard can scaffold
   a `secrets/<name>/` folder when a new skill needs a new credential.
5. **Docs pass** — per-building-block docs + README.

Phases 1–2 are the core; each ships and is verified live before the next.

## Locked decisions

- Derive dependencies, don't declare (`requirements.yaml` dropped). Make
  readiness **visible**.
- Unmet required dep → **block** (fail the run with a clear reason), not degrade.
- On-demand re-verify before blocking (transient recovery) + a deterministic
  verification **heartbeat**.
- Blocked is a **distinct state** from quarantined.
- Freshness handled by the heartbeat + on-demand re-probe.
- `exports.sh` is dev-only; documented as such.
