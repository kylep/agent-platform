"""P1 `village` tool action handlers (plan §7, U5).

Each handler takes an ActionContext (who is calling, in which run and role)
and the already-parsed args and returns a contract.CommandResult. Identity,
request_id idempotency and the journal belong to commands.py (U4); it calls
`dispatch()` after it has resolved the caller from the broker-verified
headers. Handlers never take an agent argument: the persona is ctx.persona.

Everything a handler touches outside this file goes through ActionPorts, so
commands.py (and tests) wire the real engine, store and planner_ops in.
Role and schema checks are repeated here as defence in depth.
"""
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol

from . import contract as c
from .glossary import GLOSSARY

FINAL_SLOT = "final"

# Wording for the plain-words `help` menu (one clause per action).
ACTION_HELP: dict[str, str] = {
    "help": "this menu",
    "look": "your character, to-do list, goals, habits and what is nearby",
    "wait": "planner only: sleep until something changes",
    "edit_queue": "change your to-do list with whole chunks of steps",
    "set_goals": "up to 3 goals and one public line",
    "set_habits": "when-then habits for you or one of your residents",
    "resident": "create, edit or retire one of your residents",
    "note": "keep a private note for your future self",
    "recall_notes": "search your own private notes",
    "mark": "mark a moment worth remembering",
    "keepsake": "leave a small public mark on an object",
    "prompt": "rewrite your 'how I play' section (applies when the session closes)",
    "trial": "keep or renew a trial change",
    "pull": "set a visible nudge toward a person or place",
    "invite": "invite someone to a place",
    "answer": "answer an invitation",
    "consent": "let someone take your set-aside supplies",
    "propose_object": "propose a new object",
    "tomorrow": "final look-back only: write tomorrow's first ten minutes and an away note",
    "say": "say one short line aloud",
}


class ActionPorts(Protocol):
    """Seams into units this file does not own. Each is one small coroutine."""

    async def apply_world(self, action: str, args: dict[str, Any]) -> c.CommandResult:
        """Journal and apply a world-changing command for the caller (commands.py -> World.apply)."""

    async def look(self) -> dict[str, Any]:
        """Caller's own state: character, queue, goals, habits, nearby (view-limited),
        residents, wishes, today's block list. Built by the engine from the World."""

    async def queue_edit(self, base_version: int, ops: list[dict[str, Any]]) -> c.CommandResult:
        """planner_ops (U3): validate and apply edit_queue ops; data carries per-op refusals."""

    async def add_note(self, text: str, links: list[str]) -> str:
        """Append a private village_notes row for the caller; returns its id."""

    async def search_notes(self, q: str, limit: int) -> list[dict[str, Any]]:
        """The caller's own notes only, newest first; empty q lists the latest."""

    async def write_tomorrow(self, steps: list[dict[str, Any]], away_note: str | None) -> c.CommandResult:
        """Replace tomorrow's list (engine checks the ten-minute work budget) and emit tomorrow_written."""


@dataclass
class ActionContext:
    session_id: str
    persona: str
    run_id: str
    role: str                          # "planner" | "reflection" (village_runs.role)
    ports: ActionPorts
    slot: str | None = None            # reflection slot name; "final" for the last one
    rules: dict[str, Any] = field(default_factory=lambda: c.merged_rules(None))


Handler = Callable[[ActionContext, dict[str, Any]], Awaitable[c.CommandResult]]
HANDLERS: dict[str, Handler] = {}

NOTE_RECALL_LIMIT = 10
LONG_POLL_ACTIONS = frozenset({"wait"})   # served by the planner long-poll (U3/U4), not dispatch()


def refused(reason: str, **data: Any) -> c.CommandResult:
    return c.CommandResult(state="refused", reason=reason, data=data)


def applied(**data: Any) -> c.CommandResult:
    return c.CommandResult(state="applied", data=data)


def handler(name: str) -> Callable[[Handler], Handler]:
    if name not in c.ACTION_ROLES:
        raise ValueError(f"unknown village action {name!r}")

    def register(fn: Handler) -> Handler:
        HANDLERS[name] = fn
        return fn
    return register


async def dispatch(ctx: ActionContext, action: str, args: dict[str, Any]) -> c.CommandResult:
    """Role, schema and slot checks, then the handler. Never raises for caller mistakes."""
    roles = c.ACTION_ROLES.get(action)
    if roles is None:
        return refused(f"unknown action {action!r}; known: {sorted(c.ACTION_ROLES)}")
    if ctx.role not in roles:
        return refused(f"{action} is not available to a {ctx.role} run")
    if action in c.FINAL_SLOT_ONLY and ctx.slot != FINAL_SLOT:
        return refused(f"{action} can only be used in the final look-back of the session")
    body = {k: v for k, v in args.items() if k != "action"}
    errors = c.check(c.ACTION_ARGS[action], body)
    if errors:
        return refused("; ".join(errors[:5]), errors=errors)
    fn = HANDLERS.get(action)
    if fn is None:
        return refused(f"{action} is not available yet")
    return await fn(ctx, body)


# --- both roles --------------------------------------------------------------

@handler("help")
async def _help(ctx: ActionContext, args: dict[str, Any]) -> c.CommandResult:
    menu = {a: ACTION_HELP[a] for a in c.ACTIONS
            if ctx.role in c.ACTION_ROLES[a] and (a in HANDLERS or a in LONG_POLL_ACTIONS)
            and (a not in c.FINAL_SLOT_ONLY or ctx.slot == FINAL_SLOT)}
    return applied(role=ctx.role, actions=menu, glossary=dict(GLOSSARY))


@handler("look")
async def _look(ctx: ActionContext, args: dict[str, Any]) -> c.CommandResult:
    return applied(**await ctx.ports.look())


@handler("say")
async def _say(ctx: ActionContext, args: dict[str, Any]) -> c.CommandResult:
    return await ctx.ports.apply_world("say", args)


@handler("edit_queue")
async def _edit_queue(ctx: ActionContext, args: dict[str, Any]) -> c.CommandResult:
    return await ctx.ports.queue_edit(args["base_version"], args["ops"])


# --- reflection only ---------------------------------------------------------

@handler("set_goals")
async def _set_goals(ctx: ActionContext, args: dict[str, Any]) -> c.CommandResult:
    return await ctx.ports.apply_world("set_goals", args)


@handler("set_habits")
async def _set_habits(ctx: ActionContext, args: dict[str, Any]) -> c.CommandResult:
    # The World refuses a target that is neither the caller nor one of its residents.
    return await ctx.ports.apply_world("set_habits", args)


@handler("note")
async def _note(ctx: ActionContext, args: dict[str, Any]) -> c.CommandResult:
    note_id = await ctx.ports.add_note(args["text"], list(args.get("links", [])))
    return applied(note_id=note_id)


@handler("recall_notes")
async def _recall_notes(ctx: ActionContext, args: dict[str, Any]) -> c.CommandResult:
    return applied(notes=await ctx.ports.search_notes(args.get("q", ""), NOTE_RECALL_LIMIT))


@handler("tomorrow")
async def _tomorrow(ctx: ActionContext, args: dict[str, Any]) -> c.CommandResult:
    return await ctx.ports.write_tomorrow(args["steps"], args.get("away_note"))
