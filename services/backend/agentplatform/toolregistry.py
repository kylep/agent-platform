"""Custom platform tools as first-class building blocks (docs/design/12).

A tool is a directory under the repo's `tools/` tree:

    tools/<name>/
      tool.yaml          # manifest: description, JSON-schema params, infra
      run.py             # trusted entrypoint the executor runs (args on stdin)
      requirements.txt   # optional; CI bakes the union into the executor image
      test_run.py        # optional; CI's tools job runs it

Agents declare a custom tool exactly like a core one — `mcp__platform__<name>`
in their `tools:` — and the MCP broker forwards calls to the tool-executor,
which runs `run.py` in a subprocess seeing only the tool's declared secrets.
The model controls ARGUMENTS only, never code: run.py arrived via PR review.

Mirrors skill/report registries: folders in the synced git checkout, reloaded
on read, folder name as the default name.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ValidationError, field_validator, model_validator

# MCP tool-name style, and safe to embed in env prefixes / pg identifiers.
_NAME = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
_ENV = re.compile(r"^[A-Z][A-Z0-9_]*$")

# Suffixes of the broker's built-in mcp__platform__* tools. A custom tool may
# not shadow one (the broker registers customs beside these; a collision would
# be ambiguous). Kept here, next to the validation that uses it; agentspec's
# GRANTABLE_PLATFORM_TOOLS derives from the same set via a lockstep test.
# `agents_edit`/`agents_grant` are core for exactly this reason even though
# they sit on a different auth rung: they are broker-resident (they forward the
# caller's bearer, which the executor never sees), so a `tools/agents_edit/`
# directory would be a silent collision rather than a second implementation.
CORE_TOOL_SUFFIXES = frozenset({
    "runs_read", "runs_write", "metrics", "query_app",
    "agents_edit", "agents_grant", "relay", "tickets", "wiki",
    "get_quota_usage", "artifacts", "image_gen", "quota_ok", "agent_self",
    "discord", "health_incident", "tasks", "apps", "app_data",
})


class ToolInfra(BaseModel):
    """Infrastructure the tool declares (docs/design/12): bound secrets and an
    optionally provisioned private pg schema (`tool_<name>`, creds delivered
    to the subprocess as TOOL_DB_URL). Secrets bind by BLOCK NAME, same as
    skills: the block's keys are already env-var style, and the executor
    injects them into the subprocess env at call time (never into a pod)."""
    secrets: list[str] = []
    database: bool = False

    @field_validator("secrets", mode="before")
    @classmethod
    def _coerce_names(cls, v):
        # Accept a bare name or a {name: ...} mapping (skill-frontmatter style).
        return [s["name"] if isinstance(s, dict) else s for s in (v or [])]


APP_VERBS = ("read", "create", "update", "delete")
# appdata.definitions.NAME_RE: a role is a name in an App tool definition.
_ROLE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


class AppAccess(BaseModel):
    """What a tool may reach in Apps (docs/design/39, "Tool-call credentials"):
    collection ROLES and verbs. A call credential's scope is the caller's own
    access cut down to these, so this is a ceiling the manifest asks for, never
    a grant. Roles bind to real collections only through each App's App tool
    fact for this tool (a `tool` definition Kyle approves); a role's name
    alone binds nothing."""
    model_config = {"extra": "forbid"}
    roles: list[str]
    verbs: list[Literal["read", "create", "update", "delete"]]

    @field_validator("roles")
    @classmethod
    def _roles(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("app_access.roles must name at least one role")
        bad = [r for r in v if not _ROLE.match(r)]
        if bad:
            raise ValueError(f"app_access.roles must match {_ROLE.pattern}, got {bad}")
        if len(set(v)) != len(v):
            raise ValueError("app_access.roles lists a role twice")
        return v

    @field_validator("verbs")
    @classmethod
    def _verbs(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("app_access.verbs must name at least one verb")
        if len(set(v)) != len(v):
            raise ValueError("app_access.verbs lists a verb twice")
        return v


# `broker.FILES_ARG`: the one argument name no manifest may claim.
RESERVED_PARAM = "files"

# The executor caps every tool's output at 256 KiB (tools/README.md); a view
# action can only promise less.
VIEW_MAX_BYTES = 262_144
VIEW_MAX_ROWS = 10_000


def _object_schema(v, what: str) -> dict:
    # Structural only: jsonschema is a dev dependency, so the catalog lockstep
    # test runs the full Draft 2020-12 check on what reaches the catalog.
    if not isinstance(v, dict) or v.get("type") != "object":
        raise ValueError(f"{what} must be a JSON Schema with type: object")
    if not isinstance(v.get("properties", {}), dict):
        raise ValueError(f"{what}.properties must be a mapping")
    return v


class ViewAction(BaseModel):
    """A read action a tool offers as a tool view (docs/design/39, "Tool
    views" -> Eligibility). Declaring one is necessary, not sufficient: the
    catalog marks it `view_eligible` only when the reviewed effect row also
    says it does nothing but `reads_sensitive`."""
    model_config = {"extra": "forbid"}
    output_schema: dict
    max_rows: int
    max_bytes: int
    # App tool roles the action reads; the App's App tool fact maps each to a
    # real collection, and the view's credential reaches only those.
    sources: list[str]
    # The arguments a view binding may pass, beside the fixed `action`.
    params: dict = {"type": "object", "properties": {}, "additionalProperties": False}

    @field_validator("output_schema")
    @classmethod
    def _output(cls, v: dict) -> dict:
        return _object_schema(v, "output_schema")

    @field_validator("params")
    @classmethod
    def _params(cls, v: dict) -> dict:
        return _object_schema(v, "params")

    @field_validator("max_rows")
    @classmethod
    def _rows(cls, v: int) -> int:
        if not 1 <= v <= VIEW_MAX_ROWS:
            raise ValueError(f"max_rows must be between 1 and {VIEW_MAX_ROWS}")
        return v

    @field_validator("max_bytes")
    @classmethod
    def _bytes(cls, v: int) -> int:
        if not 1 <= v <= VIEW_MAX_BYTES:
            raise ValueError(f"max_bytes must be between 1 and {VIEW_MAX_BYTES}")
        return v

    @field_validator("sources")
    @classmethod
    def _sources(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("sources must name at least one role")
        if len(set(v)) != len(v):
            raise ValueError("sources lists a role twice")
        return v


class ToolManifest(BaseModel):
    name: str
    # Product classification only: MCP still calls every callable a Tool.
    # This never changes a grant or execution path.
    category: Literal["service_connector", "platform_capability", "domain_capability",
                      "image_generation"] = "service_connector"
    # What the model sees as the MCP tool description — write it for the model.
    description: str
    # JSON Schema (object) for the tool's arguments; the executor validates
    # every call against it before running run.py.
    params: dict = {"type": "object", "properties": {}}
    infra: ToolInfra = ToolInfra()
    # Clamped again by the executor; the ceiling is what polling tools
    # (image generation, docs/design/23) actually need.
    timeout_seconds: int = 30
    # The broker's scan skips an internal tool, so no agent can call it; the
    # platform API is its only caller. `image_gen` is the first.
    internal: bool = False
    # Absent: the broker asks for no call credential and the tool can't reach
    # App data at all.
    app_access: AppAccess | None = None
    # Per-action tool view declarations, keyed by action (plan decision D3).
    view_actions: dict[str, ViewAction] = {}

    @field_validator("name")
    @classmethod
    def _name_style(cls, v: str) -> str:
        if not _NAME.match(v):
            raise ValueError(f"tool name must match {_NAME.pattern}, got {v!r}")
        return v

    @model_validator(mode="after")
    def _no_core_shadow(self):
        # An internal tool is exempt: the API runs it by directory name and
        # the broker never registers it, so a core `image_gen` broker tool and
        # a `tools/image_gen/` directory are one feature, not a collision.
        if self.name in CORE_TOOL_SUFFIXES and not self.internal:
            raise ValueError(f"{self.name!r} shadows a core platform tool")
        return self

    @property
    def actions(self) -> list[str]:
        """The catalog's action names: the `action` enum, or `call` for a
        tool without one (scripts/compile_live_operation_catalog.py)."""
        action = self.params.get("properties", {}).get("action", {})
        choices = action.get("enum") if isinstance(action, dict) else None
        return list(choices or ["call"])

    @model_validator(mode="after")
    def _view_actions_fit(self):
        if not self.view_actions:
            return self
        # A view reads through the call credential, which binds only
        # app_access roles; without `read` there is nothing for it to read.
        if self.app_access is None or "read" not in self.app_access.verbs:
            raise ValueError("view_actions need app_access with the read verb")
        props = self.params.get("properties", {})
        for name, va in self.view_actions.items():
            if name not in self.actions:
                raise ValueError(f"view action {name!r} is not in the action enum "
                                 f"{self.actions}")
            stray = [r for r in va.sources if r not in self.app_access.roles]
            if stray:
                raise ValueError(f"view action {name!r} sources {stray} are not "
                                 "app_access roles")
            # The executor validates every call against `params`, so a view
            # argument the tool doesn't declare would fail at run time; the
            # action itself is fixed by the binding, never a view argument.
            bad = [p for p in va.params.get("properties", {})
                   if p == "action" or p not in props]
            if bad:
                raise ValueError(f"view action {name!r} params {bad} are not tool "
                                 "params (or name `action`)")
        return self

    @field_validator("description")
    @classmethod
    def _description_useful(cls, v: str) -> str:
        if len(v.strip()) < 20:
            raise ValueError("description must actually describe the tool (>= 20 chars)")
        return v.strip()

    @field_validator("params")
    @classmethod
    def _params_object_schema(cls, v: dict) -> dict:
        if not isinstance(v, dict) or v.get("type") != "object":
            raise ValueError("params must be a JSON Schema with type: object")
        # The broker's argument on every custom tool (docs/design/25): artifact
        # ids it resolves into the executor's `files_in` and strips before the
        # forward. A manifest describing it would describe an argument the
        # tool never receives — and its own shape would be the wrong one.
        props = v.get("properties") if isinstance(v.get("properties"), dict) else {}
        required = v.get("required") if isinstance(v.get("required"), list) else []
        if RESERVED_PARAM in props or RESERVED_PARAM in required:
            raise ValueError(f"params must not declare {RESERVED_PARAM!r}: it is reserved for "
                             "the artifact ids the broker resolves into files_in "
                             "(docs/building-blocks/tools.md, \"Files from artifacts\")")
        return v

    @field_validator("timeout_seconds")
    @classmethod
    def _timeout_sane(cls, v: int) -> int:
        if not 1 <= v <= 300:
            raise ValueError("timeout_seconds must be between 1 and 300")
        return v

    @property
    def mcp_name(self) -> str:
        return f"mcp__platform__{self.name}"


class ToolInfo(BaseModel):
    name: str
    manifest: ToolManifest | None
    dir: Path
    # run.py is what makes the tool executable; a manifest without it is a
    # validation error surfaced in the UI, not a silently dead tool.
    has_entrypoint: bool = False
    has_requirements: bool = False
    error: str | None = None


class ToolRegistry:
    def __init__(self, root: Path | str):
        self.root = Path(root)
        self._cache: dict[str, ToolInfo] = {}
        self.reload()

    def reload(self) -> None:
        found: dict[str, ToolInfo] = {}
        if self.root.is_dir():
            for d in sorted(p for p in self.root.iterdir() if p.is_dir()):
                info = self._load(d)
                if info is not None:
                    found[info.name] = info
        self._cache = found

    def _load(self, d: Path) -> ToolInfo | None:
        yml = d / "tool.yaml"
        if not yml.is_file():
            return None
        has_entry = (d / "run.py").is_file()
        has_reqs = (d / "requirements.txt").is_file()
        try:
            raw = yaml.safe_load(yml.read_text()) or {}
            raw.setdefault("name", d.name)
            manifest = ToolManifest(**raw)
            error = None
            if manifest.name != d.name:
                error = f"tool.yaml name {manifest.name!r} must match directory {d.name!r}"
            elif not has_entry:
                error = "missing run.py entrypoint"
            if error:
                return ToolInfo(name=d.name, manifest=None, dir=d, has_entrypoint=has_entry,
                                has_requirements=has_reqs, error=error)
            return ToolInfo(name=manifest.name, manifest=manifest, dir=d,
                            has_entrypoint=True, has_requirements=has_reqs)
        except (yaml.YAMLError, ValidationError) as e:
            return ToolInfo(name=d.name, manifest=None, dir=d, has_entrypoint=has_entry,
                            has_requirements=has_reqs, error=str(e))

    def list(self) -> list[ToolInfo]:
        return list(self._cache.values())

    def get(self, name: str) -> ToolInfo | None:
        return self._cache.get(name)

    def valid(self) -> list[ToolManifest]:
        """Only the tools that can actually run (manifest parsed + entrypoint)."""
        return [t.manifest for t in self._cache.values() if t.manifest is not None]

    def mcp_names(self) -> list[str]:
        """The grantable names: internal tools are not on the MCP surface, so
        a grant to one would be dead."""
        return [m.mcp_name for m in self.valid() if not m.internal]
