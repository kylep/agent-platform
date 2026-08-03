"""Apps as self-describing services (docs/design/11). An app is a directory
under the repo's `apps/` tree: its own backend/frontend CODE (built and
deployed like any platform service — apps are not change-loop blocks) plus an
`app.yaml` manifest declaring what it is and what it needs. The manifest is
the contract this registry reads (from the synced checkout) and the
provisioner acts on: a postgres schema+role, kafka topics, and a scoped
platform API key all exist because app.yaml says so.

Apps stay separable from platform code: they may depend only on public
contracts (the SDK, HTTP APIs, @ap/ui) — a future repo split is a git mv."""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator


class AppNeeds(BaseModel):
    # A dedicated pg schema (app_<name>) + role, credentials in k8s secret
    # app-<name>-db.
    postgres: bool = False
    # Namespaced topics this app owns; must all start with app.<name>.
    kafka_topics: list[str] = []
    # Reserved: the chart grows a shared redis the first time an app sets it.
    redis: bool = False


class AppAgentKey(BaseModel):
    # Role for the app's single-owner platform API key (app:<name>), delivered
    # in k8s secret app-<name>-key. `operator` can trigger runs and read
    # results; `reader` is lookup-only.
    role: str = "operator"

    @field_validator("role")
    @classmethod
    def _known_role(cls, v: str) -> str:
        if v not in ("reader", "annotator", "operator"):
            raise ValueError("agent_key.role must be reader, annotator, or operator")
        return v


class AppSpec(BaseModel):
    name: str
    description: str = ""
    icon: str = ""
    ui: bool = False           # serves a UI at /apps/<name>/
    api: bool = False          # serves an API at /apps/<name>/api/
    needs: AppNeeds = Field(default_factory=AppNeeds)
    agent_key: AppAgentKey | None = None


class AppInfo(BaseModel):
    name: str
    spec: AppSpec | None
    dir: Path
    error: str | None = None


class AppRegistry:
    def __init__(self, root: Path | str):
        self.root = Path(root)
        self._cache: dict[str, AppInfo] = {}
        self.reload()

    def reload(self) -> None:
        found: dict[str, AppInfo] = {}
        if self.root.is_dir():
            for d in sorted(p for p in self.root.iterdir() if p.is_dir()):
                info = self._load(d)
                if info is not None:
                    found[info.name] = info
        self._cache = found

    def _load(self, d: Path) -> AppInfo | None:
        yml = d / "app.yaml"
        if not yml.is_file():
            return None
        try:
            raw = yaml.safe_load(yml.read_text()) or {}
            raw.setdefault("name", d.name)
            spec = AppSpec(**raw)
            for t in spec.needs.kafka_topics:
                if not t.startswith(f"app.{spec.name}."):
                    raise ValueError(f"kafka topic `{t}` must be namespaced app.{spec.name}.*")
            return AppInfo(name=raw["name"], spec=spec, dir=d)
        except (yaml.YAMLError, ValidationError, ValueError) as e:
            return AppInfo(name=d.name, spec=None, dir=d, error=str(e))

    def list(self) -> list[AppInfo]:
        return list(self._cache.values())

    def get(self, name: str) -> AppInfo | None:
        return self._cache.get(name)
