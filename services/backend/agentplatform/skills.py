"""Skills as first-class components. A skill is a directory under the repo's
`skills/` tree containing a `SKILL.md` whose YAML frontmatter declares its
`name`, `description`, and any `secrets` it needs. Agents reference skills by
name in their manifest `skills:` list; the runner mounts the referenced skills
into the pod and the pod is granted the union of those skills' secrets."""
# Defer annotation evaluation: this module's SkillStore defines a `list()`
# method, which would otherwise shadow the builtin in the `list[str]`
# annotations below — a runtime TypeError on Python < 3.14, where annotations
# are evaluated eagerly at class-definition time.
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError


class Skill(BaseModel):
    name: str
    description: str = ""
    # Secrets this skill needs; an agent using the skill gets these bound.
    secrets: list[str] = []


class SkillInfo(BaseModel):
    name: str
    skill: Skill | None
    body: str
    error: str | None = None


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
            return SkillInfo(name=fm["name"], skill=Skill(**fm), body=body)
        except (yaml.YAMLError, ValidationError) as e:
            return SkillInfo(name=d.name, skill=None, body=body_full, error=str(e))

    def list(self) -> list[SkillInfo]:
        return list(self._cache.values())

    def get(self, name: str) -> SkillInfo | None:
        return self._cache.get(name)

    def secrets_for(self, skill_names: list[str]) -> list[str]:
        """The de-duplicated union of secrets required by the named skills
        (unknown skills contribute nothing). Used to bind an agent's pod to the
        union of its skills' secrets and nothing more."""
        out: list[str] = []
        for n in skill_names:
            info = self._cache.get(n)
            if info and info.skill:
                for s in info.skill.secrets:
                    if s not in out:
                        out.append(s)
        return out
