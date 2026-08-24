"""The automatic PR-summary loop: comment-as-state, one run per head sha."""
import pytest

from agentplatform.db import Run, RunState
from agentplatform.events import FakeProducer, TOPIC_RUN_REQUESTS
from agentplatform.prsummarizer import (PrSummarizer, build_prompt, parse_marker,
                                        run_tags, summary_marker)


class FakeGH:
    def __init__(self, prs, comments=None):
        self._prs = prs
        self.comments = comments or {}
        self.posted = []

    def list_pull_requests(self):
        return self._prs

    def list_issue_comments(self, number):
        return self.comments.get(number, [])

    def create_issue_comment(self, number, body):
        self.posted.append((number, body))
        return {"id": 1}

    def pull_request_files(self, number):
        return [{"filename": "skills/linear/SKILL.md", "status": "added",
                 "additions": 5, "deletions": 0, "patch": "+name: linear"}]


class FakeAgents:
    def __init__(self, available=True):
        self.available = available

    async def reload(self):
        pass

    def get(self, name):
        if not self.available:
            return None
        class Info:
            error = None
        return Info()


def _pr(number=11, sha="abc123def456", branch="coder/agent-platform-coder"):
    return {"number": number, "title": "Create skill linear",
            "head": {"ref": branch, "sha": sha}}


@pytest.fixture
def summarizer_env(sf):
    producer = FakeProducer()
    def mk(gh):
        return PrSummarizer(lambda: gh, sf, producer, FakeAgents()), producer
    return mk


def test_marker_roundtrip():
    assert parse_marker(summary_marker("abc123")) == "abc123"
    assert parse_marker("just a human comment") is None
    assert "abc" in build_prompt(1, "t", "b", [{"filename": "f", "status": "added",
                                               "additions": 1, "deletions": 0,
                                               "patch": "abc"}])


async def test_dispatches_run_for_unsummarized_pr(sf, summarizer_env):
    gh = FakeGH([_pr()])
    loop, producer = summarizer_env(gh)
    await loop.tick()
    reqs = [p for p in producer.published if p[0] == TOPIC_RUN_REQUESTS]
    assert len(reqs) == 1
    from sqlalchemy import select
    async with sf() as s:
        run = (await s.execute(select(Run))).scalars().one()
    assert run.agent == "change-summarizer" and run.trigger == "pr-summary"
    assert set(run_tags(11, "abc123def456")).issubset(set(run.tags))
    # second tick: run exists and is active → no duplicate dispatch
    await loop.tick()
    assert len([p for p in producer.published if p[0] == TOPIC_RUN_REQUESTS]) == 1


async def test_posts_comment_when_run_succeeds(sf, summarizer_env):
    gh = FakeGH([_pr()])
    loop, _ = summarizer_env(gh)
    async with sf() as s:
        s.add(Run(agent="change-summarizer", trigger="pr-summary", requested_by="t",
                  prompt="x", state=RunState.SUCCEEDED, result="Adds a linear skill.",
                  tags=run_tags(11, "abc123def456")))
        await s.commit()
    await loop.tick()
    assert len(gh.posted) == 1
    number, body = gh.posted[0]
    assert number == 11 and parse_marker(body) == "abc123def456"
    assert "Adds a linear skill." in body


async def test_existing_comment_is_terminal(sf, summarizer_env):
    gh = FakeGH([_pr()], comments={11: [{"body": summary_marker("abc123def456") + "\nsummary"}]})
    loop, producer = summarizer_env(gh)
    await loop.tick()
    assert gh.posted == []
    assert [p for p in producer.published if p[0] == TOPIC_RUN_REQUESTS] == []


async def test_new_head_resummarizes(sf, summarizer_env):
    # comment exists for the OLD sha; a push moved head → dispatch again
    gh = FakeGH([_pr(sha="newsha9999999")],
                comments={11: [{"body": summary_marker("abc123def456") + "\nold"}]})
    loop, producer = summarizer_env(gh)
    await loop.tick()
    assert len([p for p in producer.published if p[0] == TOPIC_RUN_REQUESTS]) == 1


async def test_non_coder_prs_ignored(sf, summarizer_env):
    gh = FakeGH([_pr(branch="dependabot/npm")])
    loop, producer = summarizer_env(gh)
    await loop.tick()
    assert [p for p in producer.published if p[0] == TOPIC_RUN_REQUESTS] == []
