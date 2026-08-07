import json, os, stat
from pathlib import Path
import runner

class FakeProducer:
    def __init__(self): self.published = []
    async def start(self): pass
    async def stop(self): pass
    async def publish(self, topic, key, value, type="run.transcript"): self.published.append((topic, key, value))

def test_relays_stream_and_terminal(tmp_path, monkeypatch):
    fake = tmp_path / "claude"
    fake.write_text("#!/bin/sh\necho '{\"type\":\"assistant\",\"text\":\"hi\"}'\nexit 0\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    creds = tmp_path / "secrets"; creds.mkdir()
    (creds / "credentials.json").write_text("{}")
    agents = tmp_path / "agentdefs" / "hello-world"; agents.mkdir(parents=True)
    (agents / "agent.md").write_text("# hello-world")
    monkeypatch.setenv("AP_RUN_ID", "RID"); monkeypatch.setenv("AP_AGENT", "hello-world")
    monkeypatch.setenv("AP_PROMPT", "hi"); monkeypatch.setenv("CLAUDE_BIN", str(fake))
    monkeypatch.setenv("AP_SECRETS_DIR", str(creds))
    monkeypatch.setenv("AP_AGENTS_DIR", str(tmp_path / "agentdefs"))
    monkeypatch.setenv("HOME", str(tmp_path))
    p = FakeProducer()
    rc = runner.run(producer=p)
    assert rc == 0
    topics = [t for t, _, _ in p.published]
    assert "run.transcript" in topics and "run.events" in topics
    first = p.published[0][2]
    assert first["seq"] == 1 and first["type"] == "assistant"
    assert p.published[-1][2]["terminal"] is True
    assert (tmp_path / ".claude" / "agents" / "hello-world.md").exists()


def test_install_credentials_prefers_claude_proxy(tmp_path, monkeypatch):
    """Token brokering (docs/design/09): with a proxy URL the pod holds no real
    credential — claude gets the proxy as base URL plus a placeholder token
    (the CLI refuses to start with none at all), and the secrets dir is never
    read (it isn't mounted in proxied pods)."""
    monkeypatch.setenv("AP_CLAUDE_PROXY_URL", "http://agent-platform-claude-proxy:8000")
    monkeypatch.setenv("AP_SECRETS_DIR", str(tmp_path / "does-not-exist"))
    env = runner._install_credentials()
    assert env["ANTHROPIC_BASE_URL"] == "http://agent-platform-claude-proxy:8000"
    assert env["CLAUDE_CODE_OAUTH_TOKEN"]  # non-empty placeholder, not a secret


def test_install_credentials_legacy_token_file(tmp_path, monkeypatch):
    monkeypatch.delenv("AP_CLAUDE_PROXY_URL", raising=False)
    creds = tmp_path / "secrets"; creds.mkdir()
    (creds / "token").write_text("tok-123\n")
    monkeypatch.setenv("AP_SECRETS_DIR", str(creds))
    env = runner._install_credentials()
    assert env == {"CLAUDE_CODE_OAUTH_TOKEN": "tok-123"}


def test_kafka_wrapper_constructible_outside_event_loop():
    # Regression: AIOKafkaProducer must not be built in __init__ (no loop yet).
    w = runner.KafkaProducerWrapper("kafka:9092")
    assert w._p is None


import subprocess
import pytest


def _git(cwd, *a):
    subprocess.run(["git", "-C", str(cwd), *a], check=True, capture_output=True, text=True)


def test_title_takes_first_nonblank_line():
    assert runner._title("\n  Add a greeting\nmore\n") == "Add a greeting"
    assert runner._title("   ") == "edit"
    assert len(runner._title("x" * 200)) == 60


@pytest.fixture
def bare_and_clone(tmp_path):
    bare = tmp_path / "origin.git"
    _git(tmp_path, "init", "--bare", "-b", "main", "-q", str(bare))
    seed = tmp_path / "seed"
    _git(tmp_path, "clone", "-q", str(bare), str(seed))
    _git(seed, "config", "user.email", "s@s"); _git(seed, "config", "user.name", "s")
    (seed / "agents" / "demo").mkdir(parents=True)
    (seed / "agents" / "demo" / "agent.md").write_text("demo\n")
    _git(seed, "add", "-A"); _git(seed, "commit", "-qm", "init"); _git(seed, "push", "-q", "origin", "main")
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", str(bare), str(clone))
    return bare, clone


def test_target_block_from_status():
    assert runner._target_block(" M agents/demo/agent.md\n?? other.txt") == ("agent", "demo")
    assert runner._target_block("?? skills/linear/SKILL.md") == ("skill", "linear")
    assert runner._target_block("?? secrets/linear-api-key/secret.yaml") == ("secret", "linear-api-key")
    # skill + its secret (the New-Skill wizard) → the SKILL's branch, even
    # though git status lists secrets/ first alphabetically
    assert runner._target_block(
        "?? secrets/linear-api-key/secret.yaml\n?? skills/linear/SKILL.md"
    ) == ("skill", "linear")
    # an agent edit outranks both
    assert runner._target_block(
        "?? secrets/x/secret.yaml\n M agents/demo/agent.md\n?? skills/y/SKILL.md"
    ) == ("agent", "demo")
    assert runner._target_block(" M services/x.py") is None


def test_self_edit_publish_uses_per_agent_branch(bare_and_clone, monkeypatch):
    bare, clone = bare_and_clone
    monkeypatch.setenv("AP_GITHUB_TOKEN", "ghs_x")
    monkeypatch.setenv("AP_GITHUB_REPO", "o/r")
    calls = {}
    monkeypatch.setattr(runner, "_open_or_find_pr",
                        lambda branch, run_id, prompt: calls.update(branch=branch) or {"number": 7, "url": "u"})
    (clone / "agents" / "demo" / "agent.md").write_text("demo improved\n")
    env = {**os.environ}   # local remote needs no real auth
    res = runner.self_edit_publish(clone, env, "abcd1234efgh", "platform-coder", "improve demo")
    # deterministic branch derived from the edited agent, not the run id
    assert res["changed"] and res["branch"] == "coder/agent-demo" and res["target"] == "demo"
    assert res["pr"] == {"number": 7, "url": "u"} and calls["branch"] == "coder/agent-demo"
    out = subprocess.run(["git", "-C", str(bare), "branch", "--format=%(refname:short)"],
                         capture_output=True, text=True, check=True).stdout
    assert "coder/agent-demo" in out.split()


def test_self_edit_publish_wizard_skill_lands_on_skill_branch(bare_and_clone, monkeypatch):
    # The New-Skill wizard's coder run touches skills/<name>/ + secrets/<name>/ —
    # the PR must land on coder/skill-<name>, not the authoring agent's branch.
    bare, clone = bare_and_clone
    monkeypatch.setenv("AP_GITHUB_TOKEN", "ghs_x")
    monkeypatch.setenv("AP_GITHUB_REPO", "o/r")
    monkeypatch.setattr(runner, "_open_or_find_pr",
                        lambda branch, run_id, prompt: {"number": 8, "url": "u"})
    (clone / "skills" / "linear").mkdir(parents=True)
    (clone / "skills" / "linear" / "SKILL.md").write_text("linear\n")
    (clone / "secrets" / "linear-api-key").mkdir(parents=True)
    (clone / "secrets" / "linear-api-key" / "secret.yaml").write_text("name: linear-api-key\n")
    res = runner.self_edit_publish(clone, {**os.environ}, "abcd1234efgh",
                                   "platform-coder", "Create a new skill `linear`")
    assert res["changed"] and res["branch"] == "coder/skill-linear" and res["target"] == "linear"


def test_self_edit_publish_noop_when_no_change(bare_and_clone, monkeypatch):
    bare, clone = bare_and_clone
    monkeypatch.setattr(runner, "_open_or_find_pr", lambda *a, **k: pytest.fail("should not open PR"))
    res = runner.self_edit_publish(clone, {**os.environ}, "abcd1234", "platform-coder", "noop")
    assert res == {"changed": False}


def test_agent_tools_parses_frontmatter(tmp_path, monkeypatch):
    d = tmp_path / "agentdefs" / "news"; d.mkdir(parents=True)
    (d / "agent.md").write_text("---\nname: news\ntools: WebSearch, WebFetch\n---\nbody")
    monkeypatch.setenv("AP_AGENTS_DIR", str(tmp_path / "agentdefs"))
    assert runner._agent_tools("news") == ["WebSearch", "WebFetch"]
    # No tools line → empty.
    (d / "agent.md").write_text("---\nname: news\n---\nbody")
    assert runner._agent_tools("news") == []


def test_permission_args_credential_less_agent_is_least_privilege(tmp_path, monkeypatch):
    d = tmp_path / "agentdefs" / "news"; d.mkdir(parents=True)
    (d / "agent.md").write_text("---\nname: news\ntools: WebSearch, WebFetch\n---\nbody")
    monkeypatch.setenv("AP_AGENTS_DIR", str(tmp_path / "agentdefs"))
    args = runner._permission_args(self_edit=False, has_api_token=False, agent="news")
    # Web tools pre-approved; Bash/Read/etc stripped from context; no bypass.
    assert "--allowedTools" in args and "WebSearch" in args and "WebFetch" in args
    assert "--disallowedTools" in args and "Bash" in args and "Read" in args
    assert "bypassPermissions" not in args


def test_permission_args_no_agent_bypass_even_with_token(tmp_path, monkeypatch):
    # No bypassPermissions for a token-bearing agent — scoped like everyone else.
    d = tmp_path / "agentdefs" / "mon"; d.mkdir(parents=True)
    (d / "agent.md").write_text("---\nname: mon\ntools: mcp__platform__runs_read\n---\nbody")
    monkeypatch.setenv("AP_AGENTS_DIR", str(tmp_path / "agentdefs"))
    args = runner._permission_args(self_edit=False, has_api_token=True, agent="mon")
    assert "bypassPermissions" not in args
    assert args[:2] == ["--allowedTools", "mcp__platform__runs_read"]
    assert "--disallowedTools" in args and "Bash" in args and "Read" in args


def test_permission_args_declared_bash_is_stripped_for_non_selfedit(tmp_path, monkeypatch):
    """The trifecta-break is enforced, not merely conventional: a non-self-edit
    agent that DECLARES a token-reading tool still doesn't get it. Otherwise a
    mis-declared (or injection-altered) manifest could hand a web agent Bash and
    let it read the mounted Claude token."""
    d = tmp_path / "agentdefs" / "sneaky"; d.mkdir(parents=True)
    (d / "agent.md").write_text("---\nname: sneaky\ntools: WebFetch, Bash, Read\n---\nbody")
    monkeypatch.setenv("AP_AGENTS_DIR", str(tmp_path / "agentdefs"))
    args = runner._permission_args(self_edit=False, has_api_token=False, agent="sneaky")
    allowed = args[args.index("--allowedTools") + 1:args.index("--disallowedTools")]
    assert "WebFetch" in allowed
    assert "Bash" not in allowed and "Read" not in allowed          # declared, still stripped
    disallowed = args[args.index("--disallowedTools") + 1:]
    assert "Bash" in disallowed and "Read" in disallowed


def test_permission_args_selfedit():
    assert runner._permission_args(True, False, "x") == ["--permission-mode", "acceptEdits"]
