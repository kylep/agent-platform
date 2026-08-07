"""Render and surgically edit an agent's on-disk definition.

An agent is two files under `agents/<name>/`:
  - `manifest.yaml` — the platform Manifest (role, skills, secrets, …)
  - `agent.md` — YAML frontmatter (name, description, tools) + prompt body

These helpers turn structured edits (from the UI) into file *content*, which the
deterministic git path (`EditService`) then commits or opens as a PR. Edits are
surgical: unrelated manifest fields and frontmatter keys are preserved so a
checkbox change produces a minimal, reviewable diff.
"""
from __future__ import annotations

import re

import yaml

from agentplatform.skills import parse_frontmatter

# The Claude Code tools an agent may be granted via `agent.md` frontmatter
# `tools:`. Omitting the line entirely means "all tools" (the CLI default), so
# the editor treats a fully-checked list as unrestricted and drops the line.
CLAUDE_TOOLS: list[str] = [
    "Bash", "Read", "Write", "Edit", "Glob", "Grep",
    "WebSearch", "WebFetch", "Task", "TodoWrite", "NotebookEdit",
]

# Tools the platform's own MCP broker exposes (services/mcp-broker). Agents that
# act on the platform declare these instead of Bash, so they get a token-scoped
# API call rather than a shell. Keep in sync with broker.py's @mcp.tool set.
PLATFORM_MCP_TOOLS: list[str] = [
    "mcp__platform__list_runs", "mcp__platform__get_run",
    "mcp__platform__list_tags", "mcp__platform__annotate_run",
    "mcp__platform__metrics_overview", "mcp__platform__metrics_agents",
    "mcp__platform__kafka_health",
    "mcp__platform__read_memory", "mcp__platform__save_memory",
    "mcp__platform__post_message",
    "mcp__platform__app_api",
]

AVAILABLE_TOOLS: list[str] = CLAUDE_TOOLS + PLATFORM_MCP_TOOLS

# Help text for every grantable tool (the /help/tools page + picker docs).
# A test asserts this covers AVAILABLE_TOOLS exactly — a tool cannot be added
# without explaining what turning it on actually does. `sensitive: True`
# marks the runner's always-denied set: declaring those does NOTHING for a
# normal agent (they are self-edit only — the trifecta break, design/08).
TOOL_HELP: list[dict] = [
    {"name": "Bash", "kind": "claude", "sensitive": True,
     "description": "Run shell commands inside the agent's pod."},
    {"name": "Read", "kind": "claude", "sensitive": True,
     "description": "Read any file in the pod's filesystem."},
    {"name": "Write", "kind": "claude", "sensitive": True,
     "description": "Create or overwrite files in the pod."},
    {"name": "Edit", "kind": "claude", "sensitive": True,
     "description": "Make targeted edits to files in the pod."},
    {"name": "Glob", "kind": "claude",
     "description": "Find files by name pattern (read-only discovery)."},
    {"name": "Grep", "kind": "claude",
     "description": "Search file contents by regex (read-only discovery)."},
    {"name": "WebSearch", "kind": "claude",
     "description": "Search the public web. This is an UNTRUSTED-INPUT "
                    "channel: anything the agent reads can try to steer it, "
                    "so keep web-reading agents credential-free."},
    {"name": "WebFetch", "kind": "claude",
     "description": "Fetch a URL and read the page. Same untrusted-input "
                    "caution as WebSearch."},
    {"name": "Task", "kind": "claude",
     "description": "Spawn subagents to work on subtasks in parallel."},
    {"name": "TodoWrite", "kind": "claude",
     "description": "Keep an internal working task list during a run "
                    "(harmless bookkeeping; helps long runs stay on track)."},
    {"name": "NotebookEdit", "kind": "claude", "sensitive": True,
     "description": "Edit Jupyter notebook cells."},
    {"name": "mcp__platform__list_runs", "kind": "platform",
     "description": "List recent runs (optionally just those missing a "
                    "summary). Read-only."},
    {"name": "mcp__platform__get_run", "kind": "platform",
     "description": "Read one run's full detail: agent, trigger, state, "
                    "prompt, metrics. Read-only."},
    {"name": "mcp__platform__list_tags", "kind": "platform",
     "description": "List the run tags that already exist (so taggers reuse "
                    "instead of inventing). Read-only."},
    {"name": "mcp__platform__annotate_run", "kind": "platform",
     "description": "Write a run's one-line summary and tags — how "
                    "run-summarizer files history for skimming."},
    {"name": "mcp__platform__metrics_overview", "kind": "platform",
     "description": "Platform-wide run metrics (volumes, success rate, "
                    "tokens). Read-only."},
    {"name": "mcp__platform__metrics_agents", "kind": "platform",
     "description": "Per-agent metrics including failure streaks — what "
                    "health-monitor watches. Read-only."},
    {"name": "mcp__platform__kafka_health", "kind": "platform",
     "description": "Event-bus health: reachability, lag, DLQ backlog. "
                    "Read-only."},
    {"name": "mcp__platform__read_memory", "kind": "platform",
     "description": "Search the agent's OWN memory namespace (it can never "
                    "read another agent's)."},
    {"name": "mcp__platform__save_memory", "kind": "platform",
     "description": "Save a memory in the agent's own namespace — durable "
                    "notes across runs (the write half of read_memory)."},
    {"name": "mcp__platform__post_message", "kind": "platform",
     "description": "Post to a Discord channel by name, via the connector. "
                    "The agent never holds a Discord credential; text is "
                    "length-capped and mass-pings are defanged."},
    {"name": "mcp__platform__app_api", "kind": "platform",
     "description": "Read an app's API (GET only) through the platform "
                    "proxy — e.g. query the news archive. Traversal-guarded; "
                    "mutations stay with the app's own flows."},
]

# Models the UI offers for an agent's `model:` (runner passes it to
# `claude --model`). ADVISORY, not an allow-list: the server accepts any value,
# so a brand-new model isn't blocked by a stale registry (the inverse trade-off
# from AVAILABLE_TOOLS, where unknown = privilege escalation; an unknown model
# just fails the run visibly). Verified against the models docs 2026-07-30.
KNOWN_MODELS: list[dict[str, str]] = [
    {"id": "", "label": "Platform default"},
    {"id": "claude-fable-5", "label": "Fable 5 — most capable, long-running agents"},
    {"id": "claude-opus-5", "label": "Opus 5 — complex agentic work"},
    {"id": "claude-sonnet-5", "label": "Sonnet 5 — speed + intelligence"},
    {"id": "claude-haiku-4-5", "label": "Haiku 4.5 — fastest"},
    {"id": "claude-opus-4-8", "label": "Opus 4.8 (legacy)"},
    {"id": "claude-sonnet-4-6", "label": "Sonnet 4.6 (legacy)"},
    {"id": "claude-sonnet-4-5", "label": "Sonnet 4.5 (legacy)"},
    {"id": "claude-opus-4-5", "label": "Opus 4.5 (legacy)"},
]

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


def validate_agent_name(name: str) -> str:
    """Return the name if it is a safe directory/agent slug, else raise
    ValueError. Lowercase alphanumerics and hyphens keep it safe as a path
    segment and a `claude --agent` identifier."""
    if not _NAME_RE.match(name or ""):
        raise ValueError("name must be lowercase letters, digits, and hyphens "
                         "(1–63 chars, not starting with a hyphen)")
    return name


def _tools_line(tools: list[str]) -> str | None:
    """The frontmatter `tools:` value for a selection, or None to omit the line
    (which the CLI reads as 'all tools'). All-selected → unrestricted → omit.

    A tool this build doesn't know about is preserved verbatim rather than
    dropped: silently forgetting an entry would *widen* the agent's access (drop
    enough of them and the line vanishes, which means unrestricted), so an
    unrecognized name must survive an edit it wasn't part of."""
    selected = set(tools)
    chosen = [t for t in AVAILABLE_TOOLS if t in selected]
    chosen += [t for t in dict.fromkeys(tools) if t not in set(AVAILABLE_TOOLS)]
    if not chosen or selected >= set(AVAILABLE_TOOLS):
        return None
    return ", ".join(chosen)


def render_agent_md(name: str, description: str, tools: list[str], body: str) -> str:
    """Compose an `agent.md` from its parts. Frontmatter carries name,
    description, and (only when restricted) tools; then the prompt body."""
    fm: dict[str, str] = {"name": name, "description": description}
    line = _tools_line(tools)
    if line is not None:
        fm["tools"] = line
    front = yaml.safe_dump(fm, sort_keys=False, default_flow_style=False).strip()
    return f"---\n{front}\n---\n{body.strip()}\n"


def render_manifest(fields: dict) -> str:
    """Dump a manifest dict, dropping empty/None values so the file stays lean
    (defaults are supplied by the Manifest model at load time)."""
    clean = {k: v for k, v in fields.items()
             if v not in (None, "", [], {})}
    return yaml.safe_dump(clean, sort_keys=False, default_flow_style=False)


def mutate_manifest_yaml(text: str, *, skills: list[str] | None = None,
                         description: str | None = None) -> str:
    """Parse an existing manifest, apply only the given changes, re-dump.
    Preserves unrelated fields (concurrency, secrets, can_invoke, …). Comments
    are not preserved on a real edit — a structured edit normalizes the file,
    and the PR diff is the review surface. A *semantic* no-op returns the
    original text verbatim (so it never produces a spurious, comment-stripping
    diff that would sneak straight to main)."""
    before = yaml.safe_load(text) or {}
    data = dict(before)
    if description is not None:
        data["description"] = description
    if skills is not None:
        if skills:
            data["skills"] = skills
        else:
            data.pop("skills", None)   # empty list → omit the key
    if data == before:
        return text
    return yaml.safe_dump(data, sort_keys=False, default_flow_style=False)


def mutate_agent_md(text: str, *, tools: list[str] | None = None,
                    description: str | None = None) -> str:
    """Update an `agent.md`'s frontmatter (tools/description) in place, keeping
    its name and prompt body. A semantic no-op (frontmatter unchanged) returns
    the original text verbatim so it never emits a spurious diff."""
    fm, body = parse_frontmatter(text)
    before = dict(fm)
    if description is not None:
        fm["description"] = description
    if tools is not None:
        line = _tools_line(tools)
        if line is not None:
            fm["tools"] = line
        else:
            fm.pop("tools", None)      # all/none → unrestricted → omit
    if fm == before:
        return text
    # Preserve a stable key order: name, description, tools, then anything else.
    order = ["name", "description", "tools"]
    ordered = {k: fm[k] for k in order if k in fm}
    ordered.update({k: v for k, v in fm.items() if k not in order})
    front = yaml.safe_dump(ordered, sort_keys=False, default_flow_style=False).strip()
    return f"---\n{front}\n---\n{body.strip()}\n"


def parse_agent_tools(text: str) -> list[str] | None:
    """The tools an `agent.md` declares, or None when it has no `tools:` line
    (meaning: all tools). A present-but-empty line yields []."""
    fm, _ = parse_frontmatter(text)
    if "tools" not in fm:
        return None
    raw = fm["tools"]
    if isinstance(raw, list):
        return [str(t).strip() for t in raw if str(t).strip()]
    return [t.strip() for t in str(raw).split(",") if t.strip()]
