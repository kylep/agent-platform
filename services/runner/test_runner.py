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


def test_target_agent_from_status():
    assert runner._target_agent(" M agents/demo/agent.md\n?? other.txt") == "demo"
    assert runner._target_agent(" M services/x.py") is None


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


def test_self_edit_publish_noop_when_no_change(bare_and_clone, monkeypatch):
    bare, clone = bare_and_clone
    monkeypatch.setattr(runner, "_open_or_find_pr", lambda *a, **k: pytest.fail("should not open PR"))
    res = runner.self_edit_publish(clone, {**os.environ}, "abcd1234", "platform-coder", "noop")
    assert res == {"changed": False}
