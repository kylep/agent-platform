import json, os, stat
import urllib.error
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


def _install_from_mount(tmp_path, monkeypatch, name, agent_md, home=None):
    """Install an agent the OLD way: a git-synced /agents tree, no session
    token. Returns after `_install_agent` has taken the fallback path."""
    d = tmp_path / "agentdefs" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "agent.md").write_text(agent_md)
    monkeypatch.setenv("AP_AGENTS_DIR", str(tmp_path / "agentdefs"))
    monkeypatch.setenv("HOME", str(home or tmp_path))
    monkeypatch.delenv("AP_SESSION_TOKEN", raising=False)
    runner._install_agent(name)


def test_agent_tools_parses_the_installed_definition(tmp_path, monkeypatch):
    _install_from_mount(tmp_path, monkeypatch, "news",
                        "---\nname: news\ntools: WebSearch, WebFetch\n---\nbody")
    assert runner._agent_tools("news") == ["WebSearch", "WebFetch"]
    # No tools line → empty (fail-closed; nothing is pre-approved).
    _install_from_mount(tmp_path, monkeypatch, "news", "---\nname: news\n---\nbody")
    assert runner._agent_tools("news") == []


def test_permission_args_credential_less_agent_is_least_privilege(tmp_path, monkeypatch):
    _install_from_mount(tmp_path, monkeypatch, "news",
                        "---\nname: news\ntools: WebSearch, WebFetch\n---\nbody")
    args = runner._permission_args(self_edit=False, has_api_token=False, agent="news")
    # Web tools pre-approved; Bash/Read/etc stripped from context; no bypass.
    assert "--allowedTools" in args and "WebSearch" in args and "WebFetch" in args
    assert "--disallowedTools" in args and "Bash" in args and "Read" in args
    assert "bypassPermissions" not in args


def test_permission_args_no_agent_bypass_even_with_token(tmp_path, monkeypatch):
    # No bypassPermissions for a token-bearing agent — scoped like everyone else.
    _install_from_mount(tmp_path, monkeypatch, "mon",
                        "---\nname: mon\ntools: mcp__platform__runs_read\n---\nbody")
    args = runner._permission_args(self_edit=False, has_api_token=True, agent="mon")
    assert "bypassPermissions" not in args
    assert args[:2] == ["--allowedTools", "mcp__platform__runs_read"]
    assert "--disallowedTools" in args and "Bash" in args and "Read" in args


def test_permission_args_declared_bash_is_stripped_for_non_selfedit(tmp_path, monkeypatch):
    """The trifecta-break is enforced, not merely conventional: a non-self-edit
    agent that DECLARES a token-reading tool still doesn't get it. Otherwise a
    mis-declared (or injection-altered) definition could hand a web agent Bash
    and let it read the mounted Claude token."""
    _install_from_mount(tmp_path, monkeypatch, "sneaky",
                        "---\nname: sneaky\ntools: WebFetch, Bash, Read\n---\nbody")
    args = runner._permission_args(self_edit=False, has_api_token=False, agent="sneaky")
    allowed = args[args.index("--allowedTools") + 1:args.index("--disallowedTools")]
    assert "WebFetch" in allowed
    assert "Bash" not in allowed and "Read" not in allowed          # declared, still stripped
    disallowed = args[args.index("--disallowedTools") + 1:]
    assert "Bash" in disallowed and "Read" in disallowed


def test_permission_args_selfedit():
    assert runner._permission_args(True, False, "x") == ["--permission-mode", "acceptEdits"]


# --- DB-first definition delivery (docs/design/15) -------------------------

def _fetch_env(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AP_RUN_ID", "RID")
    monkeypatch.setenv("AP_SESSION_TOKEN", "ap_sess")
    monkeypatch.setenv("AP_API_URL", "http://api:8090")


def _payload(**over):
    d = {"name": "newsy", "prompt": "You are newsy.\n",
         "harness_tools": ["WebSearch", "WebFetch"],
         "platform_tools": ["mcp__platform__memory"], "skills": [], "model": ""}
    d.update(over)
    return d


def test_install_agent_writes_the_fetched_definition(tmp_path, monkeypatch):
    """The pod materializes ~/.claude/agents/<name>.md from the API: frontmatter
    naming the agent and its granted tools, body = the prompt."""
    _fetch_env(monkeypatch, tmp_path)
    seen = {}
    monkeypatch.setattr(runner, "_api_req", lambda m, p, body=None:
                        seen.update(method=m, path=p) or _payload())
    runner._install_agent("newsy")
    assert seen == {"method": "GET", "path": "/api/runs/RID/agentdef"}
    assert (tmp_path / ".claude" / "agents" / "newsy.md").read_text() == (
        "---\nname: newsy\n"
        "tools: WebSearch, WebFetch, mcp__platform__memory\n"
        "---\n\nYou are newsy.\n")
    assert runner._agent_tools("newsy") == ["WebSearch", "WebFetch",
                                            "mcp__platform__memory"]


def test_install_agent_omits_the_tools_line_when_nothing_is_granted(tmp_path, monkeypatch):
    """Empty grants are explicit, not a mistake — and they must not become
    --allowedTools entries."""
    _fetch_env(monkeypatch, tmp_path)
    monkeypatch.setattr(runner, "_api_req", lambda m, p, body=None:
                        _payload(harness_tools=[], platform_tools=[]))
    runner._install_agent("newsy")
    text = (tmp_path / ".claude" / "agents" / "newsy.md").read_text()
    assert "tools:" not in text
    assert runner._agent_tools("newsy") == []
    args = runner._permission_args(self_edit=False, has_api_token=False, agent="newsy")
    assert "--allowedTools" not in args
    assert args[0] == "--disallowedTools" and "Bash" in args     # still hard-denied


def test_install_agent_falls_back_to_the_mount_on_a_failed_fetch(tmp_path, monkeypatch):
    """Transition safety: an unreachable API (or an older dispatcher that minted
    no session token) must not wedge a run — the git-synced tree still works."""
    _fetch_env(monkeypatch, tmp_path)
    d = tmp_path / "agentdefs" / "newsy"; d.mkdir(parents=True)
    (d / "agent.md").write_text("---\nname: newsy\ntools: WebFetch\n---\nfrom the mount")
    monkeypatch.setenv("AP_AGENTS_DIR", str(tmp_path / "agentdefs"))

    def boom(*a, **k):
        raise urllib.error.URLError("connection refused")
    monkeypatch.setattr(runner, "_api_req", boom)
    runner._install_agent("newsy")
    assert (tmp_path / ".claude" / "agents" / "newsy.md").read_text().endswith("from the mount")
    assert runner._agent_tools("newsy") == ["WebFetch"]


def test_install_agent_falls_back_when_the_env_is_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "_api_req",
                        lambda *a, **k: pytest.fail("must not call the API without a token"))
    _install_from_mount(tmp_path, monkeypatch, "newsy",
                        "---\nname: newsy\ntools: WebFetch\n---\nfrom the mount")
    assert runner._agent_tools("newsy") == ["WebFetch"]


def test_db_granted_agent_gets_the_same_flags_as_its_file_based_self(tmp_path, monkeypatch):
    """Parity, the whole point of the frontmatter shape: swapping the delivery
    channel must not change one character of --allowedTools/--disallowedTools."""
    tools = ["WebFetch", "Bash", "mcp__platform__runs_read"]
    _install_from_mount(tmp_path, monkeypatch, "twin",
                        "---\nname: twin\ntools: " + ", ".join(tools) + "\n---\nbody",
                        home=tmp_path / "file-home")
    from_file = runner._permission_args(self_edit=False, has_api_token=True, agent="twin")

    _fetch_env(monkeypatch, tmp_path / "db-home")
    monkeypatch.setattr(runner, "_api_req", lambda m, p, body=None: _payload(
        name="twin", prompt="body", harness_tools=["WebFetch", "Bash"],
        platform_tools=["mcp__platform__runs_read"]))
    runner._install_agent("twin")
    from_db = runner._permission_args(self_edit=False, has_api_token=True, agent="twin")

    assert from_db == from_file
    assert "Bash" not in from_db[:from_db.index("--disallowedTools")]  # denied both ways


# --- conversation session resume (docs/design/14) --------------------------

def _session_env(monkeypatch, tmp_path, fake_body):
    """Common setup for a conversation-resume run: fake claude, creds, agent,
    and the session env. Returns the FakeProducer after running."""
    fake = tmp_path / "claude"; fake.write_text(fake_body)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    creds = tmp_path / "secrets"; creds.mkdir()
    (creds / "credentials.json").write_text("{}")
    agents = tmp_path / "agentdefs" / "hello-world"; agents.mkdir(parents=True)
    (agents / "agent.md").write_text("# hello-world")
    monkeypatch.setenv("AP_RUN_ID", "RID"); monkeypatch.setenv("AP_AGENT", "hello-world")
    monkeypatch.setenv("AP_PROMPT", "flattened fallback prompt")
    monkeypatch.setenv("CLAUDE_BIN", str(fake))
    monkeypatch.setenv("AP_SECRETS_DIR", str(creds))
    monkeypatch.setenv("AP_AGENTS_DIR", str(tmp_path / "agentdefs"))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AP_USER_MESSAGE", "continue please")
    monkeypatch.setenv("AP_SESSION_TOKEN", "ap_sess")
    monkeypatch.setenv("AP_API_URL", "http://api:8090")
    # These tests are about resume; keep definition delivery on the mount copy
    # above (and off the network) so only one thing is under test.
    monkeypatch.setattr(runner, "_agentdef", lambda: None)


def test_project_dir_slug(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    d = runner._project_dir("/workspace/some.dir_x")
    assert d == tmp_path / ".claude" / "projects" / "-workspace-some-dir-x"


def test_restore_session_writes_blob(tmp_path, monkeypatch):
    import base64
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AP_API_URL", "http://api")
    monkeypatch.setenv("AP_SESSION_TOKEN", "ap_x")
    monkeypatch.setenv("AP_RUN_ID", "r1")
    monkeypatch.setattr(runner, "_api_req", lambda m, p, body=None: {
        "session_id": "sid-1", "blob_b64": base64.b64encode(b"{}").decode()})
    assert runner._restore_session("/workspace") == "sid-1"
    assert (tmp_path / ".claude/projects/-workspace/sid-1.jsonl").read_bytes() == b"{}"


def test_restore_session_absent_env(monkeypatch):
    monkeypatch.delenv("AP_SESSION_TOKEN", raising=False)
    assert runner._restore_session("/workspace") is None


def test_restore_session_null_blob(monkeypatch):
    monkeypatch.setenv("AP_API_URL", "http://api")
    monkeypatch.setenv("AP_SESSION_TOKEN", "ap_x")
    monkeypatch.setenv("AP_RUN_ID", "r1")
    monkeypatch.setattr(runner, "_api_req", lambda m, p, body=None: {
        "session_id": None, "blob_b64": None})
    assert runner._restore_session("/workspace") is None


def test_resume_invocation_and_upload(tmp_path, monkeypatch):
    """A restorable session -> claude runs with --resume + just the user message;
    the updated session is uploaded after a clean exit."""
    _session_env(monkeypatch, tmp_path,
                 '#!/bin/sh\necho \'{"type":"result","session_id":"sid-9","result":"ok"}\'\nexit 0\n')
    monkeypatch.setattr(runner, "_restore_session", lambda cwd: "sid-9")
    uploaded = {}
    monkeypatch.setattr(runner, "_upload_session",
                        lambda cwd, run_id, sid: uploaded.update(run_id=run_id, sid=sid))
    seen_args = {}
    real_popen = runner.subprocess.Popen
    def spy(args, **kw):
        seen_args["args"] = args
        return real_popen(args, **kw)
    monkeypatch.setattr(runner.subprocess, "Popen", spy)
    p = FakeProducer()
    rc = runner.run(producer=p)
    assert rc == 0
    assert "--resume" in seen_args["args"] and "sid-9" in seen_args["args"]
    assert "continue please" in seen_args["args"]
    assert "flattened fallback prompt" not in seen_args["args"]   # resume path skips the flattened prompt
    assert uploaded == {"run_id": "RID", "sid": "sid-9"}


def test_resume_failure_falls_back(tmp_path, monkeypatch):
    """A corrupt/incompatible session (resume exits non-zero) must not kill the
    turn: the runner retries once with the flattened fallback prompt."""
    _session_env(monkeypatch, tmp_path,
                 '#!/bin/sh\ncase "$*" in *--resume*) exit 1;; esac\n'
                 'echo \'{"type":"result","session_id":"sid-new","result":"ok"}\'\nexit 0\n')
    monkeypatch.setattr(runner, "_restore_session", lambda cwd: "sid-old")
    monkeypatch.setattr(runner, "_upload_session", lambda cwd, run_id, sid: None)
    p = FakeProducer()
    rc = runner.run(producer=p)
    assert rc == 0
    types = [v.get("type") for _, _, v in p.published]
    assert "session_fallback" in types
    assert p.published[-1][2]["terminal"] is True and p.published[-1][2]["state"] == "succeeded"
