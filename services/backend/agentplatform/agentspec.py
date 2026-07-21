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
AVAILABLE_TOOLS: list[str] = [
    "Bash", "Read", "Write", "Edit", "Glob", "Grep",
    "WebSearch", "WebFetch", "Task", "TodoWrite", "NotebookEdit",
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
    (which the CLI reads as 'all tools'). All-selected → unrestricted → omit."""
    chosen = [t for t in AVAILABLE_TOOLS if t in set(tools)]
    if not chosen or len(chosen) == len(AVAILABLE_TOOLS):
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
