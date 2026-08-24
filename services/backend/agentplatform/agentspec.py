"""The fixed vocabulary an agent definition is written in.

What an agent IS lives in `agent_defs` (docs/design/15) and is validated by
`agentdefs`; what it may be *made of* is code, and this module is the part of
that vocabulary the platform hard-codes: the grantable harness/platform tool
names, the help text that explains each one, the models the picker offers, and
the slug rule every agent name obeys.

It used to also render `agents/<name>/{agent.md,manifest.yaml}` for the PR-based
editor. Definitions are rows now — nothing writes those files — so the renderers
went with the flow they served.
"""
from __future__ import annotations

import re

# The Claude Code tools an agent may be granted. Historically these were an
# `agent.md` frontmatter `tools:` line, where omitting the line meant "all
# tools"; a row's `harness_tools` is explicit, and empty means empty.
CLAUDE_TOOLS: list[str] = [
    "Bash", "Read", "Write", "Edit", "Glob", "Grep",
    "WebSearch", "WebFetch", "Task", "TodoWrite", "NotebookEdit",
]

# Tools the platform's own MCP broker exposes (services/mcp-broker). Agents that
# act on the platform declare these instead of Bash, so they get a token-scoped
# API call rather than a shell. Keep in sync with broker.py's @mcp.tool set.
PLATFORM_MCP_TOOLS: list[str] = [
    "mcp__platform__runs_read", "mcp__platform__runs_write",
    "mcp__platform__metrics",
    "mcp__platform__query_app",
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
    {"name": "TodoWrite", "kind": "claude", "display_name": "Todo",
     "description": "Keep an internal working task list during a run "
                    "(harmless bookkeeping; helps long runs stay on track)."},
    {"name": "NotebookEdit", "kind": "claude", "sensitive": True,
     "description": "Edit Jupyter notebook cells."},
    {"name": "mcp__platform__runs_read", "kind": "platform",
     "description": "Read run history: list recent runs (optionally just "
                    "those missing a summary), fetch one run's full detail, "
                    "or list existing run tags. Read-only."},
    {"name": "mcp__platform__runs_write", "kind": "platform",
     "description": "Annotate a run with a one-line summary and tags — how "
                    "run-summarizer files history for skimming. The only "
                    "run mutation."},
    {"name": "mcp__platform__metrics", "kind": "platform",
     "description": "Platform health metrics: run volumes/success/tokens "
                    "(overview), per-agent metrics incl. failure streaks, or "
                    "event-bus health (lag, DLQ backlog). Read-only."},
    {"name": "mcp__platform__query_app", "kind": "platform",
     "description": "Call a read-only API endpoint of an installed platform "
                    "app through the traversal-guarded proxy — e.g. query "
                    "the news archive by day/topic/keyword. GET only; "
                    "mutations stay with the app's own flows. Each app's "
                    "companion skill documents its endpoints."},
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
