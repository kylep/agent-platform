"""The legacy Judgment copy must preserve its meaningful history and links."""
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from agentplatform.appdata.definitions import validate_app
from agentplatform.appdata import intents
from agentplatform.appdata.access import RecordError
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
    assert belief.doc["number"] == 3
    assert belief.history[0]["doc"]["number"] == 1
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


async def _seed_review(sf, source=None):
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
        await import_snapshot(s, source or snapshot(), dry_run=False)
        await s.commit()


async def test_review_confirms_belief_once_with_tool_only_writer(sf):
    await _seed_review(sf)
    async with sf() as s:
        intent = await intents.create(s, "judgment", page="belief",
                                      template="confirm_belief", record_id="b" * 32)
    async with sf() as s:
        first = await intents.dispatch(s, intent["intent_id"], intent["digest"])
        second = await intents.dispatch(s, intent["intent_id"], intent["digest"])
        assert first["belief_version"] == 4
        assert second["replayed"] is True
        rows = (await s.execute(select(AppDataRecord).where(
            AppDataRecord.app_id == "a" * 32,
            AppDataRecord.collection == "belief_versions"))).scalars().all()
        assert sorted(row.doc["number"] for row in rows) == [1, 3, 4]


async def test_delete_feedback_restores_prior_domain_version_without_reusing_storage_version(sf):
    source = snapshot()
    source["belief_versions"][1]["feedback_id"] = "f" * 32
    source["feedback"][0]["belief_version"] = 1
    await _seed_review(sf, source)
    async with sf() as s:
        intent = await intents.create(s, "judgment", page="review",
                                      template="delete_feedback", record_id="f" * 32)
        assert intent["confirmation"]["delete_plan"]["head_changes"]["b" * 32][
            "number"] == 1
    async with sf() as s:
        result = await intents.dispatch(s, intent["intent_id"], intent["digest"])
        assert result["deleted"] is True
        belief = await s.get(AppDataRecord, ("a" * 32, "beliefs", "b" * 32))
        assert belief.current_version == 4
        assert belief.doc["number"] == 1
        assert belief.doc["claim"] == "original"
        assert await s.get(AppDataRecord, ("a" * 32, "feedback", "f" * 32)) is None
        assert await s.get(AppDataRecord, ("a" * 32, "belief_versions", "3" * 32)) is None


async def test_review_correction_rejection_and_feedback_confirmation(sf):
    await _seed_review(sf)
    async with sf() as s:
        with pytest.raises(RecordError, match="claim needs nonempty text"):
            await intents.create(s, "judgment", page="belief",
                                 template="correct_belief", record_id="b" * 32,
                                 values={"claim": "  "})
        correction = await intents.create(s, "judgment", page="belief",
                                          template="correct_belief", record_id="b" * 32,
                                          values={"claim": "new claim"})
    async with sf() as s:
        assert (await intents.dispatch(s, correction["intent_id"],
                                       correction["digest"]))["belief_version"] == 4
        belief = await s.get(AppDataRecord, ("a" * 32, "beliefs", "b" * 32))
        assert belief.doc["claim"] == "new claim"
        assert belief.doc["provenance"] == "kyle_confirmed"
        rejection = await intents.create(s, "judgment", page="belief",
                                         template="reject_belief", record_id="b" * 32,
                                         values={"reason": "Not true now"})
    async with sf() as s:
        assert (await intents.dispatch(s, rejection["intent_id"],
                                       rejection["digest"]))["belief_version"] == 5
        belief = await s.get(AppDataRecord, ("a" * 32, "beliefs", "b" * 32))
        assert belief.doc["status"] == "rejected"
        feedback = await intents.create(s, "judgment", page="review",
                                        template="confirm_feedback", record_id="f" * 32,
                                        values={"kyle_words": "Kyle corrected the quote"})
    async with sf() as s:
        result = await intents.dispatch(s, feedback["intent_id"], feedback["digest"])
        assert result["version"] == 2
        row = await s.get(AppDataRecord, ("a" * 32, "feedback", "f" * 32))
        assert row.doc["kyle_words"] == "Kyle corrected the quote"
        assert row.doc["confirmed_at"] is not None


async def test_review_delete_plan_change_needs_fresh_confirmation(sf):
    await _seed_review(sf)
    async with sf() as s:
        intent = await intents.create(s, "judgment", page="prediction",
                                      template="delete_prediction", record_id="p" * 32)
        # A newly linked item changes the bounded delete plan, even if the
        # prediction itself has not moved.
        existing = (await s.execute(select(AppDataRecord).where(
            AppDataRecord.collection == "prediction_beliefs"))).scalar_one()
        s.add(AppDataRecord(app_id="a" * 32, collection="prediction_beliefs",
                            id="9" * 32, current_version=1, collection_version=1,
                            doc=dict(existing.doc), author="agent:kai", size_bytes=1))
        await s.commit()
    async with sf() as s:
        with pytest.raises(RecordError, match="confirm again"):
            await intents.dispatch(s, intent["intent_id"], intent["digest"])
