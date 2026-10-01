"""Merged platform PRs close the ticket their branch names (mergedtickets.py)."""
import uuid

from sqlalchemy import select

from agentplatform import ticket_store as store
from agentplatform.db import Conversation, Ticket
from agentplatform.events import FakeProducer
from agentplatform.mergedtickets import MergedTicketCloser, merged_ticket_key


def _pr(number, head, *, merged=True, base="main"):
    return {"number": number, "head": {"ref": head}, "base": {"ref": base},
            "merged_at": "2026-09-29T12:00:00Z" if merged else None}


def test_only_a_merged_platform_pr_into_main_names_a_ticket():
    assert merged_ticket_key(_pr(1, "coder/eng-6"), "main") == "ENG-6"
    assert merged_ticket_key(_pr(2, "qa/qa-3"), "main") == "QA-3"
    assert merged_ticket_key(_pr(3, "coder/eng-6", merged=False), "main") is None
    assert merged_ticket_key(_pr(4, "coder/eng-6", base="release"), "main") is None
    assert merged_ticket_key(_pr(5, "feature/eng-6"), "main") is None
    assert merged_ticket_key(_pr(6, "coder/run-0123456789ab"), "main") is None


class _GH:
    def __init__(self, prs):
        self.prs = prs
        self.asked = []

    def list_pull_requests(self, *, state="open"):
        self.asked.append(state)
        return self.prs


async def _ticket(sf, producer, state: str) -> str:
    async with sf() as s:
        conv = Conversation(connector="web", kind="channel", open=True,
                            name=f"proj-{uuid.uuid4().hex[:8]}", title="#proj",
                            ticket_prefix=f"M{uuid.uuid4().hex[:4].upper()}",
                            ticket_seq=0)
        s.add(conv)
        await s.commit()
        t = await store.create_ticket(s, producer, conv, actor="user:admin", title="x")
        key = t.key
    if state != "open":
        async with sf() as s:
            row = (await s.execute(select(Ticket).where(Ticket.key == key))).scalar_one()
            await store.move_ticket(s, producer, row, actor="user:admin", to_state=state)
    return key


async def _state(sf, key):
    async with sf() as s:
        return str((await s.execute(select(Ticket).where(Ticket.key == key))).scalar_one().state)


async def test_a_merged_prs_ticket_moves_to_done_once(sf):
    producer = FakeProducer()
    key = await _ticket(sf, producer, "review")
    gh = _GH([_pr(23, f"coder/{key.lower()}"), _pr(24, "coder/zz-999")])
    closer = MergedTicketCloser(lambda: gh, sf, producer)
    assert await closer.tick() == [key]
    assert gh.asked == ["closed"]
    assert await _state(sf, key) == "done"
    # The next tick sees the same closed PR and leaves the done ticket alone.
    assert await closer.tick() == []


async def test_an_unmerged_or_cancelled_ticket_is_left_alone(sf):
    producer = FakeProducer()
    open_key = await _ticket(sf, producer, "review")
    cancelled = await _ticket(sf, producer, "cancelled")
    gh = _GH([_pr(1, f"coder/{open_key.lower()}", merged=False),
              _pr(2, f"qa/{cancelled.lower()}")])
    assert await MergedTicketCloser(lambda: gh, sf, producer).tick() == []
    assert await _state(sf, open_key) == "review"
    assert await _state(sf, cancelled) == "cancelled"


async def test_no_github_app_is_a_quiet_no_op(sf):
    assert await MergedTicketCloser(lambda: None, sf, FakeProducer()).tick() == []
