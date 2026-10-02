"""The `app_judgment` tables, created by the app at boot from `schema.COLUMNS`.

The column lists have one home (`schema.py`, which the `judgment` tool loads
by path); this module gives each name a type and asserts the two agree at
import, so a column added to one side without the other fails before anything
runs. `schema.UNIQUE` and `schema.PRIMARY_KEYS` become the constraints.

There are no foreign keys on purpose: design 38 deletes with explicit
statements in one transaction rather than a database cascade, so SQLite and
Postgres behave the same, and `api.py`'s deletion plan is what keeps the
references whole (its tests check that nothing dangles).

Every statement in `schema.py` is written with `%s` placeholders for psycopg
(the tool's driver). The app runs the SAME text through SQLAlchemy's `text()`
on asyncpg in production and aiosqlite in tests, so `translate` rewrites the
placeholders to named binds once, here, and on sqlite drops `FOR UPDATE`
(sqlite locks the whole database on write anyway). No statement is written
twice.
"""
from __future__ import annotations

import itertools
import os
import re
from datetime import datetime

from sqlalchemy import (Column, DateTime, Integer, MetaData, String, Table, Text,
                        UniqueConstraint, text)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from judgmentapp import schema

SCHEMA = "app_judgment"

_TS = DateTime(timezone=True)
_ID = String(32)
_AUTHOR = String(200)

# Types keyed by the names in schema.COLUMNS; nothing here may add a column.
# Nullability is the loosest the design allows: what is required is decided by
# schema.py's validation, which both writers run, not by a NOT NULL here.
_TYPES: dict[str, dict[str, tuple]] = {
    "beliefs": {
        "id": (_ID, dict(nullable=False)),
        "created_at": (_TS, dict(nullable=False, index=True)),
        "status": (String(16), dict(nullable=False)),
        "current_version": (Integer, dict(nullable=False)),
    },
    "belief_versions": {
        "id": (_ID, dict(nullable=False)),
        "belief_id": (_ID, dict(nullable=False, index=True)),
        "version": (Integer, dict(nullable=False)),
        "claim": (Text, dict(nullable=False)),
        "scope": (Text, dict(nullable=True)),
        "evidence": (Text, dict(nullable=True)),
        "provenance": (String(16), dict(nullable=False)),
        "source_ref": (String(200), dict(nullable=True)),
        "confidence": (String(8), dict(nullable=True)),
        "reason": (Text, dict(nullable=True)),
        "feedback_id": (_ID, dict(nullable=True, index=True)),
        "author": (_AUTHOR, dict(nullable=False)),
        "created_at": (_TS, dict(nullable=False)),
    },
    "predictions": {
        "id": (_ID, dict(nullable=False)),
        "created_at": (_TS, dict(nullable=False, index=True)),
        "scenario": (Text, dict(nullable=False)),
        "alternatives": (Text, dict(nullable=False)),
        "predicted_choice": (Text, dict(nullable=False)),
        "rationale": (Text, dict(nullable=True)),
        "confidence": (String(8), dict(nullable=True)),
        "timing": (String(16), dict(nullable=False)),
        "question_ref": (String(200), dict(nullable=True)),
        "author": (_AUTHOR, dict(nullable=False)),
    },
    "prediction_beliefs": {
        "prediction_id": (_ID, dict(nullable=False)),
        "belief_id": (_ID, dict(nullable=False, index=True)),
        "belief_version": (Integer, dict(nullable=False)),
    },
    "feedback": {
        "id": (_ID, dict(nullable=False)),
        "created_at": (_TS, dict(nullable=False, index=True)),
        "prediction_id": (_ID, dict(nullable=True, index=True)),
        "belief_id": (_ID, dict(nullable=True, index=True)),
        "belief_version": (Integer, dict(nullable=True)),
        "kyle_words": (Text, dict(nullable=False)),
        "source_ref": (String(200), dict(nullable=True)),
        "source_at": (_TS, dict(nullable=True)),
        "outcome": (String(16), dict(nullable=False)),
        "interpretation": (Text, dict(nullable=True)),
        "author": (_AUTHOR, dict(nullable=False)),
        "confirmed_at": (_TS, dict(nullable=True)),
    },
    "requests": {
        "request_id": (String(100), dict(nullable=False)),
        "action": (String(16), dict(nullable=False)),
        "args_hash": (String(64), dict(nullable=False)),
        "result": (Text, dict(nullable=False)),
        "created_at": (_TS, dict(nullable=False)),
    },
}

metadata = MetaData()


def _build() -> dict[str, Table]:
    tables = {}
    for name, cols in schema.COLUMNS.items():
        typed = _TYPES[name]
        if set(typed) != set(cols):
            raise RuntimeError(f"{name}: db.py types {sorted(typed)} != schema.COLUMNS {cols}")
        pk = set(schema.PRIMARY_KEYS[name])
        if not pk <= set(cols):
            raise RuntimeError(f"{name}: schema.PRIMARY_KEYS {sorted(pk)} not in {cols}")
        # Columns in COLUMNS order, so the DDL reads like the tool's INSERTs.
        columns = [Column(c, typed[c][0], primary_key=c in pk, **typed[c][1]) for c in cols]
        constraints = [UniqueConstraint(*u) for u in [schema.UNIQUE.get(name)] if u]
        tables[name] = Table(name, metadata, *columns, *constraints)
    for registry in (schema.UNIQUE, schema.PRIMARY_KEYS):
        if set(registry) - set(schema.COLUMNS):
            raise RuntimeError(f"schema names an unknown table: {sorted(registry)}")
    return tables


TABLES = _build()


def make_engine(url: str | None = None):
    url = url or os.environ["APP_DB_URL"]
    kwargs = {}
    if url.startswith("postgresql"):
        # create_all lands in the app's schema; the raw statements find it
        # through search_path (the role's `$user` schema, made explicit).
        kwargs["execution_options"] = {"schema_translate_map": {None: SCHEMA}}
        kwargs["connect_args"] = {"server_settings": {"search_path": SCHEMA}}
    return create_async_engine(url, **kwargs)


def make_session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


async def init_db(engine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)


_PLACEHOLDER = re.compile(r"%s")


def translate(sql: str, dialect: str) -> str:
    """A `%s` statement as `text()` runs it on `dialect`."""
    counter = itertools.count()
    sql = _PLACEHOLDER.sub(lambda _m: f":p{next(counter)}", sql)
    if dialect == "sqlite":
        sql = sql.replace(" FOR UPDATE", "")
    return sql


def _bind(params, dialect: str) -> dict:
    # sqlite has no timestamp type: store ISO text, which orders and compares
    # like the timestamps the tool writes on Postgres (all UTC, one offset).
    return {f"p{i}": (v.isoformat() if dialect == "sqlite" and isinstance(v, datetime) else v)
            for i, v in enumerate(params)}


async def execute(session, sql: str, params=()):
    dialect = session.bind.dialect.name
    return await session.execute(text(translate(sql, dialect)), _bind(params, dialect))


async def query(session, sql: str, params=()) -> list[tuple]:
    return [tuple(r) for r in (await execute(session, sql, params)).all()]
