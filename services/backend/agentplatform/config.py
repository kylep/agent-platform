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
    # GC finished run Jobs + pods this long after they finish (k8s TTL
    # controller). Run history lives in postgres, so pods are disposable.
    run_ttl_seconds: int = 3600
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
    # News pipeline: a terminal run of `news_gatherer_agent` whose result is a
    # digest JSON is projected (deduped/sanitized) and posted to `news_channel`
    # via the connector. Kept credential-free — see the news-pipeline spec.
    news_gatherer_agent: str = "news"
    news_channel: str = "news"
    news_retention_days: int = 14

@lru_cache
def get_settings() -> Settings:
    return Settings()
