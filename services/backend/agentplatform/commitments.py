"""Persona commitments (docs/design/44 part A, GEN-8).

A promise of future work ("I'll bring this to the next roundtable") is a
record with a status, not a free-text note. Open records are pushed into every
later run of the persona (agentdef `open_commitments`, rendered by the runner
as a data block in the user turn), so a detached run that never saw the
original chat still sees the promise. The open limit equals the delivery
limit: an open commitment is never silently left out.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import DateTime, Index, String, Text, func, select
from sqlalchemy.orm import Mapped, mapped_column

from agentplatform.db import Base, utcnow

OPEN_LIMIT = 20
STALE_DAYS = 7
STALE_DELIVERIES = 5
LIMITS = {"summary": 200, "meaning": 400, "destination": 120, "follow_up": 200,
          "source_note": 200, "result_ref": 300, "note": 300}


class Commitment(Base):
    __tablename__ = "commitments"
    __table_args__ = (Index("ix_commitments_agent_status", "agent", "status"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    agent: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(8), default="open")  # open | done | void
    summary: Mapped[str] = mapped_column(Text)
    meaning: Mapped[str] = mapped_column(Text, default="")
    destination: Mapped[str] = mapped_column(String(120))
    follow_up: Mapped[str] = mapped_column(Text, default="")
    # From the creating run's row, never from the model.
    source_run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_channel_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_message_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Model-authored: where a dream says the promise was really made.
    source_note: Mapped[str] = mapped_column(Text, default="")
    # True when a dream run created it: the inline capture missed it.
    backfilled: Mapped[bool] = mapped_column(default=False)
    result_ref: Mapped[str] = mapped_column(Text, default="")
    note: Mapped[str] = mapped_column(Text, default="")
    closed_by_run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CommitmentDelivery(Base):
    """One row per (run, commitment) handed to that run: what makes a
    retrieval miss observable rather than inferred."""
    __tablename__ = "commitment_deliveries"
    run_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    commitment_id: Mapped[str] = mapped_column(String(32), primary_key=True, index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


def view(c: Commitment) -> dict:
    return {"id": c.id, "agent": c.agent, "status": c.status, "summary": c.summary,
            "meaning": c.meaning, "destination": c.destination, "follow_up": c.follow_up,
            "source_run_id": c.source_run_id, "source_channel_id": c.source_channel_id,
            "source_message_id": c.source_message_id, "source_note": c.source_note,
            "backfilled": c.backfilled, "result_ref": c.result_ref, "note": c.note,
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "closed_at": c.closed_at.isoformat() if c.closed_at else None}


def delivered_view(c: Commitment) -> dict:
    """What a run sees: the promise and its meaning, not bookkeeping."""
    out = {"id": c.id, "summary": c.summary, "destination": c.destination,
           "made": c.created_at.date().isoformat() if c.created_at else None}
    for field in ("meaning", "follow_up", "source_note"):
        if getattr(c, field):
            out[field] = getattr(c, field)
    return out


async def open_for(s, agent: str) -> list[Commitment]:
    return list((await s.execute(select(Commitment).where(
        Commitment.agent == agent, Commitment.status == "open")
        .order_by(Commitment.created_at, Commitment.id).limit(OPEN_LIMIT))).scalars())


def deliverable(agent_row, frozen_tools: list[str] | None) -> bool:
    """Personas holding memory both now and in the run's frozen grant: the
    same fence the memory routes use, so a run is never shown commitments it
    could not complete or void."""
    tool = "mcp__platform__memory"
    return bool(agent_row and agent_row.enabled and agent_row.agent_type == "persona"
                and tool in (agent_row.platform_tools or [])
                and (frozen_tools is None or tool in frozen_tools))


async def deliver(s, run, agent_row) -> list[dict]:
    """The run's `open_commitments`, recording each delivery once."""
    frozen = (run.definition_snapshot or {}).get("platform_tools") if run.definition_snapshot else None
    if not deliverable(agent_row, frozen):
        return []
    rows = await open_for(s, run.agent)
    if rows:
        seen = set((await s.execute(select(CommitmentDelivery.commitment_id).where(
            CommitmentDelivery.run_id == run.id))).scalars())
        s.add_all(CommitmentDelivery(run_id=run.id, commitment_id=c.id) for c in rows if c.id not in seen)
    return [delivered_view(c) for c in rows]


async def stats(s) -> list[dict]:
    rows = (await s.execute(select(Commitment.agent, Commitment.status, Commitment.backfilled,
                                   func.count()).group_by(Commitment.agent, Commitment.status,
                                                          Commitment.backfilled))).all()
    out: dict[str, dict] = {}
    for agent, status, backfilled, n in rows:
        a = out.setdefault(agent, {"agent": agent, "open": 0, "done": 0, "void": 0,
                                   "backfilled": 0, "stale_open": []})
        a[status] = a.get(status, 0) + n
        if backfilled:
            a["backfilled"] += n
    counts = dict((await s.execute(select(CommitmentDelivery.commitment_id, func.count())
                                   .group_by(CommitmentDelivery.commitment_id))).all())
    cutoff = utcnow() - timedelta(days=STALE_DAYS)
    for c in (await s.execute(select(Commitment).where(Commitment.status == "open"))).scalars():
        made = c.created_at if c.created_at.tzinfo else c.created_at.replace(tzinfo=cutoff.tzinfo)
        if made < cutoff or counts.get(c.id, 0) >= STALE_DELIVERIES:
            out[c.agent]["stale_open"].append({"id": c.id, "summary": c.summary,
                                               "deliveries": counts.get(c.id, 0),
                                               "created_at": c.created_at.isoformat()})
    return sorted(out.values(), key=lambda a: a["agent"])
