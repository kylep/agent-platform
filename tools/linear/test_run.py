"""Pure-logic tests: identifier parsing + action plumbing against a stubbed
gql (no network, no key)."""
import pytest

import run


def test_identifier_regex():
    assert run.IDENT.match("ENG-123").groups() == ("ENG", "123")
    assert run.IDENT.match("eng2-9").groups() == ("eng2", "9")
    assert run.IDENT.match("nope") is None
    assert run.IDENT.match("ENG-") is None


def test_actions_validate_before_calling_api(monkeypatch):
    calls = []
    monkeypatch.setattr(run, "gql", lambda q, v=None: calls.append((q, v)) or {"data": {}})
    with pytest.raises(SystemExit):
        run.act({"action": "create", "title": "no team"})
    with pytest.raises(SystemExit):
        run.act({"action": "raw_graphql"})
    with pytest.raises(SystemExit):
        run.act({"action": "unknown"})
    assert calls == []  # every rejection happened before any API call


def test_search_builds_team_filter(monkeypatch):
    seen = {}
    def fake(q, v=None):
        seen["q"], seen["v"] = q, v
        return {"data": {"issues": {"nodes": []}}}
    monkeypatch.setattr(run, "gql", fake)
    run.act({"action": "search", "query": "bug", "team_key": "eng", "limit": 5})
    assert "team: {key: {eq: $team}}" in seen["q"]
    assert seen["v"] == {"q": "bug", "first": 5, "team": "ENG"}
    run.act({"action": "search", "query": "bug"})
    assert "team:" not in seen["q"].split("or:")[1].split("]")[1]
