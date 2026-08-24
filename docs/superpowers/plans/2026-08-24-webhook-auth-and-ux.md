# Webhook Auth + UX Sweep Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Opus implementers, Sonnet Playwright visual reviewers, task review after each, scoped re-reviews on fixes.

**Goal:** Per-webhook shared-secret auth (design-16), runner mount-fallback removal, a Webhook column on /agents, and a UX/CSS quality pass over every web page (config page first, Reports flagged as broken-looking).

**Spec:** `docs/design/16-webhook-auth.md` (binding for Tasks 1/3). Kyle's decisions: None = platform key as today; Secret = `X-AP-Webhook-Secret` header, platform key still accepted. UI: dropdown per webhook row, password field with eye toggle, tooltip printing the exact header, /agents Webhook column (checkmark for ≥1 declared webhook).

## Global Constraints
- Single `main`, commit directly, explicit adds, trailer `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`; python 3.12-safe.
- Secrets NEVER in `agent_defs`/`agent_versions`/API responses/logs; constant-time compares; fail closed.
- Suites: backend `cd services/backend && .venv/bin/pytest tests/ -q`; runner `cd services/runner && ../backend/.venv/bin/python -m pytest test_runner.py -q`; web `npx tsc -b services/web --force` + `npm run build -w web` + Playwright per prior runs. sdk regen in a python:3.12 container when OpenAPI changes.
- Design system: Terminal tokens, src/ui primitives, no raw hex.
- Deploy per docs/deployment.md; live verification before declaring done.

### Task 1: Webhook-auth backend
`webhook_secrets` table (agent+path pk, salted-hash, timestamps; cascade-delete with agent); `auth` on webhook entries (agentdefs model + wire schemas, default "none", validated); `PUT/DELETE /api/agents/{name}/webhooks/{path}/secret` (admin or agents_edit via WriteScope; min length 16; write-only; 404 unless the path is declared on that agent); `secret_set` derived on GET; ingress auth per spec (platform key OR header when mode=secret; constant-time; fail closed on missing hash); tests incl. rotation, fail-closed, header-only external call, platform-key-still-works, mode+secret survive def updates, delete-agent cleans hashes.

### Task 2: Mount-fallback removal
Delete `_install_agent`'s mount-copy branch + `AP_AGENTS_DIR` + its 4 fallback tests (fetch failure now aborts via the existing `AgentUnavailable` double-failure frame with api error only); KEEP the `/agents` mount (skills install from `/agents/skills`); chart: drop any AP_AGENTS_DIR env; runner+backend suites green.

### Task 3: Webhook-auth web UI + Webhook column
Per spec UI section. AgentForm webhooks editor: auth dropdown, password+eye (input type toggle), save flow (def PUT then secret PUT when a new secret typed; "secret set · rotate" state from `secret_set`), tooltip with the literal header line. Agents list: Webhook column (✓ when entrypoints.webhooks non-empty; both tables). api.ts types. Playwright specs asserting the secret is sent only to the secret endpoint and never rendered back.

### Task 4: UX findings sweep (Sonnet, read-only, live site)
Every page, agent config page first and Reports second (Kyle: "kind of fucky"). Kyle's screenshot specifics to anchor: the `enabled` / `can invoke agents` checkbox chips render in mono font, cramped, visually inconsistent with adjacent selects; heading/label hierarchy, font sizes/colours, margins generally. Output: a findings file (structured: page, element, problem, severity, suggested fix) — no code changes.

### Task 5: UX fix wave (after 3+4)
Opus implements Task 4's findings + the screenshot specifics, staying inside the design system (fix tokens/primitives once where possible rather than page-by-page overrides); tsc+build+Playwright+axe green.

### Task 6: Deploy + live verify
Images (backend/runner/web) → roll → live checks: external-style curl with only the secret header fires the webhook; wrong secret 401s; None-mode webhook still key-gated; /agents Webhook column; Sonnet Playwright visual re-verify of config page + Reports + spot-check others against Task 4's findings.
