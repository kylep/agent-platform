"""Automatic AI reviewer summaries for pending changes.

Every open coder/* pull request gets a change-summarizer run over its diff;
the result is posted as a PR comment (so it lives with the change on GitHub)
and the UI renders that comment at the top of the review. The comment itself
is the state — a marker line carries the head sha, so a new push simply gets
a fresh summary and nothing needs a table.

Loop (dispatcher): for each open coder/* PR
  no marker comment for the current head sha?
    -> no matching summarizer run yet: dispatch one (tagged pr/sha)
    -> matching run succeeded: post the comment
    -> matching run active: wait; failed: log and leave it (review still works)
"""
import asyncio
import logging

from sqlalchemy import select

from agentplatform.db import ACTIVE_STATES, Run, RunState
from agentplatform.events import TOPIC_RUN_REQUESTS

log = logging.getLogger("pr-summarizer")

MARKER = "<!-- ap:ai-summary sha="
CODER_PREFIX = "coder/"
SUMMARIZER_AGENT = "change-summarizer"


def summary_marker(sha: str) -> str:
    return f"{MARKER}{sha} -->"


def parse_marker(body: str) -> str | None:
    """The head sha a summary comment was written for, or None."""
    if not body.startswith(MARKER):
        return None
    return body[len(MARKER):].split(" ", 1)[0].strip()


def run_tags(number: int, sha: str) -> list[str]:
    return ["pr-summary", f"pr-{number}", f"sha:{sha[:12]}"]


def build_prompt(number: int, title: str, branch: str, files: list[dict]) -> str:
    parts = []
    for f in files:
        parts.append(f"--- {f['filename']} ({f['status']}, +{f['additions']} −{f['deletions']})\n"
                     + (f.get("patch") or "(no textual diff)"))
    diff = "\n\n".join(parts)
    if len(diff) > 60_000:   # keep the prompt bounded on huge changes
        diff = diff[:60_000] + "\n\n[diff truncated for length]"
    return (f"Summarize pending change #{number} — \"{title}\" "
            f"(branch `{branch}`) for its reviewer.\n\nUnified diff:\n\n{diff}")


class PrSummarizer:
    def __init__(self, gh_factory, session_factory, producer, agent_store,
                 interval_seconds: int = 90):
        # gh_factory: () -> GitHubClient | None (token is minted per call —
        # App installation tokens expire hourly).
        self.gh_factory = gh_factory
        self.sf = session_factory
        self.producer = producer
        self.agents = agent_store
        self.interval = interval_seconds

    async def _recent_summary_runs(self) -> list[Run]:
        async with self.sf() as s:
            return list((await s.execute(
                select(Run).where(Run.agent == SUMMARIZER_AGENT)
                .order_by(Run.created_at.desc()).limit(30))).scalars())

    async def _dispatch(self, number: int, sha: str, prompt: str) -> None:
        run = Run(agent=SUMMARIZER_AGENT, trigger="pr-summary",
                  requested_by="pr-summarizer", prompt=prompt,
                  tags=run_tags(number, sha))
        async with self.sf() as s:
            s.add(run)
            await s.commit()
        try:
            await self.producer.publish(TOPIC_RUN_REQUESTS, run.id,
                                        {"type": "run", "run_id": run.id},
                                        type="run.request")
        except Exception:
            pass  # the dispatcher sweep drains it
        log.info("dispatched summary run %s for PR #%d @ %s", run.id, number, sha[:10])

    async def tick(self) -> None:
        # factory mints an installation token (blocking HTTP) — keep it off
        # the event loop
        gh = await asyncio.to_thread(self.gh_factory)
        if gh is None:
            return
        self.agents.reload()
        info = self.agents.get(SUMMARIZER_AGENT)
        if info is None or info.error is not None:
            return
        prs = await asyncio.to_thread(gh.list_pull_requests)
        prs = [p for p in prs if p["head"]["ref"].startswith(CODER_PREFIX)]
        if not prs:
            return
        runs = await self._recent_summary_runs()
        for pr in prs:
            number, sha = pr["number"], pr["head"]["sha"]
            comments = await asyncio.to_thread(gh.list_issue_comments, number)
            if any(parse_marker(c.get("body", "")) == sha for c in comments):
                continue   # already summarized at this head
            tags = set(run_tags(number, sha))
            mine = [r for r in runs if tags.issubset(set(r.tags or []))]
            active = [r for r in mine if r.state in ACTIVE_STATES]
            done = [r for r in mine if r.state == RunState.SUCCEEDED and r.result]
            if done:
                body = (f"{summary_marker(sha)}\n**AI reviewer summary**\n\n"
                        f"{done[0].result}")
                await asyncio.to_thread(gh.create_issue_comment, number, body)
                log.info("posted summary comment on PR #%d @ %s", number, sha[:10])
            elif not active and not mine:
                # no run yet for this head — author one
                files = await asyncio.to_thread(gh.pull_request_files, number)
                await self._dispatch(number, sha,
                                     build_prompt(number, pr["title"],
                                                  pr["head"]["ref"], files))
            # active run: wait; failed run (mine but not done/active): leave it
            # — the diff/impact review still works, and a new push retries.

    async def run_forever(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("pr-summarizer tick failed")
            await asyncio.sleep(self.interval)
