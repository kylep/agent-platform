"""One-shot, guarded copy of the legacy Running tables into the approved App.

Run inside the API pod after stopping the old Running consumer. The source
share lock and destination copy live in one transaction; a failed comparison
rolls the whole copy back. Default is a read-only dry run.
"""
from __future__ import annotations

import argparse
import asyncio
import json

from sqlalchemy import select

from agentplatform.appdata.definitions import validate_app
from agentplatform.appdata.models import AppDataApp, AppDataDefinition, AppDataRecord
from agentplatform.appdata.records import load_app, normalize_value
from agentplatform.appdata.running_migration import (bundle, convert_snapshot,
                                                     import_snapshot, read_source)
from agentplatform.config import Settings
from agentplatform.db import make_engine, make_session_factory


async def copy(*, apply: bool = False) -> dict:
    engine = make_engine(Settings().db_url)
    try:
        factory = make_session_factory(engine)
        async with factory() as session:
            async with session.begin():
                source = await read_source(session)
                prepared = convert_snapshot(source)
                definitions = bundle()
                validate_app(definitions)
                counts = {name: len(items) for name, items in prepared.items()}
                if not apply:
                    return {"dry_run": True, "counts": counts}
                app = (await session.execute(select(AppDataApp).where(
                    AppDataApp.name == "running"))).scalar_one_or_none()
                if app is None:
                    app = AppDataApp(name="running", owner_kind="agent",
                                     owner_id="running-coach", timezone="America/Toronto",
                                     description="Running history, trends, and coaching notes.",
                                     approved_version=1, authority_generation=1)
                    session.add(app)
                    await session.flush()
                    for kind, plural in (("collection", "collections"), ("view", "views"),
                                         ("page", "pages"), ("tool", "app_tools")):
                        for body in definitions[plural]:
                            session.add(AppDataDefinition(
                                app_id=app.id, kind=kind, name=body[kind],
                                state="published", version=1, revision=1, body=body,
                                author="migration:running", approved_by="kyle",
                                reason="One-time Running state cutover"))
                    await session.flush()
                receipt = await import_snapshot(session, source, dry_run=False)
                if apply:
                    ctx = await load_app(session, receipt["app_id"])
                    expected = {item.id: item for items in prepared.values()
                                for item in items}
                    rows = (await session.execute(select(AppDataRecord).where(
                        AppDataRecord.app_id == receipt["app_id"]))).scalars().all()
                    if len(rows) != len(expected) or {row.id for row in rows} != set(expected):
                        raise RuntimeError("Running copy did not preserve every record ID")
                    for row in rows:
                        item = expected[row.id]
                        collection = ctx.collection(item.collection)
                        canonical = {key: normalize_value(ctx, collection, key, value)
                                     for key, value in item.doc.items() if value is not None}
                        if row.collection != item.collection or row.doc != canonical:
                            raise RuntimeError(f"Running copy differed at {row.collection}:{row.id}")
                    receipt["verified_records"] = len(rows)
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
