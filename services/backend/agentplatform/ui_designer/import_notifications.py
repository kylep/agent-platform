"""Durable completion card; notifications never undo a completed import."""
from __future__ import annotations

import re

from sqlalchemy import select

from agentplatform.db import RelayMessage, UiDesignerImport
from agentplatform.events import TOPIC_UI_DESIGNER_EVENTS
from agentplatform.relay import SYSTEM_AUTHOR
from agentplatform.relay_store import (channel_by_name, outbound_for_message,
                                      post_relay_message, publish_relay_message)
from agentplatform.tickets import one_line


def _safe(text: str) -> str:
    text = re.sub(r"\[(?=\[)", "[\u200b", one_line(text, 120))
    return re.sub(r"\](?=\])", "]\u200b", text).replace("@", "@\u200b")


async def notify_import(session_factory, producer, job_id: str) -> None:
    """Retry after a restart without duplicating the stored Relay card.

    Kafka publication is at least once. A crash after publish can repeat the
    same event, but the message id remains stable and document creation is
    already committed. Lock the job to serialize concurrent notification ticks.
    """
    async with session_factory() as s:
        job = (await s.execute(select(UiDesignerImport).where(
            UiDesignerImport.id == job_id).with_for_update())).scalar_one_or_none()
        if job is None or job.state != "done" or not job.report.get("notification_pending"):
            return
        conv = await channel_by_name(s, "design")
        msg = None
        if conv is not None:
            msg_id = job.report.get("notification_message_id")
            msg = await s.get(RelayMessage, msg_id) if msg_id else None
            if msg is None:
                count = job.report.get("pages_imported", 0)
                body = f"Imported {_safe(job.slug)}: {count} page(s). Open UI Designer for the fidelity report."
                msg = await post_relay_message(
                    s, conv, author=SYSTEM_AUTHOR, kind="event", mentions=[], body=body,
                    card={"type": "ui_design", "event": "imported", "slug": job.slug,
                          "version": 1, "import_id": job.id, "author": job.initiated_by})
                job.report = {**job.report, "notification_message_id": msg.id}
            outbound = await outbound_for_message(s, conv, msg)
        payload = {"type": "imported", "slug": job.slug, "version": 1,
                   "author": job.initiated_by, "run_id": None, "import_id": job.id}
        await s.commit()
    if producer is None:
        return
    await producer.publish(TOPIC_UI_DESIGNER_EVENTS, payload["slug"], payload,
                           type="ui_designer.event")
    if msg is not None:
        await publish_relay_message(producer, conv, msg, outbound=outbound)
    async with session_factory() as s:
        job = await s.get(UiDesignerImport, job_id)
        job.report = {**job.report, "notification_pending": False}
        await s.commit()
