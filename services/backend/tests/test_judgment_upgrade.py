"""The one-off upgrade publishes the new Judgment bundle onto the live App."""
import copy

import pytest
from sqlalchemy import select

from agentplatform.appdata import intents, judgment_migration, judgment_upgrade
from agentplatform.appdata.models import AppDataApp, AppDataDefinition, AppDataRecord
from tests.test_judgment_migration import sf, snapshot  # noqa: F401

APP = judgment_upgrade.APP_ID
NEW_BUNDLE = judgment_migration.bundle
NEW_VIEWS = {"feedback_for_version", "links_for_belief", "versions_for_feedback",
             "predictions_by_timing", "feedback_by_outcome"}


def live_bundle() -> dict:
    """The definition published at the 2026-10-04 cutover, before ENG-8/ENG-10."""
    b = copy.deepcopy(NEW_BUNDLE())
    c = {body["collection"]: body for body in b["collections"]}
    c["beliefs"]["writers"] = {"create": ["tool:judgment"], "update": ["tool:judgment"]}
    del c["beliefs"]["rules"]
    c["belief_versions"].update(writers={"create": ["tool:judgment"]},
                                rules=c["belief_versions"]["rules"][:1],
                                indexed=["belief", "number"])
    for name in ("predictions", "prediction_beliefs"):
        c[name]["writers"] = {"create": ["tool:judgment"]}
    fb = c["feedback"]
    del fb["fields"]["shared_as"]
    fb["access"]["create"] = ["owner"]
    fb["writers"] = {"create": ["tool:judgment"]}
    fb["rules"] = [{"kind": "immutable_after_create",
                    "fields": ["prediction", "belief_version", "source_at", "outcome"]}]
    b["views"] = [v for v in b["views"] if v["view"] not in NEW_VIEWS]
    pages = {p["page"]: p for p in b["pages"]}
    pages["review"]["blocks"] = pages["review"]["blocks"][2:]
    prediction = pages["prediction"]
    prediction["blocks"] = prediction["blocks"][:1]
    prediction["blocks"][0]["actions"] = ["delete_prediction"]
    prediction["actions"] = prediction["actions"][1:]
    return b


async def _seed_live(sf, monkeypatch):  # noqa: F811
    definitions = live_bundle()
    async with sf() as s:
        s.add(AppDataApp(id=APP, name="judgment", owner_kind="agent", owner_id="kai",
                         approved_version=1))
        for kind, plural in judgment_upgrade.KINDS:
            for body in definitions[plural]:
                s.add(AppDataDefinition(app_id=APP, kind=kind, name=body[kind], version=1,
                                        body=body, state="published",
                                        author="migration:judgment"))
        await s.commit()
    monkeypatch.setattr(judgment_migration, "bundle", live_bundle)
    async with sf() as s:
        await judgment_migration.import_snapshot(s, snapshot(), dry_run=False)
        await s.commit()
    monkeypatch.undo()


async def test_upgrade_keeps_records_and_a_second_apply_is_a_no_op(sf, monkeypatch):  # noqa: F811
    await _seed_live(sf, monkeypatch)
    async with sf() as s:
        before = {(r.collection, r.id): (r.current_version, r.doc) for r in
                  (await s.execute(select(AppDataRecord))).scalars().all()}
        dry = await judgment_upgrade.upgrade(s, app_id=APP)
        assert dry["dry_run"] is True and dry["approved_version"] == 1
        changed = {(c["kind"], c["name"]) for c in dry["changes"]}
        assert {("collection", n) for n in ("beliefs", "belief_versions", "predictions",
                                            "prediction_beliefs", "feedback")} <= changed
        assert {("view", n) for n in NEW_VIEWS} <= changed
        feedback = next(c for c in dry["changes"] if c["name"] == "feedback")
        assert feedback["fields_added"] == ["shared_as"]
        # belief_versions gains an indexed `feedback`; stored rows get the column.
        assert dry["reindex"] == ["belief_versions"]
        await s.rollback()
    async with sf() as s:
        applied = await judgment_upgrade.upgrade(s, apply=True, app_id=APP)
        assert applied["published_version"] == 2
        await s.commit()
    async with sf() as s:
        after = {(r.collection, r.id): (r.current_version, r.doc) for r in
                 (await s.execute(select(AppDataRecord))).scalars().all()}
        assert after == before
        again = await judgment_upgrade.upgrade(s, apply=True, app_id=APP)
        assert again["up_to_date"] is True and again["changes"] == []
        assert (await s.get(AppDataApp, APP)).approved_version == 2
        # The new page action works on the upgraded App.
        intent = await intents.create(s, "judgment", page="prediction",
                                      template="add_feedback", record_id="p" * 32,
                                      values={"kyle_words": "built it", "outcome": "mixed",
                                              "shared_as": "choice"})
    async with sf() as s:
        result = await intents.dispatch(s, intent["intent_id"], intent["digest"])
        row = await s.get(AppDataRecord, (APP, "feedback", result["id"]))
        assert row.doc["shared_as"] == "choice"


async def test_upgrade_refuses_to_drop_a_live_definition(sf, monkeypatch):  # noqa: F811
    await _seed_live(sf, monkeypatch)
    async with sf() as s:
        s.add(AppDataDefinition(app_id=APP, kind="view", name="kyle_extra", version=1,
                                body={"view": "kyle_extra", "collection": "beliefs"},
                                state="published", author="kyle"))
        await s.commit()
    async with sf() as s:
        with pytest.raises(RuntimeError, match="would drop"):
            await judgment_upgrade.upgrade(s, apply=True, app_id=APP)
