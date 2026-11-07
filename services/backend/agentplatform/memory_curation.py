"""Curation signals for agent memory, after Hermes's skill curator.

ENG-12 usage: how often each memory is retrieved (a searched read, a hybrid
hit, a startup selection) and when it was last retrieved, so a dream can
see what nobody uses. Listing the newest notes and a dream's own reading
don't count.

ENG-13 guards: an admin pins a memory (its agent may edit but not delete
it) or locks it (no agent change at all). The trigger holds every writer
— the memory Tool, the REST routes, dream deletes — except admin-attributed
transactions (`admin:*`, `rest:admin`; see memory_history.attribute).

Both tables live in `public`, like memory_versions: the Tool role holds DML
on tool_memory and must not inflate its own counts or lift its own guards.
SQLite (the unit-test database) has no trigger, so guards are not enforced
there; usage is recorded through the ORM instead.
"""
from __future__ import annotations

import functools
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import DateTime, Integer, String, Text, select, text
from sqlalchemy.orm import Mapped, mapped_column

from agentplatform.db import Base, utcnow

LEVELS = ("pin", "lock")
REFUSALS = {"memory_pinned": "an admin pinned this memory; it can be edited but not deleted",
            "memory_locked": "an admin locked this memory; it cannot be changed or deleted"}


class MemoryUsage(Base):
    __tablename__ = "memory_usage"
    memory_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    agent: Mapped[str] = mapped_column(String(128), index=True)
    retrievals: Mapped[int] = mapped_column(Integer, default=0)
    last_retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MemoryGuard(Base):
    __tablename__ = "memory_guards"
    memory_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    agent: Mapped[str] = mapped_column(String(128), index=True)
    level: Mapped[str] = mapped_column(String(8))
    note: Mapped[str] = mapped_column(Text, default="")
    set_by: Mapped[str] = mapped_column(String(160))
    set_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


GUARD_DDL = ("""
CREATE OR REPLACE FUNCTION public.memory_guard_check() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
  rid text := coalesce(current_setting('ap.run_id', true), '');
  lvl text;
BEGIN
  IF rid NOT LIKE 'admin:%' AND rid <> 'rest:admin' THEN
    SELECT g.level INTO lvl FROM public.memory_guards g WHERE g.memory_id = OLD.id;
    IF lvl = 'lock' THEN
      RAISE EXCEPTION 'memory_locked: an admin locked memory %; it cannot be changed or deleted', OLD.id;
    ELSIF lvl = 'pin' AND TG_OP = 'DELETE' THEN
      RAISE EXCEPTION 'memory_pinned: an admin pinned memory %; it can be edited but not deleted', OLD.id;
    END IF;
  END IF;
  IF TG_OP = 'DELETE' THEN
    -- A deleted memory's signals go with it (restore starts it fresh).
    DELETE FROM public.memory_usage WHERE memory_id = OLD.id;
    DELETE FROM public.memory_guards WHERE memory_id = OLD.id;
    RETURN OLD;
  END IF;
  RETURN NEW;
END $$
""",
    "REVOKE ALL ON FUNCTION public.memory_guard_check() FROM PUBLIC",
    "DROP TRIGGER IF EXISTS memory_guard ON tool_memory.memories",
    "CREATE TRIGGER memory_guard BEFORE UPDATE OR DELETE ON tool_memory.memories "
    "FOR EACH ROW EXECUTE FUNCTION public.memory_guard_check()",
)

# Installed by the ToolProvisioner beside memory_run_scope, because it grants
# EXECUTE to the Tool role, which exists only once the Tool is provisioned.
NOTE_USE_DDL = """
CREATE OR REPLACE FUNCTION public.memory_note_use(p_run text, p_agent text, p_ids text[])
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM public.memory_run_scope(p_run, p_agent))
     OR EXISTS (SELECT 1 FROM public.dreams d WHERE d.run_id = p_run) THEN
    RETURN;
  END IF;
  INSERT INTO public.memory_usage AS u (memory_id, agent, retrievals, last_retrieved_at)
    SELECT m.id, m.agent, 1, now() FROM tool_memory.memories m
    WHERE m.agent = p_agent AND m.id = ANY(p_ids)
  ON CONFLICT (memory_id) DO UPDATE SET retrievals = u.retrievals + 1, last_retrieved_at = now();
END $$
"""


def ensure_trigger(conn) -> None:
    """init_db step, idempotent like memory_history.ensure_trigger."""
    if conn.dialect.name != "postgresql":
        return
    for statement in GUARD_DDL:
        conn.exec_driver_sql(statement)


def refusal(exc: BaseException) -> str | None:
    """The guard code a failed write raised, if a guard refused it."""
    return next((code for code in REFUSALS if code in str(exc)), None)


def guarded(route):
    """A route whose memory write a guard refuses answers 409 with the guard's
    code, rather than a 500 from the trigger's exception."""
    from sqlalchemy.exc import DBAPIError

    @functools.wraps(route)
    async def wrapper(*args, **kwargs):
        try:
            return await route(*args, **kwargs)
        except DBAPIError as exc:
            code = refusal(exc)
            if code is None:
                raise
            raise HTTPException(409, {"code": code, "message": REFUSALS[code]}) from None
    return wrapper


async def note_use(s, run_id: str | None, agent: str, ids) -> None:
    """Count one retrieval of each of `ids` for `agent`. The caller commits.
    A dream's reading is not use: it reads everything by design."""
    from agentplatform.dreams import Dream
    ids = sorted({i for i in ids if i})
    if not ids or not run_id or (await s.execute(
            select(Dream.id).where(Dream.run_id == run_id).limit(1))).first():
        return
    if s.bind.dialect.name == "postgresql":
        await s.execute(text(
            "INSERT INTO public.memory_usage AS u (memory_id, agent, retrievals, last_retrieved_at) "
            "SELECT m.id, m.agent, 1, now() FROM tool_memory.memories m "
            "WHERE m.agent = :agent AND m.id = ANY(:ids) "
            "ON CONFLICT (memory_id) DO UPDATE SET retrievals = u.retrievals + 1, "
            "last_retrieved_at = now()"), {"agent": agent, "ids": ids})
        return
    now = utcnow()
    for memory_id in ids:
        row = await s.get(MemoryUsage, memory_id)
        if row is None:
            s.add(MemoryUsage(memory_id=memory_id, agent=agent, retrievals=1, last_retrieved_at=now))
        else:
            row.retrievals, row.last_retrieved_at = row.retrievals + 1, now


async def signals(s, agent: str) -> tuple[dict, dict]:
    """(usage, guards) for every memory of `agent`, keyed by memory id."""
    usage = {u.memory_id: u for u in (await s.execute(
        select(MemoryUsage).where(MemoryUsage.agent == agent))).scalars()}
    guards = {g.memory_id: g for g in (await s.execute(
        select(MemoryGuard).where(MemoryGuard.agent == agent))).scalars()}
    return usage, guards


def usage_view(u: MemoryUsage | None) -> dict:
    return {"retrievals": u.retrievals if u else 0,
            "last_retrieved_at": u.last_retrieved_at.isoformat() if u and u.last_retrieved_at else None}


def guard_view(g: MemoryGuard) -> dict:
    return {"memory_id": g.memory_id, "agent": g.agent, "level": g.level, "note": g.note,
            "set_by": g.set_by, "set_at": g.set_at.isoformat() if g.set_at else None}
