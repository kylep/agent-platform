"""Guarded TCMS snapshot copy. The old tool writer must be frozen for --apply.

Copy, source lock and full document parity run in one database transaction.
The legacy tables are retained for rollback until the new writer is verified.
"""
from __future__ import annotations

import argparse
import asyncio
import json

from sqlalchemy import func, select

from agentplatform.appdata import quotas
from agentplatform.appdata.definitions import validate_app
from agentplatform.appdata.models import AppDataApp, AppDataDefinition, AppDataRecord
from agentplatform.appdata.records import (bump_counters, load_app, normalize_value,
                                           side_columns)
from agentplatform.appdata.tcms_migration import bundle, convert_snapshot, read_source
from agentplatform.config import Settings
from agentplatform.db import make_engine, make_session_factory


async def copy(*, apply: bool = False) -> dict:
    engine = make_engine(Settings().db_url)
    try:
        async with make_session_factory(engine)() as session:
            async with session.begin():
                source = await read_source(session)
                prepared = convert_snapshot(source)
                definition = validate_app(bundle())
                counts = {name: len(items) for name, items in prepared.items()}
                if not apply:
                    return {"dry_run": True, "counts": counts}
                existing = (await session.execute(select(AppDataApp).where(
                    AppDataApp.name == "tcms"))).scalar_one_or_none()
                if existing is not None:
                    raise ValueError("TCMS state App already exists; refusing a second copy")
                app = AppDataApp(name="tcms", owner_kind="agent", owner_id="qa",
                                 timezone="America/Toronto",
                                 description="QA cases, verified runs and results.",
                                 approved_version=1, authority_generation=1)
                session.add(app)
                await session.flush()
                for kind, plural in (("collection", "collections"), ("view", "views"),
                                     ("page", "pages"), ("tool", "app_tools")):
                    for body in bundle()[plural]:
                        session.add(AppDataDefinition(
                            app_id=app.id, kind=kind, name=body[kind],
                            state="published", version=1, revision=1, body=body,
                            author="migration:tcms", approved_by="kyle",
                            reason="One-time TCMS state cutover"))
                await session.flush()
                ctx = await load_app(session, app.id)
                if ctx.bundle != definition:
                    raise ValueError("published TCMS definitions differ from reviewed recipe")
                used = 0
                for name, items in prepared.items():
                    collection = ctx.collection(name)
                    for item in items:
                        doc = {key: normalize_value(ctx, collection, key, value)
                               for key, value in item.doc.items() if value is not None}
                        size = quotas.doc_bytes(doc)
                        used += size
                        session.add(AppDataRecord(
                            app_id=app.id, collection=name, id=item.id,
                            current_version=1, created_at=item.created_at,
                            updated_at=item.created_at, author="migration:tcms",
                            collection_version=ctx.versions[name], doc=doc,
                            size_bytes=size, **side_columns(collection, doc)))
                    await session.flush()
                quotas.note(session, ctx, records=sum(counts.values()), bytes=used)
                await quotas.settle(session)
                await bump_counters(session, app.id, counts)
                total = (await session.execute(select(func.count()).select_from(AppDataRecord).where(
                    AppDataRecord.app_id == app.id))).scalar_one()
                if total != sum(counts.values()):
                    raise RuntimeError("TCMS row-count parity failed")
                return {"dry_run": False, "app_id": app.id, "counts": counts,
                        "verified_records": total}
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(copy(apply=args.apply)), sort_keys=True))


if __name__ == "__main__":
    main()
