import hashlib
import json, os, re, stat
import base64
import urllib.error
from pathlib import Path
import pytest
import runner
import workbench


def test_verified_plugin_skill_installs_without_plugin_authority(tmp_path, monkeypatch):
    """A run receives only its assigned, checksum-pinned SKILL.md."""
    import shutil
    from pathlib import Path

    source = Path(__file__).resolve().parents[2] / "plugins" / "agent-platform-coding"
    checkout = tmp_path / "checkout"
    (checkout / "skills").mkdir(parents=True)
    shutil.copytree(source, checkout / "plugins" / "agent-platform-coding")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AP_SKILLS_DIR", str(checkout / "skills"))
    monkeypatch.setenv("AP_SKILLS", "platform-change")
    expected = hashlib.sha256((source / "skills" / "platform-change" /
                               "SKILL.md").read_bytes()).hexdigest()
    monkeypatch.setenv("AP_SKILL_HASHES", json.dumps({"platform-change": expected}))
    release_digest = hashlib.sha256((source / "release.json").read_bytes()).hexdigest()
    monkeypatch.setenv("AP_PLUGIN_RELEASE_DIGEST", release_digest)
    runner._install_skills("codex")
    target = tmp_path / "home" / ".agents" / "skills" / "platform-change"
    assert (target / "SKILL.md").read_bytes() == (
        source / "skills" / "platform-change" / "SKILL.md").read_bytes()
    assert list(target.iterdir()) == [target / "SKILL.md"]

    monkeypatch.setenv("AP_PLUGIN_RELEASE_DIGEST", "0" * 64)
    with pytest.raises(ValueError, match="release differs from launch approval"):
        runner._install_skills("claude")
    monkeypatch.setenv("AP_PLUGIN_RELEASE_DIGEST", release_digest)

    monkeypatch.setenv("AP_SKILL_HASHES", "{}")
    with pytest.raises(ValueError, match="hashes are missing"):
        runner._install_skills("claude")
    monkeypatch.setenv("AP_SKILL_HASHES", json.dumps({"platform-change": expected}))

    shutil.rmtree(tmp_path / "home")
    legacy = checkout / "skills" / "platform-change"
    legacy.mkdir()
    (legacy / "SKILL.md").write_text("collision")
    with pytest.raises(ValueError, match="ambiguous"):
        runner._install_skills("codex")
    assert not target.exists()
    shutil.rmtree(legacy)

    (checkout / "plugins" / "agent-platform-coding" / "skills" /
     "platform-change" / "SKILL.md").write_text("tampered")
    with pytest.raises(ValueError, match="checksum mismatch"):
        runner._install_skills("claude")
    assert not (tmp_path / "home" / ".claude" / "skills" /
                "platform-change" / "SKILL.md").exists()


@pytest.fixture(autouse=True)
def _no_ambient_workspace(monkeypatch):
    """This pod is itself a dev workspace, so AP_WORKSPACE=dev is set in the real
    environment ap-verify runs tests in. Left alone that leaks into every test
    here, silently switching runner.run() onto the dev-run branch (workbench
    prepare, a real network call) even for tests that never meant to exercise
    it. Clear it before each test; a test that wants the dev branch still sets
    it itself via monkeypatch, which runs after this fixture and wins."""
    monkeypatch.delenv("AP_WORKSPACE", raising=False)


class FakeProducer:
    def __init__(self): self.published = []
    async def start(self): pass
    async def stop(self): pass
    async def publish(self, topic, key, value, type="run.transcript"): self.published.append((topic, key, value))


def test_project_lookup_guidance_survives_resume():
    prompt = ("<work-context>\nProject: Family 1 (family1).\n"
              "Use Relay search with project='family1'.\n</work-context>\n\nFirst turn")
    resumed = runner._resume_work_context(prompt, "What happened next?")
    assert "project='family1'" in resumed
    assert resumed.endswith("What happened next?")
    assert runner._resume_work_context(prompt, "") == ""
    assert runner._resume_work_context("Ordinary run", "Next") == "Next"

def test_relays_stream_and_terminal(tmp_path, monkeypatch):
    fake = tmp_path / "claude"
    fake.write_text("#!/bin/sh\necho '{\"type\":\"assistant\",\"text\":\"hi\"}'\nexit 0\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    creds = tmp_path / "secrets"; creds.mkdir()
    (creds / "credentials.json").write_text("{}")
    monkeypatch.setenv("AP_RUN_ID", "RID"); monkeypatch.setenv("AP_AGENT", "hello-world")
    monkeypatch.setenv("AP_PROMPT", "hi"); monkeypatch.setenv("CLAUDE_BIN", str(fake))
    monkeypatch.setenv("AP_SECRETS_DIR", str(creds))
    monkeypatch.setenv("HOME", str(tmp_path))
    # The definition arrives as a row (docs/design/15); this test is about the
    # stream, so the fetch is stubbed rather than served.
    monkeypatch.setattr(runner, "_agentdef", lambda: (
        {"name": "hello-world", "description": "Says hi.", "prompt": "hi"}, ""))
    p = FakeProducer()
    rc = runner.run(producer=p)
    assert rc == 0
    topics = [t for t, _, _ in p.published]
    assert "run.transcript" in topics and "run.events" in topics
    first = p.published[0][2]
    assert first["seq"] == 1 and first["type"] == "assistant"
    assert p.published[-1][2]["terminal"] is True
    assert (tmp_path / ".claude" / "agents" / "hello-world.md").exists()


def test_codex_runtime_installs_oauth_and_normalizes_result(tmp_path, monkeypatch):
    fake = tmp_path / "codex"
    fake.write_text("#!/bin/sh\n"
                    "echo '{\"type\":\"thread.started\",\"thread_id\":\"thread-1\"}'\n"
                    "echo '{\"type\":\"item.completed\",\"item\":{\"type\":\"agent_message\",\"text\":\"hello from codex\"}}'\n"
                    "exit 0\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AP_RUN_ID", "RID")
    monkeypatch.setenv("AP_AGENT", "hello-world")
    monkeypatch.setenv("AP_PROMPT", "do it")
    monkeypatch.setenv("AP_RUNTIME", "codex")
    monkeypatch.setenv("AP_SESSION_TOKEN", "ap_sess")
    monkeypatch.setenv("AP_API_URL", "http://api:8090")
    monkeypatch.setenv("CODEX_BIN", str(fake))
    monkeypatch.delenv("AP_USER_MESSAGE", raising=False)
    calls = []

    def api(method, path, body=None, headers=None):
        calls.append((method, path, body))
        if path.endswith("/agentdef"):
            return {"name": "hello-world", "description": "Says hi.",
                    "prompt": "Be concise.", "skills": []}
        if method == "GET":
            return {"auth_json": '{"tokens":{"access_token":"x"}}', "sha256": "old"}
        return {"ok": True}

    monkeypatch.setattr(runner, "_api_req", api)
    p = FakeProducer()
    assert runner.run(producer=p) == 0
    assert json.loads((tmp_path / ".codex" / "auth.json").read_text())["tokens"]
    config = (tmp_path / ".codex" / "config.toml").read_text()
    assert 'approval_policy = "never"' in config
    assert "sandbox_mode" not in config  # legacy sandbox would override the profile below
    assert '":root" = "deny"' in config
    assert '":minimal" = "read"' in config
    assert '"." = "read"' in config
    assert f'{json.dumps(str(tmp_path / ".codex"))} = "deny"' in config
    assert f'{json.dumps(str(tmp_path / ".agents" / "skills"))} = "read"' in config
    result = next(v for _, _, v in p.published if v.get("type") == "result")
    assert result["result"] == "hello from codex" and result["runtime"] == "codex"
    assert any(m == "PUT" and path.endswith("/codex-auth") for m, path, _ in calls)


def test_codex_generated_images_are_uploaded_through_the_run_seam(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    generated = tmp_path / ".codex" / "generated_images" / "thread"
    generated.mkdir(parents=True)
    # A complete 1x1 PNG; the runner treats bytes as opaque and the API does
    # the authoritative raster sniff/measurement.
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6h9sAAAAASUVORK5CYII=")
    (generated / "little-garden.png").write_bytes(png)
    calls = []

    def api(method, path, body=None, headers=None):
        calls.append((method, path, body))
        return {"id": "a" * 32, "name": body["name"]}

    monkeypatch.setattr(runner, "_api_req", api)
    uploaded = runner._upload_codex_generated("r" * 32)
    assert uploaded == [{"id": "a" * 32, "name": "little-garden.png"}]
    method, path, body = calls[0]
    assert (method, path) == ("POST", f"/api/runs/{'r' * 32}/generated-images")
    assert base64.b64decode(body["content_b64"]) == png
    assert body["mime"] == "image/png"


def test_codex_dev_profile_can_write_only_the_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    config = runner._write_codex_config(True).read_text()
    assert '":root" = "deny"' in config
    assert '"." = "write"' in config
    assert "sandbox_mode" not in config


def test_codex_broker_mode_uses_only_placeholder_login(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AP_CODEX_PROXY_URL", "http://agent-platform-codex-proxy:8000")
    config = runner._write_codex_config(False).read_text()
    assert 'sandbox_mode = "danger-full-access"' in config
    assert 'model_provider = "openai"' in config
    assert 'openai_base_url = "http://agent-platform-codex-proxy:8000"' in config
    assert 'chatgpt_base_url = "http://agent-platform-codex-proxy:8000/backend-api/"' in config
    auth = json.loads((tmp_path / ".codex" / "auth.json").read_text())
    assert auth["auth_mode"] == "chatgpt"
    assert auth["tokens"]["refresh_token"] == "agent-platform-placeholder"
    assert "/Users/" not in json.dumps(auth)


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


def _installed(tmp_path, monkeypatch, name, agent_md, home=None):
    """Put an already-rendered definition where `_install_agent` would have
    written it, so the flag tests exercise the parser/permission seam alone."""
    monkeypatch.setenv("HOME", str(home or tmp_path))
    dst = runner._agent_path(name)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(agent_md)


def test_agent_tools_parses_the_installed_definition(tmp_path, monkeypatch):
    _installed(tmp_path, monkeypatch, "news",
               "---\nname: news\ntools: WebSearch, WebFetch\n---\nbody")
    assert runner._agent_tools("news") == ["WebSearch", "WebFetch"]
    # No tools line → empty (fail-closed; nothing is pre-approved).
    _installed(tmp_path, monkeypatch, "news", "---\nname: news\n---\nbody")
    assert runner._agent_tools("news") == []


def test_permission_args_credential_less_agent_is_least_privilege(tmp_path, monkeypatch):
    _installed(tmp_path, monkeypatch, "news",
               "---\nname: news\ntools: WebSearch, WebFetch\n---\nbody")
    args = runner._permission_args(agent="news")
    # Web tools pre-approved; Bash/Read/etc stripped from context; no bypass.
    assert "--allowedTools" in args and "WebSearch" in args and "WebFetch" in args
    assert "--disallowedTools" in args and "Bash" in args and "Read" in args
    assert "bypassPermissions" not in args


def test_permission_args_no_agent_bypass_even_with_token(tmp_path, monkeypatch):
    # No bypassPermissions for a token-bearing agent — scoped like everyone else.
    _installed(tmp_path, monkeypatch, "mon",
               "---\nname: mon\ntools: mcp__platform__runs_read\n---\nbody")
    args = runner._permission_args(agent="mon")
    assert "bypassPermissions" not in args
    assert args[:2] == ["--allowedTools", "mcp__platform__runs_read"]
    assert "--disallowedTools" in args and "Bash" in args and "Read" in args


def test_permission_args_declared_bash_is_stripped_for_standard_run(tmp_path, monkeypatch):
    """The trifecta-break is enforced, not merely conventional: a standard
    agent that DECLARES a token-reading tool still doesn't get it. Otherwise a
    mis-declared (or injection-altered) definition could hand a web agent Bash
    and let it read the mounted Claude token."""
    _installed(tmp_path, monkeypatch, "sneaky",
               "---\nname: sneaky\ntools: WebFetch, Bash, Read\n---\nbody")
    args = runner._permission_args(agent="sneaky")
    allowed = args[args.index("--allowedTools") + 1:args.index("--disallowedTools")]
    assert "WebFetch" in allowed
    assert "Bash" not in allowed and "Read" not in allowed          # declared, still stripped
    disallowed = args[args.index("--disallowedTools") + 1:]
    assert "Bash" in disallowed and "Read" in disallowed


# --- DB-first definition delivery (docs/design/15) -------------------------

def _fetch_env(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AP_RUN_ID", "RID")
    monkeypatch.setenv("AP_SESSION_TOKEN", "ap_sess")
    monkeypatch.setenv("AP_API_URL", "http://api:8090")


def _payload(**over):
    d = {"name": "newsy", "prompt": "You are newsy.\n",
         "description": "Gathers the day's news.",
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
        'description: "Gathers the day\'s news."\n'
        "tools: WebSearch, WebFetch, mcp__platform__memory\n"
        "---\n\nYou are newsy.\n")
    assert runner._agent_tools("newsy") == ["WebSearch", "WebFetch",
                                            "mcp__platform__memory"]


def test_rendered_frontmatter_carries_the_fields_the_cli_requires(tmp_path, monkeypatch):
    """`name` and `description` are the CLI's REQUIRED frontmatter fields: a
    subagent file with a name and no description is skipped, and the run then
    dies on `--agent '<name>' not found`. That is how this was found in
    production, so both fields are pinned here — a payload change that stops
    delivering one has to fail in a test, not in a pod."""
    _fetch_env(monkeypatch, tmp_path)
    monkeypatch.setattr(runner, "_api_req", lambda m, p, body=None: _payload())
    runner._install_agent("newsy")
    front = (tmp_path / ".claude" / "agents" / "newsy.md").read_text().split("---")[1]
    keys = [ln.split(":", 1)[0] for ln in front.strip().splitlines()]
    assert "name" in keys and "description" in keys


def test_a_blank_description_still_renders_a_populated_line(tmp_path, monkeypatch):
    """An agent row's description column defaults to "", and the CLI only
    promises to load a file whose description is THERE — `description:` with
    nothing after it is YAML null, which is the case that was skipped. So a
    blank one becomes a fallback naming the agent rather than an empty line."""
    _fetch_env(monkeypatch, tmp_path)
    monkeypatch.setattr(runner, "_api_req", lambda m, p, body=None:
                        _payload(description=""))
    runner._install_agent("newsy")
    text = (tmp_path / ".claude" / "agents" / "newsy.md").read_text()
    assert '\ndescription: "The newsy agent."\n' in text


def test_a_multiline_description_cannot_corrupt_the_frontmatter(tmp_path, monkeypatch):
    """The description is one frontmatter LINE. A raw newline in it would end
    the scalar and turn the rest of the row's prose into bogus YAML keys, so
    the value collapses to its first line and is quoted."""
    _fetch_env(monkeypatch, tmp_path)
    monkeypatch.setattr(runner, "_api_req", lambda m, p, body=None: _payload(
        description='Reads "the news".\ntools: Bash\nnot: frontmatter'))
    runner._install_agent("newsy")
    text = (tmp_path / ".claude" / "agents" / "newsy.md").read_text()
    front = text.split("---")[1]
    assert front.count("\ndescription:") == 1
    assert "not: frontmatter" not in front
    assert front.count("tools:") == 1                 # the grant line, not the prose
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
    args = runner._permission_args(agent="newsy")
    assert "--allowedTools" not in args
    assert args[0] == "--disallowedTools" and "Bash" in args     # still hard-denied


def test_a_fetched_grant_is_filtered_the_same_way_a_file_one_was(tmp_path, monkeypatch):
    """The frontmatter shape is a contract with `_agent_tools`, not decoration:
    tools that arrive as rows are parsed back out and filtered exactly like the
    ones that used to arrive as files — a granted `Bash` is still denied."""
    _fetch_env(monkeypatch, tmp_path)
    monkeypatch.setattr(runner, "_api_req", lambda m, p, body=None: _payload(
        name="twin", prompt="body", harness_tools=["WebFetch", "Bash"],
        platform_tools=["mcp__platform__runs_read"]))
    runner._install_agent("twin")
    assert runner._agent_tools("twin") == ["WebFetch", "Bash",
                                           "mcp__platform__runs_read"]
    args = runner._permission_args(agent="twin")
    allowed = args[args.index("--allowedTools") + 1:args.index("--disallowedTools")]
    assert allowed == ["WebFetch", "mcp__platform__runs_read"]
    assert "Bash" in args[args.index("--disallowedTools") + 1:]


def test_a_failed_fetch_aborts_naming_the_api_error(tmp_path, monkeypatch):
    """The fetch is the ONLY delivery path (docs/design/15), so a failed one is
    the case with no definition to run. It must be reported in words: an
    uncaught exception here kills the pod before the producer exists, so the run
    lands with an EMPTY error — the exact signature of Claude quota exhaustion.
    That misreading costs the first hour of the investigation."""
    _fetch_env(monkeypatch, tmp_path)

    def boom(*a, **k):
        raise urllib.error.URLError("connection refused")
    monkeypatch.setattr(runner, "_api_req", boom)
    with pytest.raises(runner.AgentUnavailable) as e:
        runner._install_agent("newsy")
    msg = str(e.value)
    assert msg.startswith("agent definition unavailable:")
    assert "api=" in msg and "connection refused" in msg     # why the fetch failed
    assert not (tmp_path / ".claude" / "agents" / "newsy.md").exists()


def test_a_malformed_200_aborts_rather_than_rendering(tmp_path, monkeypatch):
    """A 200 whose body isn't a definition (a proxy error page, a truncated
    body) takes the same road as a failed fetch. Rendering it would KeyError on
    `name` outside the reporting path and kill the run mutely."""
    _fetch_env(monkeypatch, tmp_path)
    monkeypatch.setattr(runner, "_api_req", lambda m, p, body=None: {})
    with pytest.raises(runner.AgentUnavailable) as e:
        runner._install_agent("newsy")
    assert "not a definition" in str(e.value)
    assert not (tmp_path / ".claude" / "agents" / "newsy.md").exists()


def test_no_session_token_aborts_without_calling_the_api(tmp_path, monkeypatch):
    """A pod launched without the run-scoped token has nothing to fetch WITH.
    It says so instead of dialing an endpoint that would 401."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("AP_SESSION_TOKEN", raising=False)
    monkeypatch.setattr(runner, "_api_req",
                        lambda *a, **k: pytest.fail("must not call the API without a token"))
    with pytest.raises(runner.AgentUnavailable) as e:
        runner._install_agent("newsy")
    assert "not attempted" in str(e.value)


def test_a_run_with_no_definition_fails_loudly_and_terminally(tmp_path, monkeypatch):
    """The double failure as the platform sees it: a nonzero exit, a transcript
    frame naming both causes, and a terminal state event carrying it as
    `detail` — which is what the recorder writes to `run.error`, so the run page
    says why instead of showing the blank the quota case shows."""
    creds = tmp_path / "secrets"; creds.mkdir()
    (creds / "credentials.json").write_text("{}")
    monkeypatch.setenv("AP_SECRETS_DIR", str(creds))
    monkeypatch.delenv("AP_CLAUDE_PROXY_URL", raising=False)
    monkeypatch.setenv("AP_AGENT", "newsy"); monkeypatch.setenv("AP_PROMPT", "hi")
    monkeypatch.setenv("CLAUDE_BIN", str(tmp_path / "no-claude-here"))
    _fetch_env(monkeypatch, tmp_path)
    monkeypatch.setattr(runner, "_api_req", lambda *a, **k: {})   # a malformed 200

    p = FakeProducer()
    assert runner.run(producer=p) == 1
    frames = [v for _, _, v in p.published]
    detail = next(v["error"] for v in frames if v.get("type") == "agent_unavailable")
    assert detail.startswith("agent definition unavailable:")
    # Terminal on BOTH topics: the events one is what marks the run failed and
    # carries the reason; the transcript one is what closes a live tail.
    state = next(v for t, _, v in p.published if t == runner.TOPIC_EVENTS)
    assert state["state"] == "failed" and state["terminal"] is True
    assert state["exit_code"] == 1 and state["detail"] == detail
    assert [v for v in frames if v.get("type") == "lifecycle"][-1]["terminal"] is True


def test_install_agent_drops_grant_tokens_that_arent_tool_names(tmp_path, monkeypatch):
    """Defense in depth behind validate_def: only bare tool names reach the
    frontmatter. A permission SPECIFIER (`Bash(...)`) is the sharp case — it
    would slip past _permission_args' exact-match strip of the sensitive set and
    land in --allowedTools."""
    _fetch_env(monkeypatch, tmp_path)
    monkeypatch.setattr(runner, "_api_req", lambda m, p, body=None: _payload(
        harness_tools=["WebFetch", "Bash(rm -rf /)", "Read; echo pwned", "", 42],
        platform_tools=["mcp__platform__memory", "mcp__x__y --flag"]))
    runner._install_agent("newsy")
    assert runner._agent_tools("newsy") == ["WebFetch", "mcp__platform__memory"]
    args = runner._permission_args(agent="newsy")
    allowed = args[args.index("--allowedTools") + 1:args.index("--disallowedTools")]
    assert allowed == ["WebFetch", "mcp__platform__memory"]


# --- conversation session resume (docs/design/14) --------------------------

def _session_env(monkeypatch, tmp_path, fake_body):
    """Common setup for a conversation-resume run: fake claude, creds, agent,
    and the session env. Returns the FakeProducer after running."""
    fake = tmp_path / "claude"; fake.write_text(fake_body)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    creds = tmp_path / "secrets"; creds.mkdir()
    (creds / "credentials.json").write_text("{}")
    monkeypatch.setenv("AP_RUN_ID", "RID"); monkeypatch.setenv("AP_AGENT", "hello-world")
    monkeypatch.setenv("AP_PROMPT", "flattened fallback prompt")
    monkeypatch.setenv("CLAUDE_BIN", str(fake))
    monkeypatch.setenv("AP_SECRETS_DIR", str(creds))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AP_USER_MESSAGE", "continue please")
    monkeypatch.setenv("AP_SESSION_TOKEN", "ap_sess")
    monkeypatch.setenv("AP_API_URL", "http://api:8090")
    # These tests are about resume; the definition fetch is stubbed (and kept
    # off the network) so only one thing is under test.
    monkeypatch.setattr(runner, "_agentdef", lambda: (
        {"name": "hello-world", "description": "Says hi.", "prompt": "hi"}, ""))


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


def test_a_plain_run_does_not_upload_a_session(tmp_path, monkeypatch):
    """Every run carries a session token now (docs/design/15), but only a
    conversation turn has a blob worth PUTting. Gating the upload on the token
    would make every plain run base64 its whole session jsonl for the API to
    decode and then 404 — wasted work, and a "session upload failed" line in
    every pod log that hides the real conversation failures."""
    _session_env(monkeypatch, tmp_path,
                 '#!/bin/sh\necho \'{"type":"result","session_id":"sid-9","result":"ok"}\'\nexit 0\n')
    monkeypatch.delenv("AP_USER_MESSAGE", raising=False)   # not a conversation turn
    monkeypatch.setattr(runner, "_upload_session",
                        lambda *a, **k: pytest.fail("a plain run must not upload a session"))
    p = FakeProducer()
    assert runner.run(producer=p) == 0
    assert p.published[-1][2]["state"] == "succeeded"


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


# --- the dev run (docs/design/24) ------------------------------------------

def test_permission_args_dev_case_pinned(tmp_path, monkeypatch):
    """A dev run is a shell-capable run in a credential-less pod: the file and
    shell tools are allowed outright, the declared grants ride along, and
    `--strict-mcp-config` sits BEFORE the variadic list so nothing can read it
    as a tool name."""
    _installed(tmp_path, monkeypatch, "engineer",
               "---\nname: engineer\ntools: Glob, Grep, mcp__platform__relay\n---\nbody")
    args = runner._permission_args(agent="engineer", dev=True)
    assert args == ["--permission-mode", "acceptEdits", "--strict-mcp-config",
                    "--allowedTools", "Bash", "Read", "Edit", "Write", "NotebookEdit",
                    "Glob", "Grep", "mcp__platform__relay", "mcp__platform__*"]
    assert "--disallowedTools" not in args


def test_permission_args_dev_default_is_off(tmp_path, monkeypatch):
    """The standard case remains least-privileged: `dev` defaults
    to False and the sensitive set stays denied for a non-dev run."""
    _installed(tmp_path, monkeypatch, "news", "---\nname: news\ntools: WebFetch\n---\nbody")
    assert runner._permission_args("news") == [
        "--allowedTools", "WebFetch", "--disallowedTools", *runner._SENSITIVE_TOOLS]
    assert runner._SENSITIVE_TOOLS == ["Bash", "Read", "Edit", "Write", "NotebookEdit"]


_DEV_SHELL_TOOLS = ["Bash", "Read", "Edit", "Write", "NotebookEdit", "Glob", "Grep"]


def test_dev_render_lists_the_shell_tools_in_the_tools_line():
    """Claude Code reads the agent file's `tools:` as the ENABLED set, and
    `--allowedTools` only pre-approves within it — so a dev agent file that
    lists only its grants leaves Bash "not enabled in this context" no matter
    what the flags say (found live: run b6738d261bc44e438ede46cdf0d33755). The
    dev render therefore leads with the same fixed list the dev flags allow,
    then the declared harness tools, then the declared platform tools —
    deduplicated, in that order, and nothing else."""
    d = _payload(name="engineer", harness_tools=["Glob", "WebFetch", "Grep"],
                 platform_tools=["mcp__platform__relay", "mcp__platform__tickets"])
    text = runner._render_agent_md(d, dev=True)
    line = next(ln for ln in text.split("---")[1].splitlines() if ln.startswith("tools:"))
    assert line == ("tools: Bash, Read, Edit, Write, NotebookEdit, Glob, Grep, "
                    "WebFetch, mcp__platform__relay, mcp__platform__tickets")
    assert text.count("tools:") == 1
    # Nothing granted still gets the shell: the line is the fixed list alone.
    bare = runner._render_agent_md(_payload(harness_tools=[], platform_tools=[]), dev=True)
    assert "tools: " + ", ".join(_DEV_SHELL_TOOLS) + "\n" in bare


def test_non_dev_render_is_byte_identical_with_the_dev_flag_off():
    """`dev` defaults to False and the non-dev rendering is exactly what it was:
    the grants alone, in declaration order, no shell tools smuggled in."""
    d = _payload()
    expected = ("---\nname: newsy\n"
                'description: "Gathers the day\'s news."\n'
                "tools: WebSearch, WebFetch, mcp__platform__memory\n"
                "---\n\nYou are newsy.\n")
    assert runner._render_agent_md(d) == expected
    assert runner._render_agent_md(d, dev=False) == expected
    assert runner._render_agent_md(d, dev=True) != expected


def test_install_agent_dev_writes_the_shell_tools_and_the_flags_do_not_double(tmp_path, monkeypatch):
    """The installed dev file carries the shell tools, and the flags built from
    parsing it back out are the same pinned list as before — the fixed set is
    filtered out of the declared tail, so nothing appears twice."""
    _fetch_env(monkeypatch, tmp_path)
    monkeypatch.setattr(runner, "_api_req", lambda m, p, body=None: _payload(
        name="engineer", harness_tools=["Glob", "Grep"],
        platform_tools=["mcp__platform__relay"]))
    runner._install_agent("engineer", dev=True)
    assert runner._agent_tools("engineer") == [*_DEV_SHELL_TOOLS, "mcp__platform__relay"]
    args = runner._permission_args(agent="engineer", dev=True)
    assert args == ["--permission-mode", "acceptEdits", "--strict-mcp-config",
                    "--allowedTools", *_DEV_SHELL_TOOLS, "mcp__platform__relay",
                    "mcp__platform__*"]


def _dev_env(monkeypatch, tmp_path, fake_body):
    fake = tmp_path / "claude"; fake.write_text(fake_body)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    creds = tmp_path / "secrets"; creds.mkdir()
    (creds / "credentials.json").write_text("{}")
    monkeypatch.setenv("AP_SECRETS_DIR", str(creds))
    monkeypatch.delenv("AP_CLAUDE_PROXY_URL", raising=False)
    monkeypatch.delenv("AP_USER_MESSAGE", raising=False)
    monkeypatch.setenv("AP_RUN_ID", "RID"); monkeypatch.setenv("AP_AGENT", "engineer")
    monkeypatch.setenv("AP_PROMPT", "<ticket>ENG-12</ticket>\nWork the ticket.")
    monkeypatch.setenv("CLAUDE_BIN", str(fake))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AP_WORKSPACE", "dev")
    monkeypatch.setenv("AP_MAX_TURNS", "200")
    monkeypatch.setenv("AP_SESSION_TOKEN", "ap_sess")
    monkeypatch.setenv("AP_API_URL", "http://api:8090")
    ws = tmp_path / "ws"; (ws / "repo").mkdir(parents=True)
    monkeypatch.setattr(workbench, "WORKSPACE", ws)
    monkeypatch.setattr(runner, "_agentdef", lambda: (
        {"name": "engineer", "description": "Codes.", "prompt": "You code.",
         "harness_tools": ["Glob", "Grep"], "platform_tools": []}, ""))
    wb = {"branch": "coder/eng-12", "base": "main", "remote_url": "https://example.invalid/o/r.git", "ticket_key": "ENG-12",
          "existing": False, "open_pr": None, "publish_nonce": NONCE}
    monkeypatch.setattr(runner, "_api_req", lambda m, p, body=None, headers=None: wb)
    seen = {}

    def fake_prepare(repo_dir, wb, env, frames=None):
        seen["prepare"] = (repo_dir, wb, dict(env))
        frames.append({"step": "npm ci", "ok": False, "exit": 1, "tail": "boom"})
        return '<workbench branch="coder/eng-12" base="main" commits_ahead="0">\nrules\n</workbench>\n'
    monkeypatch.setattr(workbench, "prepare", fake_prepare)
    real_popen = runner.subprocess.Popen

    def spy(args, **kw):
        seen["args"], seen["cwd"], seen["env"] = args, kw.get("cwd"), kw.get("env")
        return real_popen(args, **kw)
    monkeypatch.setattr(runner.subprocess, "Popen", spy)
    return seen, ws


OK_CLAUDE = '#!/bin/sh\necho \'{"type":"result","session_id":"sid-1","result":"ok"}\'\nexit 0\n'
NONCE = "d3adb33f" * 8


def test_dev_run_keeps_the_nonce_out_of_every_child_process(tmp_path, monkeypatch):
    """The publish nonce is what makes a publish the runner's and not the
    model's: it must reach `finalize` and nothing the model can read — not the
    env `claude` is spawned with (the model's shell inherits it), not the env
    `prepare` hands `npm ci` and git, not the prompt, not a transcript frame."""
    seen, ws = _dev_env(monkeypatch, tmp_path, OK_CLAUDE)
    monkeypatch.setattr(workbench, "finalize",
                        lambda repo_dir, wb, env, run_id, api_req, nonce=None:
                        seen.update(nonce=nonce, wb=wb) or {"published": True, "pr": None})
    p = FakeProducer()
    assert runner.run(producer=p) == 0
    assert seen["nonce"] == NONCE
    assert "publish_nonce" not in seen["wb"]
    for env in (seen["env"], seen["prepare"][2]):
        assert not any(NONCE in str(v) for v in env.values())
    assert NONCE not in json.dumps(seen["args"])
    assert NONCE not in json.dumps([v for _, _, v in p.published])


def test_dev_run_prepares_appends_the_block_and_finalizes(tmp_path, monkeypatch):
    seen, ws = _dev_env(monkeypatch, tmp_path, OK_CLAUDE)
    monkeypatch.setattr(workbench, "finalize",
                        lambda repo_dir, wb, env, run_id, api_req, nonce=None: seen.update(finalize=(repo_dir, run_id))
                        or {"published": True, "pr": {"number": 3, "url": "u"}})
    p = FakeProducer()
    assert runner.run(producer=p) == 0
    repo_dir, wb, env = seen["prepare"]
    assert repo_dir == ws / "repo" and wb["branch"] == "coder/eng-12"
    assert "AP_GITHUB_TOKEN" not in env and "GIT_ASKPASS" not in env
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    args = seen["args"]
    prompt = args[args.index("-p") + 1]
    assert prompt.startswith("<ticket>ENG-12</ticket>\nWork the ticket.")
    assert prompt.rstrip().endswith("</workbench>")           # the block comes LAST
    assert args[args.index("--max-turns") + 1] == "200"
    assert "--strict-mcp-config" in args and "--allowedTools" in args and "Bash" in args
    assert "--disallowedTools" not in args
    # The installed file enables the shell too — the flags alone did not (R2).
    installed = (tmp_path / ".claude" / "agents" / "engineer.md").read_text()
    assert "\ntools: Bash, Read, Edit, Write, NotebookEdit, Glob, Grep\n" in installed
    assert seen["cwd"] == str(ws / "repo")
    assert seen["finalize"] == (ws / "repo", "RID")
    frames = [v for _, _, v in p.published if v.get("type") == "workbench"]
    assert frames[0]["step"] == "npm ci" and frames[0]["ok"] is False   # prepare's note
    assert frames[-1]["published"] is True and frames[-1]["pr"] == {"number": 3, "url": "u"}
    assert p.published[-1][2]["state"] == "succeeded"
    seqs = [v["seq"] for _, _, v in p.published if "seq" in v]
    assert seqs == sorted(seqs) and len(seqs) == len(set(seqs))


def test_dev_run_failed_finalize_is_a_frame_and_a_failed_run(tmp_path, monkeypatch):
    seen, ws = _dev_env(monkeypatch, tmp_path, OK_CLAUDE)

    def boom(*a, **k):
        raise RuntimeError("git bundle failed")
    monkeypatch.setattr(workbench, "finalize", boom)
    p = FakeProducer()
    assert runner.run(producer=p) == 0          # claude's own exit code is what is returned
    frame = next(v for _, _, v in p.published if v.get("type") == "workbench" and "error" in v)
    assert frame["error"] == "git bundle failed"
    assert p.published[-1][2]["state"] == "failed"


def test_dev_run_does_not_finalize_when_claude_fails(tmp_path, monkeypatch):
    seen, ws = _dev_env(monkeypatch, tmp_path, "#!/bin/sh\nexit 3\n")
    monkeypatch.setattr(workbench, "finalize",
                        lambda *a, **k: pytest.fail("finalize runs only after a clean exit"))
    p = FakeProducer()
    assert runner.run(producer=p) == 3
    assert p.published[-1][2]["state"] == "failed"


def test_dev_run_aborts_in_words_when_prepare_fails(tmp_path, monkeypatch):
    seen, ws = _dev_env(monkeypatch, tmp_path, OK_CLAUDE)

    def boom(*a, **k):
        raise workbench.WorkbenchError("git clone failed (exit 128)")
    monkeypatch.setattr(workbench, "prepare", boom)
    p = FakeProducer()
    assert runner.run(producer=p) == 1
    assert "args" not in seen                                   # claude never started
    state = next(v for t, _, v in p.published if t == runner.TOPIC_EVENTS)
    assert state["state"] == "failed" and "git clone failed" in state["detail"]


def test_non_dev_run_never_touches_the_workbench(tmp_path, monkeypatch):
    seen, ws = _dev_env(monkeypatch, tmp_path, OK_CLAUDE)
    monkeypatch.delenv("AP_WORKSPACE")
    monkeypatch.setattr(workbench, "prepare", lambda *a, **k: pytest.fail("not a dev run"))
    monkeypatch.setattr(workbench, "finalize", lambda *a, **k: pytest.fail("not a dev run"))
    p = FakeProducer()
    assert runner.run(producer=p) == 0
    assert "--max-turns" not in seen["args"] and "--disallowedTools" in seen["args"]
    assert seen["cwd"] is None


def test_dev_run_api_refusal_fails_the_run(tmp_path, monkeypatch):
    """A 4xx/5xx from publish means the branch did NOT land; the run must say
    failed, with the API's sentence in the frame, not quietly succeed."""
    seen, ws = _dev_env(monkeypatch, tmp_path, OK_CLAUDE)
    monkeypatch.setattr(workbench, "finalize", lambda *a, **k: {
        "published": False, "status": 422, "reason": "refused: .github/ci.yaml is on the deny list"})
    p = FakeProducer()
    assert runner.run(producer=p) == 0
    frame = next(v for _, _, v in p.published if v.get("type") == "workbench" and "status" in v)
    assert frame["reason"].startswith("refused:") and frame["status"] == 422
    assert p.published[-1][2]["state"] == "failed"


def test_resumed_dev_run_carries_the_block_in_its_user_message(tmp_path, monkeypatch):
    """A conversation turn on a dev run resumes the session with just the new
    user message (docs/design/14), so the block has to ride on THAT — the
    fresh prompt it was appended to is never sent. Still last, after the
    human's text, and still the runner's own facts."""
    seen, ws = _dev_env(monkeypatch, tmp_path, OK_CLAUDE)
    monkeypatch.setenv("AP_USER_MESSAGE", "continue please")
    monkeypatch.setattr(runner, "_restore_session", lambda cwd: "sid-9")
    monkeypatch.setattr(runner, "_upload_session", lambda cwd, run_id, sid: None)
    monkeypatch.setattr(workbench, "finalize", lambda *a, **k: {"published": False, "reason": "no changes"})
    p = FakeProducer()
    assert runner.run(producer=p) == 0
    args = seen["args"]
    assert "--resume" in args and "sid-9" in args
    message = args[args.index("-p") + 1]
    assert message.startswith("continue please\n\n<workbench ")
    assert message.rstrip().endswith("</workbench>")
    assert "Work the ticket." not in message


def test_resumed_plain_run_message_is_untouched(tmp_path, monkeypatch):
    """The block is a dev-run thing: a resumed conversation on any other agent
    sends the user's text and nothing else."""
    _session_env(monkeypatch, tmp_path, OK_CLAUDE)
    monkeypatch.setattr(runner, "_restore_session", lambda cwd: "sid-9")
    monkeypatch.setattr(runner, "_upload_session", lambda cwd, run_id, sid: None)
    seen = {}
    real_popen = runner.subprocess.Popen

    def spy(args, **kw):
        seen["args"] = args
        return real_popen(args, **kw)
    monkeypatch.setattr(runner.subprocess, "Popen", spy)
    assert runner.run(producer=FakeProducer()) == 0
    args = seen["args"]
    assert args[args.index("-p") + 1] == "continue please"


def test_dev_run_with_no_changes_still_succeeds(tmp_path, monkeypatch):
    seen, ws = _dev_env(monkeypatch, tmp_path, OK_CLAUDE)
    monkeypatch.setattr(workbench, "finalize", lambda *a, **k: {"published": False, "reason": "no changes"})
    p = FakeProducer()
    assert runner.run(producer=p) == 0
    assert p.published[-1][2]["state"] == "succeeded"


@pytest.mark.parametrize("value, expected", [(None, "200"), ("abc", "200"), ("", "200"), ("50", "50")])
def test_dev_run_max_turns_always_set(tmp_path, monkeypatch, value, expected):
    seen, ws = _dev_env(monkeypatch, tmp_path, OK_CLAUDE)
    if value is None:
        monkeypatch.delenv("AP_MAX_TURNS")
    else:
        monkeypatch.setenv("AP_MAX_TURNS", value)
    monkeypatch.setattr(workbench, "finalize", lambda *a, **k: {"published": False, "reason": "no changes"})
    p = FakeProducer()
    assert runner.run(producer=p) == 0
    args = seen["args"]
    assert args[args.index("--max-turns") + 1] == expected


# --- the Playwright MCP server (docs/design/25) ------------------------------

PW_ARGS = ["--headless", "--isolated", "--no-sandbox",
           "--executable-path", "/opt/chromium/chrome",
           "--storage-state", "/workspace/qa/state.json",
           "--allowed-origins", "http://ap-web:8090",
           "--image-responses", "allow",
           "--output-dir", "/workspace/qa/mcp",
           "--viewport-size", "1280x800",
           "--config", "/workspace/qa/mcp.json"]
PW_CONFIG = {"browser": {"launchOptions": {"args": [
    "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE ap-web", "--no-sandbox"]}}}


def test_playwright_mcp_is_the_dev_images_global_bin():
    """The runner starts the package's own bin (`playwright-mcp`, the tarball's
    `bin` entry), which the dev image installs globally at the pinned version
    — never `npx <spec>`, which would resolve against the registry rather
    than the install and fetch a second copy in a pod with no egress."""
    text = (Path(__file__).parent / "Dockerfile.dev").read_text()
    assert re.search(r"npm install -g .*@playwright/mcp@0\.0\.82", text)
    assert runner.PLAYWRIGHT_MCP_BIN == "playwright-mcp"


def test_dev_render_turns_the_grant_into_the_servers_tool_pattern():
    """`PlaywrightMCP` is a grant name, not a Claude tool name: the CLI reads
    `tools:` as the enabled set and knows the server's tools as
    `mcp__playwright__*`, so that pattern is what the dev file enables — last,
    after the grants, and the word itself never appears."""
    d = _payload(name="qa", harness_tools=["Glob", "PlaywrightMCP", "Grep"],
                 platform_tools=["mcp__platform__tickets"])
    text = runner._render_agent_md(d, dev=True)
    line = next(ln for ln in text.split("---")[1].splitlines() if ln.startswith("tools:"))
    assert line == ("tools: Bash, Read, Edit, Write, NotebookEdit, Glob, Grep, "
                    "mcp__platform__tickets, mcp__playwright__*")
    assert "PlaywrightMCP" not in text


def test_non_dev_render_drops_the_grant_entirely():
    """Outside a dev run there is no server, so the grant enables nothing and
    is left off the line — not written as a word the CLI would not know."""
    d = _payload(harness_tools=["WebFetch", "PlaywrightMCP"], platform_tools=[])
    text = runner._render_agent_md(d)
    assert "\ntools: WebFetch\n" in text
    assert "PlaywrightMCP" not in text and "playwright" not in text


def test_permission_args_dev_with_the_grant_appends_the_pattern(tmp_path, monkeypatch):
    """With the grant the dev allow-list gains `mcp__playwright__*` — appended
    after the platform pattern, once — and without it the list is byte-for-byte
    the pinned dev list."""
    _fetch_env(monkeypatch, tmp_path)
    monkeypatch.setattr(runner, "_api_req", lambda m, p, body=None: _payload(
        name="qa", harness_tools=["Glob", "Grep", "PlaywrightMCP"],
        platform_tools=["mcp__platform__tickets"]))
    runner._install_agent("qa", dev=True)
    args = runner._permission_args(agent="qa", dev=True)
    assert args == ["--permission-mode", "acceptEdits", "--strict-mcp-config",
                    "--allowedTools", *_DEV_SHELL_TOOLS, "mcp__platform__tickets",
                    "mcp__platform__*", "mcp__playwright__*"]
    assert "PlaywrightMCP" not in args
    monkeypatch.setattr(runner, "_api_req", lambda m, p, body=None: _payload(
        name="eng", harness_tools=["Glob", "Grep"], platform_tools=["mcp__platform__tickets"]))
    runner._install_agent("eng", dev=True)
    args = runner._permission_args(agent="eng", dev=True)
    assert args == ["--permission-mode", "acceptEdits", "--strict-mcp-config",
                    "--allowedTools", *_DEV_SHELL_TOOLS, "mcp__platform__tickets",
                    "mcp__platform__*"]


def test_permission_args_non_dev_strips_the_grant_like_the_sensitive_set(tmp_path, monkeypatch):
    """A non-dev run that declares the grant — or a file that somehow carries
    the server's pattern — gets neither in `--allowedTools`; the denied set is
    exactly what it was (the grant is not sensitive: there is nothing to deny,
    the server is never started)."""
    _installed(tmp_path, monkeypatch, "sneaky",
               "---\nname: sneaky\ntools: WebFetch, PlaywrightMCP, mcp__playwright__*, Bash\n---\nbody")
    args = runner._permission_args(agent="sneaky")
    assert args == ["--allowedTools", "WebFetch", "--disallowedTools", *runner._SENSITIVE_TOOLS]
    assert runner._SENSITIVE_TOOLS == ["Bash", "Read", "Edit", "Write", "NotebookEdit"]
    # A non-dev row that holds ONLY the grant pre-approves nothing at all.
    _installed(tmp_path, monkeypatch, "only", "---\nname: only\ntools: PlaywrightMCP\n---\nbody")
    assert runner._permission_args("only") == [
        "--disallowedTools", *runner._SENSITIVE_TOOLS]


def _mcp_env(monkeypatch):
    monkeypatch.setenv("AP_MCP_URL", "http://broker:8080/mcp")
    monkeypatch.setenv("AP_API_TOKEN", "apk_1")
    monkeypatch.delenv("AP_RUN_TOKEN", raising=False)
    monkeypatch.setenv("AP_WEB_URL", "http://ap-web:8090")


def test_mcp_config_gains_the_playwright_server_only_with_a_state_file(tmp_path, monkeypatch):
    """The second server is written when the run asks for it AND the login
    state exists: no cookie, no browser. Its args are the runner's fixed list
    with the pod's web URL substituted — nothing from the definition, nothing
    from the model — and it is a stdio server the CLI spawns, locked to the
    platform's origin."""
    _mcp_env(monkeypatch)
    state = tmp_path / "qa" / "state.json"
    cfg = json.loads(Path(runner._write_mcp_config(playwright_state=state)).read_text())
    assert list(cfg["mcpServers"]) == ["platform"]
    state.parent.mkdir()
    state.write_text('{"cookies": [], "origins": []}')
    cfg = json.loads(Path(runner._write_mcp_config(playwright_state=state)).read_text())
    assert list(cfg["mcpServers"]) == ["platform", "playwright"]
    assert cfg["mcpServers"]["platform"]["url"] == "http://broker:8080/mcp"
    pw = cfg["mcpServers"]["playwright"]
    expected = [a.replace("/workspace/qa", str(tmp_path / "qa")) for a in PW_ARGS]
    assert pw == {"type": "stdio", "command": "playwright-mcp", "args": expected}
    # The browser's own boundary, beside the state: Chromium resolves the
    # platform's web host and nothing else — the origin flags are advisory.
    assert json.loads((tmp_path / "qa" / "mcp.json").read_text()) == PW_CONFIG
    # Not asked for (no grant) → the platform server alone, file or no file.
    (tmp_path / "qa" / "mcp.json").unlink()
    cfg = json.loads(Path(runner._write_mcp_config()).read_text())
    assert list(cfg["mcpServers"]) == ["platform"]
    assert not (tmp_path / "qa" / "mcp.json").exists()


def test_playwright_args_are_the_pinned_list(monkeypatch):
    """The production paths, verbatim: what the design says the runner starts."""
    monkeypatch.setenv("AP_WEB_URL", "http://ap-web:8090")
    server = runner._playwright_server(Path("/workspace/qa/state.json"))
    assert server == {"type": "stdio", "command": "playwright-mcp", "args": PW_ARGS}
    assert workbench.WORKSPACE == Path("/workspace")


@pytest.mark.parametrize("url, rules", [
    ("http://ap-web:8090", "MAP * ~NOTFOUND, EXCLUDE ap-web"),
    ("http://agent-platform-web.ap.svc.cluster.local:8090",
     "MAP * ~NOTFOUND, EXCLUDE agent-platform-web.ap.svc.cluster.local"),
    # A loopback web URL (a laptop) resolves both spellings, and only then.
    ("http://localhost:8090", "MAP * ~NOTFOUND, EXCLUDE localhost, EXCLUDE 127.0.0.1"),
    ("http://127.0.0.1:8090", "MAP * ~NOTFOUND, EXCLUDE localhost, EXCLUDE 127.0.0.1"),
])
def test_browser_config_resolves_only_the_web_host(url, rules):
    """The config is built from AP_WEB_URL's hostname and nothing else — no
    free text, no path from the definition — and the Chromium sandbox flag
    rides in the same list so the boundary does not depend on how the CLI
    merges its own `--no-sandbox` with the file's args."""
    assert runner._browser_config(url) == {"browser": {"launchOptions": {"args": [
        f"--host-resolver-rules={rules}", "--no-sandbox"]}}}


@pytest.mark.parametrize("url", ["http://", "http://ap web:1", "http://a;b", "http://x,y",
                                 "", "not a url"])
def test_a_web_url_without_a_clean_hostname_means_no_server(tmp_path, monkeypatch, url):
    """A hostname that is not one DNS name (a space, a comma or a semicolon —
    the rule separators — or nothing at all) cannot be turned into a rule, so
    the server is not written rather than written with a hole in its map."""
    _mcp_env(monkeypatch)
    monkeypatch.setenv("AP_WEB_URL", url)
    state = tmp_path / "state.json"; state.write_text("{}")
    cfg = json.loads(Path(runner._write_mcp_config(playwright_state=state)).read_text())
    assert list(cfg["mcpServers"]) == ["platform"]
    assert not (tmp_path / "mcp.json").exists()


def test_playwright_server_needs_a_web_url(tmp_path, monkeypatch):
    """No AP_WEB_URL means no origin to lock the browser to; the server is not
    written rather than written open."""
    _mcp_env(monkeypatch)
    monkeypatch.delenv("AP_WEB_URL")
    state = tmp_path / "state.json"; state.write_text("{}")
    cfg = json.loads(Path(runner._write_mcp_config(playwright_state=state)).read_text())
    assert list(cfg["mcpServers"]) == ["platform"]


def _qa_env(monkeypatch, tmp_path, ws, with_grant=True, with_state=True):
    _mcp_env(monkeypatch)
    monkeypatch.setenv("QA_WEB_USER", "qa")
    monkeypatch.setenv("QA_WEB_PASSWORD", "hunter2-hunter2")
    tools = ["Glob", "Grep", "PlaywrightMCP"] if with_grant else ["Glob", "Grep"]
    monkeypatch.setattr(runner, "_agentdef", lambda: (
        {"name": "engineer", "description": "Tests.", "prompt": "You test.",
         "harness_tools": tools, "platform_tools": ["mcp__platform__tickets"]}, ""))
    if with_state:
        (ws / "qa").mkdir()
        (ws / "qa" / "state.json").write_text('{"cookies": [], "origins": []}')


def test_dev_run_with_the_grant_starts_both_servers_and_hides_the_password(tmp_path, monkeypatch):
    """The whole seam: a dev run holding the grant, after `prepare` logged in,
    spawns `claude` with an MCP config naming both servers and an allow-list
    ending in the playwright pattern — and with NO `QA_WEB_PASSWORD` in its
    environment. `prepare` needed it (it ran `ap-web-login`); the model does
    not, and the MCP server it starts inherits the model's env, so the
    password leaves the environment the moment the login is done, like the
    publish nonce never enters it. The user name is not a secret and stays."""
    seen, ws = _dev_env(monkeypatch, tmp_path, OK_CLAUDE)
    _qa_env(monkeypatch, tmp_path, ws)
    monkeypatch.setattr(workbench, "finalize",
                        lambda repo_dir, wb, env, run_id, api_req, nonce=None:
                        seen.update(finalize_env=dict(env)) or {"published": False, "reason": "no changes"})
    p = FakeProducer()
    assert runner.run(producer=p) == 0
    args = seen["args"]
    allowed = args[args.index("--allowedTools") + 1:args.index("--max-turns")]
    assert allowed[-2:] == ["mcp__platform__*", "mcp__playwright__*"]
    cfg = json.loads(Path(args[args.index("--mcp-config") + 1]).read_text())
    assert list(cfg["mcpServers"]) == ["platform", "playwright"]
    assert cfg["mcpServers"]["playwright"]["args"][6] == str(ws / "qa" / "state.json")
    assert cfg["mcpServers"]["playwright"]["args"][-1] == str(ws / "qa" / "mcp.json")
    assert json.loads((ws / "qa" / "mcp.json").read_text()) == PW_CONFIG
    # prepare saw the credentials; claude, finalize and the transcript did not.
    assert seen["prepare"][2]["QA_WEB_PASSWORD"] == "hunter2-hunter2"
    assert "QA_WEB_PASSWORD" not in seen["env"] and "QA_WEB_PASSWORD" not in seen["finalize_env"]
    assert "hunter2" not in json.dumps(seen["env"]) and "hunter2" not in json.dumps(seen["args"])
    assert "hunter2" not in json.dumps([v for _, _, v in p.published])
    assert seen["env"]["QA_WEB_USER"] == "qa"
    assert not any(v.get("tail") == runner.NO_WEB_LOGIN for _, _, v in p.published)


def test_dev_run_with_the_grant_but_no_login_says_so_and_runs_on(tmp_path, monkeypatch):
    """No state file (the secret is not bound, or the login failed — prepare
    already framed that): the platform server alone, a frame that says the
    browser tools are off, and the run goes on without them."""
    seen, ws = _dev_env(monkeypatch, tmp_path, OK_CLAUDE)
    _qa_env(monkeypatch, tmp_path, ws, with_state=False)
    monkeypatch.setattr(workbench, "finalize", lambda *a, **k: {"published": False, "reason": "no changes"})
    p = FakeProducer()
    assert runner.run(producer=p) == 0
    args = seen["args"]
    cfg = json.loads(Path(args[args.index("--mcp-config") + 1]).read_text())
    assert list(cfg["mcpServers"]) == ["platform"]
    frame = next(v for _, _, v in p.published if v.get("type") == "workbench" and v.get("step") == "web login")
    assert frame["ok"] is False and frame["tail"] == "no web login — browser tools off"
    assert "QA_WEB_PASSWORD" not in seen["env"]
    assert not (ws / "qa" / "mcp.json").exists()
    assert p.published[-1][2]["state"] == "succeeded"


def test_dev_run_without_the_grant_never_writes_the_server(tmp_path, monkeypatch):
    """A state file on disk is not a grant: the engineer, with no
    `PlaywrightMCP`, gets the platform server only and no frame about it."""
    seen, ws = _dev_env(monkeypatch, tmp_path, OK_CLAUDE)
    _qa_env(monkeypatch, tmp_path, ws, with_grant=False)
    monkeypatch.setattr(workbench, "finalize", lambda *a, **k: {"published": False, "reason": "no changes"})
    p = FakeProducer()
    assert runner.run(producer=p) == 0
    args = seen["args"]
    assert "mcp__playwright__*" not in args
    cfg = json.loads(Path(args[args.index("--mcp-config") + 1]).read_text())
    assert list(cfg["mcpServers"]) == ["platform"]
    assert not any(v.get("step") == "web login" for _, _, v in p.published)
    assert "QA_WEB_PASSWORD" not in seen["env"]
    assert not (ws / "qa" / "mcp.json").exists()
