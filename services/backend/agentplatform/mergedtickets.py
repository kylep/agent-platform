"""Close a ticket when the platform PR named for it merges.

A dev run publishes a ticket's work on `coder/<key>` or `qa/<key>` and moves
the ticket to `review`; a human merges, usually on GitHub itself, and a QA PR
may auto-merge there with no platform route in the path. Nothing moved the
ticket after that, so merged work sat in `review` until someone tidied the
board. This loop reads the recently closed PRs and moves each merged one's
ticket to `done`.

Only a PR that merged into the default branch from a platform branch of the
same repository counts (a fork's `coder/eng-12` is not ours), and only a
ticket the branch names (`ticket_key_of`): a `coder/run-<id>` branch names
none, and is left to the agent and the reviewer. The ticket must still be in
`review` or `blocked` with nothing said on it since the merge — a ticket
someone reopened or talked about afterwards is theirs to close.
"""
import asyncio
import logging
from datetime import datetime

from sqlalchemy import select

from agentplatform.db import Ticket
from agentplatform.ticket_store import TicketRuleError, move_ticket
from agentplatform.workbench import ticket_key_of

log = logging.getLogger("merged-tickets")

PLATFORM_PREFIXES = ("coder/", "qa/")
ACTOR = "system:github"


AWAITING_MERGE = frozenset({"review", "blocked"})


def _repo(side: dict | None) -> str | None:
    return ((side or {}).get("repo") or {}).get("full_name")


def merged_ticket_key(pr: dict, base: str) -> str | None:
    """The ticket a merged platform PR closes, or None."""
    head = (pr.get("head") or {}).get("ref", "")
    if not pr.get("merged_at") or (pr.get("base") or {}).get("ref") != base:
        return None
    if _repo(pr.get("head")) is None or _repo(pr.get("head")) != _repo(pr.get("base")):
        return None
    if not head.startswith(PLATFORM_PREFIXES):
        return None
    return ticket_key_of(head)


async def close_merged(session_factory, producer, prs: list[dict], *, base: str) -> list[str]:
    """Move each merged PR's ticket to `done`; returns the keys it closed."""
    closed = []
    for pr in prs:
        key = merged_ticket_key(pr, base)
        if key is None:
            continue
        async with session_factory() as s:
            row = (await s.execute(select(Ticket).where(Ticket.key == key))).scalar_one_or_none()
            if row is None or str(row.state) not in AWAITING_MERGE:
                continue
            merged_at = datetime.fromisoformat(pr["merged_at"].replace("Z", "+00:00"))
            last = row.last_activity_at
            if last is not None and last.tzinfo is None:
                last = last.replace(tzinfo=merged_at.tzinfo)
            if last is not None and last > merged_at:
                continue
            try:
                await move_ticket(s, producer, row, actor=ACTOR, to_state="done",
                                  reason=f"PR #{pr['number']} merged")
            except TicketRuleError:
                # Closed under us since the read: nothing left to do.
                await s.rollback()
                continue
            closed.append(key)
    if closed:
        log.info("closed merged tickets: %s", ", ".join(closed))
    return closed


class MergedTicketCloser:
    def __init__(self, gh_factory, session_factory, producer, *, base: str = "main",
                 interval_seconds: int = 300):
        self.gh_factory = gh_factory
        self.sf = session_factory
        self.producer = producer
        self.base = base
        self.interval = interval_seconds

    async def tick(self) -> list[str]:
        gh = await asyncio.to_thread(self.gh_factory)
        if gh is None:
            return []
        prs = await asyncio.to_thread(gh.list_pull_requests, state="closed",
                                      sort="updated", per_page=50)
        return await close_merged(self.sf, self.producer, prs, base=self.base)

    async def run_forever(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("merged-tickets tick failed")
            await asyncio.sleep(self.interval)
