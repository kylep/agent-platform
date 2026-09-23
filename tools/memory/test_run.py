"""Pure-logic tests against a stubbed cursor (no database in CI)."""
import datetime
import sys
import types

import pytest

import run


class FakeCursor:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, list(params or [])))

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


def _row(**kw):
    now = datetime.datetime(2026, 8, 7, 12, 0)
    return (kw.get("id", "m1"), kw.get("key"), kw.get("content", "c"),
            kw.get("tags", []), now, now, kw.get("team_id"), kw.get("project_id"))


def test_read_builds_term_and_conditions():
    cur = FakeCursor(rows=[_row()])
    run.read(cur, "agent-a", "two words", 50)
    sql, params = cur.executed[0]
    assert sql.count("LIKE") == 4  # 2 terms x (content, key)
    assert params[0] == "agent-a"
    assert "%two%" in params and "%words%" in params


def test_read_clamps_limit():
    cur = FakeCursor(rows=[])
    run.read(cur, "a", "", 9999)
    assert cur.executed[0][1][-1] == 200
    cur = FakeCursor(rows=[])
    run.read(cur, "a", "", 0)      # falsy limit → the default page size
    assert cur.executed[0][1][-1] == 50


def test_save_requires_content_and_rejects_nul():
    with pytest.raises(SystemExit):
        run.save(FakeCursor(), "a", {})
    with pytest.raises(SystemExit):
        run.save(FakeCursor(), "a", {"content": "x\x00y"})


def test_save_upserts_on_key(monkeypatch):
    # The tool image supplies psycopg; pure-logic tests stub its JSON wrapper.
    pkg = types.ModuleType("psycopg")
    types_pkg = types.ModuleType("psycopg.types")
    json_pkg = types.ModuleType("psycopg.types.json")
    json_pkg.Json = lambda value: value
    for name, module in (("psycopg", pkg), ("psycopg.types", types_pkg),
                         ("psycopg.types.json", json_pkg)):
        monkeypatch.setitem(sys.modules, name, module)
    # UPDATE hits → no INSERT issued.
    cur = FakeCursor(rows=[_row(key="state")])
    out = run.save(cur, "a", {"content": "new", "key": "state"})
    assert out["key"] == "state"
    assert len(cur.executed) == 1 and cur.executed[0][0].startswith("UPDATE")


def test_read_can_filter_by_current_scope():
    cur = FakeCursor(rows=[_row(team_id="t1", project_id="p1")])
    rows = run.read(cur, "agent-a", "", 10, team_id="t1", project_id="p1")
    sql, params = cur.executed[0]
    assert "team_id = %s" in sql and "project_id = %s" in sql
    assert params[:3] == ["agent-a", "t1", "p1"]
    assert rows[0]["project_id"] == "p1"
