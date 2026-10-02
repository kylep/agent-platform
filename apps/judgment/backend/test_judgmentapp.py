"""Judgment app: DDL from the shared column lists, Kyle-only auth, every
endpoint, every guard and every deletion rule (design 38), on sqlite.

Rows Kai would write are seeded through the tool's own statements in
`schema.py` (INSERT_BELIEF, INSERT_VERSION, ...), so what the tests read back
is exactly what the tool will have written."""
from __future__ import annotations

import json
import os
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import event

from judgmentapp import schema
from judgmentapp.db import (TABLES, execute, init_db, make_engine, make_session_factory,
                            metadata, query, translate)

pytest_plugins = ("pytest_asyncio",)

# sqlite by default; APP_DB_URL (the env the app reads) points the same suite
# at a real Postgres, which is where the shared statements actually run.
DB_URL = os.environ.get("APP_DB_URL", "sqlite+aiosqlite:///:memory:")

API = "/apps/judgment/api"
KYLE = {"X-AP-User": "admin", "X-AP-Role": "admin"}
DAY = timedelta(days=1)


@pytest.fixture
async def engine():
    engine = make_engine(DB_URL)
    await init_db(engine)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(metadata.drop_all)
    await engine.dispose()


@pytest.fixture
def sf(engine):
    return make_session_factory(engine)


@pytest.fixture
async def client(sf):
    from judgmentapp.main import app
    app.state.sf = sf
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://t", headers=KYLE) as c:
        yield c


@pytest.fixture
def statements(engine):
    """Every SQL statement the app sends, for asserting what it never does."""
    seen: list[str] = []

    def record(_conn, _cursor, statement, *_a):
        seen.append(" ".join(statement.split()).upper())
    event.listen(engine.sync_engine, "before_cursor_execute", record)
    yield seen
    event.remove(engine.sync_engine, "before_cursor_execute", record)


# --- seeding through the tool's statements ----------------------------------------------

async def _write(sf, *steps):
    async with sf() as s:
        for sql, params in steps:
            await execute(s, sql, params)
        await s.commit()


async def _rows(sf, sql: str, params=()) -> list[tuple]:
    async with sf() as s:
        return await query(s, sql, params)


async def _count(sf, table: str, where: str = "", params=()) -> int:
    return (await _rows(sf, f"SELECT COUNT(*) FROM {table} {where}", params))[0][0]


def _version_params(bid, version, claim, *, provenance="inference", feedback_id=None,
                    scope=None, evidence="he said so", source_ref=None, at=None):
    return (schema.new_id(), bid, version, claim, scope, evidence, provenance, source_ref,
            "medium", f"v{version}", feedback_id, schema.AGENT_AUTHOR,
            at or schema.now() - DAY)


async def belief(sf, *claims, provenance="inference", status="active", at=None,
                 scope=None) -> str:
    """A belief with one version per claim, as the tool writes them."""
    bid = schema.new_id()
    at = at or schema.now() - 3 * DAY
    steps = [(schema.INSERT_BELIEF, (bid, at, status, len(claims)))]
    for n, claim in enumerate(claims, 1):
        steps.append((schema.INSERT_VERSION,
                      _version_params(bid, n, claim, provenance=provenance, scope=scope,
                                      source_ref="relay:" + "a" * 32
                                      if provenance == "kyle_relayed" else None,
                                      at=at + timedelta(minutes=n))))
    await _write(sf, *steps)
    return bid


async def add_version(sf, bid, claim, *, feedback_id=None, provenance="inference") -> int:
    status, current = (await _rows(sf, schema.BELIEF, (bid,)))[0][2:]
    await _write(sf, (schema.INSERT_VERSION,
                      _version_params(bid, current + 1, claim, provenance=provenance,
                                      feedback_id=feedback_id)),
                 (schema.SET_BELIEF_HEAD, (current + 1, status, bid)))
    return current + 1


async def prediction(sf, scenario="Pizza or tacos on Friday?", *, timing="prospective",
                     links=(), at=None, alternatives=("pizza", "tacos")) -> str:
    pid = schema.new_id()
    steps = [(schema.INSERT_PREDICTION,
              (pid, at or schema.now() - 2 * DAY, scenario, json.dumps(list(alternatives)),
               "tacos", "he always picks tacos", "medium", timing, None,
               schema.AGENT_AUTHOR))]
    for bid, version in links:
        steps.append((schema.INSERT_PREDICTION_BELIEF, (pid, bid, version)))
    await _write(sf, *steps)
    return pid


async def feedback(sf, *, prediction_id=None, belief_id=None, belief_version=None,
                   outcome="supported", confirmed=False, at=None, source_at=None,
                   words="I went with tacos") -> str:
    """Relayed feedback, as the tool writes it (unconfirmed unless asked)."""
    fid = schema.new_id()
    at = at or schema.now() - DAY
    await _write(sf, (schema.INSERT_FEEDBACK,
                      (fid, at, prediction_id, belief_id, belief_version, words,
                       "discord:123/456", source_at or at, outcome, "Kai's reading",
                       schema.AGENT_AUTHOR, at if confirmed else None)))
    return fid


async def prediction_row(sf, pid) -> tuple | None:
    rows = await _rows(sf, schema.PREDICTION, (pid,))
    return rows[0] if rows else None


# --- DDL and dialect ----------------------------------------------------------------

def test_tables_follow_the_shared_column_lists():
    for name, cols in schema.COLUMNS.items():
        assert [c.name for c in TABLES[name].columns] == cols
        assert [c.name for c in TABLES[name].primary_key] == schema.PRIMARY_KEYS[name]


def test_translate_binds_placeholders_and_drops_for_update_on_sqlite_only():
    assert translate(schema.LOCK_BELIEF, "sqlite") == \
        "SELECT id, status, current_version FROM beliefs WHERE id = :p0"
    assert translate(schema.LOCK_BELIEF, "postgresql").endswith("WHERE id = :p0 FOR UPDATE")
    assert translate(schema.SET_BELIEF_HEAD, "sqlite") == \
        "UPDATE beliefs SET current_version = :p0, status = :p1 WHERE id = :p2"


async def test_version_numbers_are_unique_per_belief(sf):
    bid = await belief(sf, "likes tacos")
    with pytest.raises(Exception):
        await _write(sf, (schema.INSERT_VERSION, _version_params(bid, 1, "again")))


# --- who may use it ------------------------------------------------------------------

ROUTES = [
    ("GET", "me"), ("GET", "beliefs"), ("GET", "beliefs/{b}"),
    ("POST", "beliefs/{b}/confirm"), ("POST", "beliefs/{b}/correct"),
    ("POST", "beliefs/{b}/reject"), ("DELETE", "beliefs/{b}"),
    ("GET", "predictions"), ("GET", "predictions/{p}"),
    ("POST", "predictions/{p}/feedback"), ("DELETE", "predictions/{p}"),
    ("GET", "feedback"), ("POST", "feedback/{f}/confirm"),
    ("GET", "feedback/{f}/delete-preview"), ("DELETE", "feedback/{f}"), ("GET", "review"),
]
BODIES = {"beliefs/{b}/confirm": {"expected_version": 1},
          "beliefs/{b}/correct": {"expected_version": 1, "claim": "x"},
          "beliefs/{b}/reject": {"expected_version": 1, "reason": "no"},
          "predictions/{p}/feedback": {"kyle_words": "x", "outcome": "supported"}}


async def _call_all(client, sf, headers) -> dict:
    """Each route against its own fresh belief, prediction and feedback, so an
    earlier delete can't turn a later call into a 404."""
    out = {}
    for method, path in ROUTES:
        bid = await belief(sf, "likes tacos")
        pid = await prediction(sf, links=[(bid, 1)])
        fid = await feedback(sf, prediction_id=pid)
        url = f"{API}/" + path.format(b=bid, p=pid, f=fid)
        body = BODIES.get(path)
        r = await client.request(method, url, json=body, headers=headers)
        out[(method, path)] = r
    return out


@pytest.mark.parametrize("headers", [
    pytest.param({"X-AP-User": "", "X-AP-Role": ""}, id="missing-headers"),
    pytest.param({"X-AP-User": "admin", "X-AP-Role": ""}, id="missing-role"),
    pytest.param({"X-AP-User": "", "X-AP-Role": "admin"}, id="missing-user"),
    pytest.param({"X-AP-User": "admin", "X-AP-Role": "reader"}, id="reader-login"),
    pytest.param({"X-AP-User": "kai", "X-AP-Role": "reader"}, id="query-app"),
    pytest.param({"X-AP-User": "ci-deploy", "X-AP-Role": "admin"}, id="admin-api-key"),
    pytest.param({"X-AP-User": "app:judgment", "X-AP-Role": "annotator"}, id="app-key"),
])
async def test_every_route_refuses_everyone_but_the_owner_session(client, sf, headers):
    for (method, path), r in (await _call_all(client, sf, headers)).items():
        assert r.status_code == 403, (method, path, r.text)
        assert "detail" in r.json()
    # What was seeded per route and nothing more: no write got through.
    n = len(ROUTES)
    assert [await _count(sf, t) for t in schema.COLUMNS] == [n, n, n, n, n, 0]
    assert await _count(sf, "feedback", "WHERE confirmed_at IS NOT NULL") == 0


async def test_the_owner_session_reaches_every_route(client, sf):
    for (method, path), r in (await _call_all(client, sf, KYLE)).items():
        assert r.status_code == 200, (method, path, r.text)


async def test_owner_principals_come_from_the_environment(client, monkeypatch):
    monkeypatch.setenv("JUDGMENT_OWNER_PRINCIPALS", "kyle, kp")
    assert (await client.get(f"{API}/me")).status_code == 403
    r = await client.get(f"{API}/me", headers={"X-AP-User": "kp", "X-AP-Role": "admin"})
    assert r.json() == {"principal": "kp"}


async def test_me_names_the_principal(client):
    assert (await client.get(f"{API}/me")).json() == {"principal": "admin"}


# --- beliefs ---------------------------------------------------------------------------

async def test_beliefs_list_with_current_and_latest_confirmed(client, sf):
    old = await belief(sf, "likes pizza", at=schema.now() - 5 * DAY)
    bid = await belief(sf, "likes tacos")
    await add_version(sf, bid, "likes tacos on Fridays", provenance="kyle_confirmed")
    await add_version(sf, bid, "likes tacos, mostly")
    gone = await belief(sf, "likes sushi", status="rejected", at=schema.now() - 2 * DAY)
    rows = (await client.get(f"{API}/beliefs")).json()
    assert [r["id"] for r in rows] == [gone, bid, old]  # newest first
    top = next(r for r in rows if r["id"] == bid)
    assert top["current_version"] == 3 and top["status"] == "active"
    assert top["current"]["claim"] == "likes tacos, mostly"
    assert top["confirmed"]["version"] == 2
    assert top["confirmed"]["claim"] == "likes tacos on Fridays"
    assert next(r for r in rows if r["id"] == old)["confirmed"] is None
    assert set(top["current"]) == set(schema.COLUMNS["belief_versions"])
    assert isinstance(top["created_at"], str) and isinstance(top["current"]["created_at"], str)

    assert [r["id"] for r in (await client.get(f"{API}/beliefs?status=rejected")).json()] == [gone]
    active = (await client.get(f"{API}/beliefs?status=active")).json()
    assert {r["id"] for r in active} == {bid, old}
    assert (await client.get(f"{API}/beliefs?status=all")).json() == rows
    assert (await client.get(f"{API}/beliefs?status=bogus")).status_code == 422


async def test_belief_detail_has_versions_predictions_and_feedback(client, sf):
    bid = await belief(sf, "likes tacos", "likes tacos on Fridays")
    pid = await prediction(sf, links=[(bid, 1)])
    fid = await feedback(sf, belief_id=bid, belief_version=2, outcome="contradicted")
    await belief(sf, "unrelated")
    body = (await client.get(f"{API}/beliefs/{bid}")).json()
    assert body["belief"]["id"] == bid and body["belief"]["current_version"] == 2
    assert [v["version"] for v in body["versions"]] == [1, 2]
    assert body["predictions"] == [{
        "id": pid, "scenario": "Pizza or tacos on Friday?", "predicted_choice": "tacos",
        "timing": "prospective", "created_at": body["predictions"][0]["created_at"]}]
    assert [f["id"] for f in body["feedback"]] == [fid]
    assert set(body["feedback"][0]) == set(schema.COLUMNS["feedback"])


async def test_unknown_or_malformed_ids_are_404(client):
    for path in ("beliefs/" + "0" * 32, "beliefs/nope", "predictions/" + "0" * 32,
                 "predictions/nope"):
        assert (await client.get(f"{API}/{path}")).status_code == 404
    for path in ("beliefs/" + "0" * 32, "predictions/" + "0" * 32, "feedback/" + "0" * 32):
        assert (await client.delete(f"{API}/{path}")).status_code == 404
    assert (await client.get(f"{API}/feedback/{'0' * 32}/delete-preview")).status_code == 404
    assert (await client.post(f"{API}/feedback/{'0' * 32}/confirm")).status_code == 404
    r = await client.post(f"{API}/beliefs/{'0' * 32}/confirm", json={"expected_version": 1})
    assert r.status_code == 404


async def test_confirm_writes_a_kyle_confirmed_version_of_the_current_claim(client, sf):
    bid = await belief(sf, "likes tacos", provenance="kyle_relayed", status="superseded",
                       scope="weeknights")
    r = await client.post(f"{API}/beliefs/{bid}/confirm", json={"expected_version": 1})
    assert r.status_code == 200, r.text
    v = r.json()
    assert (v["version"], v["provenance"], v["author"], v["claim"], v["scope"]) == \
        (2, "kyle_confirmed", "user:admin", "likes tacos", "weeknights")
    assert v["status"] == "active" and v["feedback_id"] is None
    assert (await _rows(sf, schema.BELIEF, (bid,)))[0][2:] == ("active", 2)
    detail = (await client.get(f"{API}/beliefs/{bid}")).json()
    assert detail["versions"][0]["provenance"] == "kyle_relayed"  # history untouched


async def test_correct_writes_kyle_words_as_a_confirmed_version(client, sf):
    bid = await belief(sf, "likes tacos", scope="weeknights")
    r = await client.post(f"{API}/beliefs/{bid}/correct",
                          json={"expected_version": 1, "claim": "  likes tacos al pastor  "})
    v = r.json()
    assert r.status_code == 200, r.text
    assert (v["version"], v["claim"], v["scope"], v["provenance"], v["author"]) == \
        (2, "likes tacos al pastor", "weeknights", "kyle_confirmed", "user:admin")
    # Kai's evidence is not Kyle's: it does not carry onto his correction.
    assert v["evidence"] is None and v["source_ref"] is None
    assert v["reason"] == "Kyle corrected the claim on his page"
    r = await client.post(f"{API}/beliefs/{bid}/correct",
                          json={"expected_version": 2, "claim": "likes burritos",
                                "scope": "lunch", "reason": "changed my mind"})
    assert (r.json()["scope"], r.json()["reason"]) == ("lunch", "changed my mind")


async def test_reject_records_the_reason_in_a_new_version(client, sf):
    bid = await belief(sf, "likes sushi")
    r = await client.post(f"{API}/beliefs/{bid}/reject",
                          json={"expected_version": 1, "reason": "I never eat fish"})
    v = r.json()
    assert r.status_code == 200, r.text
    assert (v["version"], v["claim"], v["reason"], v["author"], v["status"]) == \
        (2, "likes sushi", "I never eat fish", "user:admin", "rejected")
    # The claim's own provenance: a rejection never mints a kyle_confirmed claim.
    assert v["provenance"] == "inference"
    assert (await _rows(sf, schema.BELIEF, (bid,)))[0][2:] == ("rejected", 2)
    assert (await client.get(f"{API}/beliefs/{bid}")).json()["belief"]["status"] == "rejected"


@pytest.mark.parametrize("action,body", [
    ("confirm", {}), ("correct", {"claim": "x"}), ("reject", {"reason": "no"})])
async def test_belief_writes_refuse_a_stale_expected_version(client, sf, action, body):
    bid = await belief(sf, "likes tacos", "likes tacos on Fridays")
    r = await client.post(f"{API}/beliefs/{bid}/{action}", json={"expected_version": 1, **body})
    assert r.status_code == 409 and "version 2" in r.json()["detail"]
    assert await _count(sf, "belief_versions") == 2
    r = await client.post(f"{API}/beliefs/{bid}/{action}", json=body)
    assert r.status_code == 422  # expected_version is required
    r = await client.post(f"{API}/beliefs/{bid}/{action}",
                          json={"expected_version": "two", **body})
    assert r.status_code == 422


@pytest.mark.parametrize("action,body", [
    ("confirm", {"author": "agent:kai"}),
    ("confirm", {"provenance": "inference"}),
    ("correct", {"claim": "x", "confirmed_at": "2026-01-01T00:00:00Z"}),
    ("correct", {"claim": ""}),
    ("correct", {"claim": "x" * (schema.LIMITS["claim"] + 1)}),
    ("correct", {}),
    ("reject", {"reason": "   "}),
    ("reject", {}),
])
async def test_belief_writes_validate_and_refuse_server_fields(client, sf, action, body):
    bid = await belief(sf, "likes tacos")
    r = await client.post(f"{API}/beliefs/{bid}/{action}", json={"expected_version": 1, **body})
    assert r.status_code == 422, r.text
    assert await _count(sf, "belief_versions") == 1
    assert (await _rows(sf, schema.BELIEF, (bid,)))[0][2:] == ("active", 1)


async def test_only_the_page_writes_kyle_confirmed_and_always_as_a_user(client, sf):
    bid = await belief(sf, "likes tacos")
    await client.post(f"{API}/beliefs/{bid}/confirm", json={"expected_version": 1})
    await client.post(f"{API}/beliefs/{bid}/correct", json={"expected_version": 2, "claim": "y"})
    await client.post(f"{API}/beliefs/{bid}/reject", json={"expected_version": 3, "reason": "n"})
    rows = await _rows(sf, "SELECT version, provenance, author FROM belief_versions "
                           "WHERE belief_id = %s ORDER BY version", (bid,))
    assert rows == [(1, "inference", "agent:kai"), (2, "kyle_confirmed", "user:admin"),
                    (3, "kyle_confirmed", "user:admin"), (4, "kyle_confirmed", "user:admin")]


# --- predictions ------------------------------------------------------------------------

async def test_predictions_list_resolution_flags_and_state_filter(client, sf):
    now = schema.now()
    pending = await prediction(sf, "A?", at=now - 5 * DAY)
    unresolved = await prediction(sf, "B?", at=now - 4 * DAY)
    await feedback(sf, prediction_id=unresolved, outcome="unresolved")
    mixed = await prediction(sf, "C?", at=now - 3 * DAY)
    await feedback(sf, prediction_id=mixed, outcome="supported")
    await feedback(sf, prediction_id=mixed, outcome="contradicted", confirmed=True)
    quick = await prediction(sf, "D?", at=now - 2 * DAY)
    await feedback(sf, prediction_id=quick, outcome="supported", at=now - 2 * DAY +
                   timedelta(minutes=3), source_at=now - 3 * DAY)
    retro = await prediction(sf, "E?", timing="retrospective", at=now - DAY)
    await feedback(sf, prediction_id=retro, outcome="supported", at=now - DAY,
                   source_at=now - 2 * DAY)

    rows = (await client.get(f"{API}/predictions")).json()
    assert [r["id"] for r in rows] == [retro, quick, mixed, unresolved, pending]
    by = {r["id"]: r for r in rows}
    assert by[pending]["alternatives"] == ["pizza", "tacos"]
    assert (by[pending]["resolution"], by[pending]["feedback_count"]) == (None, 0)
    assert (by[unresolved]["resolution"], by[unresolved]["feedback_count"]) == (None, 1)
    assert (by[mixed]["resolution"], by[mixed]["feedback_count"]) == ("mixed", 2)
    assert by[quick]["flags"] == ["feedback predates the prediction",
                                  "feedback within 10 minutes of the prediction"]
    assert by[mixed]["flags"] == []
    assert by[retro]["flags"] == []  # timing is only in doubt when claimed prospective
    for r in rows:
        assert set(schema.COLUMNS["predictions"]) <= set(r)

    ids = lambda r: [p["id"] for p in r.json()]  # noqa: E731
    assert ids(await client.get(f"{API}/predictions?state=pending")) == [unresolved, pending]
    assert ids(await client.get(f"{API}/predictions?state=resolved")) == [retro, quick, mixed]
    assert (await client.get(f"{API}/predictions?state=open")).status_code == 422


async def test_prediction_detail_links_feedback_and_a_deleted_version(client, sf):
    bid = await belief(sf, "likes tacos", "likes tacos on Fridays")
    other = await belief(sf, "eats late")
    pid = await prediction(sf, links=[(bid, 1), (other, 1)])
    fid = await feedback(sf, prediction_id=pid, outcome="contradicted")
    await _write(sf, ("DELETE FROM belief_versions WHERE belief_id = %s AND version = 1",
                      (other,)))
    body = (await client.get(f"{API}/predictions/{pid}")).json()
    assert body["prediction"]["id"] == pid
    assert body["prediction"]["alternatives"] == ["pizza", "tacos"]
    links = {link["belief_id"]: link for link in body["links"]}
    assert links[bid] == {"belief_id": bid, "belief_version": 1, "claim": "likes tacos"}
    assert links[other]["claim"] is None
    assert [f["id"] for f in body["feedback"]] == [fid]
    assert body["resolution"] == "contradicted" and body["flags"] == []


async def test_feedback_written_on_the_page_is_confirmed_on_creation(client, sf):
    bid = await belief(sf, "likes tacos", "likes tacos on Fridays")
    pid = await prediction(sf, links=[(bid, 1)])
    r = await client.post(f"{API}/predictions/{pid}/feedback",
                          json={"kyle_words": "tacos, obviously", "outcome": "supported",
                                "belief_id": bid})
    assert r.status_code == 200, r.text
    f = r.json()
    assert set(f) == set(schema.COLUMNS["feedback"])
    assert (f["prediction_id"], f["belief_id"], f["belief_version"]) == (pid, bid, 2)
    assert (f["author"], f["kyle_words"], f["outcome"]) == ("user:admin", "tacos, obviously",
                                                            "supported")
    assert f["confirmed_at"] is not None and f["confirmed_at"] == f["created_at"]
    assert f["source_ref"] is None and f["interpretation"] is None
    r = await client.post(f"{API}/predictions/{pid}/feedback",
                          json={"kyle_words": "v1 was right", "outcome": "mixed",
                                "belief_id": bid, "belief_version": 1})
    assert r.json()["belief_version"] == 1
    r = await client.post(f"{API}/predictions/{pid}/feedback",
                          json={"kyle_words": "just this", "outcome": "unresolved"})
    assert (r.json()["belief_id"], r.json()["belief_version"]) == (None, None)
    assert (await client.get(f"{API}/predictions/{pid}")).json()["resolution"] == "mixed"


@pytest.mark.parametrize("body,status", [
    ({"kyle_words": "", "outcome": "supported"}, 422),
    ({"kyle_words": "x", "outcome": "right"}, 422),
    ({"kyle_words": "x"}, 422),
    ({"kyle_words": "x" * (schema.LIMITS["kyle_words"] + 1), "outcome": "supported"}, 422),
    ({"kyle_words": "x", "outcome": "supported", "belief_version": 1}, 422),
    ({"kyle_words": "x", "outcome": "supported", "belief_id": "nope"}, 422),
    ({"kyle_words": "x", "outcome": "supported", "belief_id": "0" * 32}, 422),
    ({"kyle_words": "x", "outcome": "supported", "belief_id": "{bid}", "belief_version": 9}, 422),
    ({"kyle_words": "x", "outcome": "supported", "author": "agent:kai"}, 422),
    ({"kyle_words": "x", "outcome": "supported", "confirmed_at": None}, 422),
])
async def test_page_feedback_is_validated(client, sf, body, status):
    bid = await belief(sf, "likes tacos")
    pid = await prediction(sf)
    if body.get("belief_id") == "{bid}":
        body = {**body, "belief_id": bid}
    r = await client.post(f"{API}/predictions/{pid}/feedback", json=body)
    assert r.status_code == status, r.text
    assert await _count(sf, "feedback") == 0
    r = await client.post(f"{API}/predictions/{'0' * 32}/feedback",
                          json={"kyle_words": "x", "outcome": "supported"})
    assert r.status_code == 404


# --- feedback --------------------------------------------------------------------------------

async def test_feedback_list_filters_by_confirmation_newest_first(client, sf):
    pid = await prediction(sf)
    a = await feedback(sf, prediction_id=pid, at=schema.now() - 3 * DAY)
    b = await feedback(sf, prediction_id=pid, confirmed=True, at=schema.now() - 2 * DAY)
    c = await feedback(sf, prediction_id=pid, at=schema.now() - DAY)

    async def ids(q):
        return [f["id"] for f in (await client.get(f"{API}/feedback{q}")).json()]
    assert await ids("") == [c, b, a]
    assert await ids("?confirmed=all") == [c, b, a]
    assert await ids("?confirmed=false") == [c, a]
    assert await ids("?confirmed=true") == [b]
    assert (await client.get(f"{API}/feedback?confirmed=maybe")).status_code == 422


async def test_confirming_feedback_confirms_words_not_interpretation(client, sf):
    pid = await prediction(sf)
    fid = await feedback(sf, prediction_id=pid, words="tacos I guess")
    r = await client.post(f"{API}/feedback/{fid}/confirm")
    f = r.json()
    assert r.status_code == 200, r.text
    assert f["confirmed_at"] is not None and f["kyle_words"] == "tacos I guess"
    assert (f["interpretation"], f["author"]) == ("Kai's reading", "agent:kai")
    first = f["confirmed_at"]
    r = await client.post(f"{API}/feedback/{fid}/confirm", json={"kyle_words": "tacos, always"})
    assert (r.json()["kyle_words"], r.json()["confirmed_at"]) == ("tacos, always", first)
    assert (await client.post(f"{API}/feedback/{fid}/confirm", json={})).status_code == 200

    other = await feedback(sf, prediction_id=pid)
    r = await client.post(f"{API}/feedback/{other}/confirm", json={"kyle_words": "edited"})
    assert r.json()["confirmed_at"] is not None and r.json()["kyle_words"] == "edited"
    for bad in ({"kyle_words": "x" * 4001}, {"confirmed_at": None}, {"outcome": "mixed"}):
        r = await client.post(f"{API}/feedback/{fid}/confirm", json=bad)
        assert r.status_code == 422, bad


# --- deletion ---------------------------------------------------------------------------

async def test_delete_belief_takes_versions_links_and_orphaned_feedback(client, sf):
    bid = await belief(sf, "likes tacos", "likes tacos on Fridays")
    keep = await belief(sf, "eats late")
    pid = await prediction(sf, links=[(bid, 1), (keep, 1)])
    only_belief = await feedback(sf, belief_id=bid, belief_version=2, outcome="contradicted")
    both = await feedback(sf, prediction_id=pid, belief_id=bid, belief_version=1)
    elsewhere = await feedback(sf, belief_id=keep, belief_version=1)
    before = await prediction_row(sf, pid)

    r = await client.delete(f"{API}/beliefs/{bid}")
    assert r.status_code == 200, r.text
    assert r.json() == {"deleted": {"beliefs": 1, "versions": 2, "links": 1, "feedback": 1,
                                    "predictions": 0}}
    assert await _count(sf, "beliefs", "WHERE id = %s", (bid,)) == 0
    assert await _count(sf, "belief_versions", "WHERE belief_id = %s", (bid,)) == 0
    assert await _count(sf, "prediction_beliefs", "WHERE belief_id = %s", (bid,)) == 0
    assert await _count(sf, "feedback", "WHERE id = %s", (only_belief,)) == 0
    # Feedback that also targeted the prediction survives, belief link cleared.
    assert (await _rows(sf, "SELECT prediction_id, belief_id, belief_version FROM feedback "
                            "WHERE id = %s", (both,))) == [(pid, None, None)]
    # The prediction keeps its own text and its other link; the rest is untouched.
    assert await prediction_row(sf, pid) == before
    assert await _rows(sf, schema.PREDICTION_LINKS, (pid,)) == [(pid, keep, 1)]
    assert await _count(sf, "feedback", "WHERE id = %s", (elsewhere,)) == 1
    assert await _count(sf, "belief_versions", "WHERE belief_id = %s", (keep,)) == 1
    assert await _count(sf, "feedback", "WHERE belief_id = %s", (bid,)) == 0


async def test_delete_prediction_takes_links_and_feedback_that_targeted_only_it(client, sf):
    bid = await belief(sf, "likes tacos")
    pid = await prediction(sf, links=[(bid, 1)])
    other = await prediction(sf, "Other?", links=[(bid, 1)])
    only = await feedback(sf, prediction_id=pid)
    also = await feedback(sf, prediction_id=pid, belief_id=bid, belief_version=1)
    theirs = await feedback(sf, prediction_id=other)

    r = await client.delete(f"{API}/predictions/{pid}")
    assert r.status_code == 200, r.text
    assert r.json() == {"deleted": {"beliefs": 0, "versions": 0, "links": 1, "feedback": 1,
                                    "predictions": 1}}
    assert await prediction_row(sf, pid) is None
    assert await _count(sf, "prediction_beliefs", "WHERE prediction_id = %s", (pid,)) == 0
    assert await _count(sf, "feedback", "WHERE id = %s", (only,)) == 0
    assert (await _rows(sf, "SELECT prediction_id, belief_id, belief_version FROM feedback "
                            "WHERE id = %s", (also,))) == [(None, bid, 1)]
    assert await _count(sf, "feedback", "WHERE prediction_id = %s", (pid,)) == 0
    assert await _count(sf, "feedback", "WHERE id = %s", (theirs,)) == 1
    assert await _count(sf, "prediction_beliefs") == 1
    assert await _count(sf, "belief_versions") == 1


async def test_delete_feedback_takes_every_citing_version_and_falls_back(client, sf):
    pid = await prediction(sf)
    fid = await feedback(sf, prediction_id=pid, outcome="contradicted", words="never sushi")
    # One belief keeps an older version; its head falls back to it.
    kept = await belief(sf, "likes sushi", provenance="kyle_relayed", status="superseded")
    await add_version(sf, kept, "likes sushi, not really")
    await add_version(sf, kept, "never sushi", feedback_id=fid)
    await add_version(sf, kept, "never sushi, even at parties", feedback_id=fid)
    # A belief born from the feedback empties and goes, with its links.
    born = schema.new_id()
    await _write(sf, (schema.INSERT_BELIEF, (born, schema.now() - DAY, "active", 1)),
                 (schema.INSERT_VERSION,
                  _version_params(born, 1, "avoids raw fish", feedback_id=fid)))
    linked = await prediction(sf, "Sushi night?", links=[(born, 1), (kept, 4)])
    untouched = await belief(sf, "eats late")
    before = await prediction_row(sf, linked)

    preview = (await client.get(f"{API}/feedback/{fid}/delete-preview")).json()
    assert preview == {
        "versions": sorted([{"belief_id": kept, "version": 3, "claim": "never sushi"},
                            {"belief_id": kept, "version": 4,
                             "claim": "never sushi, even at parties"},
                            {"belief_id": born, "version": 1, "claim": "avoids raw fish"}],
                           key=lambda v: (v["belief_id"], v["version"])),
        "beliefs_emptied": [born], "feedback": []}
    assert await _count(sf, "belief_versions") == 6  # the preview deletes nothing

    r = await client.delete(f"{API}/feedback/{fid}")
    assert r.status_code == 200, r.text
    assert r.json() == {"deleted": {"beliefs": 1, "versions": 3, "links": 1, "feedback": 1,
                                    "predictions": 0}}
    assert await _count(sf, "feedback", "WHERE id = %s", (fid,)) == 0
    assert await _count(sf, "belief_versions", "WHERE feedback_id = %s", (fid,)) == 0
    assert await _rows(sf, "SELECT version FROM belief_versions WHERE belief_id = %s "
                           "ORDER BY version", (kept,)) == [(1,), (2,)]
    # Head back to the newest remaining version; status left as it was.
    assert (await _rows(sf, schema.BELIEF, (kept,)))[0][2:] == ("superseded", 2)
    assert await _count(sf, "beliefs", "WHERE id = %s", (born,)) == 0
    assert await _count(sf, "prediction_beliefs", "WHERE belief_id = %s", (born,)) == 0
    # The kept belief's link to its deleted version stays; the claim reads null.
    links = (await client.get(f"{API}/predictions/{linked}")).json()["links"]
    assert links == [{"belief_id": kept, "belief_version": 4, "claim": None}]
    assert await prediction_row(sf, linked) == before
    assert await prediction_row(sf, pid) is not None
    assert await _count(sf, "belief_versions", "WHERE belief_id = %s", (untouched,)) == 1


async def test_delete_feedback_that_leaves_the_head_alone(client, sf):
    pid = await prediction(sf)
    fid = await feedback(sf, prediction_id=pid, outcome="mixed")
    bid = await belief(sf, "likes tacos")
    await add_version(sf, bid, "likes tacos on Fridays", feedback_id=fid)
    await add_version(sf, bid, "likes tacos, any day")
    r = await client.delete(f"{API}/feedback/{fid}")
    assert r.json()["deleted"]["versions"] == 1
    assert await _rows(sf, "SELECT version FROM belief_versions WHERE belief_id = %s "
                           "ORDER BY version", (bid,)) == [(1,), (3,)]
    assert (await _rows(sf, schema.BELIEF, (bid,)))[0][2:] == ("active", 3)


async def test_an_emptied_belief_takes_feedback_left_with_no_target(client, sf):
    pid = await prediction(sf)
    fid = await feedback(sf, prediction_id=pid, outcome="contradicted")
    born = schema.new_id()
    await _write(sf, (schema.INSERT_BELIEF, (born, schema.now() - DAY, "active", 1)),
                 (schema.INSERT_VERSION, _version_params(born, 1, "derived", feedback_id=fid)))
    orphan = await feedback(sf, belief_id=born, belief_version=1, outcome="supported")
    shared = await feedback(sf, prediction_id=pid, belief_id=born, belief_version=1)
    # A version elsewhere citing the orphan goes with it, by the feedback rule.
    elsewhere = await belief(sf, "likes tacos")
    await add_version(sf, elsewhere, "from the orphan", feedback_id=orphan)

    preview = (await client.get(f"{API}/feedback/{fid}/delete-preview")).json()
    assert preview["beliefs_emptied"] == [born]
    assert preview["feedback"] == [orphan]
    assert {(v["belief_id"], v["version"]) for v in preview["versions"]} == \
        {(born, 1), (elsewhere, 2)}
    r = await client.delete(f"{API}/feedback/{fid}")
    assert r.json()["deleted"] == {"beliefs": 1, "versions": 2, "links": 0, "feedback": 2,
                                   "predictions": 0}
    assert await _count(sf, "feedback", "WHERE id IN (%s, %s)", (fid, orphan)) == 0
    assert (await _rows(sf, "SELECT prediction_id, belief_id FROM feedback WHERE id = %s",
                        (shared,))) == [(pid, None)]
    assert (await _rows(sf, schema.BELIEF, (elsewhere,)))[0][3] == 1
    # Nothing anywhere points at a row that is gone.
    assert await _count(sf, "belief_versions v", "WHERE v.feedback_id IS NOT NULL AND NOT "
                        "EXISTS (SELECT 1 FROM feedback f WHERE f.id = v.feedback_id)") == 0
    assert await _count(sf, "feedback f", "WHERE f.belief_id IS NOT NULL AND NOT EXISTS "
                        "(SELECT 1 FROM beliefs b WHERE b.id = f.belief_id)") == 0


async def test_deleting_everything_leaves_the_tables_empty(client, sf):
    bid = await belief(sf, "likes tacos", "likes tacos on Fridays")
    pid = await prediction(sf, links=[(bid, 2)])
    fid = await feedback(sf, prediction_id=pid, belief_id=bid, belief_version=2,
                         outcome="contradicted")
    await add_version(sf, bid, "not on Fridays", feedback_id=fid)
    for path in (f"feedback/{fid}", f"predictions/{pid}", f"beliefs/{bid}"):
        assert (await client.delete(f"{API}/{path}")).status_code == 200, path
    for table in ("beliefs", "belief_versions", "predictions", "prediction_beliefs",
                  "feedback"):
        assert await _count(sf, table) == 0, table


async def test_predictions_are_never_updated(client, sf, statements):
    bid = await belief(sf, "likes tacos")
    pid = await prediction(sf, links=[(bid, 1)])
    other = await prediction(sf, "Other?", links=[(bid, 1)])
    before = await prediction_row(sf, pid)
    statements.clear()
    fid = (await client.post(f"{API}/predictions/{pid}/feedback",
                             json={"kyle_words": "no", "outcome": "contradicted",
                                   "belief_id": bid})).json()["id"]
    await client.post(f"{API}/feedback/{fid}/confirm", json={"kyle_words": "nope"})
    await client.post(f"{API}/beliefs/{bid}/correct", json={"expected_version": 1, "claim": "z"})
    await client.post(f"{API}/beliefs/{bid}/reject", json={"expected_version": 2, "reason": "r"})
    await client.get(f"{API}/review")
    await client.delete(f"{API}/feedback/{fid}")
    await client.delete(f"{API}/beliefs/{bid}")
    await client.delete(f"{API}/predictions/{other}")
    assert statements and not [s for s in statements if s.startswith("UPDATE PREDICTIONS")]
    assert await prediction_row(sf, pid) == before


# --- review ---------------------------------------------------------------------------------

def _keys(value) -> set[str]:
    if isinstance(value, dict):
        return set(value) | set().union(*(_keys(v) for v in value.values()))
    if isinstance(value, list):
        return set().union(set(), *(_keys(v) for v in value))
    return set()


async def test_review_counts_and_resolved_prospective_items_only(client, sf):
    now = schema.now()
    confirmed = await prediction(sf, "A?", at=now - 6 * DAY)
    await feedback(sf, prediction_id=confirmed, outcome="supported", confirmed=True)
    relayed = await prediction(sf, "B?", at=now - 5 * DAY)
    await feedback(sf, prediction_id=relayed, outcome="contradicted")
    mixed = await prediction(sf, "C?", at=now - 4 * DAY)
    await feedback(sf, prediction_id=mixed, outcome="supported")
    await feedback(sf, prediction_id=mixed, outcome="context_changed")
    pending = await prediction(sf, "D?", at=now - 3 * DAY)
    await feedback(sf, prediction_id=pending, outcome="unresolved", confirmed=True)
    flagged = await prediction(sf, "E?", at=now - 2 * DAY)
    await feedback(sf, prediction_id=flagged, outcome="supported",
                   at=now - 2 * DAY + timedelta(minutes=1))
    retro = await prediction(sf, "F?", timing="retrospective", at=now - DAY)
    await feedback(sf, prediction_id=retro, outcome="supported")

    body = (await client.get(f"{API}/review")).json()
    assert body["counts"] == {
        "prospective_resolved": {"supported": 2, "contradicted": 1, "mixed": 1,
                                 "context_changed": 0},
        "prospective_resolved_confirmed": 1,
        "prospective_resolved_relayed_only": 3,
        "prospective_pending": 1,
        "retrospective": 1,
        "flagged": 1,
        "feedback_unconfirmed": 5,
    }
    items = body["items"]
    assert [i["prediction"]["id"] for i in items] == [flagged, mixed, relayed, confirmed]
    by = {i["prediction"]["id"]: i for i in items}
    assert by[mixed]["resolution"] == "mixed" and len(by[mixed]["feedback"]) == 2
    assert by[flagged]["flags"] == ["feedback within 10 minutes of the prediction"]
    assert by[confirmed]["feedback"][0]["confirmed_at"] is not None
    assert by[confirmed]["prediction"]["alternatives"] == ["pizza", "tacos"]
    # Counts, never a score.
    assert not {k for k in _keys(body) if any(w in k for w in
                                              ("percent", "pct", "accuracy", "rate", "score"))}


async def test_review_of_an_empty_store(client):
    body = (await client.get(f"{API}/review")).json()
    assert body["items"] == [] and body["counts"]["prospective_pending"] == 0
