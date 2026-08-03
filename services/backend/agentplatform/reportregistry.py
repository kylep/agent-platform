"""Report types as first-class building blocks (docs/design/11). A report type
is a directory under the repo's `reports/` tree containing a `report.yaml`
that declares everything ABOUT a class of reports: what it is, which agent
generates it, its cadence, and how long instances are kept. The instances
themselves (dated HTML artifacts) live in Postgres — see api/reports.py.

Mirrors secretregistry.SecretRegistry: folders in the synced git checkout,
reloaded on read, folder name as the default name."""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError, field_validator


CADENCES = ("daily", "intraday", "adhoc")


class ReportTypeSpec(BaseModel):
    name: str
    description: str = ""
    icon: str = ""
    # The agent expected to produce this report. Doubles as the write ACL:
    # an agent-scoped API key may only save reports whose type names it.
    generator: str = ""
    # Display + validation hint: daily reports refuse a time component,
    # intraday ones require it, adhoc accepts either.
    cadence: str = "daily"
    # Instances older than this are pruned (0 = keep forever).
    retention_days: int = 0

    @field_validator("cadence")
    @classmethod
    def _known_cadence(cls, v: str) -> str:
        if v not in CADENCES:
            raise ValueError(f"cadence must be one of {', '.join(CADENCES)}")
        return v


class ReportTypeInfo(BaseModel):
    name: str
    spec: ReportTypeSpec | None
    dir: Path
    error: str | None = None


class ReportTypeRegistry:
    def __init__(self, root: Path | str):
        self.root = Path(root)
        self._cache: dict[str, ReportTypeInfo] = {}
        self.reload()

    def reload(self) -> None:
        found: dict[str, ReportTypeInfo] = {}
        if self.root.is_dir():
            for d in sorted(p for p in self.root.iterdir() if p.is_dir()):
                info = self._load(d)
                if info is not None:
                    found[info.name] = info
        self._cache = found

    def _load(self, d: Path) -> ReportTypeInfo | None:
        yml = d / "report.yaml"
        if not yml.is_file():
            return None
        try:
            raw = yaml.safe_load(yml.read_text()) or {}
            raw.setdefault("name", d.name)
            return ReportTypeInfo(name=raw["name"], spec=ReportTypeSpec(**raw), dir=d)
        except (yaml.YAMLError, ValidationError) as e:
            return ReportTypeInfo(name=d.name, spec=None, dir=d, error=str(e))

    def list(self) -> list[ReportTypeInfo]:
        return list(self._cache.values())

    def get(self, name: str) -> ReportTypeInfo | None:
        return self._cache.get(name)
