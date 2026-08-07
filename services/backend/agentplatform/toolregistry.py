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

import yaml
from pydantic import BaseModel, ValidationError, field_validator

# MCP tool-name style, and safe to embed in env prefixes / pg identifiers.
_NAME = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
_ENV = re.compile(r"^[A-Z][A-Z0-9_]*$")

# Suffixes of the broker's built-in mcp__platform__* tools. A custom tool may
# not shadow one (the broker registers customs beside these; a collision would
# be ambiguous). Kept here, next to the validation that uses it; agentspec's
# PLATFORM_MCP_TOOLS derives from the same set via a lockstep test.
CORE_TOOL_SUFFIXES = frozenset({
    "runs_read", "runs_write", "metrics",
    "read_memory", "save_memory", "query_app",
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


class ToolManifest(BaseModel):
    name: str
    # What the model sees as the MCP tool description — write it for the model.
    description: str
    # JSON Schema (object) for the tool's arguments; the executor validates
    # every call against it before running run.py.
    params: dict = {"type": "object", "properties": {}}
    infra: ToolInfra = ToolInfra()
    timeout_seconds: int = 30

    @field_validator("name")
    @classmethod
    def _name_style(cls, v: str) -> str:
        if not _NAME.match(v):
            raise ValueError(f"tool name must match {_NAME.pattern}, got {v!r}")
        if v in CORE_TOOL_SUFFIXES:
            raise ValueError(f"{v!r} shadows a core platform tool")
        return v

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
        return v

    @field_validator("timeout_seconds")
    @classmethod
    def _timeout_sane(cls, v: int) -> int:
        if not 1 <= v <= 120:
            raise ValueError("timeout_seconds must be between 1 and 120")
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
        return [m.mcp_name for m in self.valid()]
