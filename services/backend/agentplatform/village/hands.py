"""The hands (plan §3): what one step does, one fixed tick at a time.

Pure code driven by `world.World`. No I/O, no awaits, no model. `precheck` says
whether a step can run right now (and, if not, the plain-words reason); `run`
advances the actor's active step by one tick and returns a status:

    None                       still working
    "done"                     finished
    ("blocked", reason, who)   can't continue right now (retried later)
    ("failed", reason)         will never work

`act` is the actor's persistent scratch dict (`actor["act"]`): every field is
JSON so a snapshot taken mid-step resumes exactly.
"""
from . import materials as M
from . import rules
from .contract import LIMITS
from .mapdata import FARBANK, STREAM_ROW, cheb, find_path, passable, terrain_at


# --- small helpers -----------------------------------------------------------

def cap(a: dict) -> int:
    return LIMITS["cart_carry"] if a["cart"] else LIMITS["carry"]


def carried(w, a: dict, material: str) -> list[str]:
    return [p for p in a["carry"] if w.pieces[p]["m"] == material]


def speed(a: dict) -> int:
    if a["cart"]:
        return M.SPEED["cart"]
    if a["carry"] or a.get("carry_obj"):
        return M.SPEED["carry"]
    return M.SPEED["walk"]


def _timer(act: dict, name: str, ticks: int) -> bool:
    """Count `ticks` down under `name`; True on the tick it reaches zero."""
    if act.get("timer") != name:
        act["timer"], act["left"] = name, ticks
    act["left"] -= 1
    if act["left"] <= 0:
        act["timer"] = None
        return True
    return False


def _blocked(reason: str, who: str | None = None):
    return ("blocked", reason, who)


def _failed(reason: str):
    return ("failed", reason)


def goto(w, a: dict, goal, stop: int = 0, chosen: bool = False) -> str:
    """Advance toward `goal` by this tick's movement. 'arrived' | 'moving' | 'unreachable'."""
    gx, gy = goal
    if stop == 0 and not passable(gx, gy):
        stop = 2          # an impassable landmark (the pond): get close
    if cheb(a["x"], a["y"], gx, gy) <= stop:
        a["path"], a["pgoal"], a["acc"] = [], None, 0
        return "arrived"
    cart = a["cart"] is not None
    key = [gx, gy, stop, int(cart)]
    if a["pgoal"] != key or not a["path"]:
        path = find_path((a["x"], a["y"]), (gx, gy), stop, w.bridge, w.paths, cart)
        if path is None:
            a["path"], a["pgoal"] = [], None
            return "unreachable"
        a["path"], a["pgoal"] = [list(p) for p in path], key
    a["acc"] += speed(a) * 250 // 1000
    while a["acc"] >= 1000 and a["path"]:
        nx, ny = a["path"].pop(0)
        a["x"], a["y"] = nx, ny
        a["acc"] -= 1000
        if chosen:
            w.wear_tile(nx, ny)
    if not a["path"]:
        a["acc"] = 0
    return "arrived" if cheb(a["x"], a["y"], gx, gy) <= stop else "moving"


def _unreachable(w, a: dict, goal):
    gx, gy = goal
    if a["cart"] and passable(gx, gy, w.bridge, w.paths):
        return _blocked("the cart can't leave the stone paths")
    if terrain_at(gx, gy, w.bridge, w.paths) == FARBANK or gy <= STREAM_ROW:
        return _blocked("can't cross the stream yet; it needs a footbridge")
    return _blocked("there's no way to get there")


def _reach(w, a, act, goal, stop=0, chosen=False):
    """('arrived', None) | ('moving', None) | ('stop', status)."""
    r = goto(w, a, goal, stop, chosen)
    if r == "unreachable":
        return "stop", _unreachable(w, a, goal)
    return r, None


def _aside_reason(w, p: dict) -> str:
    return f"{w.name(p['aside'])} set that aside"


def _piece_ok_for(w, a: dict, p: dict, strict: bool) -> bool:
    if p["split"] or (strict and p["aside"]):
        return False
    return w.can_take(a, p)


def _hammer_ok(w) -> bool:
    return not w.tools["hammer"]["broken"]


def _slot_for(w, obj: dict, a: dict):
    """(slot index, piece id) for the first carried piece that fits an open slot."""
    for pid in a["carry"]:
        p = w.pieces[pid]
        if p["split"]:
            continue
        for i, s in enumerate(obj["slots"]):
            if s["piece"] is None and s["m"] == p["m"]:
                return i, pid
    return None


def _open_materials(obj: dict) -> list[str]:
    return [s["m"] for s in obj["slots"] if s["piece"] is None]


def _site(w, step: dict):
    t = step.get("target")
    return w.locate(t) if t else None


# What each step op wants, in the planner's words. Shown by `help`/`look` and
# appended to refusals so a step that can't run says how to write it.
ARG_HELP = {
    "walk": "target: a place name (square, dock, pond...), 'tile:x,y', a character or an object id",
    "pickup": "target: a material (wood, reeds, stone, seeds, clay), a source id or a loose piece id; hands must have room",
    "carry_to": "target: where to bring what you carry (place, character, object id), or '<object id>><place>' to move an object",
    "drop": "optional target: where to put down everything you carry (default: here)",
    "place": "target: the object id (or project id) being built, with a matching piece carried; or 'tile:x,y' with stones for a path",
    "dismantle": "target: an object id",
    "repair": "target: an object id, or tool:hammer / tool:saw",
    "use": "target: a finished object id",
    "visit": "target: a place, character or object id",
    "rest": "optional target: a place to rest at",
    "look": "no arguments",
    "say": "say: the line to speak",
    "wait_for": "target: a character id",
    "plant": "target: the id of a finished garden bed (a project built with function 'grow'), with 1 seed carried",
    "harvest": "target: the id of a garden bed with a ripe crop",
    "set_aside": "target: a material or piece id; needs.n is how many",
    "keepsake": "target: an object id, say: the sentence to leave on it",
}


def _arg_error(op: str, what: str) -> str:
    return f"{what}; {op} wants {ARG_HELP[op]}"


def garden_for(w, a: dict, t: str | None) -> dict | None:
    """The garden bed a plant/harvest step means: the object itself, or the nearest
    bed to a place/tile/character it names (or to the actor when it names nothing)."""
    obj = w.object_for(t)
    if obj is not None:
        return obj if obj["function"] == "grow" else None
    loc = w.locate(t, a) if t else {"x": a["x"], "y": a["y"]}
    if loc is None:
        return None
    beds = sorted((o for o in w.objects.values() if o["function"] == "grow"),
                  key=lambda o: (cheb(loc["x"], loc["y"], o["x"], o["y"]), not o["ready"], o["id"]))
    return beds[0] if beds and cheb(loc["x"], loc["y"], beds[0]["x"], beds[0]["y"]) <= 3 else None


# --- precheck ----------------------------------------------------------------
# (ok, reason, who, fatal)

def precheck(w, a: dict, step: dict, strict: bool = False):
    op, t = step["op"], step.get("target")
    ok = (True, None, None, False)
    needs = step.get("needs")
    if needs and op not in ("pickup", "set_aside"):
        have = len(carried(w, a, needs["material"]))
        if have < needs.get("n", 1):
            return False, f"waiting for {needs.get('n', 1)} {needs['material']}", None, False
    if op in ("walk", "visit", "carry_to") and not (t and w.locate(t, a)) and not (
            op == "carry_to" and t and ">" in t):
        return False, _arg_error(op, f"don't know where '{t}' is"), None, True
    if op == "pickup":
        return _pre_pickup(w, a, step, strict)
    if op == "drop":
        if not a["carry"]:
            return False, "carrying nothing to drop", None, False
        if t and not w.locate(t, a):
            return False, _arg_error(op, f"don't know where '{t}' is"), None, True
        return ok
    if op == "carry_to":
        if t and ">" in t:
            return _pre_move(w, a, step)
        if not a["carry"] and not a["cart"]:
            return False, "carrying nothing", None, False
        return ok
    if op == "place":
        return _pre_place(w, a, step)
    if op == "dismantle":
        obj = w.object_for(t)
        if obj is None:
            return False, _arg_error(op, f"no object '{t}'"), None, True
        if w.tools["saw"]["broken"]:
            return False, "the saw is broken, waiting for a repair", None, False
        return ok
    if op == "repair":
        return _pre_repair(w, a, step)
    if op == "use":
        return _pre_use(w, a, step)
    if op == "wait_for":
        other = w.actors.get((t or "").lower())
        if other is None:
            return False, _arg_error("wait_for", f"nobody called '{t}'"), None, True
        if cheb(a["x"], a["y"], other["x"], other["y"]) > 2:
            return False, f"waiting for {other['name']}", other["id"], False
        return ok
    if op == "plant":
        obj = garden_for(w, a, t)
        if obj is None:
            return False, _arg_error("plant", f"'{t}' is not a garden bed"), None, True
        if not obj["ready"]:
            return False, f"the garden bed {obj['id']} isn't finished yet", None, False
        if obj.get("crop"):
            return False, f"something is already growing in {obj['id']}", None, False
        if not carried(w, a, "seeds"):
            return False, "waiting for seeds", None, False
        return ok
    if op == "harvest":
        obj = garden_for(w, a, t)
        if obj is None:
            return False, _arg_error("harvest", f"'{t}' is not a garden bed"), None, True
        crop = obj.get("crop")
        if not crop or crop["ripe_at"] > w.tick:
            return False, "nothing is ripe yet", None, False
        return ok
    if op == "keepsake":
        if w.object_for(t) is None:
            return False, _arg_error(op, f"no object '{t}'"), None, True
        if not step.get("say"):
            return False, _arg_error("keepsake", "a keepsake needs a sentence"), None, True
        return ok
    if op == "set_aside":
        return ok
    if op == "say":
        if not (step.get("say") or t):
            return False, "nothing to say", None, True
    return ok


def _pre_pickup(w, a, step, strict):
    t = step.get("target")
    if not t:
        return False, _arg_error("pickup", "pickup has no target"), None, True
    if len(a["carry"]) >= cap(a) or a.get("carry_obj"):
        return False, f"hands full (carrying {len(a['carry'])})", None, False
    if t in w.sources:
        s = w.sources[t]
        if s["amount"] <= 0:
            return False, f"no {s['m']} left at {t}, waiting for it to regrow", None, False
        return True, None, None, False
    if t in w.pieces or t.startswith("piece:"):
        p = w.pieces.get(t.split(":")[-1])
        if p is None or not p["loc"].startswith("tile:"):
            return False, "that piece is gone", None, False
        if not _piece_ok_for(w, a, p, strict):
            return False, _aside_reason(w, p) if p["aside"] else "that piece is no use", None, False
        return True, None, None, False
    if t in M.MATERIAL_SET:
        cand, why = w.pick_candidate(a, t, strict)
        if cand is None:
            return False, why, None, False
        return True, None, None, False
    return False, _arg_error("pickup", f"unknown material or source '{t}'"), None, True


def _pre_place(w, a, step):
    t = step.get("target")
    if w.tools_blocked("hammer"):
        return False, "the hammer is broken, waiting for a repair", None, False
    if t and t.startswith("tile:"):
        loc = w.locate(t, a)
        if loc is None:
            return False, _arg_error("place", f"don't know where '{t}' is"), None, True
        if w.is_raining() and not w.covered(loc["x"], loc["y"]):
            return False, "paused for rain", None, False
        if not carried(w, a, "stone") and not w.stones_at(loc["x"], loc["y"]):
            return False, "waiting for stone", None, False
        return True, None, None, False
    obj = w.object_for(t)
    if obj is None or obj["ready"] or obj.get("moving"):
        return False, f"nothing to build at '{t}'", None, True
    if w.is_raining() and not w.covered(obj["x"], obj["y"]):
        return False, "paused for rain", None, False
    if _slot_for(w, obj, a) is None:
        need = sorted(set(_open_materials(obj)))
        return False, "waiting for " + (" and ".join(need) if need else "pieces"), None, False
    return True, None, None, False


def _pre_repair(w, a, step):
    t = step.get("target")
    tool = w.tool_for(t)
    if tool is not None:
        if tool["wear"] <= 0 and not tool["broken"]:
            return False, "nothing to repair", None, True
        return True, None, None, False
    obj = w.object_for(t)
    if obj is None:
        return False, _arg_error("repair", f"no object '{t}'"), None, True
    if obj["wear"] <= 0:
        return False, "nothing to repair", None, True
    if w.tools_blocked("hammer"):
        return False, "the hammer is broken, waiting for a repair", None, False
    other = obj.get("repairer")
    if other and other != a["id"] and w.is_repairing(other, obj["id"]):
        return False, f"{w.name(other)} is already repairing that", other, False
    if w.is_raining() and not w.covered(obj["x"], obj["y"]):
        return False, "paused for rain", None, False
    return True, None, None, False


def _pre_use(w, a, step):
    obj = w.object_for(step.get("target"))
    if obj is None:
        return False, _arg_error("use", f"no object '{step.get('target')}'"), None, True
    if not w.usable(obj):
        return False, "it isn't finished yet", None, False
    if obj["wear"] >= M.WEAR_BROKEN:
        return False, "it's broken and needs a repair", None, False
    if obj["kind"] == "drying_rack":
        if not carried(w, a, "reeds"):
            return False, "waiting for reeds", None, False
        if obj["rack"]["pieces"]:
            return False, "the rack is already drying a load", None, False
    return True, None, None, False


def _pre_move(w, a, step):
    oid, _, place = step["target"].partition(">")
    obj = w.object_for(oid)
    if obj is None:
        return False, _arg_error("carry_to", f"no object '{oid}'"), None, True
    if w.locate(place, a) is None:
        return False, f"don't know where '{place}' is", None, True
    if obj.get("moving"):
        if obj.get("mover") not in (None, a["id"]):
            return False, f"{w.name(obj['mover'])} is already moving that", obj["mover"], False
        return True, None, None, False
    if not w.usable(obj) and obj["carried_by"] is None:
        return False, "it isn't finished yet", None, False
    if obj["carried_by"] not in (None, a["id"]):
        return False, f"{w.name(obj['carried_by'])} is already carrying that", None, False
    if a["carry"] or a["cart"]:
        return False, "hands full", None, False
    if len(obj["slots"]) > 4 and w.tools["saw"]["broken"]:
        return False, "the saw is broken, waiting for a repair", None, False
    return True, None, None, False


# --- run ---------------------------------------------------------------------

def run(w, a: dict, step: dict, strict: bool = False):
    act = a["act"]
    fn = _OPS.get(step["op"])
    if fn is None:
        return _failed(f"unknown op {step['op']}")
    return fn(w, a, step, act, strict)


def _op_walk(w, a, step, act, strict):
    loc = w.locate(step["target"], a)
    if loc is None:
        return _failed(f"don't know where '{step['target']}' is")
    r, bad = _reach(w, a, act, (loc["x"], loc["y"]), loc["stop"], chosen=True)
    if bad:
        return bad
    return "done" if r == "arrived" else None


def _op_visit(w, a, step, act, strict):
    loc = w.locate(step["target"], a)
    if loc is None:
        return _failed(f"don't know where '{step['target']}' is")
    if not act.get("there"):
        r, bad = _reach(w, a, act, (loc["x"], loc["y"]), loc["stop"])
        if bad:
            return bad
        if r != "arrived":
            return None
        act["there"] = True
        w.emit("visit_started", a["id"], [step["target"]], f"{a['name']} is visiting {step['target']}")
    if _timer(act, "linger", M.WORK["visit"]):
        w.emit("visit_ended", a["id"], [step["target"]], f"{a['name']} finished visiting")
        return "done"
    return None


def _op_rest(w, a, step, act, strict):
    t = step.get("target")
    if t and not act.get("there"):
        loc = w.locate(t, a)
        if loc is None:
            return _failed(f"don't know where '{t}' is")
        r, bad = _reach(w, a, act, (loc["x"], loc["y"]), loc["stop"])
        if bad:
            return bad
        if r != "arrived":
            return None
    act["there"] = True
    return "done" if _timer(act, "rest", M.WORK["rest"]) else None


def _op_look(w, a, step, act, strict):
    return "done" if _timer(act, "look", M.WORK["look"]) else None


def _op_say(w, a, step, act, strict):
    line = step.get("say") or step.get("target") or ""
    w.emit("say", a["id"], [], line[:LIMITS["say"]], {"line": line})
    return "done"


def _op_wait_for(w, a, step, act, strict):
    return "done"


def _op_set_aside(w, a, step, act, strict):
    n = (step.get("needs") or {}).get("n")
    t = step.get("target") or ""
    w.set_aside(a, t if t in M.MATERIAL_SET else None, n, [t] if t in w.pieces else None)
    return "done"


def _op_keepsake(w, a, step, act, strict):
    if not _timer(act, "keep", M.WORK["keepsake"]):
        return None
    w.add_keepsake(a, w.object_for(step["target"]), step["say"])
    return "done"


def _op_pickup(w, a, step, act, strict):
    t = step["target"]
    if "cand" not in act:
        if t in w.sources:
            cand = ["source", t]
        elif t in M.MATERIAL_SET:
            found, why = w.pick_candidate(a, t, strict)
            if found is None:
                return _blocked(why)
            cand = found
        else:
            cand = ["piece", t.split(":")[-1]]
        act["cand"] = cand
    kind, cid = act["cand"]
    if kind == "source":
        src = w.sources[cid]
        gx, gy = src["x"], src["y"]
    else:
        p = w.pieces.get(cid)
        if p is None or not p["loc"].startswith("tile:"):
            act.pop("cand")
            return _blocked("that piece is gone")
        gx, gy = w.piece_xy(p)
    r, bad = _reach(w, a, act, (gx, gy), 0)
    if bad:
        return bad
    if r != "arrived":
        return None
    if not _timer(act, "pick", M.WORK["pickup"]):
        return None
    cand = act.pop("cand")
    if len(a["carry"]) >= cap(a):
        return _blocked(f"hands full (carrying {len(a['carry'])})")
    if kind == "source":
        src = w.sources[cid]
        if src["amount"] <= 0:
            return _blocked(w.taken_reason(src))
        w.take_from_source(a, src)
    else:
        p = w.pieces.get(cid)
        if p is None or not p["loc"].startswith("tile:") or w.piece_xy(p) != (a["x"], a["y"]):
            return _blocked("that piece was taken")
        if not _piece_ok_for(w, a, p, strict):
            return _blocked(_aside_reason(w, p) if p["aside"] else "that piece is no use")
        w.take_piece(a, p)
    return "done"


def _op_drop(w, a, step, act, strict):
    t = step.get("target")
    if t:
        loc = w.locate(t, a)
        if loc is None:
            return _failed(f"don't know where '{t}' is")
        r, bad = _reach(w, a, act, (loc["x"], loc["y"]), min(loc["stop"], 1) if loc["kind"] != "tile" else 0)
        if bad:
            return bad
        if r != "arrived":
            return None
    if not _timer(act, "drop", M.WORK["drop"]):
        return None
    w.drop_all(a)
    return "done"


def _op_carry_to(w, a, step, act, strict):
    t = step["target"]
    if ">" in t:
        return _move_object(w, a, step, act)
    loc = w.locate(t, a)
    if loc is None:
        return _failed(f"don't know where '{t}' is")
    r, bad = _reach(w, a, act, (loc["x"], loc["y"]), loc["stop"])
    if bad:
        return bad
    return "done" if r == "arrived" else None


def _op_place(w, a, step, act, strict):
    t = step.get("target")
    if t and t.startswith("tile:"):
        return _place_path(w, a, step, act)
    obj = w.object_for(t)
    if obj is None or obj["ready"]:
        return _failed(f"nothing to build at '{t}'")
    r, bad = _reach(w, a, act, (obj["x"], obj["y"]), 1)
    if bad:
        return bad
    if r != "arrived":
        return None
    if not _timer(act, "place", M.WORK["place"]):
        return None
    fit = _slot_for(w, obj, a)
    if fit is None:
        return _blocked("waiting for " + " and ".join(sorted(set(_open_materials(obj))) or ["pieces"]))
    idx, pid = fit
    p = w.pieces[pid]
    out = rules.evaluate("place_piece", {"material": p["m"], "wet": p["wet"]})
    w.wear_tool("hammer")
    if out is not None and not out.ok:
        p["split"] = True
        w.move_piece(pid, f"tile:{a['x']},{a['y']}")
        w.resolve_outcome(a, step, out, [obj["id"]])
        return _failed(out.text)
    w.move_piece(pid, f"slot:{obj['id']}:{idx}")
    w.emit("piece_placed", a["id"], [obj["id"], pid],
           f"{a['name']} fitted {p['m']} into {obj['caption'] or obj['kind']}",
           {"object": obj["id"], "slot": idx, "material": p["m"]})
    if w.is_complete(obj):
        w.finish_object(a, obj, step)
    w.resolve_outcome(a, step, None, [obj["id"]])
    return "done"


def _place_path(w, a, step, act, ):
    loc = w.locate(step["target"], a)
    r, bad = _reach(w, a, act, (loc["x"], loc["y"]), 0)
    if bad:
        return bad
    if r != "arrived":
        return None
    if not _timer(act, "place", M.WORK["place"]):
        return None
    for pid in carried(w, a, "stone"):
        w.move_piece(pid, f"tile:{a['x']},{a['y']}")
    stones = w.stones_at(a["x"], a["y"])
    out = rules.evaluate("path_tile", {"stones": len(stones)})
    w.wear_tool("hammer")
    if out.ok:
        for pid in stones[:rules.STONES_PER_PATH_TILE]:
            w.destroy_piece(pid)
        w.build_path(a["x"], a["y"])
        w.resolve_outcome(a, step, out, [])
        return "done"
    w.resolve_outcome(a, step, out, [])
    return _failed(out.text)


def _op_dismantle(w, a, step, act, strict):
    obj = w.object_for(step["target"])
    if obj is None:
        return _failed("it's already gone")
    r, bad = _reach(w, a, act, (obj["x"], obj["y"]), 1)
    if bad:
        return bad
    if r != "arrived":
        return None
    if not _timer(act, "dismantle", M.WORK["dismantle"]):
        return None
    w.wear_tool("saw")
    w.dismantle_object(a, obj)
    return "done"


def _op_repair(w, a, step, act, strict):
    t = step.get("target")
    tool = w.tool_for(t)
    if tool is not None:
        gx, gy = w.workshop
        stop = 1
    else:
        obj = w.object_for(t)
        if obj is None:
            return _failed("it's already gone")
        gx, gy, stop = obj["x"], obj["y"], 1
        obj["repairer"] = a["id"]
    r, bad = _reach(w, a, act, (gx, gy), stop)
    if bad:
        return bad
    if r != "arrived":
        return None
    if not _timer(act, "repair", M.WORK["repair"]):
        return None
    if tool is not None:
        w.repair_tool(a, tool)
    else:
        w.wear_tool("hammer")
        w.repair_object(a, obj)
    return "done"


def _op_use(w, a, step, act, strict):
    obj = w.object_for(step["target"])
    if obj is None:
        return _failed("it's already gone")
    r, bad = _reach(w, a, act, (obj["x"], obj["y"]), 1)
    if bad:
        return bad
    if r != "arrived":
        return None
    if not _timer(act, "use", M.WORK["use"]):
        return None
    ok, why = w.use_object(a, obj, step)
    return "done" if ok else _failed(why)


def _op_plant(w, a, step, act, strict):
    obj = garden_for(w, a, step.get("target"))
    if obj is None:
        return _failed(_arg_error("plant", f"'{step.get('target')}' is not a garden bed"))
    r, bad = _reach(w, a, act, (obj["x"], obj["y"]), 1)
    if bad:
        return bad
    if r != "arrived":
        return None
    if not _timer(act, "plant", M.WORK["plant"]):
        return None
    seed = carried(w, a, "seeds")
    if not seed or obj.get("crop"):
        return _blocked("waiting for seeds")
    w.destroy_piece(seed[0])
    obj["crop"] = {"ripe_at": w.tick + M.GARDEN_RIPE_TICKS, "announced": False}
    return "done"


def _op_harvest(w, a, step, act, strict):
    obj = garden_for(w, a, step.get("target"))
    if obj is None:
        return _failed(_arg_error("harvest", f"'{step.get('target')}' is not a garden bed"))
    r, bad = _reach(w, a, act, (obj["x"], obj["y"]), 1)
    if bad:
        return bad
    if r != "arrived":
        return None
    if not _timer(act, "harvest", M.WORK["harvest"]):
        return None
    crop = obj.get("crop")
    if not crop or crop["ripe_at"] > w.tick:
        return _blocked("nothing is ripe yet")
    obj["crop"] = None
    for _ in range(2):
        w.new_piece("seeds", a["x"], a["y"])
    w.emit("shared_meal", a["id"], [obj["id"]], f"{a['name']} harvested the garden",
           {"object": obj["id"]})
    return "done"


# --- moving an object (carry_to "<object>><place>") ---------------------------

def _move_object(w, a, step, act):
    oid, _, place = step["target"].partition(">")
    obj = w.object_for(oid)
    dest = w.locate(place, a)
    if obj is None or dest is None:
        return _failed("can't move that")
    dx, dy = dest["x"], dest["y"]
    if "mv" not in act:
        act["mv"] = "haul_pick" if obj.get("moving") else "goto"
    ph = act["mv"]
    if ph == "goto":
        r, bad = _reach(w, a, act, (obj["x"], obj["y"]), 1)
        if bad:
            return bad
        if r != "arrived":
            return None
        if len(obj["slots"]) <= LIMITS["carry"] * 2:
            obj["carried_by"], a["carry_obj"] = a["id"], obj["id"]
            act["mv"] = "carry"
        else:
            act["mv"] = "disassemble"
        return None
    if ph == "carry":
        r, bad = _reach(w, a, act, (dx, dy), 0)
        if bad:
            return bad
        if r != "arrived":
            return None
        obj["x"], obj["y"], obj["carried_by"], a["carry_obj"] = a["x"], a["y"], None, None
        w.moved_object(a, obj, place)
        return "done"
    if ph == "disassemble":
        if not _timer(act, "disassemble", M.WORK["disassemble"]):
            return None
        w.wear_tool("saw")
        w.start_haul(a, obj, dx, dy)
        act["mv"] = "haul_pick"
        return None
    if ph == "haul_pick":
        pend = [p for p in obj["pending"] if w.pieces[p]["loc"].startswith("tile:")]
        if not pend:
            act["mv"] = "haul_place" if a["carry"] else "haul_done"
            return None
        ox, oy = w.piece_xy(w.pieces[pend[0]])
        r, bad = _reach(w, a, act, (ox, oy), 0)
        if bad:
            return bad
        if r != "arrived":
            return None
        if not _timer(act, "pick", M.WORK["pickup"]):
            return None
        for pid in pend:
            if len(a["carry"]) >= cap(a):
                break
            if w.pieces[pid]["loc"] == f"tile:{a['x']},{a['y']}":
                w.move_piece(pid, f"carry:{a['id']}")
        act["mv"] = "haul_to"
        return None
    if ph == "haul_to":
        r, bad = _reach(w, a, act, (dx, dy), 0)
        if bad:
            return bad
        if r != "arrived":
            return None
        act["mv"] = "haul_place"
        return None
    if ph == "haul_place":
        if not _timer(act, "place", M.WORK["place"]):
            return None
        w.haul_place(a, obj)
        act["mv"] = "haul_pick" if obj["pending"] else "haul_done"
        return None
    # haul_done
    w.finish_haul(a, obj, place)
    return "done"


_OPS = {
    "walk": _op_walk, "pickup": _op_pickup, "carry_to": _op_carry_to, "drop": _op_drop,
    "place": _op_place, "dismantle": _op_dismantle, "repair": _op_repair, "use": _op_use,
    "visit": _op_visit, "rest": _op_rest, "look": _op_look, "say": _op_say,
    "wait_for": _op_wait_for, "plant": _op_plant, "harvest": _op_harvest,
    "set_aside": _op_set_aside, "keepsake": _op_keepsake,
}
