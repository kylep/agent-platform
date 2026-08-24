"""Export the git-synced `agents/` tree as the definitions API's import payload.

Definitions are ROWS now (docs/design/15). This was the one-shot bridge between
the two eras: it reads the file tree the platform used to boot from — an
`agent.md` (YAML frontmatter + prompt body), a `manifest.yaml`, an optional
`entrypoints.yaml` — and emits exactly the JSON list `POST /api/agents/import`
accepts.

**That migration has run, and `agents/` is deleted.** So this now reads an OLD
checkout — a commit from before the deletion, or a restored backup — and
pointing it at this repo correctly reports that there is nothing to export. It
is kept as the audited path from the file era to a row, not as anything the
platform reads at runtime: nothing may grow a dependency on the file layout
again.

Usage (`--root` is now effectively required — this checkout has no tree):
    python -m agentplatform.export_agents --root <old-checkout> --out /tmp/agents.json
    python -m agentplatform.export_agents --root <old-checkout> --check

Both modes are STRICT and all-or-nothing, matching the import endpoint: a
directory that fails to parse, names a grant the repo does not ship, or carries
a key this exporter would silently drop is an error, and nothing is written. A
half-migrated platform is worse than an unmigrated one, and the whole value of
the export is that what it emits is what the files meant.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml
from pydantic import ValidationError

from agentplatform.agentdefs import AgentDefModel, validate_def
from agentplatform.agentspec import (CLAUDE_TOOLS, GRANTABLE_PLATFORM_TOOLS,
                                     TOOL_HELP)
from agentplatform.secretregistry import SecretRegistry
from agentplatform.skills import SkillStore, parse_frontmatter
from agentplatform.toolregistry import ToolRegistry

# services/backend/agentplatform/export_agents.py -> the checkout root, so the
# script runs from anywhere without being told where the repo is.
REPO_ROOT = Path(__file__).resolve().parents[3]

# The harness tools the runner ALWAYS denies (`runner._SENSITIVE_TOOLS`), read
# off the same help table the /help/tools page renders so the two cannot drift.
SENSITIVE_TOOLS: set[str] = {t["name"] for t in TOOL_HELP if t.get("sensitive")}

# manifest.yaml keys that became columns of the same name.
MANIFEST_FIELDS: tuple[str, ...] = (
    "role", "concurrency", "timeout_seconds", "skills", "secrets",
    "description", "model", "system", "can_invoke",
    "transcript_retention_days", "result_topic",
)
# Keys the file era already called deprecated. `schedule` is carried over as a
# cron entry (below) so nothing stops firing; `memory` was superseded by the
# memory TOOL grant (docs/design/12) and stopped being consulted before this
# migration, so it is dropped on purpose rather than silently.
MANIFEST_DEPRECATED: tuple[str, ...] = ("schedule", "memory")

FRONTMATTER_KEYS: tuple[str, ...] = ("name", "description", "tools")
ENTRYPOINT_KEYS: tuple[str, ...] = ("cron", "timezone", "webhooks", "kafka")


def _declared_tools(fm: dict) -> list[str] | None:
    """The tools an agent.md frontmatter declares, or None for NO `tools:` line.

    The distinction is the whole reason this returns an optional: in the file
    era a missing line meant "all tools", and an empty one meant none."""
    if "tools" not in fm:
        return None
    raw = fm["tools"]
    if raw is None:
        # `tools:` with nothing after it is YAML null, and it means the line is
        # there and grants nothing. Stringifying it would export the literal
        # tool name "None" (the file era's parser did exactly that).
        return []
    if isinstance(raw, list):
        return [str(t).strip() for t in raw if str(t).strip()]
    return [t.strip() for t in str(raw).split(",") if t.strip()]


def split_tools(declared: list[str] | None) -> tuple[list[str], list[str]]:
    """A `tools:` line as the row's two grant lists: (harness, platform).

    A declared list is copied verbatim, split on the `mcp__` prefix — an
    unknown name is left where it lands so `validate_def` rejects it by name
    instead of this function quietly deciding it was a harness tool.

    NO line materializes the EFFECTIVE set rather than the literal one: the
    file rules read it as "all tools", but the runner denies the sensitive set
    unconditionally on every non-self-edit run, so what such an agent could
    actually use is the non-sensitive harness tools and no platform tools. A
    row's `harness_tools` is explicit — there is no "unset" to migrate — so the
    choice is between the effective set and something the agent never had."""
    if declared is None:
        return [t for t in CLAUDE_TOOLS if t not in SENSITIVE_TOOLS], []
    harness = [t for t in declared if not t.startswith("mcp__")]
    platform = [t for t in declared if t.startswith("mcp__")]
    return harness, platform


def _cron_entries(exprs: list[str], legacy: str) -> list[dict]:
    """Cron entries for a definition: entrypoints.yaml's expressions plus the
    deprecated manifest `schedule:`, unioned and deduplicated exactly as
    `AgentInfo.crons()` used to do it at read time. Each carries an empty
    prompt — the file era had no per-fire prompt, and the scheduler falls back
    to the generic one, which is what these agents have always received."""
    out: list[dict] = []
    for expr in [*exprs, legacy]:
        expr = str(expr).strip()
        if expr and not any(e["schedule"] == expr for e in out):
            out.append({"schedule": expr, "prompt": ""})
    return out


def load_agent(d: Path) -> tuple[dict, list[str]]:
    """One `agents/<name>/` directory → (import payload fields, problems).

    Problems are anything that could not be carried over faithfully — a file
    that will not parse, or a key this exporter would drop on the floor. They
    are collected rather than raised so one bad directory reports alongside the
    other nine instead of hiding them."""
    problems: list[str] = []

    def note(msg: str) -> None:
        problems.append(f"{d.name}: {msg}")

    fm: dict = {}
    body = ""
    md = d / "agent.md"
    if not md.is_file():
        note("no agent.md")
    else:
        try:
            fm, body = parse_frontmatter(md.read_text())
        except (OSError, yaml.YAMLError) as e:
            note(f"agent.md does not parse: {e}")
        if not isinstance(fm, dict):
            note("agent.md frontmatter is not a mapping")
            fm = {}
    for key in fm:
        if key not in FRONTMATTER_KEYS:
            note(f"agent.md frontmatter key {key!r} has no home in a row")
    if fm.get("name") and fm["name"] != d.name:
        note(f"agent.md declares name {fm['name']!r}, directory says {d.name!r}")

    raw: dict = {}
    manifest = d / "manifest.yaml"
    if not manifest.is_file():
        note("no manifest.yaml")
    else:
        try:
            raw = yaml.safe_load(manifest.read_text()) or {}
        except (OSError, yaml.YAMLError) as e:
            note(f"manifest.yaml does not parse: {e}")
        if not isinstance(raw, dict):
            note("manifest.yaml is not a mapping")
            raw = {}
    for key in raw:
        if key not in MANIFEST_FIELDS and key not in MANIFEST_DEPRECATED:
            note(f"manifest.yaml key {key!r} has no home in a row")

    ep: dict = {}
    ep_file = d / "entrypoints.yaml"
    if ep_file.is_file():
        try:
            ep = yaml.safe_load(ep_file.read_text()) or {}
        except (OSError, yaml.YAMLError) as e:
            note(f"entrypoints.yaml does not parse: {e}")
        if not isinstance(ep, dict):
            note("entrypoints.yaml is not a mapping")
            ep = {}
    for key in ep:
        if key not in ENTRYPOINT_KEYS:
            note(f"entrypoints.yaml key {key!r} has no home in a row")

    harness, platform = split_tools(_declared_tools(fm))
    payload: dict = {
        "name": d.name,
        # The prompt is the BODY. Name, description and tools are columns now,
        # and the runner re-renders the frontmatter from them (docs/design/15);
        # carrying the old header into the prompt would duplicate it in every
        # pod and hand the model a stale tool list to read.
        "prompt": body,
        "harness_tools": harness,
        "platform_tools": platform,
        # manifest.yaml is the platform's own view and the one the Manifest
        # model read, so it wins the description; the frontmatter's copy (the
        # CLI subagent picker's blurb) fills in only when there is no other.
        "description": raw.get("description") or fm.get("description") or "",
    }
    for field in MANIFEST_FIELDS:
        if field != "description" and field in raw:
            payload[field] = raw[field]
    crons = _cron_entries(ep.get("cron") or [], str(raw.get("schedule") or ""))
    payload["entrypoints"] = {
        "crons": crons,
        "webhooks": ep.get("webhooks") or [],
        # `kafka:` was the file era's name for the same list of topics.
        "topics": ep.get("kafka") or [],
        "timezone": ep.get("timezone") or "",
    }
    return payload, problems


def _registries(root: Path) -> dict[str, set[str]]:
    """The code-defined names a grant may reference, read from the same trees
    the API reads at runtime — so the export fails here, on a laptop, rather
    than as a 422 in the middle of the live import."""
    return {
        "skill_names": {s.name for s in SkillStore(root / "skills").list()},
        "secret_names": {s.name for s in SecretRegistry(root / "secrets").list()},
        "tool_names": (set(GRANTABLE_PLATFORM_TOOLS)
                       | set(ToolRegistry(root / "tools").mcp_names())),
    }


def export_tree(root: Path) -> tuple[list[dict], list[str]]:
    """The whole `agents/` tree under `root` as (import payload, problems).

    Agents come out sorted by name and every payload is the definition as
    `AgentDefModel` normalizes it, so a rerun against an unchanged tree is
    byte-identical and a diff shows only what actually moved."""
    agents_root = root / "agents"
    if not agents_root.is_dir():
        return [], [f"no agents directory at {agents_root}"]

    registries = _registries(root)
    payloads: list[dict] = []
    problems: list[str] = []
    for d in sorted(p for p in agents_root.iterdir() if p.is_dir()):
        payload, found = load_agent(d)
        problems += found
        try:
            model = AgentDefModel(**payload)
        except ValidationError as e:
            problems += [
                f"{d.name}: {'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
                for err in e.errors()]
            continue
        problems += [f"{d.name}: {p}" for p in validate_def(model, **registries)]
        payloads.append(model.model_dump(mode="json"))
    return payloads, problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=REPO_ROOT,
                    help="OLD checkout root holding agents/, skills/, secrets/, "
                         "tools/ (default: this checkout)")
    ap.add_argument("--out", type=Path,
                    help="write the payload here instead of stdout")
    ap.add_argument("--check", action="store_true",
                    help="parse and validate only; write nothing")
    args = ap.parse_args(argv)

    payloads, problems = export_tree(args.root)
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        print(f"{len(problems)} problem(s); nothing exported", file=sys.stderr)
        return 1
    # sort_keys + a trailing newline: the output is meant to be committed to a
    # scratch file, diffed, and re-run.
    text = json.dumps(payloads, indent=2, sort_keys=True) + "\n"
    if args.check:
        print(f"{len(payloads)} agent(s) OK", file=sys.stderr)
        return 0
    if args.out:
        args.out.write_text(text)
        print(f"wrote {len(payloads)} agent(s) to {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
