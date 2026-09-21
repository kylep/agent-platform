"""TCMS app: DDL built from the shared column lists, the browse API and the
reconciler, on sqlite. Rows are seeded straight through `schema.COLUMNS`, so a
column the tool writes and this app reads has exactly one spelling."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import inspect as sa_inspect

from tcmsapp import schema
from tcmsapp.db import (TABLES, execute, init_db, make_engine, make_session_factory,
                        metadata, query, translate)
from tcmsapp.reconcile import TOPIC_RECORDED, Reconciler, format_note

pytest_plugins = ("pytest_asyncio",)

T0 = datetime(2026, 9, 1, 2, 0, tzinfo=timezone.utc)


# sqlite by default; APP_DB_URL (the env the app reads) points the same suite
# at a real Postgres, which is where the shared statements actually run.
DB_URL = os.environ.get("APP_DB_URL", "sqlite+aiosqlite:///:memory:")


@pytest.fixture
async def sf():
    engine = make_engine(DB_URL)
    await init_db(engine)
    yield make_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(metadata.drop_all)
    await engine.dispose()


# --- seeding through the shared column lists ----------------------------------

async def _insert(sf, table: str, **row) -> int | None:
    """One row through `schema.COLUMNS[table]`, bound the way the app binds
    (`db.execute`): an unknown column is a test bug, and every column the tool
    writes is one the app can read back."""
    unknown = set(row) - set(schema.COLUMNS[table])
    assert not unknown, f"{table} has no column {unknown}"
    cols = [c for c in schema.COLUMNS[table] if c in row]
    vals = [json.dumps(row[c]) if isinstance(row[c], (list, dict)) else row[c] for c in cols]
    sql = (f"INSERT INTO {table} ({', '.join(cols)}) VALUES "
           f"({', '.join('%s' for _ in cols)})")
    if "id" in schema.COLUMNS[table] and "id" not in row:
        sql += " RETURNING id"
    async with sf() as s:
        r = await execute(s, sql, vals)
        rid = r.scalar() if sql.endswith("RETURNING id") else None
        await s.commit()
        return rid


async def _case(sf, key: str, layer: str = "integration", refs: list | None = None,
                status: str = "active", area: str = "relay") -> None:
    await _insert(sf, "cases", key=key, suite=key.split(".")[0], area=area,
                  title=f"Case {key}", layer=layer, priority="p1",
                  preconditions=[], steps=["do"], expected="ok",
                  automation=refs if refs is not None else [f"pytest:t/{key}.py::t"],
                  tags=[], tickets=[], status=status,
                  synced_at=T0, source_sha="abc")


async def _run(sf, n: int, sha: str = "a1b2c3d4e5", results: list | None = None,
               coverage: list | None = None, run_id: str | None = None,
               published: bool = False, days_ago: float = 0.0) -> int:
    """Run number `n` (later n = later run), with `(ref, status, ms[, case_key])`
    results and `(package, covered, total)` coverage."""
    started = T0 + timedelta(hours=n) - timedelta(days=days_ago)
    rid = await _insert(sf, "test_runs", commit_sha=sha, branch="main",
                        run_id=run_id or f"run{n:04d}" + "0" * 24, agent="qa",
                        started_at=started,
                        finished_at=started + timedelta(minutes=4, seconds=12),
                        verify_ok=True,
                        suites=[{"name": "backend", "exit": 0, "seconds": 252.0}],
                        published_at=started if published else None)
    for row in results or []:
        ref, status, ms = row[:3]
        await _insert(sf, "results", test_run_id=rid, ref=ref,
                      case_key=row[3] if len(row) > 3 else None, status=status,
                      duration_ms=ms, message="boom" if status in ("fail", "error") else "",
                      layer=_layer(ref))
    for pkg, covered, total in coverage or []:
        await _insert(sf, "coverage_snapshots", test_run_id=rid, package=pkg,
                      lines_covered=covered, lines_total=total, branch_rate=None)
    return rid


def _layer(ref: str) -> str:
    path = ref.split(":", 1)[1].split("::", 1)[0]
    if path.startswith("services/web/tests/"):
        return "e2e"
    if path.startswith("tools/") and path.endswith("test_run.py"):
        return "unit"
    return "integration"


BE = "pytest:services/backend/tests/test_relay.py::test_"
WEB = "playwright:services/web/tests/relay.spec.ts::"
TOOL = "pytest:tools/tcms/test_run.py::test_"


# --- DDL ------------------------------------------------------------------------

def test_ddl_is_built_from_the_shared_columns():
    for table, cols in schema.COLUMNS.items():
        assert [c.name for c in TABLES[table].columns] == cols
    assert set(TABLES) == set(schema.COLUMNS)


def test_ddl_declares_the_unique_the_tool_inserts_on():
    from sqlalchemy import UniqueConstraint
    for table, cols in schema.UNIQUE.items():
        declared = [list(c.columns.keys()) for c in TABLES[table].constraints
                    if isinstance(c, UniqueConstraint)]
        assert cols in declared, f"{table} lacks UNIQUE({cols})"


async def test_init_db_creates_every_table_with_every_column(sf):
    engine = sf.kw["bind"]
    async with engine.connect() as conn:
        def cols(sync_conn):
            insp = sa_inspect(sync_conn)
            return {t: [c["name"] for c in insp.get_columns(t)] for t in insp.get_table_names()}
        found = await conn.run_sync(cols)
    for table, expected in schema.COLUMNS.items():
        assert found[table] == expected


def test_translate_rewrites_placeholders_once_and_sqlite_casts():
    sql = "SELECT AVG(x)::int AS a FROM t WHERE k ILIKE %s AND n > %s"
    assert translate(sql, "postgresql") == \
        "SELECT AVG(x)::int AS a FROM t WHERE k ILIKE :p0 AND n > :p1"
    assert translate(sql, "sqlite") == \
        "SELECT AVG(x) AS a FROM t WHERE k LIKE :p0 AND n > :p1"


async def test_query_runs_the_tools_own_read_statements(sf):
    """The statements the tool executes with psycopg run here too, so the UI and
    the tool answer from the same SQL."""
    await _case(sf, "relay.a", refs=[BE + "a"])
    await _run(sf, 1, results=[(BE + "a", "fail", 100, "relay.a")])
    await _run(sf, 2, results=[(BE + "a", "pass", 100, "relay.a")])
    async with sf() as s:
        flaky = await query(s, schema.FLAKY, (30,))
        slowest = await query(s, schema.SLOWEST, (30, 5))
        prune = await query(s, schema.PRUNE_CANDIDATES, (30,))
        by_layer = await query(s, schema.RUNTIME_BY_LAYER, (30,))
        gaps = await query(s, schema.COVERAGE_GAPS, (80,))
    assert [r[0] for r in flaky] == [BE + "a"]
    assert slowest[0][0] == BE + "a" and int(slowest[0][2]) == 100
    assert prune == [] and len(by_layer) == 2 and gaps == []


# --- API ------------------------------------------------------------------------

@pytest.fixture
async def client(sf):
    from tcmsapp.main import app
    app.state.sf = sf
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t",
                                 headers={"X-AP-User": "qa"}) as c:
        yield c


async def test_api_requires_gateway_identity(sf, client):
    bare = httpx.AsyncClient(transport=httpx.ASGITransport(app=client._transport.app),
                             base_url="http://t")
    for path in ("overview", "runs", "runs/1", "cases", "cases/x.y", "flaky",
                 "slowest", "prune-candidates", "coverage"):
        r = await bare.get(f"/apps/tcms/api/{path}")
        assert r.status_code == 401, path
    await bare.aclose()


async def test_overview_empty_database(client):
    r = await client.get("/apps/tcms/api/overview")
    assert r.status_code == 200
    body = r.json()
    assert body["latest_run"] is None
    assert body["pyramid"] == [{"layer": "e2e", "count": 0, "seconds": 0.0},
                               {"layer": "integration", "count": 0, "seconds": 0.0},
                               {"layer": "unit", "count": 0, "seconds": 0.0}]
    assert body["pass_rate"] == [] and body["coverage"]["lines_total"] == 0
    assert body["attention"] == {"failing": 0, "flaky": 0, "unlinked": 0,
                                 "prune_candidates": 0}


async def test_overview_pyramid_pass_rate_coverage_and_attention(sf, client):
    await _case(sf, "relay.linked", refs=[BE + "a"])
    await _case(sf, "relay.unlinked", refs=[BE + "never"])
    await _case(sf, "relay.manual", layer="manual", refs=[])
    # An older run with worse coverage, then the latest with a flake and a
    # fresh failure.
    await _run(sf, 1, sha="sha1", results=[(BE + "a", "pass", 100, "relay.linked"),
                                           (BE + "b", "fail", 50),
                                           (WEB + "w", "pass", 4000)],
               coverage=[("agentplatform", 50, 100)])
    await _run(sf, 2, sha="sha1", results=[(BE + "a", "pass", 100, "relay.linked"),
                                           (BE + "b", "pass", 50),
                                           (BE + "c", "fail", 10),
                                           (WEB + "w", "pass", 5000),
                                           (TOOL + "t", "pass", 20)],
               coverage=[("agentplatform", 80, 100), ("tcmsapp", 10, 20)])
    body = (await client.get("/apps/tcms/api/overview")).json()
    assert body["latest_run"]["commit_sha"] == "sha1"
    assert body["latest_run"]["totals"] == {"pass": 4, "fail": 1, "skip": 0,
                                            "flaky": 0, "error": 0}
    pyramid = {p["layer"]: p for p in body["pyramid"]}
    assert pyramid["e2e"] == {"layer": "e2e", "count": 1, "seconds": 5.0}
    assert pyramid["integration"]["count"] == 3
    assert pyramid["unit"]["count"] == 1
    rates = body["pass_rate"]
    assert [round(r["rate"], 2) for r in rates] == [0.67, 0.8]     # oldest first
    assert body["coverage"]["lines_covered"] == 90
    assert body["coverage"]["lines_total"] == 120
    assert body["coverage"]["pct"] == 75.0
    assert [t["pct"] for t in body["coverage"]["trend"]] == [50.0, 75.0]
    # failing now: c; flaky: b (fail then pass on the same commit); unlinked:
    # the case whose ref no result ever matched (manual cases do not count);
    # prune: w, the slowest ref that never failed.
    assert body["attention"] == {"failing": 1, "flaky": 1, "unlinked": 1,
                                 "prune_candidates": 1}


async def test_runs_list_carries_totals_newest_first(sf, client):
    await _run(sf, 1, sha="old", results=[(BE + "a", "pass", 10)])
    await _run(sf, 2, sha="new", results=[(BE + "a", "fail", 10), (BE + "b", "skip", 1)])
    rows = (await client.get("/apps/tcms/api/runs")).json()
    assert [r["commit_sha"] for r in rows] == ["new", "old"]
    assert rows[0]["totals"] == {"pass": 0, "fail": 1, "skip": 1, "flaky": 0, "error": 0}
    assert rows[0]["seconds"] == 252.0 and rows[0]["verify_ok"] is True
    assert rows[0]["suites"] == [{"name": "backend", "exit": 0, "seconds": 252.0}]
    assert rows[0]["agent"] == "qa" and rows[0]["branch"] == "main"
    assert (await client.get("/apps/tcms/api/runs?limit=1")).json()[0]["id"] == rows[0]["id"]


async def test_run_detail_groups_by_file_with_failures_first(sf, client):
    rid = await _run(sf, 1, results=[
        (BE + "ok1", "pass", 10), (BE + "bad", "fail", 10),
        (WEB + "w1", "pass", 100), (TOOL + "t", "pass", 5),
        ("pytest:services/backend/tests/test_zzz.py::test_boom", "error", 3)])
    r = await client.get(f"/apps/tcms/api/runs/{rid}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == rid and body["totals"]["fail"] == 1
    groups = body["groups"]
    # Files with failures come first; inside a file, the failures come first.
    assert [g["file"] for g in groups][:2] == ["services/backend/tests/test_relay.py",
                                                "services/backend/tests/test_zzz.py"]
    first = groups[0]
    assert first["failures"] == 1
    assert [x["status"] for x in first["results"]] == ["fail", "pass"]
    assert first["results"][0]["message"] == "boom"
    assert first["results"][0]["name"] == "test_bad"
    assert (await client.get("/apps/tcms/api/runs/999")).status_code == 404
    assert (await client.get("/apps/tcms/api/runs/abc")).status_code == 422


async def test_cases_filters_and_unlinked(sf, client):
    await _case(sf, "relay.a", refs=[BE + "a"], area="relay")
    await _case(sf, "relay.b", refs=[BE + "b"], area="relay")
    await _case(sf, "wiki.m", layer="manual", refs=[], area="wiki")
    await _case(sf, "wiki.old", refs=[BE + "old"], area="wiki", status="retired")
    await _run(sf, 1, results=[(BE + "a", "pass", 10, "relay.a")])
    api = "/apps/tcms/api/cases"
    keys = lambda r: [c["key"] for c in r.json()["cases"]]
    assert keys(await client.get(api)) == ["relay.a", "relay.b", "wiki.m", "wiki.old"]
    assert (await client.get(api)).json()["total"] == 4
    assert keys(await client.get(api + "?layer=manual")) == ["wiki.m"]
    assert keys(await client.get(api + "?area=wiki")) == ["wiki.m", "wiki.old"]
    assert keys(await client.get(api + "?status=retired")) == ["wiki.old"]
    assert keys(await client.get(api + "?automation=manual")) == ["wiki.m"]
    assert keys(await client.get(api + "?q=RELAY.A")) == ["relay.a"]
    assert (await client.get(api + "?area=nope")).status_code == 422
    # unlinked: automated cases none of whose refs ever produced a result.
    assert keys(await client.get(api + "?unlinked=true")) == ["relay.b", "wiki.old"]
    assert keys(await client.get(api + "?unlinked=true&status=active")) == ["relay.b"]
    assert (await client.get(api + "?layer=bogus")).status_code == 422
    assert (await client.get(api + "?limit=1")).json()["total"] == 4
    assert len((await client.get(api + "?limit=1")).json()["cases"]) == 1
    row = (await client.get(api)).json()["cases"][0]
    assert row["automation"] == [BE + "a"] and row["last_status"] == "pass"


async def test_cases_search_takes_like_wildcards_literally(sf, client):
    """`%`, `_` and `\\` in `q` are characters, not patterns: a search for
    "100%" must not match every title, and `a_b` must not match `axb`."""
    await _insert(sf, "cases", key="perf.pct", suite="perf", area="perf", title="100% done",
                  layer="unit", priority="p2", preconditions=[], steps=[], expected="",
                  automation=[], tags=[], tickets=[], status="active", synced_at=T0,
                  source_sha="s")
    await _insert(sf, "cases", key="perf.axb", suite="perf", area="perf", title="axb",
                  layer="unit", priority="p2", preconditions=[], steps=[], expected="",
                  automation=[], tags=[], tickets=[], status="active", synced_at=T0,
                  source_sha="s")
    await _insert(sf, "cases", key="perf.a-b", suite="perf", area="perf", title="a_b",
                  layer="unit", priority="p2", preconditions=[], steps=[], expected="",
                  automation=[], tags=[], tickets=[], status="active", synced_at=T0,
                  source_sha="s")
    api = "/apps/tcms/api/cases"
    keys = lambda r: [c["key"] for c in r.json()["cases"]]
    assert keys(await client.get(api, params={"q": "100%"})) == ["perf.pct"]
    assert keys(await client.get(api, params={"q": "%"})) == ["perf.pct"]
    assert keys(await client.get(api, params={"q": "a_b"})) == ["perf.a-b"]
    assert keys(await client.get(api, params={"q": "\\"})) == []


async def test_case_detail_with_last_results_per_ref(sf, client):
    await _case(sf, "relay.a", refs=[BE + "a", BE + "never"])
    await _run(sf, 1, sha="s1", results=[(BE + "a", "fail", 10, "relay.a")])
    await _run(sf, 2, sha="s2", results=[(BE + "a", "pass", 12, "relay.a")])
    body = (await client.get("/apps/tcms/api/cases/relay.a")).json()
    assert body["key"] == "relay.a" and body["steps"] == ["do"]
    assert body["file"] == "tcms/cases/relay.yaml"
    refs = {r["ref"]: r for r in body["refs"]}
    assert refs[BE + "a"]["path"] == "services/backend/tests/test_relay.py"
    assert [x["status"] for x in refs[BE + "a"]["results"]] == ["pass", "fail"]
    assert refs[BE + "a"]["results"][0]["commit_sha"] == "s2"
    assert refs[BE + "never"]["results"] == []
    assert (await client.get("/apps/tcms/api/cases/nope.x")).status_code == 404
    assert (await client.get("/apps/tcms/api/cases/Bad Key")).status_code == 422


async def test_flaky_fail_then_pass_on_the_same_commit(sf, client):
    await _run(sf, 1, sha="same", results=[(BE + "flip", "fail", 10), (BE + "solid", "pass", 1)])
    await _run(sf, 2, sha="same", results=[(BE + "flip", "pass", 10), (BE + "solid", "pass", 1)])
    await _run(sf, 3, sha="other", results=[(BE + "pw", "flaky", 10), (BE + "solid", "pass", 1)])
    rows = (await client.get("/apps/tcms/api/flaky")).json()
    assert [r["ref"] for r in rows] == [BE + "flip", BE + "pw"]
    assert rows[0] == {"ref": BE + "flip", "fails": 1, "passes": 1, "flaky": 0,
                       "failing_commits": 1, "same_commit_flips": 1,
                       "path": "services/backend/tests/test_relay.py", "name": "test_flip"}
    assert rows[1]["flaky"] == 1
    # A narrower window drops the flip; Playwright's own flaky mark still counts.
    assert [r["ref"] for r in (await client.get("/apps/tcms/api/flaky?runs=1")).json()] == [BE + "pw"]


async def test_slowest_with_history(sf, client):
    await _run(sf, 1, results=[(BE + "slow", "pass", 3000), (BE + "fast", "pass", 10)])
    await _run(sf, 2, results=[(BE + "slow", "pass", 5000), (BE + "fast", "pass", 20)])
    rows = (await client.get("/apps/tcms/api/slowest?limit=1")).json()
    assert len(rows) == 1 and rows[0]["ref"] == BE + "slow"
    assert rows[0]["avg_ms"] == 4000 and rows[0]["max_ms"] == 5000 and rows[0]["n"] == 2
    assert rows[0]["layer"] == "integration"
    assert [h["duration_ms"] for h in rows[0]["history"]] == [3000, 5000]   # oldest first


async def test_prune_candidates_never_failed_slow_decile_and_retired(sf, client):
    await _case(sf, "wiki.old", refs=[BE + "old"], status="retired")
    # Ten refs: only the slowest sits in the top decile (PERCENT_RANK >= 0.9).
    refs = [(f"{BE}t{i}", "pass", 10 * (i + 1)) for i in range(8)]
    slow = (BE + "whale", "pass", 100000)
    for n in (1, 2):
        await _run(sf, n, results=refs + [slow, (BE + "old", "pass", 1, "wiki.old")])
    rows = (await client.get("/apps/tcms/api/prune-candidates")).json()
    assert [(r["ref"], r["reason"]) for r in rows] == [
        (BE + "old", "retired-case"), (BE + "whale", "never-failed-slow")]
    assert rows[1]["avg_ms"] == 100000 and rows[1]["n"] == 2 and rows[1]["fails"] == 0
    assert rows[0]["case_key"] == "wiki.old"


async def test_coverage_by_package_latest_with_totals_and_trend(sf, client):
    await _run(sf, 1, coverage=[("a", 10, 100)])
    await _run(sf, 2, coverage=[("a", 50, 100), ("b", 30, 30)])
    await _run(sf, 3)                                     # no coverage recorded
    body = (await client.get("/apps/tcms/api/coverage")).json()
    assert [p["package"] for p in body["packages"]] == ["a", "b"]
    assert body["packages"][0]["pct"] == 50.0
    assert body["lines_covered"] == 80 and body["lines_total"] == 130
    assert body["pct"] == 61.5
    assert [t["pct"] for t in body["trend"]] == [10.0, 61.5]
    assert body["trend"][1]["commit_sha"] == "a1b2c3d4e5"


# --- reconciler ---------------------------------------------------------------

class FakeProducer:
    def __init__(self):
        self.sent: list[tuple[str, dict, bytes | None]] = []

    async def send_and_wait(self, topic, value, key=None):
        self.sent.append((topic, json.loads(value), key))


class FakeApi:
    """The platform API as the app key sees it: `/api/relay/notify` names the
    room; a room the platform lacks is a 404."""

    def __init__(self, channels=("qa", "x")):
        self.posts: list[dict] = []
        self.channels = set(channels)
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(self._handle),
                                        base_url="http://api",
                                        headers={"Authorization": "Bearer ap_t"})

    def _handle(self, req: httpx.Request) -> httpx.Response:
        assert req.headers["authorization"] == "Bearer ap_t"
        if req.method == "POST" and req.url.path == "/api/relay/notify":
            body = json.loads(req.content)
            if body["channel"] not in self.channels:
                return httpx.Response(404, json={"detail": "unknown channel"})
            self.posts.append(body)
            return httpx.Response(201, json={"id": "m1", "kind": "event"})
        return httpx.Response(404)


def test_format_note():
    note = format_note({"id": 7, "run_id": "4f2e" + "a" * 28, "commit_sha": "a1b2c3d4e5f6",
                        "totals": {"pass": 912, "fail": 2, "skip": 3, "flaky": 1, "error": 0},
                        "seconds": 252.0, "unlinked": 4})
    assert note == ("🧪 test run 4f2eaaaa… on a1b2c3d · 912 pass · 2 fail · 3 skip · 1 flaky"
                    " · 4 unlinked · 4 m 12 s · [open](/apps/tcms/runs/7)")
    plain = format_note({"id": 8, "run_id": None, "commit_sha": "zz\nnot hex",
                         "totals": {"pass": 1, "fail": 0, "skip": 0, "flaky": 0, "error": 0},
                         "seconds": 9.4, "unlinked": 0})
    assert "\n" not in plain and "not hex" not in plain
    assert plain.startswith("🧪 test run on ")
    assert "· 1 pass · 9 s ·" in plain


async def test_reconciler_publishes_each_unpublished_run_once(sf):
    await _case(sf, "relay.a", refs=[BE + "a"])
    r1 = await _run(sf, 1, results=[(BE + "a", "pass", 10, "relay.a"), (BE + "b", "fail", 5)])
    r2 = await _run(sf, 2, results=[(BE + "c", "pass", 10)])
    done = await _run(sf, 3, published=True, results=[(BE + "c", "pass", 10)])
    producer, api = FakeProducer(), FakeApi()
    rec = Reconciler(sf, producer, api.client, channel="qa")
    assert await rec.tick() == 2
    assert [t for t, _, _ in producer.sent] == [TOPIC_RECORDED, TOPIC_RECORDED]
    env = producer.sent[0][1]
    assert env["type"] == "tcms.run.recorded" and env["schema_version"] == 1
    assert env["source"] == "app-tcms" and env["key"] == str(r1)
    assert env["data"] == {"test_run_id": r1, "commit_sha": "a1b2c3d4e5", "branch": "main",
                           "run_id": "run0001" + "0" * 24, "agent": "qa",
                           "totals": {"pass": 1, "fail": 1, "skip": 0, "flaky": 0, "error": 0},
                           "seconds": 252.0, "verify_ok": True, "unlinked": 1}
    assert producer.sent[1][1]["data"]["test_run_id"] == r2
    assert [p["channel"] for p in api.posts] == ["qa", "qa"]
    assert api.posts[0] == {"channel": "qa", "text": format_note({**env["data"], "id": r1})}
    async with sf() as s:
        rows = await query(s, "SELECT id, published_at FROM test_runs ORDER BY id", ())
    assert all(p is not None for _, p in rows)
    # Nothing goes twice: a second tick finds no work.
    assert await rec.tick() == 0
    assert len(producer.sent) == 2 and len(api.posts) == 2
    assert done not in [e["data"]["test_run_id"] for _, e, _ in producer.sent]


async def test_reconciler_stamps_even_when_the_room_post_fails(sf):
    """A refused or unreachable room must not republish the envelope every tick."""
    await _run(sf, 1, results=[(BE + "c", "pass", 10)])
    producer = FakeProducer()
    api = FakeApi(channels=["x"])                                    # no #qa
    rec = Reconciler(sf, producer, api.client, channel="qa")
    assert await rec.tick() == 1 and len(producer.sent) == 1 and api.posts == []
    assert await rec.tick() == 0 and len(producer.sent) == 1
    # No API client at all (key not provisioned yet): publish + stamp still.
    await _run(sf, 2, results=[(BE + "c", "pass", 10)])
    rec2 = Reconciler(sf, producer, None, channel="qa")
    assert await rec2.tick() == 1 and len(producer.sent) == 2


async def test_reconciler_does_not_stamp_when_the_publish_fails(sf):
    await _run(sf, 1, results=[(BE + "c", "pass", 10)])

    class Broken:
        async def send_and_wait(self, *a, **k):
            raise RuntimeError("kafka down")

    rec = Reconciler(sf, Broken(), None, channel="qa")
    assert await rec.tick() == 0
    async with sf() as s:
        rows = await query(s, "SELECT published_at FROM test_runs", ())
    assert rows == [(None,)]


async def test_retention_deletes_only_old_runs_and_their_rows(sf):
    old = await _run(sf, 1, days_ago=100, results=[(BE + "a", "pass", 1)],
                     coverage=[("a", 1, 2)], published=True)
    keep = await _run(sf, 2, days_ago=10, results=[(BE + "a", "pass", 1)],
                      coverage=[("a", 1, 2)], published=True)
    rec = Reconciler(sf, FakeProducer(), None, channel="qa", retention_days=90)
    assert await rec.prune(now=T0 + timedelta(hours=3)) == 1
    async with sf() as s:
        runs = await query(s, "SELECT id FROM test_runs", ())
        results = await query(s, "SELECT test_run_id FROM results", ())
        cov = await query(s, "SELECT test_run_id FROM coverage_snapshots", ())
    assert runs == [(keep,)] and results == [(keep,)] and cov == [(keep,)]
    assert old != keep
