"""The `agents/` tree → import payload exporter (docs/design/15, plan task 9).

The tree these once ran against is GONE — the migration ran, was verified live,
and task 9 part B deleted it. So the exporter is now an OLD-CHECKOUT tool (git
history, a restored backup) and its tests build the trees they read. What they
still hold is every mapping rule the live import depended on, so a future
re-import from an old checkout produces the same rows the cluster is running.

The one assertion that had to change shape rather than move: "the repo's ten
agents export" is now "the repo has no agents directory, and the exporter says
so instead of quietly emitting nothing".
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from agentplatform import export_agents
from agentplatform.agentspec import CLAUDE_TOOLS
from agentplatform.api.schemas import AgentCreateIn

REPO_ROOT = Path(__file__).resolve().parents[3]


def _tree(root: Path, name: str, agent_md: str, manifest: str = "role: operator\n",
          entrypoints: str | None = None) -> Path:
    d = root / "agents" / name
    d.mkdir(parents=True)
    (d / "agent.md").write_text(agent_md)
    (d / "manifest.yaml").write_text(manifest)
    if entrypoints is not None:
        (d / "entrypoints.yaml").write_text(entrypoints)
    return root


# --- the tree is gone -------------------------------------------------------

def test_the_deleted_tree_is_reported_not_silently_empty(capsys):
    """Part B deleted `agents/`. An exporter that answered "0 agents, no
    problems" would let a future re-import wipe the platform with an empty
    payload; it has to fail instead."""
    payloads, problems = export_agents.export_tree(REPO_ROOT)
    assert payloads == []
    assert problems == [f"no agents directory at {REPO_ROOT / 'agents'}"]
    assert export_agents.main([]) == 1
    assert "no agents directory" in capsys.readouterr().err


# --- a full migration, end to end -------------------------------------------

def _migration_tree(root: Path) -> Path:
    """Three agents reproducing the shapes the real migration carried: a mixed
    `tools:` line (both grant lists), a DST-pinned cron with a result topic,
    and a system agent with a plain UTC cron. Grants are code-defined broker
    tools, so they validate against both the synthetic root's registries and
    the API's real ones."""
    _tree(root, "chatty",
          "---\nname: chatty\ndescription: from the frontmatter\n"
          "tools: WebSearch, WebFetch, mcp__platform__query_app\n---\n"
          "You are **chatty**.\n",
          manifest="description: Conversational assistant.\nrole: operator\n"
                   "model: opus\ntimeout_seconds: 180\n")
    _tree(root, "briefer",
          "---\nname: briefer\ntools: WebSearch, mcp__platform__query_app\n---\n"
          "You write the brief.\n",
          manifest='description: "Weekday brief."\nrole: operator\nmodel: sonnet\n'
                   "timeout_seconds: 600\nresult_topic: app.demo.inbound\n",
          entrypoints="cron: ['35 9 * * 1-5']\ntimezone: America/Toronto\n")
    _tree(root, "keeper",
          "---\nname: keeper\ntools: mcp__platform__runs_read, mcp__platform__runs_write\n---\n"
          "You file the history.\n",
          manifest="description: System agent.\nrole: operator\nmodel: sonnet\n"
                   "system: true\nconcurrency: 1\ntimeout_seconds: 300\n",
          entrypoints="cron: ['0 * * * *']\n")
    return root


@pytest.fixture
def migrated(tmp_path):
    payloads, problems = export_agents.export_tree(_migration_tree(tmp_path))
    assert problems == []
    return {p["name"]: p for p in payloads}


def test_every_payload_is_a_body_the_import_endpoint_accepts(migrated):
    """`AgentCreateIn` forbids extra fields, so this fails loudly if the export
    grows a key the endpoint would 422 on — the failure mode that would only
    show up mid-migration otherwise."""
    for name, payload in migrated.items():
        assert AgentCreateIn(**payload).name == name


async def test_an_exported_tree_imports_into_a_fresh_platform(admin_client, migrated):
    """The migration itself: the exporter's own output through the real
    endpoint, against the real skill/secret/tool registries (the test client
    points at the repo trees). This is what task 11 ran live."""
    names = sorted(migrated)
    payloads = [migrated[n] for n in names]
    r = await admin_client.post("/api/agents/import", json=payloads)
    assert r.status_code == 200, r.text
    assert r.json() == [{"name": n, "status": "created"} for n in names]
    # Idempotent: re-running the same payload changes nothing, which is what
    # makes a re-run safe if the first attempt half-lands.
    again = await admin_client.post("/api/agents/import", json=payloads)
    assert again.json() == [{"name": n, "status": "unchanged"} for n in names]

    got = (await admin_client.get("/api/agents/chatty")).json()
    assert got["harness_tools"] == ["WebSearch", "WebFetch"]
    assert got["platform_tools"] == ["mcp__platform__query_app"]
    assert got["model"] == "opus" and got["timeout_seconds"] == 180


def test_a_mixed_tools_line_splits_across_the_two_grant_lists(migrated):
    """The split is by prefix, and the row's two lists are what the launcher's
    role ladder reads."""
    chatty = migrated["chatty"]
    assert chatty["harness_tools"] == ["WebSearch", "WebFetch"]
    assert chatty["platform_tools"] == ["mcp__platform__query_app"]
    assert chatty["role"] == "operator" and chatty["system"] is False
    assert chatty["model"] == "opus" and chatty["timeout_seconds"] == 180
    # The prompt is the BODY: no frontmatter comes across.
    assert chatty["prompt"] == "You are **chatty**.\n"


def test_a_market_pinned_agent_keeps_its_cron_and_its_zone(migrated):
    """A cron that must not drift across DST is why the timezone travels with
    the expression rather than being assumed UTC."""
    b = migrated["briefer"]
    assert b["entrypoints"] == {
        "crons": [{"schedule": "35 9 * * 1-5", "prompt": ""}],
        "webhooks": [], "topics": [], "timezone": "America/Toronto"}
    assert b["result_topic"] == "app.demo.inbound"


def test_a_system_agent_keeps_the_flag_that_injects_its_token(migrated):
    """`system: true` is the difference between an agent that gets an API token
    injected and one that does not — losing it would silently break its run."""
    k = migrated["keeper"]
    assert k["system"] is True
    assert k["entrypoints"]["crons"] == [{"schedule": "0 * * * *", "prompt": ""}]
    assert k["entrypoints"]["timezone"] == ""       # UTC, unlike the market one
    assert k["harness_tools"] == []
    assert k["platform_tools"] == ["mcp__platform__runs_read",
                                   "mcp__platform__runs_write"]


def test_the_manifest_description_wins_over_the_frontmatters(migrated):
    """Two files carried a description and a row has one column. The manifest's
    is the one the platform read (`Manifest.description`), so it wins."""
    assert migrated["chatty"]["description"] == "Conversational assistant."


def test_the_export_is_deterministic(tmp_path):
    """Reruns must diff clean, or nobody can tell a real change from noise."""
    root = _migration_tree(tmp_path)
    first, _ = export_agents.export_tree(root)
    second, _ = export_agents.export_tree(root)
    assert (json.dumps(first, indent=2, sort_keys=True)
            == json.dumps(second, indent=2, sort_keys=True))
    assert [p["name"] for p in first] == sorted(p["name"] for p in first)


# --- the mapping rules ------------------------------------------------------

def test_no_tools_line_materializes_the_effective_set(tmp_path):
    """A missing `tools:` line meant "all tools" in the file era. What such an
    agent could actually USE is narrower — the runner denies Bash/Read/Write/
    Edit/NotebookEdit on every non-self-edit run — and a row has no "unset", so
    the export writes the effective set."""
    _tree(tmp_path, "wide", "---\nname: wide\n---\nbody\n")
    payload, problems = export_agents.load_agent(tmp_path / "agents" / "wide")
    assert problems == []
    assert payload["platform_tools"] == []
    assert payload["harness_tools"] == ["Glob", "Grep", "WebSearch", "WebFetch",
                                        "Task", "TodoWrite"]
    for denied in ("Bash", "Read", "Write", "Edit", "NotebookEdit"):
        assert denied not in payload["harness_tools"]
    # and it is genuinely the non-sensitive half of the harness set, not a
    # hand-copied list that could drift from CLAUDE_TOOLS.
    assert set(payload["harness_tools"]) | export_agents.SENSITIVE_TOOLS == set(CLAUDE_TOOLS)


def test_an_empty_tools_line_stays_empty(tmp_path):
    """The other half of the same distinction: `tools:` present but empty meant
    NOTHING granted, and must not be widened into the effective set."""
    _tree(tmp_path, "narrow", "---\nname: narrow\ntools:\n---\nbody\n")
    payload, problems = export_agents.load_agent(tmp_path / "agents" / "narrow")
    assert problems == []
    assert payload["harness_tools"] == [] and payload["platform_tools"] == []


def test_a_deprecated_manifest_schedule_becomes_a_cron(tmp_path):
    """`schedule:` was deprecated but still honoured at read time. Dropping it
    in the migration would silently stop an agent firing, which is the one
    migration bug nobody notices until the report is missing."""
    _tree(tmp_path, "old", "---\nname: old\ntools: Grep\n---\nbody\n",
          manifest="role: operator\nschedule: '0 6 * * *'\n")
    payload, _ = export_agents.load_agent(tmp_path / "agents" / "old")
    assert payload["entrypoints"]["crons"] == [{"schedule": "0 6 * * *", "prompt": ""}]
    assert "schedule" not in payload      # it is not a column


def test_a_schedule_that_duplicates_an_entrypoint_is_unioned_once(tmp_path):
    _tree(tmp_path, "both", "---\nname: both\ntools: Grep\n---\nbody\n",
          manifest="role: operator\nschedule: '0 6 * * *'\n",
          entrypoints="cron: ['0 6 * * *', '30 6 * * *']\n")
    payload, _ = export_agents.load_agent(tmp_path / "agents" / "both")
    assert payload["entrypoints"]["crons"] == [
        {"schedule": "0 6 * * *", "prompt": ""},
        {"schedule": "30 6 * * *", "prompt": ""}]


def test_legacy_kafka_and_webhook_entrypoints_map_across(tmp_path):
    _tree(tmp_path, "wired", "---\nname: wired\ntools: Grep\n---\nbody\n",
          entrypoints="webhooks:\n  - path: ping\nkafka:\n  - app.x.inbound\n")
    payload, problems = export_agents.load_agent(tmp_path / "agents" / "wired")
    assert problems == []
    assert payload["entrypoints"]["webhooks"] == [{"path": "ping"}]
    assert payload["entrypoints"]["topics"] == ["app.x.inbound"]


def test_a_key_with_no_home_in_a_row_is_a_problem_not_a_silent_drop(tmp_path):
    """The export's value is that what it emits is what the files meant. A key
    it does not understand is reported, never dropped quietly."""
    _tree(tmp_path, "odd", "---\nname: odd\ntools: Grep\nnickname: o\n---\nbody\n",
          manifest="role: operator\nmystery: 3\n",
          entrypoints="cron: []\nsurprise: yes\n")
    _, problems = export_agents.load_agent(tmp_path / "agents" / "odd")
    assert any("nickname" in p for p in problems)
    assert any("mystery" in p for p in problems)
    assert any("surprise" in p for p in problems)
    # the deprecated pair is understood, so it says nothing about them
    _tree(tmp_path, "dep", "---\nname: dep\ntools: Grep\n---\nbody\n",
          manifest="role: operator\nschedule: '0 6 * * *'\nmemory: true\n")
    _, none = export_agents.load_agent(tmp_path / "agents" / "dep")
    assert none == []


def test_a_name_that_disagrees_with_its_directory_is_a_problem(tmp_path):
    _tree(tmp_path, "real", "---\nname: impostor\ntools: Grep\n---\nbody\n")
    payload, problems = export_agents.load_agent(tmp_path / "agents" / "real")
    assert payload["name"] == "real"           # the directory is authoritative
    assert any("impostor" in p for p in problems)


def test_unparseable_and_missing_files_are_problems(tmp_path):
    _tree(tmp_path, "broken", "---\nname: broken\ntools: Grep\n---\nbody\n",
          manifest="role: [unclosed\n")
    (tmp_path / "agents" / "gone").mkdir()
    payloads, problems = export_agents.export_tree(tmp_path)
    assert any("broken: manifest.yaml does not parse" in p for p in problems)
    assert any("gone: no agent.md" in p for p in problems)
    assert any("gone: no manifest.yaml" in p for p in problems)
    # A directory that fails to PARSE still yields a payload shape (so the rest
    # of the tree reports too); what stops the export is `main`'s exit code.
    assert [p["name"] for p in payloads] == ["broken", "gone"]


def test_a_grant_the_repo_does_not_ship_fails_validation(tmp_path):
    """This is where CI's file linting went: a skill/tool/secret that does not
    exist is a dead grant, and it must fail here rather than at launch."""
    (tmp_path / "skills").mkdir()
    (tmp_path / "secrets").mkdir()
    (tmp_path / "tools").mkdir()
    _tree(tmp_path, "hopeful",
          "---\nname: hopeful\ntools: Grep, mcp__platform__nope\n---\nbody\n",
          manifest="role: operator\nskills:\n  - imaginary\n")
    _, problems = export_agents.export_tree(tmp_path)
    assert any("unknown skill: 'imaginary'" in p for p in problems)
    assert any("unknown platform tool: 'mcp__platform__nope'" in p for p in problems)


def test_a_role_the_platform_does_not_have_fails_validation(tmp_path):
    _tree(tmp_path, "wrong", "---\nname: wrong\ntools: Grep\n---\nbody\n",
          manifest="role: superuser\n")
    payloads, problems = export_agents.export_tree(tmp_path)
    assert any("wrong: role: Value error" in p for p in problems)
    assert payloads == []          # an invalid definition is never emitted


# --- the CLI ---------------------------------------------------------------

def test_check_mode_is_green_on_a_good_tree_and_writes_nothing(tmp_path, capsys):
    _migration_tree(tmp_path)
    out = tmp_path / "should-not-exist.json"
    assert export_agents.main(["--check", "--root", str(tmp_path),
                               "--out", str(out)]) == 0
    assert not out.exists()
    assert "3 agent(s) OK" in capsys.readouterr().err


def test_check_mode_exits_nonzero_on_a_broken_tree(tmp_path, capsys):
    _tree(tmp_path, "broken", "---\nname: broken\ntools: Grep\n---\nbody\n",
          manifest="role: nonsense\n")
    assert export_agents.main(["--check", "--root", str(tmp_path)]) == 1
    assert "broken: role" in capsys.readouterr().err


def test_out_writes_the_payload_and_a_broken_tree_writes_nothing(tmp_path):
    _migration_tree(tmp_path)
    out = tmp_path / "agents.json"
    assert export_agents.main(["--root", str(tmp_path), "--out", str(out)]) == 0
    payload = json.loads(out.read_text())
    assert [a["name"] for a in payload] == ["briefer", "chatty", "keeper"]
    assert out.read_text().endswith("}\n]\n")     # trailing newline, diff-clean

    bad = tmp_path / "bad"
    _tree(bad, "broken", "---\nname: broken\n---\nbody\n", manifest="role: x\n")
    missed = tmp_path / "not-written.json"
    assert export_agents.main(["--root", str(bad), "--out", str(missed)]) == 1
    assert not missed.exists()


def test_the_module_runs_as_a_script(tmp_path):
    """`python -m agentplatform.export_agents` is how the migration invoked it —
    the entrypoint is part of the deliverable, not just the functions."""
    _migration_tree(tmp_path)
    r = subprocess.run([sys.executable, "-m", "agentplatform.export_agents",
                        "--check", "--root", str(tmp_path)],
                       capture_output=True, text=True, cwd=str(REPO_ROOT))
    assert r.returncode == 0, r.stderr
    assert "3 agent(s) OK" in r.stderr
