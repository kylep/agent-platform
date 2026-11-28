"""The to-do list (plan §3 "Picking the next step"): queue edits, step state, and
the per-actor tick that chooses what the hands do next.

Order of preference each time an actor is free:
  1. the first queued step whose deps are done and whose needs hold
     (blocked steps before it keep their reason and are skipped over)
  2. the first habit whose `when` holds
  3. rest, or walk to the character being waited on
"""
from . import habits as H
from . import hands
from .contract import (LIMITS, MATERIALS, STEP_OPS, TICK_HZ, CommandResult, Step)
from .materials import MATERIAL_SET, secs

RETRY_TICKS = secs(5)
QUEUE_CAP = 60
FINISHED_CAP = 200
_BIG_REFUSAL = "the last minutes are for finishing up; no new big jobs"


def describe_step(s: dict) -> str:
    if s.get("say"):
        return s["say"]
    t = s.get("target")
    return f"{s['op']} {t}" if t else s["op"]


# --- state transitions ------------------------------------------------------------

def _set_state(w, a: dict, s: dict, state: str, reason: str | None = None, who: str | None = None,
               quiet: bool = False):
    if state == "blocked":
        # A step that was retried and blocked again within moments is the same
        # blockage: keep its clock (so it can still give up) and stay quiet.
        rec = a["since"].pop(s["id"], None)
        reuse = rec is not None and w.tick - rec[1] <= secs(2)
        new = not (reuse and rec[2] == reason) and (s["state"] != "blocked" or s["blocked_reason"] != reason)
        if s["state"] != "blocked":
            s["blocked_since"] = rec[0] if reuse else w.tick
        s["state"], s["blocked_reason"] = "blocked", reason
        if who:
            a["wait_on"][s["id"]] = who
        else:
            a["wait_on"].pop(s["id"], None)
        if new:
            w.emit("step_blocked", a["id"], [s["id"]], f"{a['name']} is blocked: {reason}",
                   {"step": s["id"], "reason": reason, "waiting_on": who})
            w.log_block(a, s, reason)
        return
    was_blocked = s["state"] == "blocked"
    s["state"], s["blocked_reason"], s["blocked_since"] = state, None, None
    a["wait_on"].pop(s["id"], None)
    if was_blocked and state in ("queued", "active") and not quiet:
        w.emit("step_unblocked", a["id"], [s["id"]], f"{a['name']} can carry on", {"step": s["id"]})


def _finish(w, a: dict, s: dict, state: str, reason: str | None = None):
    """Remove `s` from the queue as done/failed/skipped and tell the world."""
    s["state"], s["blocked_reason"], s["blocked_since"] = state, None, None
    a["wait_on"].pop(s["id"], None)
    a["retry"].pop(s["id"], None)
    a["since"].pop(s["id"], None)
    if state == "done" and s["id"].startswith("sq") and s["op"] == "say":
        a["gathered"] = True
    if s["id"] in a["finished"]:
        a["fin_order"].remove(s["id"])
    a["finished"][s["id"]] = state
    a["fin_order"].append(s["id"])                 # a list: JSONB keeps its order, dict keys it does not
    while len(a["fin_order"]) > FINISHED_CAP:
        a["finished"].pop(a["fin_order"].pop(0), None)
    a["queue"] = [q for q in a["queue"] if q["id"] != s["id"]]
    kind = {"done": "step_done", "failed": "step_failed", "skipped": "step_skipped"}[state]
    text = {"done": f"{a['name']} finished: {describe_step(s)}",
            "failed": f"{a['name']} gave up: {describe_step(s)} ({reason})",
            "skipped": f"{a['name']} skipped: {describe_step(s)}"}[state]
    w.emit(kind, a["id"], [s["id"]], text, {"step": s["id"], "reason": reason})


def release(w, a: dict) -> None:
    """Drop the active step's scratch state (and anything it was holding)."""
    act = a["act"]
    if act is None:
        return
    for obj in w.objects.values():
        if obj.get("repairer") == a["id"]:
            obj["repairer"] = None
    if a.get("carry_obj"):
        obj = w.objects.get(a["carry_obj"])
        if obj is not None:
            obj["carried_by"] = None
        a["carry_obj"] = None
    a["act"] = None
    a["path"], a["pgoal"] = [], None


def _requeue_active(w, a: dict) -> None:
    for s in a["queue"]:
        if s["state"] == "active":
            _set_state(w, a, s, "queued")
    release(w, a)


# --- building steps -----------------------------------------------------------------

def _new_id(a: dict) -> str:
    while True:
        a["next_sid"] += 1
        sid = f"s{a['next_sid']}"
        if sid not in a["finished"] and all(q["id"] != sid for q in a["queue"]):
            return sid


def normalize(w, a: dict, raw: dict) -> tuple[dict | None, str | None]:
    """A planner step dict -> a queue step, or (None, why it is refused)."""
    if raw.get("op") not in STEP_OPS:
        return None, f"unknown op '{raw.get('op')}'"
    unknown = set(raw) - {"op", "id", "target", "say", "deps", "needs", "big", "guess", "decor"}
    if unknown:
        return None, f"unknown fields {sorted(unknown)}"
    needs = raw.get("needs")
    if needs is not None and needs.get("material") not in MATERIAL_SET:
        return None, f"unknown material '{(needs or {}).get('material')}'"
    t = raw.get("target")
    if raw["op"] == "pickup":
        if not t:
            return None, "pickup needs a target"
        if t not in MATERIAL_SET and t not in w.sources and t.split(":")[-1] not in w.pieces:
            return None, f"unknown material '{t}'; materials are {', '.join(MATERIALS)}"
    if raw["op"] in ("walk", "visit", "plant", "harvest", "dismantle", "keepsake", "use", "repair",
                     "wait_for", "carry_to") and not t:
        return None, f"{raw['op']} needs a target"
    if raw.get("big") and w.closing["no_big"]:
        return None, _BIG_REFUSAL
    sid = raw.get("id") or ""
    if sid and (sid in a["finished"] or any(q["id"] == sid for q in a["queue"])):
        return None, f"step id '{sid}' is already used"
    step = Step(op=raw["op"], id=sid, target=t, say=raw.get("say"), deps=list(raw.get("deps") or []),
                needs=dict(needs) if needs else None, big=bool(raw.get("big")),
                guess=raw.get("guess"), decor=list(raw.get("decor") or [])).to_dict()
    if not step["id"]:
        step["id"] = _new_id(a)
    return step, None


def _normalize_all(w, a, raws, taken: set[str]) -> tuple[list[dict] | None, str | None]:
    out = []
    for r in raws:
        step, why = normalize(w, a, r)
        if step is None:
            return None, why
        if step["id"] in taken:
            return None, f"step id '{step['id']}' is used twice"
        taken.add(step["id"])
        out.append(step)
    return out, None


def edit_queue(w, a: dict, args: dict) -> CommandResult:
    """Apply an `edit_queue` call's ops in order; each op stands alone (§5.2)."""
    if args.get("base_version") != a["queue_version"]:
        return CommandResult("refused", f"the list changed; it is now version {a['queue_version']}, look again",
                             {"queue_version": a["queue_version"], "ops": []})
    results, mutated = [], False
    for i, op in enumerate(args.get("ops", [])):
        ok, why, data = _edit_op(w, a, op)
        mutated = mutated or (ok and data.pop("_queue", False))
        row = {"i": i, "op": op.get("op"), "state": "applied" if ok else "refused"}
        if why:
            row["reason"] = why
        row.update({k: v for k, v in data.items() if not k.startswith("_")})
        results.append(row)
        if not ok:
            w.emit("command_refused", a["id"], [], f"{a['name']}: {op.get('op')} refused ({why})",
                   {"op": op.get("op"), "reason": why})
    if mutated:
        a["queue_version"] += 1
        w.emit("queue_edited", a["id"], [], f"{a['name']} changed the list",
               {"version": a["queue_version"], "length": len(a["queue"])})
    applied = any(r["state"] == "applied" for r in results)
    return CommandResult("applied" if applied else "refused",
                         None if applied else "; ".join(r.get("reason", "") for r in results) or "nothing to do",
                         {"queue_version": a["queue_version"], "ops": results})


def _edit_op(w, a: dict, op: dict):
    name = op.get("op")
    q = a["queue"]
    if name in ("append", "insert_after", "replace"):
        taken = {s["id"] for s in q} | set(a["finished"])
        if name == "replace":
            target = _find(a, op["step_id"])
            if target is None:
                return False, f"no step '{op['step_id']}' in the list", {}
            taken.discard(target["id"])
        new, why = _normalize_all(w, a, op["steps"], taken)
        if new is None:
            return False, why, {}
        if len(q) + len(new) - (1 if name == "replace" else 0) > QUEUE_CAP:
            return False, "the list is full", {}
        if name == "append":
            q.extend(new)
        elif name == "insert_after":
            idx = next((i for i, s in enumerate(q) if s["id"] == op["after"]), None)
            if idx is None:
                return False, f"no step '{op['after']}' in the list", {}
            q[idx + 1:idx + 1] = new
        else:
            idx = q.index(target)
            if target["state"] == "active":
                release(w, a)
            q[idx:idx + 1] = new
        return True, None, {"_queue": True, "ids": [s["id"] for s in new]}
    if name in ("remove", "clear_from"):
        target = _find(a, op["step_id"])
        if target is None:
            return False, f"no step '{op['step_id']}' in the list", {}
        idx = q.index(target)
        gone = [target] if name == "remove" else q[idx:]
        if any(s["state"] == "active" for s in gone):
            release(w, a)
        for s in gone:
            _finish(w, a, s, "skipped")
        return True, None, {"_queue": True, "removed": [s["id"] for s in gone]}
    if name == "say":
        w.emit("say", a["id"], [], op["line"][:LIMITS["say"]], {"line": op["line"]})
        return True, None, {}
    if name == "tell":
        w.emit("tell", a["id"], [], op["line"][:LIMITS["say"]],
               {"line": op["line"], "claim": op["claim"], "rule_key": op.get("rule_key"),
                "witnesses": w.in_view(a)})
        return True, None, {}
    if name == "drop_plan":
        return _drop_plan(w, a, op)
    if name == "start_project":
        return w.start_project(a, op)
    if name == "set_aside":
        n = w.set_aside(a, op.get("material"), op.get("n"), op.get("pieces"))
        return (n > 0, None if n else "nothing of yours to set aside", {"set_aside": n})
    if name == "allow_set_aside":
        return w.add_consent(a, op["to"], op["scope"])
    if name == "keepsake":
        obj = w.object_for(op["object_id"])
        if obj is None:
            return False, f"no object '{op['object_id']}'", {}
        w.add_keepsake(a, obj, op["sentence"])
        return True, None, {}
    handler = w.handlers.get("op:" + str(name))
    if handler is not None:
        return handler(w, a, op)
    return False, f"'{name}' is not available yet", {}


def _find(a: dict, sid: str) -> dict | None:
    return next((s for s in a["queue"] if s["id"] == sid), None)


def _drop_plan(w, a: dict, op: dict):
    pid = op.get("project_id")
    project = w.projects.get(pid) if pid else None
    if pid and (project is None or project["owner"] != a["id"]):
        return False, f"no project '{pid}' of yours", {}
    ref = {project["id"], project["object_id"]} if project else None
    gone = [s for s in a["queue"]
            if ref is None or (s.get("target") or "").split(":")[-1].split(">")[0] in ref]
    if any(s["state"] == "active" for s in gone):
        release(w, a)
    for s in gone:
        _finish(w, a, s, "skipped")
    if project:
        w.cancel_project(a, project)
    w.emit("plan_dropped", a["id"], [pid] if pid else [], f"{a['name']} dropped a plan: {op['reason']}",
           {"reason": op["reason"], "project": pid})
    return True, None, {"_queue": bool(gone), "removed": [s["id"] for s in gone]}


def prepend(w, a: dict, raws: list[dict]) -> None:
    """Put steps at the front of the list (system closing commands)."""
    new, _ = _normalize_all(w, a, raws, {s["id"] for s in a["queue"]} | set(a["finished"]))
    _requeue_active(w, a)
    a["gathered"] = False
    a["queue"][0:0] = new or []
    a["habit_steps"], a["habit"] = [], None
    a["queue_version"] += 1


# --- the per-actor tick ---------------------------------------------------------------

def _deps_state(a: dict, s: dict) -> str:
    """'ready' | 'wait' | 'dead' (a dep failed or was skipped)."""
    state = "ready"
    for d in s["deps"]:
        f = a["finished"].get(d)
        if f in ("failed", "skipped"):
            return "dead"
        if f != "done":
            state = "wait"
    return state


def _check(w, a: dict, s: dict, strict: bool = False):
    hold = a["retry"].get(s["id"])
    if hold and hold[0] > w.tick:
        return False, hold[1], None, False
    return hands.precheck(w, a, s, strict)


def refresh(w, a: dict) -> None:
    """Re-check blocked steps against their needs; give up on the stuck ones."""
    for s in list(a["queue"]):
        if s["state"] != "blocked":
            continue
        ok, reason, who, fatal = _check(w, a, s)
        if fatal:
            _finish(w, a, s, "failed", reason)
        elif ok:
            if s["id"] not in a["retry"]:        # a runtime block is retried by _pick, quietly
                _set_state(w, a, s, "queued")
        elif reason == "paused for rain":
            s["blocked_since"] = w.tick     # a rain pause never counts toward giving up
        elif w.tick - s["blocked_since"] >= LIMITS["give_up_blocked_s"] * TICK_HZ:
            _finish(w, a, s, "failed", f"blocked too long: {s['blocked_reason']}")
        elif reason != s["blocked_reason"] or who != a["wait_on"].get(s["id"]):
            _set_state(w, a, s, "blocked", reason, who)
    for s in list(a["queue"]):
        if s["state"] in ("queued", "blocked") and _deps_state(a, s) == "dead":
            _finish(w, a, s, "skipped")


def _pick(w, a: dict):
    skipped: list[dict] = []
    for s in list(a["queue"]):
        if s["state"] not in ("queued", "blocked"):
            continue
        d = _deps_state(a, s)
        if d == "wait":
            continue
        if d == "dead":
            _finish(w, a, s, "skipped")
            continue
        ok, reason, who, fatal = _check(w, a, s)
        if fatal:
            _finish(w, a, s, "failed", reason)
            continue
        if not ok:
            _set_state(w, a, s, "blocked", reason, who)
            skipped.append(s)
            continue
        return s, skipped
    return None, skipped


def _start(w, a: dict, s: dict, habit: bool) -> None:
    a["act"] = {"sid": s["id"], "op": s["op"], "habit": habit, "target": s.get("target")}
    a["path"], a["pgoal"], a["acc"] = [], None, 0
    if not habit:
        was = s["state"] == "blocked"
        if was:
            a["since"][s["id"]] = [s["blocked_since"], w.tick, s["blocked_reason"]]
        _set_state(w, a, s, "active", quiet=was)
        a["retry"].pop(s["id"], None)
    a["say"] = s.get("say") or ""
    w.emit("step_started", a["id"], [s["id"]], f"{a['name']}: {describe_step(s)}",
           {"step": s["id"], "op": s["op"], "habit": habit})
    if s.get("guess"):
        w.emit("guess_made", a["id"], [s["id"]], f"{a['name']} has a hunch: {s['guess']}",
               {"step": s["id"], "guess": s["guess"]})


def _handle(w, a: dict, s: dict, res, habit: bool) -> None:
    if res is None:
        return
    release(w, a)
    if res == "done":
        if habit:
            a["habit_steps"].pop(0)
            w.emit("step_done", a["id"], [s["id"]], f"{a['name']} finished: {describe_step(s)}",
                   {"step": s["id"], "habit": True})
        else:
            _finish(w, a, s, "done")
        return
    if res[0] == "blocked":
        if habit:
            a["habit_steps"], a["habit"] = [], None
            return
        a["retry"][s["id"]] = [w.tick + RETRY_TICKS, res[1]]
        _set_state(w, a, s, "blocked", res[1], res[2])
    else:
        if habit:
            a["habit_steps"], a["habit"] = [], None
            return
        _finish(w, a, s, "failed", res[1])


def advance(w, a: dict, strict_habit: bool = True) -> None:
    """One tick of one actor: refresh the list, pick or continue a step, run it,
    then bin the second for idle attribution."""
    refresh(w, a)
    habit_step = False
    if a["gathered"] and w.closing["square"]:
        a["caption"] = "talking in the square"        # closing: unfinished work stays where it stands
        _bin(w, a)
        return
    if a["act"] is None:
        s, skipped = _pick(w, a)
        if s is not None:
            a["habit_steps"], a["habit"] = [], None
            if skipped:
                a["caption"] = (f"skipping {describe_step(skipped[0])} until "
                                f"{skipped[0]['blocked_reason']}, {describe_step(s)} first")
            else:
                a["caption"] = describe_step(s)
            _start(w, a, s, False)
        else:
            if not a["habit_steps"] and a["habits"]:
                got = H.choose(w, a)
                if got is not None:
                    idx, steps = got
                    a["habit_steps"], a["habit"] = steps, a["habits"][idx].get("say") or H.describe(
                        a["habits"][idx]["do"])
                    w.emit("habit_started", a["id"], [], f"{a['name']}: {a['habit']}",
                           {"habit": idx, "label": a["habit"]})
            if a["habit_steps"]:
                hs = a["habit_steps"][0]
                ok, reason, _, fatal = hands.precheck(w, a, hs, strict_habit)
                if ok:
                    a["caption"] = a["habit"]
                    _start(w, a, hs, True)
                else:
                    a["habit_steps"], a["habit"] = [], None
    if a["act"] is not None:
        habit_step = a["act"]["habit"]
        s = a["habit_steps"][0] if habit_step else _find(a, a["act"]["sid"])
        if s is None:
            release(w, a)
        else:
            rec = a["since"].get(s["id"])
            if rec and w.tick - rec[1] >= secs(2) and not habit_step:
                a["since"].pop(s["id"])
                w.emit("step_unblocked", a["id"], [s["id"]], f"{a['name']} can carry on", {"step": s["id"]})
            _handle(w, a, s, hands.run(w, a, s, strict_habit and habit_step), habit_step)
    elif not a["queue"] or all(q["state"] != "active" for q in a["queue"]):
        _idle_move(w, a)
    _bin(w, a)


def _idle_move(w, a: dict) -> None:
    """Nothing runnable: walk over to the character we are waiting on, else rest."""
    for s in a["queue"]:
        who = a["wait_on"].get(s["id"]) if s["state"] == "blocked" else None
        other = w.actors.get(who) if who else None
        if other is not None:
            a["caption"] = f"waiting for {other['name']}"
            hands.goto(w, a, (other["x"], other["y"]), 1)
            return
    if not a["queue"] and not a["habit_steps"]:
        a["caption"] = "resting"


def _bin(w, a: dict) -> None:
    act = a["act"]
    if act is not None:
        key = "resting" if act["op"] == "rest" and not act["habit"] else ("habit" if act["habit"] else "working")
    else:
        blocked = [s for s in a["queue"] if s["state"] == "blocked"]
        if blocked:
            who = a["wait_on"].get(blocked[0]["id"])
            key = f"waiting_for:{who}" if who else f"blocked:{blocked[0]['blocked_reason']}"
        elif a["queue"]:
            key = "working"
        elif a["planner"] == "thinking":
            key = "thinking_no_steps"
        else:
            key = "resting"
    idle = a["idle"]
    if key not in idle and len(idle) >= 40:
        key = "blocked:other" if key.startswith("blocked") else "working"
    idle[key] = idle.get(key, 0) + 1
