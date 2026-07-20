from agentplatform.tiers import (
    TIER_DIRECT, TIER_PR, FileChange, classify_tier,
)


def mod(path, fields=()):
    return FileChange(path=path, kind="modified", manifest_fields=frozenset(fields))


def test_empty_is_direct():
    assert classify_tier([]) == TIER_DIRECT


def test_edit_agent_body_is_direct():
    assert classify_tier([mod("agents/hello-world/agent.md")]) == TIER_DIRECT


def test_safe_manifest_field_is_direct():
    assert classify_tier([mod("agents/hello-world/manifest.yaml", ["description"])]) == TIER_DIRECT


def test_body_and_safe_manifest_together_is_direct():
    assert classify_tier([
        mod("agents/x/agent.md"),
        mod("agents/x/manifest.yaml", ["description"]),
    ]) == TIER_DIRECT


def test_sensitive_manifest_field_forces_pr():
    for f in ("role", "concurrency", "timeout_seconds", "secrets", "skills"):
        assert classify_tier([mod("agents/x/manifest.yaml", [f])]) == TIER_PR


def test_mixed_safe_and_sensitive_forces_pr():
    assert classify_tier([mod("agents/x/manifest.yaml", ["description", "concurrency"])]) == TIER_PR


def test_new_agent_forces_pr():
    assert classify_tier([
        FileChange("agents/new/agent.md", "added"),
        FileChange("agents/new/manifest.yaml", "added"),
    ]) == TIER_PR


def test_deleting_agent_forces_pr():
    assert classify_tier([FileChange("agents/gone/agent.md", "deleted")]) == TIER_PR


def test_files_outside_agents_force_pr():
    for p in ("charts/agent-platform/values.yaml", "services/backend/x.py", "README.md"):
        assert classify_tier([mod(p)]) == TIER_PR


def test_unknown_file_in_agent_dir_forces_pr():
    assert classify_tier([mod("agents/x/secrets.env")]) == TIER_PR


def test_nested_path_in_agent_dir_forces_pr():
    # Deeper than agents/<name>/<file> — unrecognized shape, fail closed.
    assert classify_tier([mod("agents/x/sub/agent.md")]) == TIER_PR


def test_any_tier2_change_taints_the_whole_set():
    assert classify_tier([
        mod("agents/x/agent.md"),                 # tier 1 alone
        mod("services/backend/app.py"),           # tier 2
    ]) == TIER_PR
