"""Verified, one-transaction copy of the legacy Stockmarket archive.

Run --apply only after a fresh encrypted backup. The source tables are locked
against writers until every destination document has been compared in place.
The old schema is retained for a deliberate rollback, never dropped here.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter

from sqlalchemy import func, insert, select, text

from agentplatform.appdata import quotas
from agentplatform.appdata.definitions import validate_app
from agentplatform.appdata.models import (
    AppDataApp,
    AppDataDefinition,
    AppDataQuota,
    AppDataRecord,
)
from agentplatform.appdata.records import (
    bump_counters,
    load_app,
    normalize_value,
    side_columns,
)
from agentplatform.appdata.stockmarket_migration import TABLES, bundle, convert
from agentplatform.config import Settings
from agentplatform.db import make_engine, make_session_factory

DESTINATION = {"symbols": "symbols", "bars": "bars", "watchlist": "watchlist",
               "briefs": "briefs", "backtest_datasets": "datasets",
               "backtest_experiments": "experiments", "backtest_results": "results",
               "backtest_series": "series", "backtest_events": "events"}
ORDER = {"symbols": "symbol", "bars": "symbol, day", "watchlist": "id",
         "briefs": "day", "backtest_datasets": "sha",
         "backtest_experiments": "id", "backtest_results": "experiment_id, strategy_id",
         "backtest_series": "experiment_id, strategy_id, day",
         "backtest_events": "id"}
PAGE = 1000


async def _definitions(session, app):
    definitions = bundle()
    validate_app(definitions)
    for kind, plural in (("collection", "collections"), ("view", "views"),
                         ("page", "pages"), ("tool", "app_tools")):
        for body in definitions[plural]:
            session.add(AppDataDefinition(
                app_id=app.id, kind=kind, name=body[kind], state="published",
                version=1, revision=1, body=body, author="migration:stockmarket",
                approved_by="kyle", reason="Verified Stockmarket state cutover"))
    await session.flush()
    ctx = await load_app(session, app.id)
    if ctx.bundle != validate_app(definitions):
        raise RuntimeError("published definition drifted from migration recipe")
    return ctx


async def copy(*, apply: bool = False) -> dict:
    engine = make_engine(Settings().db_url)
    try:
        async with make_session_factory(engine)() as session, session.begin():
                # SHARE permits readers, but prevents the prices/backtest writers
                # from changing the snapshot while the document copy is checked.
                for table in TABLES:
                    await session.execute(text(
                        f"LOCK TABLE app_stockmarket.{table} IN SHARE MODE"))
                source_counts = {}
                for table in TABLES:
                    source_counts[table] = (await session.execute(text(
                        f"SELECT count(*) FROM app_stockmarket.{table}"))).scalar_one()
                if not apply:
                    return {"dry_run": True, "source_counts": source_counts}
                if (await session.execute(select(AppDataApp.id).where(
                        AppDataApp.name == "stockmarket"))).first():
                    raise ValueError("Stockmarket state App already exists")
                app = AppDataApp(name="stockmarket", owner_kind="agent",
                                 owner_id="stockmarket-data", timezone="America/Toronto",
                                 description="Market archive, briefings and backtests.",
                                 approved_version=1, authority_generation=1)
                session.add(app)
                await session.flush()
                session.add(AppDataQuota(
                    scope_kind="app", scope_id=app.id, max_records=2_000_000,
                    max_bytes=1024 ** 3, writes_per_hour=2_000_000,
                    scan_rows_per_hour=10_000_000, set_by="kyle"))
                ctx = await _definitions(session, app)
                counts = Counter()
                bytes_used = 0
                for table in TABLES:
                    for offset in range(0, source_counts[table], PAGE):
                        rows = (await session.execute(text(
                            f"SELECT * FROM app_stockmarket.{table} "
                            f"ORDER BY {ORDER[table]} LIMIT :limit OFFSET :offset"),
                            {"limit": PAGE, "offset": offset})).mappings().all()
                        if len(rows) != min(PAGE, source_counts[table] - offset):
                            raise RuntimeError(f"source {table} changed during copy")
                        expected = {}
                        payload = []
                        for row in rows:
                            for item in convert(table, dict(row)):
                                collection = ctx.collection(item.collection)
                                doc = {key: normalize_value(ctx, collection, key, value)
                                       for key, value in item.doc.items() if value is not None}
                                if item.id in expected:
                                    raise RuntimeError(f"duplicate {item.collection} ID")
                                expected[item.id] = doc
                                size = quotas.doc_bytes(doc)
                                bytes_used += size
                                counts[item.collection] += 1
                                payload.append(dict(
                                    app_id=app.id, collection=item.collection, id=item.id,
                                    current_version=1, created_at=item.created_at,
                                    updated_at=item.created_at, author="migration:stockmarket",
                                    collection_version=ctx.versions[item.collection],
                                    doc=doc, size_bytes=size,
                                    **side_columns(collection, doc)))
                        for start in range(0, len(payload), 200):
                            await session.execute(insert(AppDataRecord), payload[start:start + 200])
                        got = (await session.execute(select(AppDataRecord.id, AppDataRecord.doc)
                               .where(AppDataRecord.app_id == app.id,
                                      AppDataRecord.id.in_(expected)))).all()
                        if len(got) != len(expected) or any(
                                expected[record_id] != doc for record_id, doc in got):
                            raise RuntimeError(f"full document parity failed for {table} offset {offset}")
                if counts["datasets"] < source_counts["backtest_datasets"]:
                    raise RuntimeError("dataset chunk parity failed")
                for table, collection in DESTINATION.items():
                    if table != "backtest_datasets" and counts[collection] != source_counts[table]:
                        raise RuntimeError(f"row count parity failed for {table}")
                quotas.note(session, ctx, records=sum(counts.values()), bytes=bytes_used)
                await quotas.settle(session)
                await bump_counters(session, app.id, dict(counts))
                copied = (await session.execute(select(func.count()).select_from(AppDataRecord)
                          .where(AppDataRecord.app_id == app.id))).scalar_one()
                if copied != sum(counts.values()):
                    raise RuntimeError("destination count differs from verified inserts")
                return {"dry_run": False, "app_id": app.id, "source_counts": source_counts,
                        "state_counts": dict(counts), "verified_records": copied,
                        "document_bytes": bytes_used}
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(copy(apply=args.apply)), sort_keys=True))


if __name__ == "__main__":
    main()
