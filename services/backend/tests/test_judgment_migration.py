"""The legacy Judgment copy must preserve its meaningful history and links."""
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from agentplatform.appdata.definitions import validate_app
from agentplatform.appdata.judgment_cutover import verify_copy
from agentplatform.appdata.judgment_migration import (
    bundle,
    convert_snapshot,
    import_snapshot,
)
from agentplatform.appdata.models import (
    AppDataApp,
    AppDataDefinition,
    AppDataRecord,
    AppDataRecordVersion,
)
from agentplatform.db import Base, make_engine, make_session_factory

NOW = datetime(2026, 10, 1, tzinfo=UTC)


def snapshot():
    return {
        "beliefs": [{"id": "b" * 32, "created_at": NOW, "status": "active",
                     "current_version": 3}],
        "belief_versions": [
            {"id": "1" * 32, "belief_id": "b" * 32, "version": 1,
             "claim": "original", "scope": None, "evidence": None,
             "provenance": "observed", "source_ref": None, "confidence": "low",
             "reason": None, "feedback_id": None, "author": "agent:kai",
             "created_at": NOW},
            {"id": "3" * 32, "belief_id": "b" * 32, "version": 3,
             "claim": "corrected", "scope": "sometimes", "evidence": None,
             "provenance": "kyle_confirmed", "source_ref": None,
             "confidence": "high", "reason": "Kyle corrected it",
             "feedback_id": None, "author": "user:admin", "created_at": NOW},
        ],
        "predictions": [{"id": "p" * 32, "created_at": NOW, "scenario": "choice",
                         "alternatives": '["a","b"]', "predicted_choice": "a",
                         "rationale": None, "confidence": "medium",
                         "timing": "prospective", "question_ref": None,
                         "author": "agent:kai"}],
        # Deleted version 2 remains a historical pin, with no claim.
        "prediction_beliefs": [{"prediction_id": "p" * 32,
                                "belief_id": "b" * 32, "belief_version": 2}],
        "feedback": [{"id": "f" * 32, "created_at": NOW,
                      "prediction_id": "p" * 32, "belief_id": "b" * 32,
                      "belief_version": 3, "kyle_words": "b", "source_ref": None,
                      "source_at": None, "outcome": "contradicted",
                      "interpretation": None, "author": "user:admin",
                      "confirmed_at": NOW}],
    }


def test_bundle_accepts_legacy_judgment_shape():
    app = validate_app(bundle())
    assert set(app.collections) == set(snapshot())
    assert app.collections["beliefs"].write_mode == "versioned"
    assert app.pages["belief"].blocks[0].history


def test_copy_keeps_ids_authors_versions_and_dangling_historical_pin():
    records = convert_snapshot(snapshot())
    belief = records["beliefs"][0]
    assert belief.id == "b" * 32 and belief.version == 3
    assert belief.author == "user:admin"
    assert belief.doc["claim"] == "corrected"
    assert belief.history[0]["version"] == 1
    assert "status" not in belief.history[0]["doc"]
    assert records["prediction_beliefs"][0].doc["belief_version"] == 2
    assert records["feedback"][0].doc["belief_version"] == "3" * 32


def test_copy_refuses_missing_version_referenced_by_feedback():
    source = snapshot()
    source["feedback"][0]["belief_version"] = 4
    with pytest.raises(ValueError, match="missing belief version"):
        convert_snapshot(source)


@pytest.fixture
async def sf(tmp_path):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path}/migration.db")
    tables = [table for name, table in Base.metadata.tables.items()
              if name.startswith("app_data_")]
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
    factory = make_session_factory(engine)
    yield factory
    await engine.dispose()


async def test_import_is_atomic_and_never_overwrites_destination(sf):
    definitions = bundle()
    async with sf() as s:
        s.add(AppDataApp(id="a" * 32, name="judgment", owner_kind="agent",
                         owner_id="kai", approved_version=1))
        for kind, plural in (("collection", "collections"), ("view", "views"),
                             ("page", "pages"), ("tool", "app_tools")):
            for body in definitions[plural]:
                s.add(AppDataDefinition(app_id="a" * 32, kind=kind,
                                        name=body[kind], version=1, body=body,
                                        state="published", author="agent:kai"))
        await s.commit()
    async with sf() as s:
        dry = await import_snapshot(s, snapshot())
        assert dry["counts"]["beliefs"] == 1
        assert (await s.execute(select(func.count()).select_from(AppDataRecord))) \
            .scalar_one() == 0
        applied = await import_snapshot(s, snapshot(), dry_run=False)
        assert applied["history"] == 1
        await s.commit()
    async with sf() as s:
        assert (await s.execute(select(func.count()).select_from(AppDataRecord))) \
            .scalar_one() == 6
        assert (await s.execute(select(func.count()).select_from(AppDataRecordVersion))) \
            .scalar_one() == 1
        assert await verify_copy(s, "a" * 32, convert_snapshot(snapshot())) == {
            "verified_records": 6, "verified_history": 1}
        with pytest.raises(ValueError, match="destination is not empty"):
            await import_snapshot(s, snapshot(), dry_run=False)


async def test_parity_check_rejects_a_changed_historical_document(sf):
    definitions = bundle()
    async with sf() as s:
        s.add(AppDataApp(id="a" * 32, name="judgment", owner_kind="agent",
                         owner_id="kai", approved_version=1))
        for kind, plural in (("collection", "collections"), ("view", "views"),
                             ("page", "pages"), ("tool", "app_tools")):
            for body in definitions[plural]:
                s.add(AppDataDefinition(app_id="a" * 32, kind=kind,
                                        name=body[kind], version=1, body=body,
                                        state="published", author="migration:judgment"))
        await s.commit()
    async with sf() as s:
        await import_snapshot(s, snapshot(), dry_run=False)
        await s.flush()
        old = (await s.execute(select(AppDataRecordVersion))).scalar_one()
        old.doc = {**old.doc, "claim": "wrong"}
        await s.flush()
        with pytest.raises(RuntimeError, match="historical document differs"):
            await verify_copy(s, "a" * 32, convert_snapshot(snapshot()))
