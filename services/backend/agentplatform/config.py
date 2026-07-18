from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AP_")
    db_url: str = "sqlite+aiosqlite:///:memory:"
    kafka_bootstrap: str = "localhost:9092"
    k8s_namespace: str = "agent-platform"
    runner_image: str = "agent-platform-runner:dev"
    agents_root: str = "./agents"
    agents_volume_claim: str = "agent-definitions"
    session_secret: str = "dev-insecure"
    global_concurrency: int = 3
    run_timeout_seconds: int = 1800

@lru_cache
def get_settings() -> Settings:
    return Settings()
