# Agent config editor: New-Agent wizard + checkbox skills/tools

## Problem

Agents are defined declaratively in the git-synced `agents/<name>/` tree
(`manifest.yaml` + `agent.md`). Two axes of capability are editable only by
hand-writing files or dispatching platform-coder:

- **Skills** — the `skills:` list in `manifest.yaml` (an enforced allow-list:
  the runner mounts exactly these and binds their secrets).
- **Tools** — the `tools:` frontmatter line in `agent.md` (Bash / Edit /
  WebFetch / …). This is interpreted by the `claude` CLI, but the platform is
  otherwise unaware of it: it is not validated, not audited, and — worse — a
  change to it currently classifies as a **Tier 1** edit (straight to `main`),
  so an agent could silently widen its own tool access.

We want a UI to create agents and to toggle their skills and tools with
checkboxes, with every save flowing through the existing Changes / PR review
gate.

## Design

### Write path — reuse `EditService` (deterministic, no LLM)

All writes go through the existing `EditService` (clone → write files →
`classify_tier` → commit-to-main or branch+PR), the same path the deterministic
`quick-edit` uses. No agent is dispatched. This gives us the Changes/PR flow
for free and keeps saves fast and predictable.

- **New agent** → writes two *new* files under `agents/<name>/` → Tier 2 → PR.
- **Edit skills/tools/description** → modifies `manifest.yaml` and/or the
  `agent.md` frontmatter → Tier 2 → PR (see tier change below).

`api/agents.py` grows a shared `_apply_files()` helper (extracted from
`quick_edit`'s credential/writer plumbing) that both new endpoints call.

### Tier rule change — frontmatter is sensitive

`skills` is already a sensitive manifest field (forces Tier 2). We extend the
tier classifier so that **an `agent.md` change touching its frontmatter is Tier
2**, while a body-only edit stays Tier 1 (preserving the freeform prompt-edit
UX). Rationale: the `tools:` line is a security boundary; changing it deserves
review just like `skills`/`secrets`.

Implementation: `FileChange` gains `agent_md_frontmatter_changed: bool`;
`compute_changes` sets it by diffing HEAD vs working frontmatter for
`agent.md`; `classify_tier` returns Tier 2 when it is set.

### Endpoints (all `require_admin`)

- `POST /api/agents` — body `{name, description, role?, model?, concurrency?,
  timeout_seconds?, skills[], tools[], prompt}`. Renders `manifest.yaml` +
  `agent.md`, applies via `_apply_files`. 409 if the agent exists; 422 on a bad
  name, unknown skill, or unknown tool.
- `PATCH /api/agents/{name}/config` — body `{skills[], tools[], description?}`.
  Reads the current synced files, surgically updates only those keys, re-renders,
  applies. A no-op edit returns tier 0 (nothing to do).
- `GET /api/agent-tools` — the canonical `AVAILABLE_TOOLS` list, so the UI and
  backend validation share one source of truth.

### Tools semantics

`claude` subagent frontmatter: **omit `tools:` = inherit all tools**; a list =
allow-list. So the editor treats "all boxes checked" as *unrestricted* and
omits the `tools:` line; any subset writes an explicit `tools: A, B, C`. When
reading an agent with no `tools:` line, all boxes render checked.

### Skill icons

`Skill` frontmatter gains an optional `icon:` (an emoji). Surfaced by
`/api/skills*` and shown on the Skills page and in the checkbox lists. Absent →
a default glyph. The three shipped skills get icons.

### Rendering helpers (`agentspec.py`)

- `AVAILABLE_TOOLS` — curated tool list.
- `render_manifest(fields)` / `render_agent_md(name, description, tools, body)`.
- `mutate_manifest_yaml(text, **changes)` / `mutate_agent_md(text, ...)` —
  parse-mutate-dump for surgical edits that preserve unrelated fields.
- `validate_agent_name(name)`.

### UI

- **Skills page** — an icon column.
- **Agents page** — a "New Agent" button → `/agents/new`.
- **New Agent** (`/agents/new`) — a guided form: name, description, role,
  model, skill checkboxes (with icons), tool checkboxes, prompt body. Submit →
  `POST /api/agents` → shows the PR link / links to Changes.
- **Agent detail → Config tab** — skills and tools become interactive
  checkboxes plus an editable description and a "Save (opens PR)" button →
  `PATCH …/config`. The existing freeform "Edit with platform-coder" and
  "Run now" sections stay.

## Testing

- `tiers`: `agent.md` frontmatter change → Tier 2; body-only → Tier 1.
- `agentspec`: render/round-trip manifest + agent.md; tools all-checked omits
  the line; name validation.
- API: create agent (new files, PR), reject dup/bad-name/unknown-skill/-tool;
  patch config round-trips and no-op returns tier 0; `agent-tools` lists.
- skills: `icon` surfaces through the API.

## Out of scope (YAGNI)

Editing concurrency/timeout/schedule/secrets from the UI (still file/manifest);
multi-step wizard chrome; per-tool audit rows (the PR diff is the audit trail).
