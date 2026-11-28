"""Habits (plan §7.1): a closed `when`/`do` grammar the hands evaluate with no model.

`validate` checks a habit list against the contract schema. `choose` returns the
first habit whose `when` holds and whose `do` can be planned right now, as a
short list of ordinary steps (ids `h1`, `h2`, ...). Habits never touch
set-aside pieces (the hands run them with strict=True).
"""
from . import materials as M
from .contract import HABIT_DO, HABIT_SCHEMA, HABIT_WHEN, LIMITS, VIEW_RADIUS, check
from .mapdata import LANDMARKS, cheb, passable

PILE_RADIUS = 4
WORK_RADIUS = 3


def validate(habits) -> list[str]:
    """Plain-words problems with a habit list ([] when it is acceptable)."""
    if not isinstance(habits, list):
        return ["habits must be a list"]
    errs: list[str] = []
    if len(habits) > LIMITS["habits"]:
        errs.append(f"at most {LIMITS['habits']} habits")
    for i, h in enumerate(habits):
        errs += check(HABIT_SCHEMA, h, f"habits[{i}]")
        if isinstance(h, dict):
            for part, table in (("when", HABIT_WHEN), ("do", HABIT_DO)):
                kind = (h.get(part) or {}).get("kind") if isinstance(h.get(part), dict) else None
                if kind is not None and kind not in table:
                    errs.append(f"habits[{i}].{part}: kind must be one of {sorted(table)}")
    return errs


# --- conditions -----------------------------------------------------------------

def _near(a: dict, x: int, y: int, r: int) -> bool:
    return cheb(a["x"], a["y"], x, y) <= r


def _loose(w, a: dict, material: str | None = None, radius: int | None = None):
    """Loose, unclaimed, usable pieces (optionally in view of `a`), nearest first."""
    out = []
    for pid in sorted(w.pieces):
        p = w.pieces[pid]
        if not p["loc"].startswith("tile:") or p["aside"] or p["split"]:
            continue
        if material and p["m"] != material:
            continue
        x, y = w.piece_xy(p)
        if radius is not None and not _near(a, x, y, radius):
            continue
        out.append((cheb(a["x"], a["y"], x, y), pid, x, y))
    out.sort()
    return out


def pile_count(w, material: str) -> int:
    px, py = LANDMARKS["pile"]
    return sum(1 for pid, p in w.pieces.items()
               if p["m"] == material and p["loc"].startswith("tile:") and not p["aside"]
               and cheb(*w.piece_xy(p), px, py) <= PILE_RADIUS)


def _work_sites(w) -> list[tuple[int, int]]:
    sites = [(o["x"], o["y"]) for o in w.objects.values() if not o["ready"]]
    for other in w.actors.values():
        act = other.get("act")
        if act and act.get("op") in ("place", "repair", "dismantle"):
            sites.append((other["x"], other["y"]))
    return sites


def _hammering(w, a: dict):
    return [o for o in sorted(w.actors.values(), key=lambda o: o["id"])
            if o["id"] != a["id"] and o.get("act") and o["act"].get("op") in ("place", "repair", "dismantle")
            and _near(a, o["x"], o["y"], WORK_RADIUS + 1)]


def _broken(w, a: dict, r: int):
    out = []
    for oid in sorted(w.objects):
        o = w.objects[oid]
        if o["wear"] >= M.WEAR_BROKEN and _near(a, o["x"], o["y"], r):
            out.append((cheb(a["x"], a["y"], o["x"], o["y"]), oid))
    out.sort()
    return out


def _ripe(w, a: dict):
    return sorted((cheb(a["x"], a["y"], o["x"], o["y"]), oid) for oid, o in w.objects.items()
                  if o["kind"] == "garden_bed" and (o.get("crop") or {}).get("ripe_at", 1 << 60) <= w.tick)


def when_holds(w, a: dict, when: dict) -> bool:
    k = when["kind"]
    if k == "pile_below":
        return pile_count(w, when["material"]) < when["n"]
    if k == "loose_near_work":
        sites = _work_sites(w)
        px, py = LANDMARKS["pile"]
        return any(cheb(x, y, sx, sy) <= WORK_RADIUS and cheb(x, y, px, py) > 2
                   for _, _, x, y in _loose(w, a, when["material"], VIEW_RADIUS) for sx, sy in sites)
    if k == "hammering_nearby":
        return bool(_hammering(w, a))
    if k == "raining":
        return w.is_raining()
    if k == "ripe_garden":
        return bool(_ripe(w, a))
    if k == "broken_object_within":
        return bool(_broken(w, a, when["r"]))
    if k == "idle":
        return True
    if k == "time_left_under":
        end = w.session.get("end_tick")
        return end is not None and (end - w.tick) < when["min"] * 60 * 4
    return False


# --- actions --------------------------------------------------------------------

def _steps(a: dict, raw: list[dict]) -> list[dict]:
    out = []
    for r in raw:
        a["next_hid"] += 1
        out.append({"op": r["op"], "id": f"h{a['next_hid']}", "target": r.get("target"),
                    "say": r.get("say"), "deps": [], "needs": None, "big": False,
                    "guess": None, "decor": [], "state": "queued", "blocked_reason": None,
                    "blocked_since": None})
    return out


def plan(w, a: dict, do: dict) -> list[dict] | None:
    k = do["kind"]
    px, py = LANDMARKS["pile"]
    if k == "fetch":
        dest = w.locate(do["to"], a)
        if dest is None:
            return None
        best = None
        for sid in sorted(w.sources):
            src = w.sources[sid]
            if src["m"] == do["material"] and src["amount"] > 0:
                key = (cheb(a["x"], a["y"], src["x"], src["y"]), sid)
                best = key if best is None or key < best else best
        for d, pid, x, y in _loose(w, a, do["material"]):
            if cheb(x, y, dest["x"], dest["y"]) > 2:          # never re-fetch what is already there
                best = (d, pid) if best is None or (d, pid) < best else best
                break
        if best is None:
            return None
        return [{"op": "pickup", "target": best[1]}, {"op": "carry_to", "target": do["to"]},
                {"op": "drop"}]
    if k == "stack_loose":
        for _, pid, x, y in _loose(w, a, None, VIEW_RADIUS):
            if cheb(x, y, px, py) > 2:
                return [{"op": "pickup", "target": pid}, {"op": "carry_to", "target": "pile"},
                        {"op": "drop"}]
        return None
    if k == "move_away":
        near = _hammering(w, a)
        if not near:
            return None
        o = near[0]
        dx = 1 if a["x"] >= o["x"] else -1
        dy = 1 if a["y"] >= o["y"] else -1
        for d in (6, 5, 4):
            for tx, ty in ((a["x"] + dx * d, a["y"] + dy * d), (a["x"] + dx * d, a["y"]),
                           (a["x"], a["y"] + dy * d)):
                if passable(tx, ty):
                    return [{"op": "walk", "target": f"tile:{tx},{ty}"}]
        return None
    if k == "repair_nearest":
        best = None
        for oid in sorted(w.objects):
            o = w.objects[oid]
            if o["wear"] > 0 and not o.get("moving") and (
                    not o.get("repairer") or o["repairer"] == a["id"] or not w.is_repairing(o["repairer"], oid)):
                key = (-(o["wear"] >= M.WEAR_BROKEN), cheb(a["x"], a["y"], o["x"], o["y"]), oid)
                if best is None or key < best:
                    best = key
        return None if best is None else [{"op": "repair", "target": best[2]}]
    if k == "harvest":
        ripe = _ripe(w, a)
        return [{"op": "harvest", "target": ripe[0][1]}] if ripe else None
    if k == "walk_path":
        return [{"op": "walk", "target": do["a"]}, {"op": "walk", "target": do["b"]}]
    if k == "rest_at":
        return [{"op": "rest", "target": do["place"]}]
    if k == "carry_tools_to_broken":
        broken = _broken(w, a, 40)
        if not broken:
            return None
        return [{"op": "walk", "target": "workshop"}, {"op": "carry_to", "target": broken[0][1]}]
    return None


def describe(do: dict) -> str:
    k = do["kind"]
    return {
        "fetch": f"fetching {do.get('material', '')} to {do.get('to', '')}",
        "stack_loose": "stacking loose pieces",
        "move_away": "moving out of the way",
        "repair_nearest": "repairing the nearest damage",
        "harvest": "harvesting the garden",
        "walk_path": f"walking from {do.get('a', '')} to {do.get('b', '')}",
        "rest_at": f"resting at {do.get('place', '')}",
        "carry_tools_to_broken": "carrying tools to something broken",
    }[k]


def choose(w, a: dict):
    """(habit index, steps) for the first habit that applies now, else None."""
    for i, h in enumerate(a["habits"]):
        if when_holds(w, a, h["when"]):
            raw = plan(w, a, h["do"])
            if raw:
                return i, _steps(a, raw)
    return None
