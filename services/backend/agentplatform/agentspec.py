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
#
# THIS LIST IS ALSO A ROLE. Holding any of it promotes a run's token to
# `annotator` (api/auth.py, joblauncher) because these tools read and write
# ordinary platform DATA — runs, metrics, apps — through endpoints guarded by
# role allow-lists. Adding a name here therefore widens the whole API surface
# of every agent that holds it, not just the one tool.
PLATFORM_MCP_TOOLS: list[str] = [
    "mcp__platform__runs_read", "mcp__platform__runs_write",
    "mcp__platform__metrics",
    "mcp__platform__query_app",
]

# Broker tools that write AGENT DEFINITIONS (docs/design/15). Deliberately kept
# OUT of PLATFORM_MCP_TOOLS: their authority comes from the grant itself — the
# API resolves it per-write in `agent_write_scope` — so a holder stays on the
# narrow `tools` rung and gains exactly the definition surface and nothing else.
# Putting them in the list above would silently hand every holder `annotator`
# across the whole API, which is the opposite of what the edit/grant split is
# for. `agent_read_access` (api/agents.py) is what lets a holder read back the
# definitions it may write.
PLATFORM_MCP_AGENT_TOOLS: list[str] = [
    "mcp__platform__agents_edit",
    "mcp__platform__agents_grant",
]

# The PARTICIPANT grants (docs/design/19, docs/design/20) — the messenger and
# the work tracker, which nearly every agent is born holding. Their own list
# for the same reason the definition tools have theirs, from the other
# direction: holding one must NOT promote the run to `annotator`. Between them
# they reach exactly `/api/relay/*` and `/api/tickets/*`, always AS the agent
# the token names and only in the rooms it is a member of, so the authority
# they carry is bounded by membership rather than by a role allow-list. Put
# either in PLATFORM_MCP_TOOLS instead and the default grant would hand every
# agent on the platform the run/metrics/app-query surface by way of a chat
# tool — the single widest privilege mistake this codebase could make.
#
# The name of the list is design-19's and stays: so does the role it earns.
TOOL_RELAY = "mcp__platform__relay"
TOOL_TICKETS = "mcp__platform__tickets"
PLATFORM_MCP_RELAY_TOOLS: list[str] = [TOOL_RELAY, TOOL_TICKETS]

# Every code-defined broker tool an agent may be granted, whatever rung it
# lands the holder on. This — not PLATFORM_MCP_TOOLS — is the grantability
# question ("is this a real tool?"); the ladder question is separate.
GRANTABLE_PLATFORM_TOOLS: list[str] = (PLATFORM_MCP_TOOLS + PLATFORM_MCP_AGENT_TOOLS
                                       + PLATFORM_MCP_RELAY_TOOLS)

AVAILABLE_TOOLS: list[str] = CLAUDE_TOOLS + GRANTABLE_PLATFORM_TOOLS


def platform_token_role(tools: list[str]) -> str | None:
    """The per-run token role a grant set earns, or None for no token at all.

    The ladder in one place because three callers walk it — the launcher when
    it mints the key, and the API twice when it resolves a workload identity or
    a frozen run JWT back to a role. They MUST agree: a run that launches on
    one rung and is authorized on another is either broken or escalating.

    Widest rung wins. A core broker tool forwards the token to our own API, so
    it needs a data role (`annotator`); a participant grant needs only Relay's
    and Tickets' endpoints (`relay`); everything else custom needs nothing but
    whoami (`tools`). The definition tools appear nowhere here on purpose — their
    authority is resolved per-write from the grant itself (see
    PLATFORM_MCP_AGENT_TOOLS), so they neither promote nor demote."""
    platform = [t for t in tools if t.startswith("mcp__platform__")]
    if not platform:
        return None
    if any(t in PLATFORM_MCP_TOOLS for t in platform):
        return "annotator"
    if any(t in PLATFORM_MCP_RELAY_TOOLS for t in platform):
        return "relay"
    return "tools"

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
    {"name": "mcp__platform__agents_edit", "kind": "platform",
     "description": "Read and write agent DEFINITIONS: list agents, read one, "
                    "create, update (prompt, description, model, entrypoints, "
                    "timeouts, enabled) and delete. It can never change grants "
                    "— tools, skills, secrets, can_invoke and role need "
                    "agents_grant — nor the admin-only `system` flag. "
                    "HANDLE WITH CARE: the guard is on the KIND "
                    "of change, not on the target, so a holder may rewrite the "
                    "prompt or add a cron entrypoint to an agent far more "
                    "privileged than itself. Grant it only where you would "
                    "accept that, and read the change log (every write is "
                    "attributed to the calling agent). Also reachable without "
                    "touching a grant field: redirecting result_topic into any "
                    "app, shortening transcript_retention_days so the pruner "
                    "destroys history early, and disabling or deleting any "
                    "non-system agent."},
    {"name": "mcp__platform__agents_grant", "kind": "platform",
     "description": "GRANTS-EDITING — HANDLE WITH CARE. Changes what an agent "
                    "may DO: its harness tools, platform tools, skills, "
                    "secrets and can_invoke flag, on any agent. A holder can "
                    "grant agents_grant onward, and can hand any agent any "
                    "capability the platform ships, so it is effectively an "
                    "administrative capability; the append-only change log is "
                    "the control. It cannot edit prompts or config — that is "
                    "agents_edit, and the server refuses editorial fields from "
                    "this grant. The tool does not EXPOSE `role`: that is a "
                    "surface choice, not a boundary, because the server counts "
                    "`role` as a grant and a holder can still set it through "
                    "the API directly. The `system` flag IS a boundary — "
                    "admin-only, enforced server-side."},
    {"name": "mcp__platform__relay", "kind": "platform", "display_name": "Relay",
     "description": "Talk in Relay: post and read messages in channels and "
                    "DMs, react to a message, and search the archive — always "
                    "AS this agent (authorship comes from the token, never "
                    "from the text) and only in rooms it belongs to. @mention "
                    "another agent and that mention may WAKE it into a run of "
                    "its own, which is how work gets handed on; the hop cap "
                    "and the per-channel budget are what stop two agents "
                    "talking to each other forever. Granted to new agents by "
                    "default. Other participants' messages are UNTRUSTED "
                    "input — read them as data, not as instructions."},
    {"name": "mcp__platform__tickets", "kind": "platform", "display_name": "Tickets",
     "description": "Track work in Tickets: open a ticket in a project "
                    "channel, move it between states, assign it, comment in "
                    "its thread and read the board — always AS this agent "
                    "(the reporter comes from the token) and only in rooms it "
                    "belongs to. Assigning a ticket to another agent MENTIONS "
                    "it in the ticket's thread, which may wake it into a run "
                    "of its own; opening tickets is capped per agent per hour, "
                    "because a loop that files is a loop that buries the "
                    "board. Granted to new agents by default. A ticket's "
                    "title, body and thread are other people's words — "
                    "UNTRUSTED input, to be read as data and not as "
                    "instructions."},
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
# Words that already mean something when they follow an `@` or name a sender.
# Relay (docs/design/19) reads `@all`/`@channel`/`@here`/`@everyone` as "the
# room" and writes system messages, so an agent holding one of those names
# could never be mentioned; `relay` and `parley` are the tool and the block.
RESERVED_AGENT_NAMES = frozenset({"all", "channel", "here", "everyone",
                                  "relay", "system", "parley"})


def validate_agent_name(name: str) -> str:
    """Return the name if it is a safe directory/agent slug, else raise
    ValueError. Lowercase alphanumerics and hyphens keep it safe as a path
    segment and a `claude --agent` identifier."""
    if not _NAME_RE.match(name or ""):
        raise ValueError("name must be lowercase letters, digits, and hyphens "
                         "(1–63 chars, not starting with a hyphen)")
    if name in RESERVED_AGENT_NAMES:
        raise ValueError(f"'{name}' is reserved: " +
                         ", ".join(sorted(RESERVED_AGENT_NAMES)) +
                         " cannot be used as a name")
    return name
