from agentplatform.config import Settings

def test_defaults():
    s = Settings()
    assert s.global_concurrency == 3
    assert s.run_timeout_seconds == 1800

def test_env_override(monkeypatch):
    monkeypatch.setenv("AP_GLOBAL_CONCURRENCY", "5")
    assert Settings().global_concurrency == 5
