"""Guarded one-shot Judgment copy. The old writer must be frozen before --apply.

The source lock, destination creation, copy, and parity check share one
transaction. Dry-run validates the live source without changing App state.
The old tables are deliberately retained for rollback and restore checks.
"""
from __future__ import annotations

import argparse
import asyncio
import json

from sqlalchemy import select

from agentplatform.appdata.definitions import validate_app
from agentplatform.appdata.judgment_migration import (
    SOURCE_TABLES,
    ImportRecord,
    bundle,
    convert_snapshot,
    import_snapshot,
    read_source,
)
from agentplatform.appdata.models import (
    AppDataApp,
    AppDataDefinition,
    AppDataRecord,
    AppDataRecordVersion,
)
from agentplatform.appdata.records import load_app, normalize_value
from agentplatform.config import Settings
from agentplatform.db import make_engine, make_session_factory


async def verify_copy(session, app_id: str,
                      expected: dict[str, list[ImportRecord]]) -> dict:
    """Compare every copied head and history document before committing."""
    context = await load_app(session, app_id)
    expected_rows = {(name, item.id): item for name, rows in expected.items()
                     for item in rows}
    actual_rows = (await session.execute(select(AppDataRecord).where(
        AppDataRecord.app_id == app_id))).scalars().all()
    if len(actual_rows) != len(expected_rows):
        raise RuntimeError("Judgment record count differs after copy")
    for row in actual_rows:
        item = expected_rows.get((row.collection, row.id))
        if item is None or row.current_version != item.version:
            raise RuntimeError("Judgment record ID or version differs after copy")
        collection = context.collection(row.collection)
        canonical = {key: normalize_value(context, collection, key, value)
                     for key, value in item.doc.items() if value is not None}
        if row.doc != canonical:
            raise RuntimeError("Judgment current document differs after copy")

    history_count = sum(len(item.history) for rows in expected.values()
                        for item in rows)
    history = (await session.execute(select(AppDataRecordVersion).where(
        AppDataRecordVersion.app_id == app_id))).scalars().all()
    actual_history = {(row.collection, row.record_id, row.version): row
                      for row in history}
    if len(history) != history_count:
        raise RuntimeError("Judgment history count differs after copy")
    for (name, record_id), item in expected_rows.items():
        collection = context.collection(name)
        for version in item.history:
            row = actual_history.get((name, record_id, version["version"]))
            if row is None:
                raise RuntimeError("Judgment historical version missing after copy")
            canonical = {key: normalize_value(context, collection, key, value)
                         for key, value in version["doc"].items()
                         if value is not None}
            if row.doc != canonical:
                raise RuntimeError("Judgment historical document differs after copy")
    return {"verified_records": len(actual_rows), "verified_history": len(history)}


async def copy(*, apply: bool = False) -> dict:
    engine = make_engine(Settings().db_url)
    try:
        async with make_session_factory(engine)() as session:  # noqa: SIM117
            async with session.begin():
                source = await read_source(session)
                expected = convert_snapshot(source)
                definitions = bundle()
                validate_app(definitions)
                counts = {name: len(expected[name]) for name in SOURCE_TABLES}
                history_count = sum(len(item.history) for rows in expected.values()
                                    for item in rows)
                if not apply:
                    return {"dry_run": True, "counts": counts,
                            "historical_versions": history_count}

                app = (await session.execute(select(AppDataApp).where(
                    AppDataApp.name == "judgment"))).scalar_one_or_none()
                if app is None:
                    app = AppDataApp(name="judgment", owner_kind="agent",
                                     owner_id="kai", timezone="America/Toronto",
                                     description="Kai's private beliefs, predictions, and Kyle's feedback.",
                                     approved_version=1, authority_generation=1)
                    session.add(app)
                    await session.flush()
                    for kind, plural in (("collection", "collections"), ("view", "views"),
                                         ("page", "pages"), ("tool", "app_tools")):
                        for body in definitions[plural]:
                            session.add(AppDataDefinition(
                                app_id=app.id, kind=kind, name=body[kind],
                                state="published", version=1, revision=1, body=body,
                                author="migration:judgment", approved_by="kyle",
                                reason="One-time Judgment state cutover"))
                    await session.flush()

                receipt = await import_snapshot(session, source, dry_run=False)
                receipt.update(await verify_copy(session, app.id, expected))
                return receipt
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="commit the verified copy")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(copy(apply=args.apply)), sort_keys=True))


if __name__ == "__main__":
    main()
