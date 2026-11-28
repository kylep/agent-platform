"""Pure adapters between the world (U1) and the units that read it (plan §1).

No I/O, no awaits. `install_world` adds the engine-owned actions the world does
not know about (handlers are code, not state, so they are re-installed on every
load). `EngineView` is planner.WorldView over the live World. `public_view`
turns a World snapshot into the spectator shape the API serves.
"""
from typing import Any, Callable

from . import tasks
from .contract import LIMITS, MAP_H, MAP_W, TICK_HZ, VIEW_RADIUS, Event
from .mapdata import LANDMARKS, cheb
from .materials import WORK

FINISHED = ("done", "failed", "skipped")
STEP_FIELDS = ("op", "id", "target", "say", "deps", "needs", "big", "guess", "decor")
QUEUE_FIELDS = ("id", "op", "target", "say", "deps", "needs", "state", "blocked_reason")
TRAVEL_OPS = frozenset({"walk", "carry_to", "visit", "pickup", "place", "use", "repair", "plant",
                        "harvest", "dismantle", "drop", "keepsake"})
TRAVEL_GUESS_S = 8.0          # a rough walk per step: the runway estimate, not a promise
TOMORROW_MAX_S = 600          # §7: "tomorrow" is at most ten minutes of work
SOURCES_PER_MATERIAL = 3


# --- time estimates ----------------------------------------------------------

def step_seconds(step: dict[str, Any]) -> float:
    op = step.get("op")
    work = WORK.get(op, 0) / TICK_HZ
    if op in TRAVEL_OPS:
        work += TRAVEL_GUESS_S
    return work or 1.0


def estimate_seconds(steps: list[dict[str, Any]]) -> float:
    return sum(step_seconds(s) for s in steps if s.get("state") not in FINISHED)


def plain_step(step: dict[str, Any]) -> dict[str, Any]:
    """A queue step back in planner (STEP_SCHEMA) form."""
    return {k: step[k] for k in STEP_FIELDS if step.get(k) not in (None, [], False, "")}


# --- world handlers -----------------------------------------------------------

def _note(world, actor: dict, args: dict):
    """Notes live in village_notes; the journal row is what makes the write
    idempotent, and the world has nothing to change."""
    return True, None, {}


def _tomorrow(world, actor: dict, args: dict):
    steps = [plain_step(s) for s in args["steps"]][:LIMITS["tomorrow_steps"]]
    actor["tomorrow"] = {"steps": steps, "away_note": args.get("away_note"), "copied": False}
    world.emit("tomorrow_written", actor["id"], [], f"{actor['name']} wrote tomorrow's list",
               {"steps": len(steps)})
    return True, None, {"steps": len(steps)}


def _start_tomorrow(world, actor: dict, args: dict):
    """Session open (§6 "Tomorrow's list"): the hands start on it at once."""
    plan = actor.pop("tomorrow", None)
    steps = (plan or {}).get("steps") or []
    if not steps:
        return True, None, {"steps": 0}
    actor["queue"] = [s for s in actor["queue"] if s["state"] == "active"]
    actor["queue_version"] += 1
    cap = LIMITS["steps_per_edit"]
    for i in range(0, len(steps), cap):
        tasks.edit_queue(world, actor, {"base_version": actor["queue_version"],
                                        "ops": [{"op": "append", "steps": steps[i:i + cap]}]})
    return True, None, {"steps": len(steps)}


def _before_freeze(world) -> None:
    """T-0 fallback: a character whose final look-back wrote no list keeps its
    remaining queue as tomorrow's (test "Tomorrow's list exists before close")."""
    for aid in sorted(world.actors):
        a = world.actors[aid]
        if a["kind"] != "character" or "tomorrow" in a:
            continue
        steps = [plain_step(s) for s in a["queue"]
                 if s["state"] not in FINISHED and not s["id"].startswith("sq")]
        a["tomorrow"] = {"steps": steps[:LIMITS["tomorrow_steps"]], "away_note": None, "copied": True}
        world.emit("tomorrow_written", aid, [], f"{a['name']}'s list carries over to tomorrow",
                   {"steps": len(a["tomorrow"]["steps"]), "copied": True})


WORLD_HANDLERS: dict[str, Callable] = {
    "note": _note, "tomorrow": _tomorrow, "start_tomorrow": _start_tomorrow,
}


def install_world(world) -> None:
    for name, fn in WORLD_HANDLERS.items():
        world.register(name, fn)
    world.register("before_freeze", _before_freeze)


# --- planner.WorldView over the World ------------------------------------------

def _queue(a: dict) -> list[dict[str, Any]]:
    return [{k: s.get(k) for k in QUEUE_FIELDS if s.get(k) not in (None, [])} for s in a["queue"]]


class EngineView:
    """planner.WorldView. `get_world` returns the engine's current World (it is
    replaced on boot); every method is a read."""

    def __init__(self, get_world: Callable[[], Any]):
        self._world = get_world
        self._last: dict[str, dict[str, Any]] = {}

    def _actor(self, persona: str) -> dict | None:
        w = self._world()
        return None if w is None else w.actors.get(persona)

    def queue(self, persona: str) -> list[dict[str, Any]]:
        a = self._actor(persona)
        return _queue(a) if a else []

    def queue_version(self, persona: str) -> int:
        a = self._actor(persona)
        return a["queue_version"] if a else 0

    def queue_seconds(self, persona: str) -> float:
        a = self._actor(persona)
        return estimate_seconds(a["queue"]) if a else float("inf")

    def _state(self, persona: str) -> dict[str, Any]:
        w, a = self._world(), self._actor(persona)
        if a is None:
            return {"self": None}
        x, y = a["x"], a["y"]
        near = lambda ox, oy: cheb(x, y, ox, oy) <= VIEW_RADIUS   # noqa: E731
        people = [{"id": o["id"], "name": o["name"], "kind": o["kind"], "x": o["x"], "y": o["y"],
                   "doing": o["caption"]}
                  for oid, o in sorted(w.actors.items()) if oid != persona and near(o["x"], o["y"])]
        objects = [{"id": o["id"], "kind": o["kind"], "caption": o.get("caption"), "x": o["x"],
                    "y": o["y"], "ready": o["ready"],
                    "filled": sum(1 for s in o["slots"] if s["piece"]), "size": len(o["slots"])}
                   for _, o in sorted(w.objects.items()) if near(o["x"], o["y"])]
        loose: dict[str, int] = {}
        for p in w.pieces.values():
            if p["loc"].startswith("tile:") and near(*w.piece_xy(p)):
                loose[p["m"]] = loose.get(p["m"], 0) + 1
        sources: dict[str, list[dict[str, Any]]] = {}
        for s in sorted(w.sources.values(), key=lambda s: (cheb(x, y, s["x"], s["y"]), s["id"])):
            if s["amount"] > 0 and len(sources.setdefault(s["m"], [])) < SOURCES_PER_MATERIAL:
                sources[s["m"]].append({"id": s["id"], "x": s["x"], "y": s["y"], "left": s["amount"]})
        end = (w.session or {}).get("end_tick")
        return {
            "self": {"id": a["id"], "name": a["name"], "x": x, "y": y, "where": w.near_name(x, y),
                     "carrying": [w.pieces[pid]["m"] for pid in a["carry"] if pid in w.pieces],
                     "doing": a["caption"], "queue": _queue(a), "queue_version": a["queue_version"],
                     "goals": list(a["goals"]), "habits": list(a["habits"])},
            "nearby": {"people": people, "objects": objects, "loose": loose},
            "sources": sources,
            "village": {"raining": w.is_raining(), "closing": dict(w.closing),
                        "time_left_s": max(0, (end - w.tick) // TICK_HZ) if end else None},
        }

    def look(self, persona: str) -> dict[str, Any]:
        out = self._state(persona)
        out["places"] = sorted(LANDMARKS)
        self._last[persona] = out
        return out

    def changes(self, persona: str) -> dict[str, Any]:
        cur, prev = self._state(persona), self._last.get(persona) or {}
        self._last[persona] = {**cur, "places": prev.get("places")}
        out = {"self": cur["self"]}
        out.update({k: v for k, v in cur.items() if k != "self" and prev.get(k) != v})
        return out

    def is_near(self, persona: str, event: Event) -> bool:
        w, a = self._world(), self._actor(persona)
        if a is None or w is None:
            return False
        other = w.actors.get(event.actor or "")
        if other is not None:
            return cheb(a["x"], a["y"], other["x"], other["y"]) <= VIEW_RADIUS
        x, y = event.data.get("x"), event.data.get("y")
        return isinstance(x, int) and isinstance(y, int) and cheb(a["x"], a["y"], x, y) <= VIEW_RADIUS

    def private(self, persona: str) -> dict[str, Any]:
        """Phase 1 records no knowledge rows or reminders yet (U8)."""
        return {"knowledge": [], "reminders": []}


# --- the spectator shape -------------------------------------------------------

PUBLIC_STEPS = 5


def _public_actor(a: dict) -> dict[str, Any]:
    queue = [s for s in a.get("queue") or [] if s.get("state") not in FINISHED]
    blocked = next((s.get("blocked_reason") for s in queue if s.get("state") == "blocked"), None)
    return {"id": a["id"], "name": a.get("name"), "kind": a.get("kind"), "x": a["x"], "y": a["y"],
            "now": a.get("caption") or "", "say": a.get("say") or "",
            "steps": [{"op": s.get("op"), "target": s.get("target"), "say": s.get("say"),
                       "state": s.get("state"), "blocked_reason": s.get("blocked_reason")}
                      for s in queue[:PUBLIC_STEPS]],
            "blocked_reason": blocked, "planner": a.get("planner"),
            "thinking": a.get("planner") in ("thinking", "thinking_slowly"), "owner": a.get("owner")}


def public_view(state: dict[str, Any]) -> dict[str, Any]:
    """A World snapshot (`to_snapshot()`) as the Live page reads it. Only the
    fields named here leave; goals, habits, sections and notes never do."""
    actors = state.get("actors") or {}
    chars = [_public_actor(a) for _, a in sorted(actors.items()) if a.get("kind") == "character"]
    residents = [_public_actor(a) for _, a in sorted(actors.items()) if a.get("kind") != "character"]
    objects = [{"id": o["id"], "kind": o.get("kind"), "name": o.get("caption"), "x": o["x"], "y": o["y"],
                "ready": bool(o.get("ready")), "owner": o.get("owner"),
                "filled": sum(1 for s in o.get("slots") or [] if s.get("piece")),
                "size": len(o.get("slots") or [])}
               for _, o in sorted((state.get("objects") or {}).items())]
    sources = [{"id": s["id"], "kind": s.get("kind"), "material": s.get("m"), "x": s["x"], "y": s["y"],
                "amount": s.get("amount")}
               for _, s in sorted((state.get("sources") or {}).items())]
    projects = [{"id": p["id"], "owner": p.get("owner"), "caption": p.get("caption"),
                 "state": p.get("state"), "object_id": p.get("object_id")}
                for _, p in sorted((state.get("projects") or {}).items())]
    rain = state.get("rain") or {}
    return {"tick": state.get("tick"), "map": {"w": MAP_W, "h": MAP_H}, "characters": chars,
            "residents": residents, "objects": objects, "sources": sources, "projects": projects,
            "weather": {"raining": bool(rain.get("raining")), "wetness": rain.get("wetness", 0)},
            "places": {k: list(v) for k, v in sorted(LANDMARKS.items())}}
