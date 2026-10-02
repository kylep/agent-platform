"""The App lifecycle (design 39, "Apps as state", "The authority model",
"Tools" → `apps`): create, draft, notes, validate, preview, publish,
rollback, retire, authority and health, with idempotent build ops.

Every test runs on SQLite; with AP_TEST_PG_URL naming a scratch database they
run on Postgres too.
"""
import os
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from agentplatform.appdata import lifecycle as L
from agentplatform.appdata import quotas
from agentplatform.appdata.access import Caller, RecordError
from agentplatform.appdata.lifecycle import Actor
from agentplatform.appdata.models import (AppDataApp, AppDataBuildOp, AppDataDefinition,
                                          AppDataRecord)
from agentplatform.appdata.records import create_record, load_app, update_record
from agentplatform.db import Base, make_engine, make_session_factory, utcnow

PG_URL = os.environ.get("AP_TEST_PG_URL")
BACKENDS = ["sqlite"] + (["postgres"] if PG_URL else [])
APP_TABLES = [t for name, t in Base.metadata.tables.items() if name.startswith("app_data_")]

PAI = Actor("agent:pai")
BOB = Actor("agent:bob")
KYLE = Actor("kyle")


@pytest.fixture(params=BACKENDS)
async def engine(request, tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path}/appdata.db" if request.param == "sqlite" \
        else PG_URL
    e = make_engine(url)
    async with e.begin() as conn:
        await conn.run_sync(lambda sc: Base.metadata.create_all(sc, tables=APP_TABLES))
        if request.param == "postgres":
            await conn.exec_driver_sql("TRUNCATE " + ", ".join(t.name for t in APP_TABLES))
    yield e
    await e.dispose()


@pytest.fixture
def sf(engine):
    return make_session_factory(engine)


def rid() -> str:
    return uuid.uuid4().hex


def habits(**extra):
    body = {"collection": "habits",
            "fields": {"habit": {"type": "string", "required": True, "max": 40},
                       "day": {"type": "date", "required": True},
                       "done": {"type": "bool"}}}
    body.update(extra)
    return body


RECENT = {"view": "recent", "collection": "habits", "sort": [{"field": "day", "dir": "desc"}]}
DONE = {"view": "done_count", "collection": "habits",
        "filter": [{"field": "done", "op": "eq", "value": True}],
        "aggregates": [{"fn": "count", "as": "n"}]}
ENTRY = {"view": "entry", "collection": "habits", "params": {"id": {"type": "string"}},
         "filter": [{"field": "id", "op": "eq", "value": {"param": "id"}}]}
OVERVIEW = {"page": "overview", "title": "Habits", "blocks": [
    {"kind": "text", "style": "heading", "text": "This week"},
    {"kind": "metric", "label": "Days done", "view": "done_count"},
    {"kind": "table", "view": "recent", "title": "Recent",
     "columns": [{"field": "habit"}, {"field": "day", "format": "date"},
                 {"field": "done", "format": "relative_time"}],
     "row_link": {"page": "day", "param": "id"}}]}
DAY = {"page": "day", "title": "Day", "params": {"id": {"type": "string", "required": True}},
       "blocks": [{"kind": "detail", "view": "entry", "fields": ["habit", "day"],
                   "params": {"id": {"page_param": "id"}}},
                  {"kind": "text", "text": "Back", "link": {"page": "overview"}}]}


async def refused(code, awaitable):
    with pytest.raises(RecordError) as caught:
        await awaitable
    assert caught.value.code == code, caught.value
    return caught.value


async def new_app(sf, actor=PAI, name=None, **kw):
    async with sf() as s:
        out = await L.create(s, actor, request_id=rid(), name=name or f"a{rid()[:10]}", **kw)
    return out["app_id"]


async def draft(sf, app_id, kind, body, actor=PAI, **kw):
    async with sf() as s:
        return await L.draft(s, actor, app_id, request_id=rid(), kind=kind, definition=body,
                             **kw)


async def publish(sf, app_id, expected, actor=PAI, **kw):
    async with sf() as s:
        return await L.publish(s, actor, app_id, request_id=rid(),
                               expected_approved_version=expected, **kw)


async def built(sf, *defs, actor=PAI):
    """An App with these definitions published as version 1."""
    app_id = await new_app(sf, actor)
    for kind, body in defs:
        await draft(sf, app_id, kind, body, actor=actor)
    await publish(sf, app_id, None, actor=actor)
    return app_id


async def seed_approved(sf, app_id, version, *defs):
    """Write published rows straight into the tables: an approved state the
    lifecycle couldn't produce in Release 1a (sharing is a proposal, R1b)."""
    async with sf() as s:
        for kind, body in defs:
            s.add(AppDataDefinition(app_id=app_id, kind=kind, name=body[kind],
                                    version=version, body=body, state="published",
                                    author="kyle"))
        app = await s.get(AppDataApp, app_id)
        app.approved_version = version
        await s.commit()


async def add_record(sf, app_id, collection, values, who=Caller("agent:pai")):
    async with sf() as s:
        ctx = await load_app(s, app_id)
        return await create_record(s, ctx, who, collection, values)


async def detail(sf, app_id, actor=PAI):
    async with sf() as s:
        return await L.get_app(s, actor, app_id)


# --- create -----------------------------------------------------------------------------

async def test_create_owns_the_app_for_the_caller_with_the_narrowest_state(sf):
    async with sf() as s:
        out = await L.create(s, PAI, request_id="r1", name="habits", timezone="Europe/London",
                             description="Daily habits.")
    assert out["name"] == "habits" and out["owner"] == "agent:pai"
    assert out["approved_version"] is None
    got = await detail(sf, out["app_id"])
    assert got["owner"] == "pai" and got["status"] == "active"
    assert got["description"] == "Daily habits."
    assert got["approved"] == [] and got["drafts"] == [] and got["build_notes"] is None
    async with sf() as s:
        auth = await L.authority(s, PAI, out["app_id"])
        app = await s.get(AppDataApp, out["app_id"])
    assert auth["facts"] == [] and auth["approved_version"] is None
    assert app.timezone == "Europe/London" and app.authority_generation == 0


async def test_kyle_can_own_an_app(sf):
    app_id = await new_app(sf, KYLE, name="reading")
    assert (await detail(sf, app_id, KYLE))["owner"] == "kyle"


@pytest.mark.parametrize("name", ["Habits", "1st", "has space", "x" * 49, ""])
async def test_create_refuses_a_bad_name(sf, name):
    async with sf() as s:
        await refused("AL-NAME", L.create(s, PAI, request_id=rid(), name=name))


async def test_create_refuses_an_unknown_timezone(sf):
    async with sf() as s:
        await refused("AL-TIMEZONE", L.create(s, PAI, request_id=rid(), name="tz",
                                              timezone="Mars/Olympus"))


async def test_a_name_is_never_reused_even_after_retirement(sf):
    app_id = await new_app(sf, name="habits")
    async with sf() as s:
        await L.retire(s, PAI, app_id, request_id=rid())
    async with sf() as s:
        await refused("AL-NAME-TAKEN", L.create(s, BOB, request_id=rid(), name="habits"))


# --- build ops --------------------------------------------------------------------------

async def test_a_replayed_request_returns_the_stored_receipt(sf):
    async with sf() as s:
        first = await L.create(s, PAI, request_id="same", name="habits")
    async with sf() as s:
        again = await L.create(s, PAI, request_id="same", name="habits")
        apps = (await s.execute(select(func.count()).select_from(AppDataApp))).scalar_one()
    assert again["app_id"] == first["app_id"] and again["replayed"] is True
    assert apps == 1


async def test_a_request_id_reused_with_other_arguments_is_refused(sf):
    async with sf() as s:
        await L.create(s, PAI, request_id="same", name="habits")
    async with sf() as s:
        await refused("AL-REQUEST-REUSED", L.create(s, PAI, request_id="same", name="other"))
        assert (await s.execute(select(func.count()).select_from(AppDataApp))).scalar_one() == 1


async def test_request_ids_are_per_principal(sf):
    async with sf() as s:
        a = await L.create(s, PAI, request_id="same", name="one")
    async with sf() as s:
        b = await L.create(s, BOB, request_id="same", name="two")
    assert a["app_id"] != b["app_id"]


async def test_a_refusal_is_recorded_and_replays_as_the_same_refusal(sf):
    app_id = await new_app(sf)
    bad = {"collection": "habits", "fields": {}}
    async with sf() as s:
        first = await refused("AL-INVALID-DEFINITION", L.draft(
            s, PAI, app_id, request_id="d1", kind="collection", definition=bad))
    async with sf() as s:
        again = await refused("AL-INVALID-DEFINITION", L.draft(
            s, PAI, app_id, request_id="d1", kind="collection", definition=bad))
    assert again.status == first.status == 422
    ops = (await detail(sf, app_id))["build_ops"]
    assert [(op["action"], op["status"]) for op in ops][0] == ("draft", "refused")


async def test_a_write_needs_a_request_id(sf):
    async with sf() as s:
        await refused("AL-REQUEST-ID", L.create(s, PAI, request_id="", name="habits"))


async def test_get_lists_at_most_twenty_build_ops_newest_first(sf):
    app_id = await new_app(sf)
    for i in range(22):
        async with sf() as s:
            await L.write_notes(s, PAI, app_id, request_id=f"n{i}", text=f"v{i}",
                                expected_revision=i)
    ops = (await detail(sf, app_id))["build_ops"]
    assert len(ops) == 20
    assert ops[0]["request_id"] == "n21" and ops[0]["status"] == "succeeded"
    assert set(ops[0]) == {"request_id", "action", "status", "actor", "created_at", "summary"}
    assert ops[0]["actor"] == "pai"


# --- ownership --------------------------------------------------------------------------

async def test_only_the_owner_builds_an_app(sf):
    app_id = await new_app(sf)
    async with sf() as s:
        await refused("AL-NOT-OWNER", L.draft(s, BOB, app_id, request_id=rid(),
                                              kind="collection", definition=habits()))
        await refused("AL-NOT-OWNER", L.get_app(s, BOB, app_id))
        await refused("AL-NOT-OWNER", L.publish(s, BOB, app_id, request_id=rid(),
                                                expected_approved_version=None))
        await refused("AL-NOT-OWNER", L.retire(s, BOB, app_id, request_id=rid()))


async def test_an_unknown_app_is_404(sf):
    async with sf() as s:
        err = await refused("AL-NO-APP", L.get_app(s, PAI, "nope"))
    assert err.status == 404


async def test_list_shows_owned_and_readable_apps_and_kyle_sees_all(sf):
    mine = await new_app(sf, name="mine")
    theirs = await new_app(sf, BOB, name="theirs")
    shared = await new_app(sf, BOB, name="shared")
    await seed_approved(sf, shared, 1, ("collection", habits(
        access={"read": ["owner", "kyle", "agent:pai"]})))
    async with sf() as s:
        pai = {a["name"] for a in await L.list_apps(s, PAI)}
        kyle = {a["id"] for a in await L.list_apps(s, KYLE)}
    assert pai == {"mine", "shared"}
    assert kyle == {mine, theirs, shared}


async def test_deleting_the_owner_agent_transfers_its_apps_to_kyle(sf):
    app_id = await new_app(sf)
    other = await new_app(sf, BOB)
    async with sf() as s:
        moved = await L.transfer_owned_apps(s, "pai")
        await s.commit()
    assert moved == 1
    async with sf() as s:
        app = await s.get(AppDataApp, app_id)
        assert (app.owner_kind, app.owner_id) == ("kyle", "kyle")
        assert app.authority_generation == 1
        assert (await s.get(AppDataApp, other)).owner_id == "bob"
    # Kyle now builds it; the agent no longer can.
    await draft(sf, app_id, "collection", habits(), actor=KYLE)
    async with sf() as s:
        await refused("AL-NOT-OWNER", L.get_app(s, PAI, app_id))


# --- drafts -----------------------------------------------------------------------------

async def test_a_draft_saves_with_a_revision_and_base(sf):
    app_id = await new_app(sf)
    out = await draft(sf, app_id, "collection", habits())
    assert (out["kind"], out["name"], out["revision"], out["base_version"]) == (
        "collection", "habits", 1, None)
    out = await draft(sf, app_id, "collection", habits(description="v2"),
                      expected_revision=1)
    assert out["revision"] == 2
    [d] = (await detail(sf, app_id))["drafts"]
    assert d["definition"]["description"] == "v2" and d["revision"] == 2
    assert d["updated_by"] == "pai" and d["base_version"] is None


async def test_a_draft_edit_with_a_stale_revision_is_refused(sf):
    app_id = await new_app(sf)
    await draft(sf, app_id, "collection", habits())
    async with sf() as s:
        err = await refused("AL-STALE-REVISION", L.draft(
            s, PAI, app_id, request_id=rid(), kind="collection", definition=habits(),
            expected_revision=None))
    assert err.status == 409 and err.detail["revision"] == 1
    async with sf() as s:
        await refused("AL-STALE-REVISION", L.draft(
            s, PAI, app_id, request_id=rid(), kind="collection", definition=habits(),
            expected_revision=7))


async def test_a_draft_is_validated_alone(sf):
    app_id = await new_app(sf)
    async with sf() as s:
        err = await refused("AL-INVALID-DEFINITION", L.draft(
            s, PAI, app_id, request_id=rid(), kind="collection",
            definition={"collection": "habits", "fields": {"x": {"type": "blob"}}}))
    assert err.status == 422
    assert [e["code"] for e in err.detail["errors"]] == ["JD-FIELD-TYPE"]
    # A view naming a collection that doesn't exist yet is fine alone.
    await draft(sf, app_id, "view", RECENT)


async def test_a_draft_name_must_match_its_definition(sf):
    app_id = await new_app(sf)
    async with sf() as s:
        await refused("AL-NAME-MISMATCH", L.draft(s, PAI, app_id, request_id=rid(),
                                                  kind="collection", name="other",
                                                  definition=habits()))
        await refused("AL-KIND", L.draft(s, PAI, app_id, request_id=rid(), kind="widget",
                                         definition=habits()))


async def test_discarding_and_removing_drafts(sf):
    app_id = await built(sf, ("collection", habits()), ("view", RECENT))
    await draft(sf, app_id, "view", DONE)
    async with sf() as s:
        out = await L.draft(s, PAI, app_id, request_id=rid(), kind="view", name="done_count",
                            discard=True, expected_revision=1)
    assert out["discarded"] is True
    async with sf() as s:
        out = await L.draft(s, PAI, app_id, request_id=rid(), kind="view", name="recent",
                            remove=True)
    assert out["removed"] is True and out["base_version"] == 1
    async with sf() as s:
        # Removing a name the approved state doesn't hold has nothing to remove.
        await refused("AL-NOT-PUBLISHED", L.draft(s, PAI, app_id, request_id=rid(),
                                                  kind="page", name="ghost", remove=True))


# --- notes ------------------------------------------------------------------------------

async def test_notes_read_and_write_under_a_revision(sf):
    app_id = await new_app(sf)
    async with sf() as s:
        assert (await L.read_notes(s, PAI, app_id))["revision"] == 0
        out = await L.write_notes(s, PAI, app_id, request_id=rid(), text="Runbook.",
                                  expected_revision=0)
    assert out["revision"] == 1
    async with sf() as s:
        await refused("AL-STALE-REVISION", L.write_notes(
            s, PAI, app_id, request_id=rid(), text="lost", expected_revision=0))
    notes = (await detail(sf, app_id))["build_notes"]
    assert notes["text"] == "Runbook." and notes["revision"] == 1
    assert notes["updated_by"] == "pai" and notes["updated_at"]


async def test_notes_are_at_most_4kb(sf):
    app_id = await new_app(sf)
    async with sf() as s:
        await refused("AL-NOTES-TOO-LONG", L.write_notes(
            s, PAI, app_id, request_id=rid(), text="é" * 2049, expected_revision=0))
        await L.write_notes(s, PAI, app_id, request_id=rid(), text="x" * 4096,
                            expected_revision=0)


# --- validate ---------------------------------------------------------------------------

async def test_validate_checks_the_whole_app_and_names_each_definition(sf):
    app_id = await new_app(sf)
    await draft(sf, app_id, "view", {"view": "orphan", "collection": "missing"})
    async with sf() as s:
        report = await L.validate(s, PAI, app_id)
    assert report["ok"] is False and report["publishable"] is False
    [err] = report["errors"]
    assert err["code"] == "JD-VIEW-COLLECTION"
    assert err["definition"] == {"kind": "view", "name": "orphan"}


async def test_validate_reports_the_authority_delta_and_widening(sf):
    app_id = await new_app(sf)
    await draft(sf, app_id, "collection", habits(access={"read": ["owner", "kyle",
                                                                  "agent:bob"]}))
    async with sf() as s:
        report = await L.validate(s, PAI, app_id)
    assert report["ok"] is True and report["publishable"] is False
    assert any("agent:bob can read habits" in line for line in report["widening"])
    assert report["suggest"] == "propose"
    assert any("agent:bob can read habits" in line for line in report["delta"]["added"])


async def test_validate_a_private_app_is_publishable(sf):
    app_id = await new_app(sf)
    await draft(sf, app_id, "collection", habits())
    await draft(sf, app_id, "view", RECENT)
    async with sf() as s:
        report = await L.validate(s, PAI, app_id, expected_approved_version=None)
    assert report["publishable"] is True and report["widening"] == []
    assert report["stale_base"] == [] and report["app_moved"] is False


async def test_validate_reports_stale_base_when_the_definition_moved(sf):
    app_id = await built(sf, ("collection", habits()), ("view", RECENT))
    # Drafted against version 1 ...
    await draft(sf, app_id, "view", {**RECENT, "limit": 10})
    # ... while another draft of the same view publishes as version 2.
    async with sf() as s:
        row = (await s.execute(select(AppDataDefinition).where(
            AppDataDefinition.app_id == app_id, AppDataDefinition.state == "draft"))).scalar_one()
        row.base_version = 0
        s.add(AppDataDefinition(app_id=app_id, kind="view", name="recent", version=2,
                                body={**RECENT, "limit": 20}, state="published",
                                author="agent:pai"))
        (await s.get(AppDataApp, app_id)).approved_version = 2
        await s.commit()
    async with sf() as s:
        report = await L.validate(s, PAI, app_id, expected_approved_version=1)
    assert report["app_moved"] is True
    assert report["stale_base"] == [{"kind": "view", "name": "recent", "base_version": 0,
                                     "published_version": 2}]
    assert report["publishable"] is False


async def test_validate_checks_new_rules_against_existing_records(sf):
    app_id = await built(sf, ("collection", habits()))
    for _ in range(2):
        await add_record(sf, app_id, "habits", {"habit": "run", "day": "2026-09-30"})
    await draft(sf, app_id, "collection",
                habits(rules=[{"kind": "unique", "fields": ["habit", "day"]}]),
                expected_revision=None)
    async with sf() as s:
        report = await L.validate(s, PAI, app_id)
    [issue] = report["record_issues"]
    assert issue["code"] == "AL-RECORDS-UNIQUE" and issue["count"] == 2
    assert issue["collection"] == "habits" and len(issue["record_ids"]) == 2
    assert report["publishable"] is False


async def test_validate_checks_changed_fields_against_existing_records(sf):
    app_id = await built(sf, ("collection", habits()))
    await add_record(sf, app_id, "habits", {"habit": "a long habit name", "day": "2026-09-30"})
    body = habits()
    body["fields"]["habit"]["max"] = 5
    body["fields"]["mood"] = {"type": "enum", "values": ["ok"], "required": True}
    await draft(sf, app_id, "collection", body)
    async with sf() as s:
        report = await L.validate(s, PAI, app_id)
    codes = sorted((i["code"], i.get("field")) for i in report["record_issues"])
    assert codes == [("AL-RECORDS-INVALID", "habit"), ("AL-RECORDS-REQUIRED", "mood")]


async def test_removing_stored_data_is_data_dropping(sf):
    app_id = await built(sf, ("collection", habits()), ("collection", {
        "collection": "spare", "fields": {"x": {"type": "string"}}}))
    await add_record(sf, app_id, "habits", {"habit": "run", "day": "2026-09-30",
                                             "done": True})
    body = habits()
    del body["fields"]["done"]
    await draft(sf, app_id, "collection", body)
    async with sf() as s:
        await L.draft(s, PAI, app_id, request_id=rid(), kind="collection", name="spare",
                      remove=True)
        report = await L.validate(s, PAI, app_id)
    # The empty collection drops nothing; the field holding a value does.
    assert report["data_dropping"] == ["habits.done holds data in 1 record"]
    assert report["publishable"] is False and report["suggest"] == "propose"


async def test_validate_can_be_limited_to_some_drafts(sf):
    app_id = await new_app(sf)
    await draft(sf, app_id, "collection", habits())
    await draft(sf, app_id, "view", {"view": "orphan", "collection": "missing"})
    async with sf() as s:
        report = await L.validate(s, PAI, app_id,
                                  only=[{"kind": "collection", "name": "habits"}])
    assert report["ok"] is True


# --- preview ----------------------------------------------------------------------------

async def test_preview_runs_a_draft_view_over_samples_that_never_persist(sf):
    app_id = await new_app(sf)
    await draft(sf, app_id, "collection", habits())
    await draft(sf, app_id, "view", RECENT)
    await draft(sf, app_id, "view", DONE)
    samples = {"habits": [{"habit": "run", "day": "2026-09-30", "done": True},
                          {"habit": "read", "day": "2026-09-29", "done": False}]}
    async with sf() as s:
        rows = await L.preview(s, PAI, app_id, kind="view", name="recent", samples=samples)
        count = await L.preview(s, PAI, app_id, kind="view", name="done_count",
                                samples=samples)
    assert [r["values"]["habit"] for r in rows["rows"]] == ["run", "read"]
    assert count["count"] == 1
    async with sf() as s:
        assert (await s.execute(select(func.count()).select_from(AppDataRecord))
                ).scalar_one() == 0


async def test_preview_refuses_samples_for_a_published_collection(sf):
    app_id = await built(sf, ("collection", habits()), ("view", RECENT))
    async with sf() as s:
        await refused("AL-SAMPLES-PUBLISHED", L.preview(
            s, PAI, app_id, kind="view", name="recent",
            samples={"habits": [{"habit": "run", "day": "2026-09-30"}]}))


async def test_preview_refuses_bad_samples(sf):
    app_id = await new_app(sf)
    await draft(sf, app_id, "collection", habits())
    await draft(sf, app_id, "view", RECENT)
    async with sf() as s:
        await refused("AD-REQUIRED", L.preview(s, PAI, app_id, kind="view", name="recent",
                                               samples={"habits": [{"habit": "run"}]}))


async def test_preview_reads_real_data_of_a_published_collection(sf):
    app_id = await built(sf, ("collection", habits()))
    await add_record(sf, app_id, "habits", {"habit": "run", "day": "2026-09-30"})
    await draft(sf, app_id, "view", RECENT)
    async with sf() as s:
        out = await L.preview(s, PAI, app_id, kind="view", name="recent")
    assert [r["values"]["habit"] for r in out["rows"]] == ["run"]


async def test_preview_as_another_principal_never_shows_more_than_the_caller(sf):
    body = habits()
    body["fields"]["secret"] = {"type": "string", "access": {"read": ["kyle"]}}
    body["fields"]["mine"] = {"type": "string", "access": {"read": ["owner"]}}
    app_id = await new_app(sf)
    await draft(sf, app_id, "collection", body)
    await draft(sf, app_id, "view", RECENT)
    samples = {"habits": [{"habit": "run", "day": "2026-09-30", "secret": "s",
                           "mine": "m"}]}
    async with sf() as s:
        as_kyle = await L.preview(s, PAI, app_id, kind="view", name="recent",
                                  samples=samples, as_="kyle")
        as_me = await L.preview(s, PAI, app_id, kind="view", name="recent",
                                samples=samples)
    [row] = as_kyle["rows"]
    # Kyle can read `secret` but the builder can't, so the preview withholds it;
    # Kyle can't read `mine`, so neither does his preview.
    assert row["values"]["secret"] is None and "secret" in row["restricted"]
    assert row["values"]["mine"] is None and "mine" in row["restricted"]
    assert row["values"]["habit"] == "run"
    assert as_me["rows"][0]["values"]["mine"] == "m"


async def test_preview_as_a_principal_that_cant_read_is_refused(sf):
    app_id = await new_app(sf)
    await draft(sf, app_id, "collection", habits())
    await draft(sf, app_id, "view", RECENT)
    async with sf() as s:
        await refused("AD-FORBIDDEN", L.preview(s, PAI, app_id, kind="view", name="recent",
                                                as_="agent:bob"))
        await refused("AL-PRINCIPAL", L.preview(s, PAI, app_id, kind="view",
                                                name="recent", as_="root"))


async def test_preview_renders_a_draft_page_in_the_web_shape(sf):
    app_id = await new_app(sf)
    for kind, body in (("collection", habits()), ("view", RECENT), ("view", DONE),
                       ("view", ENTRY), ("page", OVERVIEW), ("page", DAY)):
        await draft(sf, app_id, kind, body)
    samples = {"habits": [{"habit": "run", "day": "2026-09-30", "done": True}]}
    async with sf() as s:
        out = await L.preview(s, PAI, app_id, kind="page", name="overview", samples=samples)
        day = await L.preview(s, PAI, app_id, kind="page", name="day", samples=samples,
                              params={"id": "nope"})
    page = out["definition"]
    assert page["renderer"] == "typed/v2" and page["title"] == "Habits"
    assert [c["kind"] for c in page["components"]] == ["text", "metric", "table"]
    table = page["components"][2]
    assert table["row_link"] == {"page": "day", "params": {"id": "id"}}
    assert table["columns"][2] == {"field": "done", "format": "datetime"}
    assert [b.get("result", {}).get("count") for b in out["blocks"]][1] == 1
    assert out["blocks"][2]["result"]["rows"][0]["values"]["habit"] == "run"
    assert out["blocks"][0] == {"index": 0, "kind": "text"}
    assert day["definition"]["components"][0]["params"] == {"id": {"query": "id"}}
    assert day["blocks"][0]["result"]["rows"] == []


# --- publish ----------------------------------------------------------------------------

async def test_publish_stores_the_bundle_and_bumps_the_versions(sf):
    app_id = await new_app(sf)
    await draft(sf, app_id, "collection", habits())
    await draft(sf, app_id, "view", RECENT)
    out = await publish(sf, app_id, None)
    assert out["approved_version"] == 1 and out["authority_generation"] == 1
    assert sorted((p["kind"], p["name"], p["version"]) for p in out["published"]) == [
        ("collection", "habits", 1), ("view", "recent", 1)]
    got = await detail(sf, app_id)
    assert got["drafts"] == [] and got["approved_version"] == 1
    assert {(a["kind"], a["name"]) for a in got["approved"]} == {
        ("collection", "habits"), ("view", "recent")}
    assert got["approved"][0]["published_by"] == "pai"
    async with sf() as s:
        ctx = await load_app(s, app_id)
    assert set(ctx.bundle.views) == {"recent"}
    # Only what changed gets a new version.
    await draft(sf, app_id, "view", DONE)
    out = await publish(sf, app_id, 1)
    assert [(p["name"], p["version"]) for p in out["published"]] == [("done_count", 2)]
    async with sf() as s:
        ctx = await load_app(s, app_id)
    assert ctx.versions == {"habits": 1} and set(ctx.bundle.views) == {"recent", "done_count"}


async def test_publish_is_a_compare_and_swap_on_the_approved_version(sf):
    app_id = await built(sf, ("collection", habits()))
    await draft(sf, app_id, "view", RECENT)
    async with sf() as s:
        err = await refused("AL-STALE-BASE", L.publish(s, PAI, app_id, request_id=rid(),
                                                       expected_approved_version=None))
    assert err.status == 409 and err.detail["approved_version"] == 1
    assert (await detail(sf, app_id))["approved_version"] == 1


async def test_publish_refuses_a_widening_and_suggests_propose(sf):
    app_id = await new_app(sf)
    await draft(sf, app_id, "collection", habits(access={"read": ["owner", "kyle",
                                                                  "agent:bob"]}))
    async with sf() as s:
        err = await refused("AL-NEEDS-PROPOSAL", L.publish(
            s, PAI, app_id, request_id=rid(), expected_approved_version=None))
    assert err.status == 409 and err.detail["suggest"] == "propose"
    assert any("agent:bob can read habits" in w for w in err.detail["widening"])
    got = await detail(sf, app_id)
    assert got["approved_version"] is None and len(got["drafts"]) == 1


async def test_action_templates_are_always_a_proposal(sf):
    page = {"page": "log", "title": "Log", "blocks": [
        {"kind": "table", "view": "recent", "columns": [{"field": "habit"}],
         "actions": ["add"]}],
        "actions": [{"name": "add", "kind": "create", "collection": "habits",
                     "editable_fields": ["habit", "day"]}]}
    app_id = await built(sf, ("collection", habits()), ("view", RECENT))
    await draft(sf, app_id, "page", page)
    async with sf() as s:
        err = await refused("AL-NEEDS-PROPOSAL", L.publish(
            s, PAI, app_id, request_id=rid(), expected_approved_version=1))
    assert any("page action log.add" in w for w in err.detail["widening"])


async def test_publish_refuses_an_invalid_app(sf):
    app_id = await new_app(sf)
    await draft(sf, app_id, "view", {"view": "orphan", "collection": "missing"})
    async with sf() as s:
        err = await refused("AL-INVALID", L.publish(s, PAI, app_id, request_id=rid(),
                                                    expected_approved_version=None))
    assert err.status == 422 and err.detail["errors"][0]["code"] == "JD-VIEW-COLLECTION"


async def test_publish_refuses_records_the_new_definitions_would_break(sf):
    app_id = await built(sf, ("collection", habits()))
    for _ in range(2):
        await add_record(sf, app_id, "habits", {"habit": "run", "day": "2026-09-30"})
    await draft(sf, app_id, "collection",
                habits(rules=[{"kind": "unique", "fields": ["habit", "day"]}]))
    async with sf() as s:
        err = await refused("AL-INCONSISTENT", L.publish(
            s, PAI, app_id, request_id=rid(), expected_approved_version=1))
    assert err.detail["record_issues"][0]["code"] == "AL-RECORDS-UNIQUE"


async def test_publish_refuses_a_stale_draft(sf):
    app_id = await built(sf, ("collection", habits()), ("view", RECENT))
    await draft(sf, app_id, "view", {**RECENT, "limit": 10})
    async with sf() as s:
        row = (await s.execute(select(AppDataDefinition).where(
            AppDataDefinition.state == "draft"))).scalar_one()
        row.base_version = 0
        await s.commit()
    async with sf() as s:
        err = await refused("AL-STALE-BASE", L.publish(s, PAI, app_id, request_id=rid(),
                                                       expected_approved_version=1))
    assert err.detail["stale_base"][0]["name"] == "recent"


async def test_redrafting_rebases_a_stale_draft(sf):
    app_id = await built(sf, ("collection", habits()), ("view", RECENT))
    await draft(sf, app_id, "view", {**RECENT, "limit": 10})
    async with sf() as s:
        row = (await s.execute(select(AppDataDefinition).where(
            AppDataDefinition.state == "draft"))).scalar_one()
        row.base_version = 0
        await s.commit()
    # Saving it again says "written against the current approved state".
    out = await draft(sf, app_id, "view", {**RECENT, "limit": 20}, expected_revision=1)
    assert out["base_version"] == 1
    await publish(sf, app_id, 1, only=[{"kind": "view", "name": "recent"}])


async def test_publish_with_nothing_to_publish_is_refused(sf):
    app_id = await built(sf, ("collection", habits()))
    async with sf() as s:
        await refused("AL-NOTHING-TO-PUBLISH", L.publish(
            s, PAI, app_id, request_id=rid(), expected_approved_version=1))
    # A draft identical to the approved definition changes nothing either.
    await draft(sf, app_id, "collection", habits())
    async with sf() as s:
        await refused("AL-NOTHING-TO-PUBLISH", L.publish(
            s, PAI, app_id, request_id=rid(), expected_approved_version=1))


async def test_publish_settles_new_fields_private(sf):
    app_id = await new_app(sf)
    shared = habits(access={"read": ["owner", "kyle", "agent:bob"]})
    await seed_approved(sf, app_id, 1, ("collection", shared))
    body = habits(access={"read": ["owner", "kyle", "agent:bob"]})
    body["fields"]["mood"] = {"type": "string"}
    await draft(sf, app_id, "collection", body)
    await publish(sf, app_id, 1)
    got = await detail(sf, app_id)
    [c] = [a for a in got["approved"] if a["name"] == "habits"]
    assert c["version"] == 2
    assert c["definition"]["fields"]["mood"]["access"] == {
        "read": ["owner", "kyle"], "create": ["owner"], "update": ["owner"]}
    async with sf() as s:
        ctx = await load_app(s, app_id)
        acc = ctx.access(ctx.bundle.collections["habits"], Caller("agent:bob"))
    assert acc.can_read("habit") and not acc.can_read("mood")


async def test_publishing_a_removal_drops_the_definition(sf):
    app_id = await built(sf, ("collection", habits()), ("view", RECENT), ("view", DONE))
    async with sf() as s:
        await L.draft(s, PAI, app_id, request_id=rid(), kind="view", name="done_count",
                      remove=True)
    out = await publish(sf, app_id, 1)
    assert out["published"] == [{"kind": "view", "name": "done_count", "version": 2,
                                 "removed": True}]
    async with sf() as s:
        ctx = await load_app(s, app_id)
    assert set(ctx.bundle.views) == {"recent"}
    assert {a["name"] for a in (await detail(sf, app_id))["approved"]} == {
        "habits", "recent"}


async def test_publish_reindexes_side_columns_when_indexed_fields_change(sf):
    app_id = await built(sf, ("collection", habits()))
    await add_record(sf, app_id, "habits", {"habit": "run", "day": "2026-09-30"})
    await draft(sf, app_id, "collection", habits(indexed=["habit", "day"]))
    await publish(sf, app_id, 1)
    async with sf() as s:
        record = (await s.execute(select(AppDataRecord))).scalar_one()
    assert record.ix_text1 == "run" and record.ix_time1 is not None


async def test_a_reindex_flushes_a_chunk_at_a_time(sf, monkeypatch):
    """A million-record reindex mustn't hold every record dirty in the
    session until one flush at the end."""
    from sqlalchemy import event
    from sqlalchemy.orm import Session
    monkeypatch.setattr(L, "SCAN_CHUNK", 3)
    app_id = await built(sf, ("collection", habits()))
    for day in range(1, 9):
        await add_record(sf, app_id, "habits", {"habit": "run", "day": f"2026-09-0{day}"})
    await draft(sf, app_id, "collection", habits(indexed=["habit", "day"]))
    dirty = []

    def note(session, context, instances):
        dirty.append(sum(isinstance(o, AppDataRecord) for o in session.dirty))
    event.listen(Session, "before_flush", note)
    try:
        await publish(sf, app_id, 1)
    finally:
        event.remove(Session, "before_flush", note)
    assert max(dirty) <= 3 and sum(dirty) == 8
    async with sf() as s:
        records = (await s.execute(select(AppDataRecord))).scalars().all()
    assert len(records) == 8 and all(r.ix_text1 == "run" and r.ix_time1 for r in records)


async def test_publish_only_some_drafts(sf):
    app_id = await new_app(sf)
    await draft(sf, app_id, "collection", habits())
    await draft(sf, app_id, "view", {"view": "orphan", "collection": "missing"})
    await publish(sf, app_id, None, only=[{"kind": "collection", "name": "habits"}])
    got = await detail(sf, app_id)
    assert [d["name"] for d in got["drafts"]] == ["orphan"]


async def test_a_publish_replay_doesnt_publish_twice(sf):
    app_id = await new_app(sf)
    await draft(sf, app_id, "collection", habits())
    async with sf() as s:
        first = await L.publish(s, PAI, app_id, request_id="p1", expected_approved_version=None)
    async with sf() as s:
        again = await L.publish(s, PAI, app_id, request_id="p1", expected_approved_version=None)
    assert again["approved_version"] == first["approved_version"] == 1
    assert (await detail(sf, app_id))["approved_version"] == 1


# --- rollback ---------------------------------------------------------------------------

async def test_rollback_restores_an_earlier_version_as_a_new_one(sf):
    app_id = await built(sf, ("collection", habits()), ("view", RECENT))
    await draft(sf, app_id, "view", DONE)
    await draft(sf, app_id, "view", {**RECENT, "limit": 5}, expected_revision=None)
    await publish(sf, app_id, 1)
    async with sf() as s:
        out = await L.rollback(s, PAI, app_id, request_id=rid(), to_version=1,
                               expected_approved_version=2)
    assert out["approved_version"] == 3 and out["rolled_back_to"] == 1
    async with sf() as s:
        ctx = await load_app(s, app_id)
    assert set(ctx.bundle.views) == {"recent"} and ctx.bundle.views["recent"].limit == 50


async def test_rollback_refuses_bad_targets_and_stale_bases(sf):
    app_id = await built(sf, ("collection", habits()))
    async with sf() as s:
        await refused("AL-ROLLBACK-TARGET", L.rollback(s, PAI, app_id, request_id=rid(),
                                                       to_version=1,
                                                       expected_approved_version=1))
        await refused("AL-ROLLBACK-TARGET", L.rollback(s, PAI, app_id, request_id=rid(),
                                                       to_version=0,
                                                       expected_approved_version=1))
        await refused("AL-STALE-BASE", L.rollback(s, PAI, app_id, request_id=rid(),
                                                  to_version=0,
                                                  expected_approved_version=5))


async def test_a_wider_rollback_is_a_proposal(sf):
    app_id = await new_app(sf)
    await seed_approved(sf, app_id, 1, ("collection", habits(
        access={"read": ["owner", "kyle", "agent:bob"]})))
    await draft(sf, app_id, "collection", habits())
    await publish(sf, app_id, 1)
    async with sf() as s:
        err = await refused("AL-NEEDS-PROPOSAL", L.rollback(
            s, PAI, app_id, request_id=rid(), to_version=1, expected_approved_version=2))
    assert any("agent:bob" in w for w in err.detail["widening"])


async def test_a_rollback_that_drops_data_is_refused(sf):
    app_id = await built(sf, ("collection", habits()))
    body = habits()
    body["fields"]["mood"] = {"type": "string"}
    await draft(sf, app_id, "collection", body)
    await publish(sf, app_id, 1)
    await add_record(sf, app_id, "habits", {"habit": "run", "day": "2026-09-30",
                                             "mood": "ok"})
    async with sf() as s:
        err = await refused("AL-NEEDS-PROPOSAL", L.rollback(
            s, PAI, app_id, request_id=rid(), to_version=1, expected_approved_version=2))
    assert err.detail["data_dropping"] == ["habits.mood holds data in 1 record"]


# --- retire -----------------------------------------------------------------------------

async def test_retire_hides_the_app_and_stops_writes(sf):
    app_id = await built(sf, ("collection", habits()))
    async with sf() as s:
        out = await L.retire(s, PAI, app_id, request_id=rid(), reason="done with it")
    assert out["status"] == "retired"
    got = await detail(sf, app_id)
    assert got["status"] == "retired"
    async with sf() as s:
        await refused("AL-APP-RETIRED", L.draft(s, PAI, app_id, request_id=rid(),
                                                kind="view", definition=RECENT))
        await refused("AL-APP-RETIRED", L.retire(s, PAI, app_id, request_id=rid()))
    with pytest.raises(RecordError) as caught:
        await add_record(sf, app_id, "habits", {"habit": "run", "day": "2026-09-30"})
    assert caught.value.code == "AD-APP-RETIRED"


# --- authority and health ---------------------------------------------------------------

async def test_authority_describes_the_approved_facts(sf):
    app_id = await built(sf, ("collection", habits()))
    async with sf() as s:
        out = await L.authority(s, PAI, app_id)
    assert out["approved_version"] == 1 and out["authority_generation"] == 1
    assert "owner can read habits: day, done, habit" in out["facts"][0] or any(
        line.startswith("owner can read habits:") for line in out["facts"])
    assert len(out["digest"]) == 64


async def test_health_is_computed_on_read(sf):
    app_id = await built(sf, ("collection", habits(
        rules=[{"kind": "unique", "fields": ["habit", "day"]}])))
    await add_record(sf, app_id, "habits", {"habit": "run", "day": "2026-09-30"})
    async with sf() as s:
        health = await L.health(s, PAI, app_id)
    assert health["status"] == "ok" and health["issues"] == 0
    assert health["quota"]["records"] == 1 and health["quota"]["bytes"] > 0
    assert health["quota"]["records_limit"] > 0 and health["checked_at"]
    # A duplicate the rule should have stopped (written around the engine).
    async with sf() as s:
        first = (await s.execute(select(AppDataRecord))).scalar_one()
        s.add(AppDataRecord(app_id=app_id, collection="habits", id="dup", author="kyle",
                            collection_version=1, doc=dict(first.doc)))
        await s.commit()
    async with sf() as s:
        health = await L.health(s, PAI, app_id)
    assert health["status"] == "failing" and health["issues"] == 1
    [v] = health["rule_violations"]
    assert v["collection"] == "habits" and v["rule"] == "unique(habit, day)"
    assert v["count"] == 2 and "dup" in v["record_ids"]


async def test_lists_and_details_reuse_the_record_checks_and_health_refreshes_them(
        sf, monkeypatch):
    """The record checks scan whole collections (seconds on a million
    records), so `apps list` and `get` reuse a recent result; the `health`
    action always checks afresh, and a publish starts over."""
    app_id = await built(sf, ("collection", habits(
        rules=[{"kind": "unique", "fields": ["habit", "day"]}])))
    await add_record(sf, app_id, "habits", {"habit": "run", "day": "2026-09-30"})
    calls = []
    real = L._duplicates

    async def counting(*args, **kwargs):
        calls.append(args)
        return await real(*args, **kwargs)
    monkeypatch.setattr(L, "_duplicates", counting)
    await detail(sf, app_id)
    async with sf() as s:
        await L.list_apps(s, PAI)
    await detail(sf, app_id)
    assert len(calls) == 1
    # A duplicate written around the engine: only a fresh check sees it...
    async with sf() as s:
        first = (await s.execute(select(AppDataRecord))).scalar_one()
        s.add(AppDataRecord(app_id=app_id, collection="habits", id="dup", author="kyle",
                            collection_version=1, doc=dict(first.doc)))
        await s.commit()
    assert (await detail(sf, app_id))["health"]["status"] == "ok"
    async with sf() as s:
        assert (await L.health(s, PAI, app_id))["status"] == "failing"
    # ...and then the list and the detail show it too.
    assert (await detail(sf, app_id))["health"]["status"] == "failing"
    assert len(calls) == 2
    # A new approved version is checked at once.
    await draft(sf, app_id, "view", RECENT)
    await publish(sf, app_id, 1)
    await detail(sf, app_id)
    assert len(calls) == 3


async def test_record_checks_are_reused_for_a_limited_time(sf, monkeypatch):
    app_id = await built(sf, ("collection", habits(
        rules=[{"kind": "unique", "fields": ["habit", "day"]}])))
    calls = []
    real = L._duplicates

    async def counting(*args, **kwargs):
        calls.append(args)
        return await real(*args, **kwargs)
    monkeypatch.setattr(L, "_duplicates", counting)
    await detail(sf, app_id)
    later = utcnow() + L.HEALTH_TTL + timedelta(seconds=1)
    monkeypatch.setattr(L, "utcnow", lambda: later)
    await detail(sf, app_id)
    assert len(calls) == 2


async def test_health_quota_is_the_enforced_quota(sf):
    """What quotas enforce, not a recount of every record on each read."""
    app_id = await built(sf, ("collection", habits()))
    await add_record(sf, app_id, "habits", {"habit": "run", "day": "2026-09-30"})
    async with sf() as s:
        health = await L.health(s, PAI, app_id)
        used = (await quotas.describe(s, "app", app_id))
    assert health["quota"] == {"records": 1, "records_limit": used["limits"]["max_records"],
                               "bytes": used["used"]["bytes"],
                               "bytes_limit": used["limits"]["max_bytes"]}


async def test_health_reports_a_binding_that_no_longer_validates(sf):
    app_id = await built(sf, ("collection", habits()), ("view", RECENT))
    async with sf() as s:
        s.add(AppDataDefinition(app_id=app_id, kind="page", name="broken", version=1,
                                body={"page": "broken", "title": "B", "blocks": [
                                    {"kind": "table", "view": "recent",
                                     "columns": [{"field": "mood"}]}]},
                                state="published", author="agent:pai"))
        await s.commit()
    got = await detail(sf, app_id)
    [b] = got["health"]["invalid_bindings"]
    assert (b["kind"], b["name"], b["code"]) == ("page", "broken", "JD-PAGE-COLUMN")
    assert got["health"]["status"] == "failing"


# --- the detail shape -------------------------------------------------------------------

async def test_get_matches_the_web_contract(sf):
    app_id = await built(sf, ("collection", habits()), ("view", RECENT))
    await draft(sf, app_id, "view", DONE)
    got = await detail(sf, app_id)
    assert set(got) == {"id", "name", "owner", "status", "description", "approved_version",
                        "updated_at", "approved", "drafts", "build_notes", "health",
                        "build_ops"}
    assert set(got["approved"][0]) == {"kind", "name", "version", "published_at",
                                       "published_by", "definition"}
    assert set(got["drafts"][0]) == {"kind", "name", "revision", "base_version",
                                     "updated_at", "updated_by", "definition"}
    assert got["drafts"][0]["base_version"] == 1
    assert set(got["health"]) == {"status", "issues", "checked_at", "invalid_bindings",
                                  "rule_violations", "quota"}
    assert set(got["health"]["quota"]) == {"records", "records_limit", "bytes", "bytes_limit"}


# --- record writes with request ids -----------------------------------------------------

async def test_record_writes_are_idempotent_by_request_id(sf):
    app_id = await built(sf, ("collection", habits()))
    values = {"habit": "run", "day": "2026-09-30"}
    async with sf() as s:
        first = await L.record_create(s, PAI, app_id, request_id="w1", collection="habits",
                                      values=values)
    async with sf() as s:
        again = await L.record_create(s, PAI, app_id, request_id="w1", collection="habits",
                                      values=values)
        await refused("AL-REQUEST-REUSED", L.record_create(
            s, PAI, app_id, request_id="w1", collection="habits",
            values={**values, "done": True}))
        n = (await s.execute(select(func.count()).select_from(AppDataRecord))).scalar_one()
    assert again["id"] == first["id"] and n == 1
    assert set(first) == {"collection", "id", "version"}
    async with sf() as s:
        up = await L.record_update(s, PAI, app_id, request_id="w2", collection="habits",
                                   record_id=first["id"], values={"done": True},
                                   expected_version=1)
    assert up["version"] == 2
    async with sf() as s:
        out = await L.record_delete(s, PAI, app_id, request_id="w3", collection="habits",
                                    record_id=first["id"], expected_version=2)
    assert out["deleted"]
    async with sf() as s:
        n = (await s.execute(select(func.count()).select_from(AppDataRecord))).scalar_one()
        ops = (await s.execute(select(func.count()).select_from(AppDataBuildOp)
                               .where(AppDataBuildOp.op.like("record_%")))).scalar_one()
    assert n == 0 and ops == 3
    # Record writes aren't builder history.
    assert all(not op["action"].startswith("record_")
               for op in (await detail(sf, app_id))["build_ops"])


async def test_a_refused_record_write_is_not_applied(sf):
    app_id = await built(sf, ("collection", habits()))
    async with sf() as s:
        await refused("AD-FORBIDDEN", L.record_create(
            s, BOB, app_id, request_id="w1", collection="habits",
            values={"habit": "run", "day": "2026-09-30"}))
        n = (await s.execute(select(func.count()).select_from(AppDataRecord))).scalar_one()
    assert n == 0


async def test_update_record_still_works_through_the_engine(sf):
    app_id = await built(sf, ("collection", habits()))
    row = await add_record(sf, app_id, "habits", {"habit": "run", "day": "2026-09-30"})
    async with sf() as s:
        ctx = await load_app(s, app_id)
        out = await update_record(s, ctx, Caller("agent:pai"), "habits", row["id"],
                                  {"done": True}, expected_version=1)
    assert out["values"]["done"] is True


# --- publish vs concurrent record writes ---------------------------------------------------
# Publish checks the stored records, then commits the definitions they passed
# for. Record writes take the same per-collection locks, so none lands in
# between; one that waited behind a publish re-reads the definitions it was
# checked against and is refused if they moved.

UNIQUE_DAY = {"kind": "unique", "fields": ["habit", "day"]}
DUPLICATE = {"habit": "run", "day": "2026-09-30"}


async def _write_via(path, sf, app_id, values):
    async with sf() as s:
        if path == "records":
            ctx = await load_app(s, app_id)
            return await create_record(s, ctx, Caller("agent:pai"), "habits", values)
        if path == "lifecycle":
            return await L.record_create(s, PAI, app_id, request_id=rid(),
                                         collection="habits", values=values)
        from agentplatform.appdata.batch import batch
        ctx = await load_app(s, app_id)
        return await batch(s, ctx, Caller("agent:pai"), "habits", [values])


def _pause_after_record_checks(monkeypatch):
    """Hold publish between its record checks and its commit."""
    import asyncio
    checked, go = asyncio.Event(), asyncio.Event()
    original = L._check_records

    async def paused(*args, **kw):
        await original(*args, **kw)
        checked.set()
        await go.wait()

    monkeypatch.setattr(L, "_check_records", paused)
    return checked, go


async def _count(sf, app_id):
    async with sf() as s:
        return (await s.execute(select(func.count()).select_from(AppDataRecord).where(
            AppDataRecord.app_id == app_id))).scalar_one()


@pytest.mark.parametrize("path", ["records", "lifecycle", "batch"])
async def test_a_write_racing_a_publish_waits_and_is_refused_on_the_old_definitions(
        sf, monkeypatch, path):
    import asyncio
    app_id = await built(sf, ("collection", habits()))
    await add_record(sf, app_id, "habits", DUPLICATE)
    await draft(sf, app_id, "collection", habits(rules=[UNIQUE_DAY]))
    checked, go = _pause_after_record_checks(monkeypatch)
    publishing = asyncio.create_task(publish(sf, app_id, 1))
    await checked.wait()
    # Under version 1 this duplicate is fine; under version 2 it isn't.
    writing = asyncio.create_task(_write_via(path, sf, app_id, DUPLICATE))
    await asyncio.sleep(0.2)
    assert not writing.done(), "the write landed inside the publish's check"
    go.set()
    assert (await publishing)["approved_version"] == 2
    err = await refused("AD-DEFINITIONS-MOVED", writing)
    assert err.status == 409 and err.detail["collections"] == ["habits"]
    assert await _count(sf, app_id) == 1


async def test_a_plain_write_waits_behind_a_publish_that_requires_a_field(sf, monkeypatch):
    """No `unique` anywhere: the write's shared lock still waits for the
    publish's exclusive one."""
    import asyncio
    app_id = await built(sf, ("collection", habits()))
    await add_record(sf, app_id, "habits", {**DUPLICATE, "done": True})
    body = habits()
    body["fields"]["done"] = {"type": "bool", "required": True}
    await draft(sf, app_id, "collection", body)
    checked, go = _pause_after_record_checks(monkeypatch)
    publishing = asyncio.create_task(publish(sf, app_id, 1))
    await checked.wait()
    writing = asyncio.create_task(_write_via("records", sf, app_id,
                                             {"habit": "read", "day": "2026-09-30"}))
    await asyncio.sleep(0.2)
    assert not writing.done()
    go.set()
    await publishing
    await refused("AD-DEFINITIONS-MOVED", writing)
    assert await _count(sf, app_id) == 1


async def test_a_publish_waits_for_a_write_in_flight_and_then_sees_it(sf, monkeypatch):
    import asyncio
    from agentplatform.appdata import records as rec_mod
    app_id = await built(sf, ("collection", habits()))
    await add_record(sf, app_id, "habits", DUPLICATE)
    await draft(sf, app_id, "collection", habits(rules=[UNIQUE_DAY]))
    inserted, go = asyncio.Event(), asyncio.Event()
    original = rec_mod.bump_counters

    async def paused(*args, **kw):
        inserted.set()
        await go.wait()
        return await original(*args, **kw)

    monkeypatch.setattr(rec_mod, "bump_counters", paused)
    writing = asyncio.create_task(_write_via("records", sf, app_id, DUPLICATE))
    await inserted.wait()
    publishing = asyncio.create_task(publish(sf, app_id, 1))
    await asyncio.sleep(0.2)
    assert not publishing.done(), "the publish checked while a write was mid-transaction"
    go.set()
    await writing
    err = await refused("AL-INCONSISTENT", publishing)
    assert err.detail["record_issues"][0]["code"] == "AL-RECORDS-UNIQUE"
    assert (await detail(sf, app_id))["approved_version"] == 1


async def test_a_publish_that_leaves_a_collection_alone_doesnt_stale_its_writes(sf):
    app_id = await built(sf, ("collection", habits()))
    async with sf() as s:
        ctx = await load_app(s, app_id)
    await draft(sf, app_id, "view", RECENT)
    await publish(sf, app_id, 1)
    async with sf() as s:
        await create_record(s, ctx, Caller("agent:pai"), "habits", DUPLICATE)
    await draft(sf, app_id, "collection", habits(rules=[UNIQUE_DAY]))
    await publish(sf, app_id, 2)
    async with sf() as s:
        err = await refused("AD-DEFINITIONS-MOVED", create_record(
            s, ctx, Caller("agent:pai"), "habits", {"habit": "x", "day": "2026-09-30"}))
    assert err.detail["approved_version"] == 3


async def test_a_rollback_takes_the_same_locks(sf, monkeypatch):
    import asyncio
    strict = habits()
    strict["fields"]["done"] = {"type": "bool", "required": True}
    app_id = await built(sf, ("collection", strict))
    await draft(sf, app_id, "collection", habits())
    await publish(sf, app_id, 1)
    await add_record(sf, app_id, "habits", {**DUPLICATE, "done": True})
    checked, go = _pause_after_record_checks(monkeypatch)
    async with sf() as s:
        rolling = asyncio.create_task(L.rollback(s, PAI, app_id, request_id=rid(),
                                                 to_version=1, expected_approved_version=2))
        await checked.wait()
        writing = asyncio.create_task(_write_via("records", sf, app_id,
                                                 {"habit": "x", "day": "2026-09-30"}))
        await asyncio.sleep(0.2)
        assert not writing.done()
        go.set()
        assert (await rolling)["approved_version"] == 3
    await refused("AD-DEFINITIONS-MOVED", writing)


async def test_concurrent_writes_never_leave_a_published_unique_broken(sf):
    """Real concurrency, no pauses: whatever the interleaving, a publish that
    went through leaves no duplicates behind it."""
    import asyncio
    app_id = await built(sf, ("collection", habits()))
    await draft(sf, app_id, "collection", habits(rules=[UNIQUE_DAY]))

    async def write(i):
        try:
            await _write_via("records", sf, app_id, {"habit": "run", "day": "2026-09-30"})
            return "ok"
        except RecordError as exc:
            return exc.code

    async def pub():
        try:
            await publish(sf, app_id, 1)
            return "published"
        except RecordError as exc:
            return exc.code

    results = await asyncio.gather(*(write(i) for i in range(4)), pub(),
                                   *(write(i) for i in range(4, 8)))
    async with sf() as s:
        dupes = (await s.execute(select(func.count()).select_from(AppDataRecord).where(
            AppDataRecord.app_id == app_id))).scalar_one()
    if "published" in results:
        assert dupes <= 1, results
    else:
        assert "AL-INCONSISTENT" in results


async def test_delete_waiting_for_publish_notices_a_new_incoming_ref(sf, monkeypatch):
    import asyncio
    from agentplatform.appdata.records import delete_record

    links = {"collection": "links", "fields": {"habit": {"type": "string"}}}
    app_id = await built(sf, ("collection", habits()), ("collection", links))
    target = await add_record(sf, app_id, "habits", DUPLICATE)
    await add_record(sf, app_id, "links", {"habit": target["id"]})
    links["fields"]["habit"] = {"type": "ref", "collection": "habits",
                                 "on_delete": "restrict"}
    await draft(sf, app_id, "collection", links, expected_revision=0)
    checked, go = _pause_after_record_checks(monkeypatch)

    async with sf() as s:
        ctx = await load_app(s, app_id)
    publishing = asyncio.create_task(publish(sf, app_id, 1))

    async def delete():
        async with sf() as s:
            return await delete_record(s, ctx, Caller("agent:pai"), "habits", target["id"])

    deleting = None
    try:
        await asyncio.wait_for(checked.wait(), 5)
        deleting = asyncio.create_task(delete())
        await asyncio.sleep(0.1)
        assert not deleting.done()
        go.set()
        await asyncio.wait_for(publishing, 5)
        err = await refused("AD-DEFINITIONS-MOVED", asyncio.wait_for(deleting, 5))
        assert err.detail["collections"] == ["links"]
        assert await _count(sf, app_id) == 2
        async with sf() as s:
            fresh = await load_app(s, app_id)
            await refused("AD-REF-RESTRICT", delete_record(
                s, fresh, Caller("agent:pai"), "habits", target["id"]))
    finally:
        go.set()
        for task in (publishing, deleting):
            if task is not None:
                task.cancel()
        await asyncio.gather(*(t for t in (publishing, deleting) if t is not None),
                             return_exceptions=True)


async def test_publish_holds_changed_collection_locks_sorted_through_commit(sf, monkeypatch):
    from contextlib import asynccontextmanager
    from sqlalchemy import event
    from agentplatform.appdata import records

    app_id = await new_app(sf)
    for name in ("zeta", "alpha"):
        await draft(sf, app_id, "collection", habits(collection=name))
    original = records.collection_lock
    held, committed = [], []

    @asynccontextmanager
    async def tracked(session, aid, name, *, shared=False):
        assert not shared
        async with original(session, aid, name, shared=shared):
            held.append(name)
            try:
                yield
            finally:
                held.remove(name)

    original_check = L._check_records

    async def checked(*args, **kwargs):
        assert held == ["alpha", "zeta"]
        await original_check(*args, **kwargs)

    monkeypatch.setattr(records, "collection_lock", tracked)
    monkeypatch.setattr(L, "_check_records", checked)
    async with sf() as s:
        event.listen(s.sync_session, "after_commit", lambda _: committed.append(list(held)))
        await L.publish(s, PAI, app_id, request_id=rid(), expected_approved_version=None)
    assert committed == [["alpha", "zeta"]]
    assert held == []
