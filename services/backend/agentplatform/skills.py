"""Skills are instructions, never a source of secret or Tool authority."""
# Defer annotation evaluation: this module's SkillStore defines a `list()`
# method, which would otherwise shadow the builtin in the `list[str]`
# annotations below — a runtime TypeError on Python < 3.14, where annotations
# are evaluated eagerly at class-definition time.
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from agentplatform.plugin_release import NAME as CODING_PLUGIN
from agentplatform.plugin_release import verify_release


class Skill(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    description: str = ""
    # An optional emoji shown next to the skill in the UI (frontmatter `icon:`).
    icon: str = ""


class SkillInfo(BaseModel):
    name: str
    skill: Skill | None
    body: str
    # The full SKILL.md text (frontmatter + body) — what the in-place editor
    # round-trips; `body` alone drops the frontmatter.
    raw: str = ""
    error: str | None = None
    origin: str = "legacy"


def parse_frontmatter(md: str) -> tuple[dict, str]:
    """Split a SKILL.md into (frontmatter dict, body). Frontmatter is the YAML
    between the first pair of `---` lines; absent frontmatter yields ({}, md)."""
    if md.startswith("---"):
        parts = md.split("---", 2)
        if len(parts) == 3:
            return yaml.safe_load(parts[1]) or {}, parts[2].lstrip("\n")
    return {}, md


class SkillStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self._cache: dict[str, SkillInfo] = {}
        self.reload()

    def reload(self) -> None:
        found: dict[str, SkillInfo] = {}
        if self.root.is_dir():
            for d in sorted(p for p in self.root.iterdir() if p.is_dir()):
                info = self._load(d)
                if info is not None:
                    found[info.name] = info
        plugin_root = self.root.parent / "plugins" / CODING_PLUGIN
        if plugin_root.exists():
            try:
                for directory in verify_release(plugin_root):
                    info = self._load(directory)
                    if info is not None and info.skill is not None:
                        info.origin = "plugin"
                        if info.name in found:
                            found[info.name] = SkillInfo(
                                name=info.name, skill=None, body="",
                                error="plugin skill name collides with a legacy skill",
                                origin="plugin")
                        else:
                            found[info.name] = info
            except (ValueError, OSError) as exc:
                # An unverified release has no skills, even if one file happens
                # to parse. Assignments to them stay unavailable until fixed.
                found[CODING_PLUGIN] = SkillInfo(
                    name=CODING_PLUGIN, skill=None, body="",
                    error=f"plugin release invalid: {exc}", origin="plugin")
        self._cache = found

    def _load(self, d: Path) -> SkillInfo | None:
        md_path = d / "SKILL.md"
        if not md_path.is_file():
            return None
        body_full = md_path.read_text()
        try:
            fm, body = parse_frontmatter(body_full)
            # Frontmatter name wins; fall back to the directory name.
            fm.setdefault("name", d.name)
            return SkillInfo(name=fm["name"], skill=Skill(**fm), body=body, raw=body_full)
        except (yaml.YAMLError, ValidationError) as e:
            return SkillInfo(name=d.name, skill=None, body=body_full, raw=body_full, error=str(e))

    def list(self) -> list[SkillInfo]:
        return list(self._cache.values())

    def get(self, name: str) -> SkillInfo | None:
        return self._cache.get(name)
