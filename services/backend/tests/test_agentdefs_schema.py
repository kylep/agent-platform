"""Schema + validation for DB-first agent definitions (docs/design/15).

The tables and the pydantic mirror are the foundation every later piece of the
migration builds on, so the round-trips and the defaults are asserted here
rather than inferred from an API test.
"""
import pytest
from pydantic import ValidationError
from sqlalchemy import select

from agentplatform.agentdefs import (AGENT_ROLES, AgentDefModel, apply_snapshot,
                                     next_version, snapshot_of)
from agentplatform.db import AgentDef, AgentVersion


async def test_agent_def_defaults_round_trip(sf):
    async with sf() as s:
        s.add(AgentDef(name="hello-world")); await s.commit()
    async with sf() as s:
        got = (await s.execute(select(AgentDef))).scalar_one()
    assert got.name == "hello-world"
    assert got.prompt == "" and got.description == "" and got.model == ""
    assert got.role == "operator" and got.system is False and got.can_invoke is False
    assert got.concurrency == 1 and got.timeout_seconds == 1800
    assert got.result_topic == "" and got.transcript_retention_days is None
    assert got.harness_tools == [] and got.platform_tools == []
    assert got.skills == [] and got.secrets == []
    assert got.entrypoints == {} and got.enabled is True
    assert got.created_at is not None and got.updated_at is not None


async def test_agent_def_stores_grants_and_entrypoints(sf):
    ep = {"crons": [{"schedule": "0 * * * *", "prompt": "Scheduled run."}],
          "webhooks": [{"path": "ping"}], "topics": ["app.news.items"]}
    async with sf() as s:
        s.add(AgentDef(name="news", prompt="You are news.", description="d",
                       model="claude-sonnet-5", role="coder", system=True,
                       can_invoke=True, concurrency=2, timeout_seconds=60,
                       result_topic="app.news.result", transcript_retention_days=7,
                       harness_tools=["WebFetch"],
                       platform_tools=["mcp__platform__runs_read"],
                       skills=["git"], secrets=["discord"], entrypoints=ep,
                       enabled=False))
        await s.commit()
    async with sf() as s:
        got = (await s.execute(select(AgentDef))).scalar_one()
    assert got.entrypoints == ep and got.skills == ["git"]
    assert got.transcript_retention_days == 7 and got.enabled is False


async def test_agent_version_round_trip(sf):
    async with sf() as s:
        s.add(AgentVersion(agent="news", version=1, snapshot={"name": "news"},
                           changed_by="admin", changed_via="admin"))
        await s.commit()
    async with sf() as s:
        got = (await s.execute(select(AgentVersion))).scalar_one()
    assert len(got.id) == 32 and got.created_at is not None
    assert got.snapshot == {"name": "news"} and got.changed_via == "admin"


async def test_next_version_starts_at_one_and_is_per_agent(sf):
    async with sf() as s:
        assert await next_version(s, "news") == 1
        s.add(AgentVersion(agent="news", version=1, snapshot={},
                           changed_by="admin", changed_via="admin"))
        await s.commit()
        assert await next_version(s, "news") == 2
        # A second agent's history is independent.
        assert await next_version(s, "pai") == 1
        s.add(AgentVersion(agent="news", version=2, snapshot={},
                           changed_by="admin", changed_via="tool:agents_edit"))
        await s.commit()
        assert await next_version(s, "news") == 3


def _model(**over) -> AgentDefModel:
    return AgentDefModel(name="news", **over)


def test_model_defaults_mirror_the_row():
    m = _model()
    assert m.prompt == "" and m.role == "operator" and m.concurrency == 1
    assert m.timeout_seconds == 1800 and m.enabled is True
    assert m.transcript_retention_days is None
    assert m.harness_tools == [] and m.platform_tools == []
    assert m.entrypoints.crons == [] and m.entrypoints.webhooks == []
    assert m.entrypoints.topics == []


def test_agent_roles_are_a_subset_of_the_auth_roles():
    from agentplatform.api.auth import ROLES
    assert set(AGENT_ROLES) < set(ROLES) and "admin" not in AGENT_ROLES


def test_bad_role_rejected():
    with pytest.raises(ValidationError):
        _model(role="admin")
    with pytest.raises(ValidationError):
        _model(role="wizard")


def test_malformed_entrypoints_rejected():
    with pytest.raises(ValidationError):       # cron entry missing `schedule`
        _model(entrypoints={"crons": [{"prompt": "go"}]})
    with pytest.raises(ValidationError):       # not a cron expression
        _model(entrypoints={"crons": [{"schedule": "every tuesday"}]})
    with pytest.raises(ValidationError):       # webhooks must be objects
        _model(entrypoints={"webhooks": ["ping"]})
    with pytest.raises(ValidationError):       # unknown zone
        _model(entrypoints={"timezone": "Mars/Olympus"})


def test_entrypoints_accepts_the_real_shape():
    m = _model(entrypoints={"crons": [{"schedule": "*/15 * * * *"}],
                            "webhooks": [{"path": "ping"}],
                            "topics": ["app.news.items"],
                            "timezone": "America/Toronto"})
    assert m.entrypoints.crons[0].prompt == ""
    assert m.entrypoints.webhooks[0].path == "ping"


def test_list_fields_must_be_strings():
    with pytest.raises(ValidationError):
        _model(skills=[{"name": "git"}])
    with pytest.raises(ValidationError):
        _model(secrets=[1])
    # Blank entries are noise, not a grant.
    assert _model(skills=["git", " ", "git"]).skills == ["git"]


def test_bad_name_rejected():
    with pytest.raises(ValidationError):
        AgentDefModel(name="Not A Slug")


REGISTRIES = dict(skill_names={"git", "discord"},
                  secret_names={"discord", "strava"},
                  tool_names={"mcp__platform__runs_read", "mcp__platform__strava"})


def test_validate_def_accepts_a_known_definition():
    from agentplatform.agentdefs import validate_def
    m = _model(skills=["git"], secrets=["strava"], harness_tools=["WebFetch"],
               platform_tools=["mcp__platform__strava"])
    assert validate_def(m, **REGISTRIES) == []


def test_validate_def_reports_unknown_grants():
    from agentplatform.agentdefs import validate_def
    m = _model(skills=["git", "nope"], secrets=["ghost"],
               platform_tools=["mcp__platform__runs_read", "mcp__platform__nope"],
               harness_tools=["WebFetch", "Telepathy"])
    problems = validate_def(m, **REGISTRIES)
    assert len(problems) == 4
    joined = " | ".join(problems)
    assert "nope" in joined and "ghost" in joined and "Telepathy" in joined
    assert "git" not in joined and "runs_read" not in joined


def test_snapshot_apply_round_trip():
    src = AgentDef(name="news", prompt="You are news.", description="d",
                   model="claude-sonnet-5", role="coder", system=True,
                   can_invoke=True, concurrency=3, timeout_seconds=90,
                   result_topic="app.news.result", transcript_retention_days=7,
                   harness_tools=["WebFetch"], platform_tools=["mcp__platform__metrics"],
                   skills=["git"], secrets=["discord"],
                   entrypoints={"crons": [{"schedule": "0 * * * *", "prompt": "go"}],
                                "webhooks": [], "topics": [], "timezone": ""},
                   enabled=False)
    snap = snapshot_of(src)
    import json
    assert json.loads(json.dumps(snap)) == snap          # JSON-safe
    assert AgentDefModel(**snap)                          # and a valid definition

    dst = AgentDef(name="other")
    apply_snapshot(dst, snap)
    assert dst.name == "other"                            # identity is not restorable
    for field in ("prompt", "description", "model", "role", "system", "can_invoke",
                  "concurrency", "timeout_seconds", "result_topic",
                  "transcript_retention_days", "harness_tools", "platform_tools",
                  "skills", "secrets", "entrypoints", "enabled"):
        assert getattr(dst, field) == getattr(src, field), field


def test_snapshot_of_a_bare_row_uses_model_defaults():
    """A row read back before create_all defaults land (or a partially built
    one) must still snapshot as a complete, valid definition."""
    snap = snapshot_of(AgentDef(name="news"))
    assert snap["role"] == "operator" and snap["concurrency"] == 1
    assert snap["entrypoints"] == {"crons": [], "webhooks": [], "topics": [],
                                   "timezone": ""}
    assert "created_at" not in snap
