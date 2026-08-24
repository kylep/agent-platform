"""Reading agent definitions (docs/design/15).

Identity is rows now: `agent_defs` holds one row per agent — prompt, grants,
entrypoints, config — and this module is the read side of that table. It keeps
the surface the rest of the platform already speaks (`get(name)`, `list()`,
`AgentInfo.manifest`, `.crons()`, `.webhook_paths()`), so the dispatcher,
launcher, recorder, scheduler and API kept their call sites when the source
moved out of `agents/<name>/{agent.md,manifest.yaml,entrypoints.yaml}`.

**`reload()` is async; `get()`/`list()` stay sync.** The store is a cache over
an async database, and every caller already runs inside an event loop, where a
blocking facade has no honest implementation: `asyncio.run` refuses to nest,
and a worker-thread loop would drive the engine's pooled connections from the
wrong loop (an asyncpg failure waiting to happen). So the refresh is awaited at
the sites that already reloaded, and the cheap reads stay synchronous.

Reads are TTL-guarded instead: reading a cache older than `ttl_seconds`
(default 5) SCHEDULES a background refresh on the running loop and returns what
it has. That is what lets long-lived processes (recorder, dispatcher, API) pick
up a UI edit without a restart, without turning every read into an `await`.

**Grants are fields, not frontmatter.** `AgentInfo.platform_tools` /
`.harness_tools` carry what an agent.md's `tools:` line used to declare.
Anything deriving privilege from an agent's tools — the launcher's role ladder,
`/api/whoami` — MUST read those fields: `agent_md` is synthesized from the
prompt and has no frontmatter, so any attempt to parse tools back out of it
would come back "no tools: line", which the file rules read as UNRESTRICTED.
"""
from __future__ import annotations

import asyncio
import logging
import time

from pydantic import BaseModel, ValidationError

from agentplatform.agentdefs import EntrypointsModel, model_of
from agentplatform.db import AgentDef

log = logging.getLogger("agents")


class Manifest(BaseModel):
    """An agent's runtime config as the dispatcher, launcher and readiness gate
    consume it. A strict projection of the row — every field here is a column
    of the same name (`_manifest_of` relies on that) — kept as its own model
    because `Launcher.launch(run, manifest)` is the contract those components
    were built against."""
    role: str = "operator"
    concurrency: int = 1
    timeout_seconds: int = 1800
    skills: list[str] = []
    secrets: list[str] = []
    description: str = ""
    # Optional claude model override (e.g. "sonnet" for cheap background work);
    # empty = the CLI default.
    model: str = ""
    # System agents are platform-internal (e.g. the run summarizer): they get
    # API access injected and are protected from deletion in the UI.
    system: bool = False
    # When set, the agent gets an operator-scoped, per-run API token injected so
    # it can invoke other agents (agent-invokes-agent). Without it a system
    # agent only gets the narrow `annotator` token (read runs + annotate).
    can_invoke: bool = False
    # Per-agent transcript retention override (days). None = use the platform
    # default; <= 0 = keep this agent's transcripts forever.
    transcript_retention_days: int | None = None
    # When set, the recorder publishes each successful run's result text to
    # this Kafka topic ({run_id, agent, result}). This is how an agent's
    # output feeds an app (docs/design/11) — topics are app-namespaced
    # (app.<name>.*) so the consuming app is explicit in the declaration.
    result_topic: str = ""


class AgentInfo(BaseModel):
    """One agent as the PLATFORM reads it — the dispatcher, launcher, scheduler
    and readiness gate. Not an API shape: the agents API serves the row itself
    (`schemas.AgentDefOut`), so a definition on the wire is the definition as
    stored, not this derived view of it."""
    name: str
    manifest: Manifest | None
    # The agent's prompt — the body of what used to be agent.md. Deliberately
    # frontmatter-free: name, description and tools are fields now, and a
    # reader that needs them must read the fields (see the module docstring).
    agent_md: str
    entrypoints: EntrypointsModel = EntrypointsModel()
    # Tool grants straight off the row. `platform_tools` is the mcp__platform__*
    # set the launcher's role ladder and the broker's grant check read.
    harness_tools: list[str] = []
    platform_tools: list[str] = []
    enabled: bool = True
    error: str | None = None

    def crons(self) -> list[str]:
        """The cron expressions this agent fires on, in declaration order,
        deduplicated. Simplified from the file era: the deprecated manifest
        `schedule:` is gone with the files, so a row's entrypoints are the only
        source. Per-cron prompts live on `entrypoints.crons`; the scheduler
        still fires one generic run per agent."""
        out: list[str] = []
        for entry in self.entrypoints.crons:
            if entry.schedule not in out:
                out.append(entry.schedule)
        return out

    def webhook_paths(self) -> list[str]:
        return [w.path for w in self.entrypoints.webhooks]


def _manifest_of(model) -> Manifest:
    # Name-matched projection: Manifest's fields are all AgentDefModel fields,
    # so adding a column to both models is enough — nothing to keep in sync
    # here, and a rename fails loudly instead of silently dropping a value.
    return Manifest(**{f: getattr(model, f) for f in Manifest.model_fields})


def info_of(row: AgentDef) -> AgentInfo:
    """One row → the read model. A row that no longer validates (an unknown
    role, a cron expression that stopped parsing) is QUARANTINED exactly as a
    broken manifest.yaml was — manifest None and `error` set — so the
    dispatcher rejects its runs instead of launching a half-understood agent."""
    try:
        model = model_of(row)
    except ValidationError as e:
        return AgentInfo(name=row.name, manifest=None,
                         agent_md=row.prompt or "", error=str(e))
    return AgentInfo(name=model.name, manifest=_manifest_of(model),
                     agent_md=model.prompt, entrypoints=model.entrypoints,
                     harness_tools=model.harness_tools,
                     platform_tools=model.platform_tools, enabled=model.enabled)


class AgentStore:
    """A cached view of `agent_defs`. One instance per process is shared by
    everything that reads definitions, so a single refresh serves them all."""

    def __init__(self, session_factory, *, ttl_seconds: float = 5.0):
        self.session_factory = session_factory
        self.ttl_seconds = ttl_seconds
        self._cache: dict[str, AgentInfo] = {}
        # None = never loaded. Reads work regardless (empty), but they schedule
        # the first refresh, so a store nobody explicitly reloaded still fills.
        self._loaded_at: float | None = None
        self._refresh: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    async def reload(self) -> None:
        """Re-read every definition. Awaited wherever the caller needs to see a
        write that just happened (its own, or a UI edit it is reacting to)."""
        if self.session_factory is None:
            return
        from sqlalchemy import select
        # Serialized, query INCLUDED. A background TTL refresh and an explicit
        # reload overlap routinely, and whichever finishes last wins the cache.
        # Unsynchronized, a refresh that read the OLD rows can land after a
        # reload that read the new ones and resurrect them for a full TTL —
        # long enough for `_frozen_tools` to mint a run JWT from a grant set an
        # operator just narrowed. Holding the lock across the read makes cache
        # writes happen in read order, so the last writer is always the one
        # that looked most recently.
        async with self._lock:
            async with self.session_factory() as s:
                rows = (await s.execute(
                    select(AgentDef).order_by(AgentDef.name))).scalars().all()
                self._cache = {r.name: info_of(r) for r in rows}
            self._loaded_at = time.monotonic()

    def list(self) -> list[AgentInfo]:
        self._tick()
        return list(self._cache.values())

    def get(self, name: str) -> AgentInfo | None:
        self._tick()
        return self._cache.get(name)

    def _tick(self) -> None:
        """TTL refresh. A stale read kicks a background reload and returns the
        cache it has: the caller is sync, so the choice is between at most
        `ttl_seconds` of staleness and no refresh at all. Outside a running
        loop (or with no session factory) it does nothing — only an explicit
        `reload()` refreshes there."""
        if self.session_factory is None or self.ttl_seconds <= 0:
            return
        if self._loaded_at is not None and time.monotonic() - self._loaded_at < self.ttl_seconds:
            return
        if self._refresh is not None and not self._refresh.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        # Arm the clock BEFORE the task runs: without it every read in the same
        # tick would queue another refresh.
        self._loaded_at = time.monotonic()
        self._refresh = loop.create_task(self._refresh_quietly())

    async def _refresh_quietly(self) -> None:
        try:
            await self.reload()
        except asyncio.CancelledError:
            raise
        except Exception:
            # A background refresh is an optimization; a DB blip must not
            # surface as an error in whatever request happened to trigger it.
            log.warning("agent definition refresh failed; serving the cached "
                        "definitions", exc_info=True)
