"""Tier computation for platform self-edits.

The self-hosting loop must never let an agent silently change anything with
security or resource impact. Every proposed change is classified from the
files (and manifest fields) it touches:

- **Tier 1** — deterministic, low-risk edits that may be committed straight
  to the default branch: editing an existing agent's prompt (`agent.md`
  body) or a safe manifest field.
- **Tier 2** — everything else (new/removed agents, sensitive manifest
  fields, anything outside `agents/`): must go through a branch + PR for
  human review.

Fail closed: anything unrecognized is Tier 2.
"""
from dataclasses import dataclass, field

# Manifest fields an agent may change about itself without review. Everything
# not listed here (role, concurrency, timeout_seconds, secrets, skills, ...)
# is sensitive and forces Tier 2.
SAFE_MANIFEST_FIELDS = frozenset({"description"})

TIER_DIRECT = 1
TIER_PR = 2


@dataclass(frozen=True)
class FileChange:
    path: str                                   # repo-relative, e.g. agents/x/agent.md
    kind: str                                   # "added" | "modified" | "deleted"
    manifest_fields: frozenset[str] = field(default_factory=frozenset)  # for manifest.yaml edits


def classify_tier(changes: list[FileChange]) -> int:
    """Return TIER_DIRECT only if every change is individually Tier-1-safe;
    otherwise TIER_PR. An empty change set is Tier 1 (a no-op)."""
    for c in changes:
        parts = c.path.split("/")
        # Must live at agents/<name>/<file>; anything else is platform code.
        if len(parts) != 3 or parts[0] != "agents":
            return TIER_PR
        fname = parts[2]
        if fname == "agent.md":
            # Editing an existing agent's body is safe; adding (a new agent)
            # or deleting one is not.
            if c.kind != "modified":
                return TIER_PR
        elif fname == "manifest.yaml":
            # Only in-place edits touching solely safe fields are direct.
            if c.kind != "modified" or (c.manifest_fields - SAFE_MANIFEST_FIELDS):
                return TIER_PR
        else:
            return TIER_PR
    return TIER_DIRECT
