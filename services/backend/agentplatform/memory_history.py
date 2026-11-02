"""Memory version history (docs/design/44 part C).

A Postgres trigger on tool_memory.memories appends a row here for every
insert (new state) and every update or delete (the state being lost). It sees
every writer — the memory Tool's direct psycopg connection, the REST routes and
dream deletes — so no writer can forget to record. The same trigger enforces a
running dream's loss budget, counting overwrites as well as deletes.

The table lives in `public`, not the tool schema: the Tool role holds DML on
everything in tool_memory and must not be able to rewrite its own history.
SQLite (the unit-test database) has no trigger, so history there is empty.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import JSON, DateTime, String, Text, delete, select, text
from sqlalchemy.orm import Mapped, mapped_column

from agentplatform.db import Base, utcnow

LOSS_LIMIT = "memory_dream_loss_limit"


class MemoryVersion(Base):
    __tablename__ = "memory_versions"
    version_id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    memory_id: Mapped[str] = mapped_column(String(32), index=True)
    agent: Mapped[str] = mapped_column(String(128), index=True)
    # insert rows carry the new state; update/delete rows the state replaced.
    op: Mapped[str] = mapped_column(String(8))
    # The writing run, or `admin:<principal>`; null when the writer set none.
    run_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    content: Mapped[str] = mapped_column(Text)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    team_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    project_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


TRIGGER_DDL = ("""
CREATE OR REPLACE FUNCTION public.memory_history_capture() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
  r record;
  rid text := nullif(current_setting('ap.run_id', true), '');
  budget integer;
  used integer;
BEGIN
  IF TG_OP = 'INSERT' THEN r := NEW; ELSE r := OLD; END IF;
  IF TG_OP <> 'INSERT' AND rid IS NOT NULL THEN
    -- Any dream row, whatever its state: a dream run keeps its rails even
    -- if finalize gave up on it. FOR UPDATE serializes the run's concurrent
    -- writers, so parallel tool calls cannot each pass the same count.
    SELECT d.loss_budget INTO budget FROM public.dreams d
      WHERE d.run_id = rid AND d.agent = r.agent FOR UPDATE;
    IF budget IS NOT NULL THEN
      SELECT count(*) INTO used FROM public.memory_versions v
        WHERE v.run_id = rid AND v.op IN ('update', 'delete');
      IF used >= budget THEN
        RAISE EXCEPTION 'memory_dream_loss_limit: this dream may replace or delete at most % memories', budget;
      END IF;
    END IF;
  END IF;
  INSERT INTO public.memory_versions
    (version_id, memory_id, agent, op, run_id, at, key, content, tags, team_id, project_id, updated_at)
    VALUES (replace(gen_random_uuid()::text, '-', ''), r.id, r.agent, lower(TG_OP), rid, now(),
            r.key, r.content, r.tags, r.team_id, r.project_id, r.updated_at);
  RETURN NULL;
END $$
""",
    "REVOKE ALL ON FUNCTION public.memory_history_capture() FROM PUBLIC",
    "DROP TRIGGER IF EXISTS memory_history ON tool_memory.memories",
    "CREATE TRIGGER memory_history AFTER INSERT OR UPDATE OR DELETE ON tool_memory.memories "
    "FOR EACH ROW EXECUTE FUNCTION public.memory_history_capture()",
)


def ensure_trigger(conn) -> None:
    """init_db step. Idempotent: the function is replaced and the trigger
    recreated on every boot, under init_db's advisory lock."""
    if conn.dialect.name != "postgresql":
        return
    for statement in TRIGGER_DDL:
        conn.exec_driver_sql(statement)


async def attribute(s, who: str | None) -> None:
    """Name the writer of this transaction's memory changes for the trigger.
    Transaction-scoped (set_config local), so it never leaks to the next use
    of a pooled connection."""
    if who and s.bind.dialect.name == "postgresql":
        await s.execute(text("SELECT set_config('ap.run_id', :v, true)"), {"v": who})


def loss_limited(exc: BaseException) -> bool:
    return LOSS_LIMIT in str(exc)


def view(v: MemoryVersion) -> dict:
    return {"version_id": v.version_id, "memory_id": v.memory_id, "agent": v.agent,
            "op": v.op, "run_id": v.run_id, "at": v.at.isoformat() if v.at else None,
            "key": v.key, "content": v.content, "tags": v.tags or [],
            "team_id": v.team_id, "project_id": v.project_id,
            "updated_at": v.updated_at.isoformat() if v.updated_at else None}


async def purge_expired(session_factory, days: int) -> int:
    """Retention: history older than `days` is gone for good, so forgetting
    eventually becomes permanent (Kyle, 2026-11-02)."""
    if days <= 0:
        return 0
    async with session_factory() as s:
        result = await s.execute(delete(MemoryVersion).where(
            MemoryVersion.at < utcnow() - timedelta(days=days)))
        await s.commit()
        return result.rowcount or 0


async def run_changes(s, run_id: str) -> list[MemoryVersion]:
    return list((await s.execute(select(MemoryVersion).where(MemoryVersion.run_id == run_id)
                                 .order_by(MemoryVersion.at, MemoryVersion.version_id))).scalars())
