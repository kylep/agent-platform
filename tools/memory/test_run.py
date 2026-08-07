"""Pure-logic tests against a stubbed cursor (no database in CI)."""
import datetime

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
            kw.get("tags", []), now, now)


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


def test_save_upserts_on_key():
    # UPDATE hits → no INSERT issued.
    cur = FakeCursor(rows=[_row(key="state")])
    out = run.save(cur, "a", {"content": "new", "key": "state"})
    assert out["key"] == "state"
    assert len(cur.executed) == 1 and cur.executed[0][0].startswith("UPDATE")
