"""The `app_tcms` tables, created by the app at boot from `schema.COLUMNS`.

The column lists have one home (`schema.py`, which the `tcms` tool loads by
path); this module gives each name a type and asserts the two agree at import,
so a column added to one side without the other fails before anything runs.
`schema.UNIQUE` becomes the constraints the tool's `ON CONFLICT` relies on.

Every statement in `schema.py` is written with `%s` placeholders for psycopg
(the tool's driver). The app runs the SAME text through SQLAlchemy's `text()`
on asyncpg in production and aiosqlite in tests, so `translate` rewrites the
placeholders to named binds once, here — and on sqlite drops the two Postgres
casts and `ILIKE`, which is all the shared reads need. That is the whole
dialect difference: no statement is written twice.
"""
from __future__ import annotations

import itertools
import os
import re
from datetime import datetime

from sqlalchemy import (JSON, Boolean, Column, DateTime, Float, ForeignKey,
                        Integer, MetaData, String, Table, Text,
                        UniqueConstraint, text)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tcmsapp import schema

SCHEMA = "app_tcms"

# jsonb in Postgres (the tool writes `%s::jsonb`), plain JSON text on sqlite.
_JSON = JSON().with_variant(JSONB(), "postgresql")
_TS = DateTime(timezone=True)

# Types keyed by the names in schema.COLUMNS; nothing here may add a column.
# Each value is what `Column(name, ...)` takes, so a table can be built fresh.
_TYPES: dict[str, dict[str, tuple]] = {
    "cases": {
        "key": (String(200), dict(primary_key=True)),
        "suite": (String(100), dict(nullable=False)),
        "area": (String(100), dict(nullable=False)),
        "title": (Text, dict(nullable=False)),
        "layer": (String(16), dict(nullable=False)),
        "priority": (String(4), dict(nullable=False)),
        "preconditions": (_JSON, dict(nullable=False)),
        "steps": (_JSON, dict(nullable=False)),
        "expected": (Text, dict(nullable=False, default="")),
        "automation": (_JSON, dict(nullable=False)),
        "tags": (_JSON, dict(nullable=False)),
        "tickets": (_JSON, dict(nullable=False)),
        "status": (String(16), dict(nullable=False, default="active")),
        "synced_at": (_TS, dict(nullable=False)),
        "source_sha": (String(64), dict(nullable=True)),
    },
    "test_runs": {
        "id": (Integer, dict(primary_key=True)),
        "commit_sha": (String(64), dict(nullable=False)),
        "branch": (String(200), dict(nullable=True)),
        "run_id": (String(64), dict(nullable=True)),
        "agent": (String(100), dict(nullable=True)),
        "started_at": (_TS, dict(nullable=False, index=True)),
        "finished_at": (_TS, dict(nullable=True)),
        "verify_ok": (Boolean, dict(nullable=True)),
        "suites": (_JSON, dict(nullable=False)),
        "published_at": (_TS, dict(nullable=True)),
    },
    "results": {
        "id": (Integer, dict(primary_key=True)),
        "test_run_id": (Integer, dict(nullable=False, index=True)),
        "ref": (Text, dict(nullable=False)),
        "case_key": (String(200), dict(nullable=True, index=True)),
        "status": (String(16), dict(nullable=False)),
        "duration_ms": (Integer, dict(nullable=False, default=0)),
        "message": (Text, dict(nullable=False, default="")),
        "layer": (String(16), dict(nullable=False)),
    },
    "coverage_snapshots": {
        "id": (Integer, dict(primary_key=True)),
        "test_run_id": (Integer, dict(nullable=False, index=True)),
        "package": (Text, dict(nullable=False)),
        "lines_covered": (Integer, dict(nullable=False)),
        "lines_total": (Integer, dict(nullable=False)),
        "branch_rate": (Float, dict(nullable=True)),
    },
}

metadata = MetaData()


def _fk() -> tuple:
    """A child row's run. Made per column: a ForeignKey binds to the column it
    is declared on, so the two children cannot share one in `_TYPES`."""
    return (ForeignKey("test_runs.id", ondelete="CASCADE"),)


def _build() -> dict[str, Table]:
    tables = {}
    for name, cols in schema.COLUMNS.items():
        typed = _TYPES[name]
        if set(typed) != set(cols):
            raise RuntimeError(f"{name}: db.py types {sorted(typed)} != schema.COLUMNS {cols}")
        # Columns in COLUMNS order, so the DDL reads like the tool's INSERTs.
        columns = [Column(c, typed[c][0], *(_fk() if c == "test_run_id" else ()),
                          **typed[c][1]) for c in cols]
        constraints = [UniqueConstraint(*u) for u in [schema.UNIQUE.get(name)] if u]
        tables[name] = Table(name, metadata, *columns, *constraints)
    if set(schema.UNIQUE) - set(schema.COLUMNS):
        raise RuntimeError(f"schema.UNIQUE names an unknown table: {schema.UNIQUE}")
    return tables


TABLES = _build()


def make_engine(url: str | None = None):
    url = url or os.environ["APP_DB_URL"]
    kwargs = {}
    if url.startswith("postgresql"):
        # create_all lands in the app's schema; the raw reads find it through
        # search_path (the role's `$user` schema is the same name, made explicit).
        kwargs["execution_options"] = {"schema_translate_map": {None: SCHEMA}}
        kwargs["connect_args"] = {"server_settings": {"search_path": SCHEMA}}
    return create_async_engine(url, **kwargs)


def make_session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


async def init_db(engine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)


_PLACEHOLDER = re.compile(r"%s")
_SQLITE_REWRITES = (("::int", ""), ("::jsonb", ""), ("::timestamptz", ""), (" ILIKE ", " LIKE "))


def translate(sql: str, dialect: str) -> str:
    """A `%s` statement as `text()` runs it on `dialect`."""
    counter = itertools.count()
    sql = _PLACEHOLDER.sub(lambda _m: f":p{next(counter)}", sql)
    if dialect == "sqlite":
        for old, new in _SQLITE_REWRITES:
            sql = sql.replace(old, new)
    return sql


def _bind(params, dialect: str) -> dict:
    # sqlite has no timestamp type: store ISO text, which orders and compares
    # like the timestamps the tool writes on Postgres.
    return {f"p{i}": (v.isoformat() if dialect == "sqlite" and isinstance(v, datetime) else v)
            for i, v in enumerate(params)}


async def execute(session, sql: str, params=()):
    dialect = session.bind.dialect.name
    return await session.execute(text(translate(sql, dialect)), _bind(params, dialect))


async def query(session, sql: str, params=()) -> list[tuple]:
    return [tuple(r) for r in (await execute(session, sql, params)).all()]
