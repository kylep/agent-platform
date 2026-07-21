from pathlib import Path
import yaml
from pydantic import BaseModel, ValidationError

class Manifest(BaseModel):
    role: str = "operator"
    concurrency: int = 1
    timeout_seconds: int = 1800
    skills: list[str] = []
    secrets: list[str] = []
    description: str = ""
    # Optional 5-field cron expression; when set the scheduler fires the agent.
    schedule: str = ""
    # Optional claude model override (e.g. "sonnet" for cheap background work);
    # empty = the CLI default.
    model: str = ""
    # System agents are platform-internal (e.g. the run summarizer): they get
    # API access injected and are protected from deletion in the UI.
    system: bool = False
    # When set, the agent gets an operator-scoped, per-run API token injected so
    # it can invoke other agents (agent-invokes-agent). Without it a system
    # agent only gets the narrow `annotator` token (read runs + annotate).
    can_invoke: bool = False

class AgentInfo(BaseModel):
    name: str
    manifest: Manifest | None
    agent_md: str
    error: str | None = None

class AgentStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self._cache: dict[str, AgentInfo] = {}
        self.reload()

    def reload(self) -> None:
        found: dict[str, AgentInfo] = {}
        if self.root.is_dir():
            for d in sorted(p for p in self.root.iterdir() if p.is_dir()):
                found[d.name] = self._load(d)
        self._cache = found

    def _load(self, d: Path) -> AgentInfo:
        md = d / "agent.md"
        agent_md = md.read_text() if md.is_file() else ""
        try:
            raw = yaml.safe_load((d / "manifest.yaml").read_text()) or {}
            return AgentInfo(name=d.name, manifest=Manifest(**raw), agent_md=agent_md)
        except (OSError, yaml.YAMLError, ValidationError) as e:
            return AgentInfo(name=d.name, manifest=None, agent_md=agent_md, error=str(e))

    def list(self) -> list[AgentInfo]:
        return list(self._cache.values())

    def get(self, name: str) -> AgentInfo | None:
        return self._cache.get(name)
