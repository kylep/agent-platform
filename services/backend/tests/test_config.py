from agentplatform.config import Settings

def test_defaults():
    s = Settings()
    assert s.global_concurrency == 3
    assert s.run_timeout_seconds == 1800

def test_env_override(monkeypatch):
    monkeypatch.setenv("AP_GLOBAL_CONCURRENCY", "5")
    assert Settings().global_concurrency == 5


def test_dev_profile_defaults():
    """docs/design/24 settings: the dev run profile and its budgets."""
    s = Settings()
    assert s.runner_dev_image == "agent-platform-runner-dev:dev"
    assert s.dev_max_turns == 200
    assert s.dev_verify_timeout_seconds == 2400
    assert s.dev_workspace_size_limit == "8Gi"
    assert s.dev_shm_size_limit == "1Gi"
    assert s.publish_max_bytes == 16 * 1024 * 1024
    assert s.web_internal_url == "http://ap-web:8090"


def test_web_internal_url_reads_the_env_the_chart_sets(monkeypatch):
    """The chart (and the pod) call it AP_WEB_URL, the runner-side name that
    pairs with AP_API_URL; the setting keeps the api_internal_url naming."""
    monkeypatch.setenv("AP_WEB_URL", "http://r-web:8090")
    assert Settings().web_internal_url == "http://r-web:8090"
