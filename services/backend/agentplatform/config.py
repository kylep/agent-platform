from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AP_")
    db_url: str = "sqlite+aiosqlite:///:memory:"
    kafka_bootstrap: str = "localhost:9092"
    k8s_namespace: str = "agent-platform"
    runner_image: str = "agent-platform-runner:dev"
    agents_root: str = "./agents"
    skills_root: str = "./skills"
    agents_volume_claim: str = "agent-definitions"
    session_secret: str = "dev-insecure"
    global_concurrency: int = 3
    run_timeout_seconds: int = 1800
    # Loop guard for agent-invokes-agent: a run whose depth would exceed this
    # is rejected. depth 0 = human/schedule/webhook; each nested invoke +1.
    max_run_chain_depth: int = 5
    # Default transcript retention: prune run_transcript_events older than this
    # many days (Run metadata/summary is kept). Per-agent manifest override wins;
    # <= 0 disables pruning (keep forever).
    transcript_retention_days: int = 30
    # Self-hosting git target. git_remote_url is what the platform clones and
    # pushes to (a local bare repo in tests, the real repo over HTTPS in prod);
    # github_repo ("owner/name") is used for the PR API. Empty = self-edit off.
    git_remote_url: str = ""
    github_repo: str = ""
    default_branch: str = "main"
    # In-cluster API base URL injected into system-agent runs so they can call
    # the platform (e.g. the run summarizer annotating runs).
    api_internal_url: str = "http://agent-platform-api:8000"

@lru_cache
def get_settings() -> Settings:
    return Settings()
