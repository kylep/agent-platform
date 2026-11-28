"""The in-memory village world (plan §2 World model, §3 tick design).

`World` implements `contract.World`: `step()` advances one fixed tick, `apply()`
takes one journaled command at the current tick, `to_snapshot()/from_snapshot()`
round-trip every bit of state. Everything here is deterministic: no wall clock,
no I/O, no awaits, no model. The state is plain JSON-safe dicts; the helpers the
other modules (hands, tasks, habits) call live on this class.
"""
import copy
import re
from typing import Any, Callable

from . import habits as H
from . import materials as M
from . import rules as R
from . import tasks
from .contract import (EVENT_KINDS, FUNCTIONS, HEARTBEAT_EVERY_TICKS, LIMITS, MATERIALS, SYSTEM_ACTIONS,
                       TICK_HZ, TICK_S, VIEW_RADIUS, Command, CommandResult, Event, hash_state,
                       merged_rules)
from .mapdata import BRIDGE_SITE, LANDMARKS, cheb, passable, starting_sources

SNAPSHOT_VERSION = 1
PLAYER_NAMES = {"pai": "Pai", "kai": "Kai", "olu": "Olu"}
_START = {"pai": (30, 20), "kai": (32, 21), "olu": (34, 20)}
_PIECE_RE = re.compile(r"^p\d+$")
EVENT_CAP = 20000


class World:
    def __init__(self, seed: int = 1, rules: dict | None = None):
        self.handlers: dict[str, Callable] = {}     # action/op name -> fn(world, actor, args)
        self.systems: list[Callable] = []           # per-tick hooks fn(world), run after the actors
        self._out: list[Event] = []
        self.problems: list[str] = []               # invariant failures (never in the state)
        self.tick = 0
        self.seed, self.rng = seed, seed or 1
        self.rules = merged_rules(rules)
        self.mode = "live"
        self.closing = {"no_big": False, "square": False}
        self.session: dict[str, Any] = {}
        self.wear: dict[str, int] = {}
        self._set_overlays([], [])
        self.actors: dict[str, dict] = {}
        self.pieces: dict[str, dict] = {}
        self.sources: dict[str, dict] = {}
        self.objects: dict[str, dict] = {}
        self.projects: dict[str, dict] = {}
        self.tools = {t: {"wear": 0, "broken": False} for t in M.TOOLS}
        self.rain = {"raining": False, "start_at": None, "end_at": None, "notice_at": None,
                     "noticed": False, "wetness": 0}
        self.pond = {"idx": 0, "next": M.POND_PERIOD}
        self.consents: list[dict] = []
        self.pile_log: list[dict] = []
        self.block_log: list[dict] = []
        self.ids: dict[str, int] = {}
        self.ext: dict[str, Any] = {}               # room for residents/social state (U7/U8)
        for s in starting_sources():
            kind = s["kind"]
            s.update({"amount": M.SOURCE_KINDS[kind]["max"], "next": None})
            self.sources[s["id"]] = s
        for pid in self.rules["players"]:
            x, y = _START.get(pid, LANDMARKS["square"])
            self.add_actor(pid, PLAYER_NAMES.get(pid, pid.title()), "character", x, y)

    # --- extension points ------------------------------------------------------

    def register(self, name: str, fn: Callable) -> None:
        """Let another unit handle an action (`name`) or an edit_queue op (`op:<name>`)."""
        self.handlers[name] = fn

    def add_actor(self, aid: str, name: str, kind: str, x: int, y: int, owner: str | None = None) -> dict:
        """`owner` is the character who may edit a resident's habits (a character owns itself)."""
        a = {"id": aid, "name": name, "kind": kind, "x": x, "y": y, "acc": 0, "path": [], "pgoal": None,
             "carry": [], "cart": None, "carry_obj": None, "queue": [], "queue_version": 0,
             "finished": {}, "fin_order": [], "next_sid": 0, "next_hid": 0, "act": None, "habit_steps": [],
             "habit": None, "goals": [], "habits": [], "section": "", "planner": "idle", "idle": {},
             "caption": "", "say": "", "wait_on": {}, "retry": {}, "since": {}, "gathered": False,
             "owner": owner if owner is not None else (aid if kind == "character" else None)}
        self.actors[aid] = a
        return a

    # --- state ------------------------------------------------------------------

    def _set_overlays(self, bridge, paths) -> None:
        self.bridge_list = sorted(map(list, bridge))
        self.path_list = sorted(map(list, paths))
        self.bridge = frozenset(map(tuple, self.bridge_list))
        self.paths = frozenset(map(tuple, self.path_list))

    def to_snapshot(self) -> dict[str, Any]:
        return copy.deepcopy({
            "version": SNAPSHOT_VERSION, "tick": self.tick, "seed": self.seed, "rng": self.rng,
            "rules": self.rules, "mode": self.mode, "closing": self.closing, "session": self.session,
            "wear": self.wear, "bridge": self.bridge_list, "paths": self.path_list,
            "actors": self.actors, "pieces": self.pieces, "sources": self.sources,
            "objects": self.objects, "projects": self.projects, "tools": self.tools,
            "rain": self.rain, "pond": self.pond, "consents": self.consents,
            "pile_log": self.pile_log, "block_log": self.block_log, "ids": self.ids, "ext": self.ext,
        })

    @classmethod
    def from_snapshot(cls, state: dict[str, Any]) -> "World":
        w = cls.__new__(cls)
        s = copy.deepcopy(state)
        w.handlers, w.systems, w._out, w.problems = {}, [], [], []
        w.tick, w.seed, w.rng = s["tick"], s["seed"], s["rng"]
        w.rules, w.mode, w.closing, w.session = s["rules"], s["mode"], s["closing"], s["session"]
        w.wear = s["wear"]
        w._set_overlays(s["bridge"], s["paths"])
        for k in ("actors", "pieces", "sources", "objects", "projects", "tools", "rain", "pond",
                  "consents", "pile_log", "block_log", "ids", "ext"):
            setattr(w, k, s[k])
        return w

    def state_hash(self) -> str:
        return hash_state(self.to_snapshot())

    # --- small helpers ------------------------------------------------------------

    def emit(self, kind: str, actor: str | None = None, subjects=(), text: str | None = None,
             data: dict | None = None) -> Event:
        if kind not in EVENT_KINDS:
            raise ValueError(f"unknown event kind {kind}")
        ev = Event(self.tick, kind, actor, list(subjects),
                   text[:LIMITS["public_text"]] if text else None, data or {})
        if len(self._out) < EVENT_CAP:
            self._out.append(ev)
        return ev

    def drain_events(self) -> list[Event]:
        out, self._out = self._out, []
        return out

    def nid(self, prefix: str) -> str:
        self.ids[prefix] = self.ids.get(prefix, 0) + 1
        return f"{prefix}{self.ids[prefix]}"

    def name(self, aid: str | None) -> str:
        return self.actors[aid]["name"] if aid in self.actors else str(aid)

    @property
    def workshop(self) -> tuple[int, int]:
        return LANDMARKS["workshop"]

    def wear_tile(self, x: int, y: int) -> None:
        k = f"{x},{y}"
        self.wear[k] = min(99, self.wear.get(k, 0) + 1)

    def log_block(self, a: dict, s: dict, reason: str | None) -> None:
        self.block_log.append({"t": self.tick, "actor": a["id"], "step": s["id"], "op": s["op"],
                               "reason": reason})
        del self.block_log[:-LIMITS["note"] // 5]

    def in_view(self, a: dict) -> list[str]:
        return sorted(o["id"] for o in self.actors.values()
                      if cheb(a["x"], a["y"], o["x"], o["y"]) <= VIEW_RADIUS)

    def near_name(self, x: int, y: int) -> str:
        best = min(LANDMARKS.items(), key=lambda kv: (cheb(x, y, *kv[1]), kv[0]))
        return f"beside the {best[0].replace('_', ' ')}" if cheb(x, y, *best[1]) <= 8 else "in the village"

    def is_raining(self) -> bool:
        return self.rain["raining"]

    def covered(self, x: int, y: int) -> bool:
        return any(o["ready"] and o["function"] in M.INDOOR_FUNCTIONS and cheb(x, y, o["x"], o["y"]) <= 1
                   for o in self.objects.values())

    def usable(self, obj: dict) -> bool:
        return obj["ready"] and obj["carried_by"] is None and not obj.get("moving")

    # --- locating things ------------------------------------------------------------

    def object_for(self, t: str | None) -> dict | None:
        if not t:
            return None
        t = t.split(":")[-1] if t.startswith(("object:", "project:")) else t
        if t in self.objects:
            return self.objects[t]
        proj = self.projects.get(t)
        return self.objects.get(proj["object_id"]) if proj else None

    def tool_for(self, t: str | None) -> dict | None:
        key = (t or "").split(":")[-1]
        return self.tools.get(key)

    def tools_blocked(self, name: str) -> bool:
        return self.tools[name]["broken"]

    def piece_xy(self, p: dict) -> tuple[int, int]:
        kind, _, rest = p["loc"].partition(":")
        if kind == "tile":
            x, y = rest.split(",")
            return int(x), int(y)
        if kind == "carry":
            a = self.actors[rest]
            return a["x"], a["y"]
        obj = self.objects[rest.split(":")[0]]
        return obj["x"], obj["y"]

    def locate(self, t: str | None, a: dict | None = None) -> dict | None:
        """Resolve a step target to {kind, id, x, y, stop}; None when unknown."""
        if not t:
            return None
        low = t.strip().lower()
        if low in LANDMARKS:
            x, y = LANDMARKS[low]
            return {"kind": "tile", "id": low, "x": x, "y": y, "stop": 0}
        if low.startswith("tile:"):
            try:
                x, y = (int(v) for v in low[5:].split(","))
            except ValueError:
                return None
            from .mapdata import in_bounds
            return {"kind": "tile", "id": low, "x": x, "y": y, "stop": 0} if in_bounds(x, y) else None
        for o in self.actors.values():
            if low in (o["id"], o["name"].lower()):
                return {"kind": "actor", "id": o["id"], "x": o["x"], "y": o["y"], "stop": 1}
        bare = low.split(":")[-1]
        if bare in self.pieces and low.split(":")[0] in (bare, "piece"):
            x, y = self.piece_xy(self.pieces[bare])
            return {"kind": "piece", "id": bare, "x": x, "y": y, "stop": 0}
        obj = self.object_for(low)
        if obj is not None:
            return {"kind": "object", "id": obj["id"], "x": obj["x"], "y": obj["y"], "stop": 1}
        if bare in self.sources:
            s = self.sources[bare]
            return {"kind": "source", "id": bare, "x": s["x"], "y": s["y"], "stop": 0}
        if bare in M.TOOLS:
            x, y = self.workshop
            return {"kind": "tool", "id": bare, "x": x, "y": y, "stop": 1}
        if low in M.MATERIAL_SET and a is not None:
            cand, _ = self.pick_candidate(a, low, False)
            if cand is not None:
                if cand[0] == "source":
                    s = self.sources[cand[1]]
                    return {"kind": "source", "id": s["id"], "x": s["x"], "y": s["y"], "stop": 0}
                x, y = self.piece_xy(self.pieces[cand[1]])
                return {"kind": "piece", "id": cand[1], "x": x, "y": y, "stop": 0}
        return None

    # --- pieces -----------------------------------------------------------------------

    def new_piece(self, material: str, x: int, y: int, loc: str | None = None) -> str:
        pid = self.nid("p")
        wet = material == "clay"
        self.pieces[pid] = {"id": pid, "m": material, "wet": wet,
                            "dry_in": M.CLAY_DRY_TICKS if wet else 0,
                            "loc": f"tile:{x},{y}", "aside": None, "split": False}
        if loc:
            self.move_piece(pid, loc)
        return pid

    def _leave(self, pid: str, loc: str) -> None:
        kind, _, rest = loc.partition(":")
        if kind == "carry":
            self.actors[rest]["carry"].remove(pid)
        elif kind == "slot":
            oid, i = rest.split(":")
            self.objects[oid]["slots"][int(i)]["piece"] = None
        elif kind == "rack":
            self.objects[rest]["rack"]["pieces"].remove(pid)

    def move_piece(self, pid: str, loc: str) -> None:
        """The one way a piece changes place, so it always has exactly one location."""
        p = self.pieces[pid]
        self._leave(pid, p["loc"])
        kind, _, rest = loc.partition(":")
        if kind == "carry":
            self.actors[rest]["carry"].append(pid)
        elif kind == "slot":
            oid, i = rest.split(":")
            self.objects[oid]["slots"][int(i)]["piece"] = pid
        elif kind == "rack":
            self.objects[rest]["rack"]["pieces"].append(pid)
        p["loc"] = loc

    def destroy_piece(self, pid: str) -> None:
        self._leave(pid, self.pieces[pid]["loc"])
        del self.pieces[pid]

    def stones_at(self, x: int, y: int) -> list[str]:
        return [pid for pid in sorted(self.pieces)
                if self.pieces[pid]["m"] == "stone" and self.pieces[pid]["loc"] == f"tile:{x},{y}"]

    def can_take(self, a: dict, p: dict) -> bool:
        owner = p["aside"]
        return owner is None or owner == a["id"] or any(
            c["owner"] == owner and c["to"] == a["id"] for c in self.consents)

    def pick_candidate(self, a: dict, material: str, strict: bool):
        """([kind, id], None) for the nearest source or loose piece of `material`
        this actor may take, else (None, plain-words reason)."""
        best, aside_by, depleted = None, None, False
        for sid in sorted(self.sources):
            s = self.sources[sid]
            if s["m"] != material:
                continue
            if s["amount"] <= 0:
                depleted = True
                continue
            d = (abs(a["x"] - s["x"]) + abs(a["y"] - s["y"]), sid)
            if best is None or d < best[0]:
                best = (d, ["source", sid])
        for pid in sorted(self.pieces):
            p = self.pieces[pid]
            if p["m"] != material or not p["loc"].startswith("tile:") or p["split"]:
                continue
            if strict and p["aside"] or not self.can_take(a, p):
                aside_by = aside_by or p["aside"]
                continue
            x, y = self.piece_xy(p)
            d = (abs(a["x"] - x) + abs(a["y"] - y), pid)
            if best is None or d < best[0]:
                best = (d, ["piece", pid])
        if best is not None:
            return best[1], None
        if aside_by:
            return None, f"{self.name(aside_by)} set that aside"
        if depleted:
            return None, f"no {material} left, waiting for it to regrow"
        return None, f"no {material} to be found"

    def taken_reason(self, src: dict) -> str:
        for rec in reversed(self.pile_log):
            if rec["src"] == src["id"] and self.tick - rec["t"] <= M.secs(30):
                return f"{self.name(rec['who'])} took that {src['m']}"
        return f"that {src['m']} is gone"

    def take_from_source(self, a: dict, src: dict) -> None:
        src["amount"] -= 1
        if src["next"] is None and not self._timer_free(src):
            src["next"] = self.tick + M.SOURCE_KINDS[src["kind"]]["regrow"]
        pid = self.new_piece(src["m"], src["x"], src["y"])
        self.move_piece(pid, f"carry:{a['id']}")
        self._log_take(a, src["m"], src["id"])

    def take_piece(self, a: dict, p: dict) -> None:
        if p["aside"] and p["aside"] != a["id"]:
            for c in list(self.consents):
                if c["owner"] == p["aside"] and c["to"] == a["id"]:
                    if c["scope"] == "once":
                        self.consents.remove(c)
                    break
            p["aside"] = None
        self.move_piece(p["id"], f"carry:{a['id']}")
        self._log_take(a, p["m"], p["id"])

    def _log_take(self, a: dict, material: str, src: str) -> None:
        self.pile_log.append({"t": self.tick, "who": a["id"], "m": material, "src": src})
        del self.pile_log[:-200]
        self.emit("pickup", a["id"], [src], f"{a['name']} picked up {material}",
                  {"material": material, "from": src})

    def drop_all(self, a: dict) -> None:
        got = list(a["carry"])
        for pid in got:
            self.move_piece(pid, f"tile:{a['x']},{a['y']}")
        if got:
            self.emit("drop", a["id"], got, f"{a['name']} put down {len(got)} piece(s)",
                      {"pieces": got, "x": a["x"], "y": a["y"]})

    def _timer_free(self, src: dict) -> bool:
        """The stone pile refills on the pond cycle, not a timer, when water_cycle is on."""
        return src["kind"] == "stone_pile" and self.rules["water_cycle"] == "on"

    def set_aside(self, a: dict, material: str | None, n: int | None, pieces: list[str] | None) -> int:
        pool: list[str] = []
        if pieces:
            pool = [p.split(":")[-1] for p in pieces]
        else:
            pool = [p for p in a["carry"] if material in (None, self.pieces[p]["m"])]
            near = sorted(pid for pid, p in self.pieces.items()
                          if p["loc"].startswith("tile:") and not p["aside"] and (material is None or p["m"] == material)
                          and cheb(a["x"], a["y"], *self.piece_xy(p)) <= VIEW_RADIUS)
            pool += [p for p in near if p not in pool]
        marked = []
        for pid in pool:
            p = self.pieces.get(pid)
            if p is None or (p["aside"] and p["aside"] != a["id"]):
                continue
            if p["loc"].startswith("carry:") and p["loc"] != f"carry:{a['id']}":
                continue
            if n is not None and len(marked) >= n:
                break
            p["aside"] = a["id"]
            marked.append(pid)
        if marked:
            self.emit("set_aside", a["id"], marked, f"{a['name']} set aside {len(marked)} "
                      f"{self.pieces[marked[0]]['m']}", {"pieces": marked})
        return len(marked)

    def add_consent(self, a: dict, to: str, scope: str):
        to = to.lower()
        if to not in self.actors or to == a["id"]:
            return False, f"nobody called '{to}' to share with", {}
        self.consents = [c for c in self.consents if not (c["owner"] == a["id"] and c["to"] == to)]
        self.consents.append({"owner": a["id"], "to": to, "scope": scope})
        self.emit("consent_given", a["id"], [to], f"{a['name']} lets {self.name(to)} use what's set aside",
                  {"to": to, "scope": scope})
        return True, None, {}

    def add_keepsake(self, a: dict, obj: dict, sentence: str) -> None:
        obj["keepsakes"] = [k for k in obj["keepsakes"] if k["by"] != a["id"]]
        obj["keepsakes"].append({"by": a["id"], "sentence": sentence[:LIMITS["keepsake"]]})
        self.emit("keepsake_added", a["id"], [obj["id"]], f"{a['name']} left a keepsake: {sentence}",
                  {"object": obj["id"]})

    # --- tools, wear, rule outcomes ------------------------------------------------------

    def wear_tool(self, name: str) -> None:
        t = self.tools[name]
        t["wear"] += 1
        if t["wear"] >= M.TOOL_BREAK and not t["broken"]:
            t["broken"] = True
            self.emit("tool_broken", None, [name], f"the {name} broke")

    def repair_tool(self, a: dict, tool: dict) -> None:
        name = next(k for k, v in self.tools.items() if v is tool)
        tool["wear"], tool["broken"] = 0, False
        self.emit("tool_repaired", a["id"], [name], f"{a['name']} repaired the {name}")

    def _add_wear(self, obj: dict, n: int) -> None:
        obj["wear"] += n
        if obj["wear"] >= M.WEAR_WORN and not obj["cracked"]:
            obj["cracked"] = True
            self.emit("object_worn", None, [obj["id"]], f"{obj['caption'] or obj['kind']} is getting worn")

    def repair_object(self, a: dict, obj: dict) -> None:
        obj["wear"], obj["cracked"], obj["repairer"] = 0, False, None
        obj["history"].append(f"Repaired by {a['name']}.")
        self.emit("object_repaired", a["id"], [obj["id"]], f"{a['name']} repaired {obj['caption'] or obj['kind']}")

    def is_repairing(self, aid: str, oid: str) -> bool:
        o = self.actors.get(aid)
        if not o or not o["act"] or o["act"]["op"] != "repair":
            return False
        obj = self.object_for(o["act"].get("target"))
        return obj is not None and obj["id"] == oid

    def resolve_outcome(self, a: dict, step: dict, out: R.Outcome | None, subjects: list[str]) -> None:
        """Turn a hidden-rule outcome (or its absence) into events; a guess is resolved here."""
        witnesses = self.in_view(a)
        if out is not None:
            self.emit("rule_outcome", a["id"], subjects, out.text,
                      {"rule_key": out.rule_key, "ok": out.ok, "claim": out.claim, "witnesses": witnesses})
        if step.get("guess"):
            text = out.text if out is not None else "nothing happened that settled it"
            self.emit("guess_resolved", a["id"], [step["id"]], f"{a['name']}'s hunch: {text}",
                      {"step": step["id"], "guess": step["guess"], "ok": None if out is None else out.ok,
                       "rule_key": out.rule_key if out else None, "claim": out.claim if out else None,
                       "witnesses": witnesses})

    # --- projects and objects ---------------------------------------------------------------

    def start_project(self, a: dict, op: dict):
        if self.closing["no_big"]:
            return False, "the last minutes are for finishing up; no new big jobs", {}
        mats, fn, size = op["materials"], op["function"], op["size"]
        bad = [m for m in mats if m not in M.MATERIAL_SET]
        if bad:
            return False, f"unknown material '{bad[0]}'; materials are {', '.join(MATERIALS)}", {}
        if fn not in FUNCTIONS:
            return False, f"unknown function '{fn}'; functions are {', '.join(FUNCTIONS)}", {}
        if size > LIMITS["project_size"]:
            return False, f"too big; at most {LIMITS['project_size']} pieces", {}
        loc = self.locate(op.get("place"), a) if op.get("place") else {"x": a["x"], "y": a["y"]}
        if loc is None:
            return False, f"don't know where '{op.get('place')}' is", {}
        kind = M.kind_for(fn, op["caption"])
        x, y = loc["x"], loc["y"]
        if kind == "footbridge":
            if op.get("place") and cheb(x, y, *BRIDGE_SITE) > 1:
                return False, "a footbridge has to span the stream at the bridge site", {}
            x, y = BRIDGE_SITE
        elif not passable(x, y, self.bridge, self.paths):
            return False, "that spot is water", {}
        proj_id, obj_id = self.nid("j"), self.nid("o")
        self.objects[obj_id] = {
            "id": obj_id, "kind": kind, "function": fn, "x": x, "y": y, "caption": op["caption"],
            "owner": a["id"], "project": proj_id, "slots": M.make_slots(mats, size), "ready": False,
            "decor": [], "wear": 0, "cracked": False, "history": [], "keepsakes": [],
            "carried_by": None, "moving": False, "pending": [], "mover": None, "to": None,
            "repairer": None, "crop": None, "rack": {"pieces": [], "done_at": None}, "used": False,
            "built_decor": list(op.get("decor") or [])}
        self.projects[proj_id] = {"id": proj_id, "owner": a["id"], "caption": op["caption"],
                                  "object_id": obj_id, "state": "building", "helpers": [], "size": size}
        self.emit("project_started", a["id"], [proj_id, obj_id], f"{a['name']} plans: {op['caption']}",
                  {"project": proj_id, "object": obj_id, "function": fn, "size": size})
        return True, None, {"project_id": proj_id, "object_id": obj_id}

    def cancel_project(self, a: dict, project: dict) -> None:
        obj = self.objects.get(project["object_id"])
        if obj is None or obj["ready"]:
            return
        for s in obj["slots"]:
            if s["piece"]:
                self.move_piece(s["piece"], f"tile:{obj['x']},{obj['y']}")
        for pid in obj["pending"]:
            if pid in self.pieces:
                self.pieces[pid]["aside"] = None
        del self.objects[obj["id"]]
        project["state"], project["caption"] = "cancelled", "Stopped building; collecting the wood"
        self.emit("project_cancelled", a["id"], [project["id"]], "Stopped building; collecting the wood",
                  {"project": project["id"]})

    def is_complete(self, obj: dict) -> bool:
        return all(s["piece"] for s in obj["slots"])

    def finish_object(self, a: dict, obj: dict, step: dict) -> None:
        obj["ready"] = True
        applied, skipped = M.split_decor(list(obj.pop("built_decor", [])) + list(step.get("decor") or []))
        obj["decor"] = applied
        if skipped:
            self.emit("decor_skipped", a["id"], [obj["id"]],
                      f"built plain: '{skipped[0]}' isn't an option", {"skipped": skipped})
        obj["history"].append(f"Built by {self.name(obj['owner'])} {self.near_name(obj['x'], obj['y'])}.")
        proj = self.projects.get(obj["project"])
        if proj:
            proj["state"] = "done"
        if obj["kind"] == "footbridge":
            self._set_overlays(self.bridge_list + [[obj["x"], obj["y"]]], self.path_list)
        self.emit("object_first_ready", a["id"], [obj["id"]],
                  f"{obj['caption'] or obj['kind']} is ready", {"object": obj["id"], "kind": obj["kind"]})

    def build_path(self, x: int, y: int) -> None:
        self._set_overlays(self.bridge_list, self.path_list + [[x, y]])

    def dismantle_object(self, a: dict, obj: dict) -> None:
        for s in obj["slots"]:
            if s["piece"]:
                self.move_piece(s["piece"], f"tile:{obj['x']},{obj['y']}")
        for pid in list(obj["rack"]["pieces"]):
            self.move_piece(pid, f"tile:{obj['x']},{obj['y']}")
        for pid in obj["pending"]:
            if pid in self.pieces:
                self.pieces[pid]["aside"] = None
        for k in obj["keepsakes"]:
            self.emit("keepsake_removed", a["id"], [obj["id"]], "a keepsake was removed with its object",
                      {"by": k["by"]})
        for o in self.actors.values():
            if o["cart"] == obj["id"]:
                o["cart"] = None
        if obj["kind"] == "footbridge":
            self._set_overlays([b for b in self.bridge_list if b != [obj["x"], obj["y"]]], self.path_list)
        proj = self.projects.get(obj["project"])
        if proj:
            proj["state"] = "dismantled"
        del self.objects[obj["id"]]
        self.emit("object_dismantled", a["id"], [obj["id"]],
                  f"{a['name']} took apart {obj['caption'] or obj['kind']}", {"object": obj["id"]})

    def use_object(self, a: dict, obj: dict, step: dict) -> tuple[bool, str | None]:
        mats = sorted(self.pieces[s["piece"]]["m"] for s in obj["slots"] if s["piece"])
        wet_clay = any(self.pieces[s["piece"]]["m"] == "clay" and self.pieces[s["piece"]]["wet"]
                       for s in obj["slots"] if s["piece"])
        out = R.evaluate("use_object", {"kind": obj["kind"], "slots": mats, "loaded": bool(a["carry"]),
                                        "wet_clay": wet_clay})
        if out is not None and not out.ok:
            self._add_wear(obj, 3 if out.rule_key == "clay_dry" else M.WEAR_BROKEN)
            if out.rule_key == "reed_rope":
                self.drop_all(a)
                a["cart"] = None
            self.resolve_outcome(a, step, out, [obj["id"]])
            return False, out.text
        self.resolve_outcome(a, step, out, [obj["id"]])
        if obj["kind"] == "handcart":
            a["cart"] = None if a["cart"] == obj["id"] else obj["id"]
            obj["x"], obj["y"] = a["x"], a["y"]
        elif obj["kind"] == "drying_rack":
            load = [p for p in a["carry"] if self.pieces[p]["m"] == "reeds"][:M.RACK_CAP]
            for pid in load:
                self.move_piece(pid, f"rack:{obj['id']}")
            obj["rack"]["done_at"] = self.tick + M.RACK_TICKS
        self._add_wear(obj, 1)
        self.emit("object_used", a["id"], [obj["id"]], f"{a['name']} used {obj['caption'] or obj['kind']}",
                  {"object": obj["id"]})
        if not obj["used"] and a["kind"] == "resident":
            obj["used"] = True
            self.emit("object_first_use", a["id"], [obj["id"]],
                      f"{a['name']} used {obj['caption'] or obj['kind']} for the first time", {"object": obj["id"]})
        return True, None

    def rack_progress(self, obj: dict) -> float:
        """0..1, for the colour stage of a drying rack."""
        done = obj["rack"]["done_at"]
        if done is None or not obj["rack"]["pieces"]:
            return 0.0
        return max(0.0, min(1.0, 1 - (done - self.tick) / M.RACK_TICKS))

    def moved_object(self, a: dict, obj: dict, place: str) -> None:
        obj["history"].append(f"Moved by {a['name']} to the {place.replace('_', ' ')}.")
        self.emit("object_moved", a["id"], [obj["id"]],
                  f"{a['name']} moved {obj['caption'] or obj['kind']} to the {place.replace('_', ' ')}",
                  {"object": obj["id"], "x": obj["x"], "y": obj["y"]})

    def start_haul(self, a: dict, obj: dict, dx: int, dy: int) -> None:
        old = (obj["x"], obj["y"])
        pend = []
        for s in obj["slots"]:
            if s["piece"]:
                pend.append(s["piece"])
                self.move_piece(s["piece"], f"tile:{old[0]},{old[1]}")
                self.pieces[pend[-1]]["aside"] = a["id"]
        obj.update({"ready": False, "moving": True, "mover": a["id"], "pending": pend,
                    "to": [dx, dy], "x": dx, "y": dy})

    def haul_place(self, a: dict, obj: dict) -> None:
        for pid in list(a["carry"]):
            if pid not in obj["pending"]:
                continue
            m = self.pieces[pid]["m"]
            idx = next((i for i, s in enumerate(obj["slots"]) if s["piece"] is None and s["m"] == m), None)
            if idx is not None:
                self.move_piece(pid, f"slot:{obj['id']}:{idx}")
                self.pieces[pid]["aside"] = None
                obj["pending"].remove(pid)

    def finish_haul(self, a: dict, obj: dict, place: str) -> None:
        obj.update({"ready": True, "moving": False, "mover": None, "pending": [], "to": None})
        self.moved_object(a, obj, place)

    # --- the clock -----------------------------------------------------------------------------

    def schedule_rain(self, in_s: float, duration_s: float) -> None:
        start = self.tick + M.secs(in_s)
        self.rain.update({"start_at": start, "end_at": start + M.secs(duration_s), "noticed": False,
                          "notice_at": max(self.tick, start - M.secs(self.rules["rain_notice_s"]))})

    def _clocks(self) -> None:
        t = self.tick
        for sid in sorted(self.sources):
            s = self.sources[sid]
            if s["next"] is not None and t >= s["next"]:
                s["amount"] += 1
                s["next"] = t + M.SOURCE_KINDS[s["kind"]]["regrow"] if s["amount"] < M.SOURCE_KINDS[s["kind"]]["max"] else None
                if s["amount"] == 1:
                    self.emit("source_refilled", None, [sid], f"{s['m']} grew back", {"material": s["m"]})
        self._rain_clock()
        for pid in sorted(self.pieces):
            p = self.pieces[pid]
            if p["m"] not in ("wood", "clay") or p["loc"].startswith(("rack:",)):
                continue
            built = p["loc"].startswith("slot:") and self.objects[p["loc"].split(":")[1]]["ready"]
            x, y = self.piece_xy(p)
            if self.rain["raining"] and not built and not self.covered(x, y):
                p["wet"], p["dry_in"] = True, M.WET_DRY_TICKS
            elif p["wet"] and not self.rain["raining"]:
                p["dry_in"] -= 1
                if p["dry_in"] <= 0:
                    p["wet"], p["dry_in"] = False, 0
        for oid in sorted(self.objects):
            o = self.objects[oid]
            rack = o["rack"]
            if rack["pieces"] and rack["done_at"] is not None and t >= rack["done_at"]:
                for pid in list(rack["pieces"]):
                    self.pieces[pid]["m"] = "rope"
                    self.move_piece(pid, f"tile:{o['x']},{o['y']}")
                rack["done_at"] = None
                self.emit("rack_done", None, [oid], "the reeds dried into rope", {"object": oid})
            crop = o.get("crop")
            if crop and not crop["announced"] and t >= crop["ripe_at"]:
                crop["announced"] = True
                near = sorted(a["id"] for a in self.actors.values()
                              if a["kind"] == "character" and cheb(a["x"], a["y"], o["x"], o["y"]) <= 10)
                self.emit("garden_ripe", None, [oid], "the garden is ripe", {"object": oid, "near": near})
        if self.rules["water_cycle"] == "on" and t >= self.pond["next"]:
            from .materials import POND_CYCLE
            self.pond["idx"] = (self.pond["idx"] + 1) % len(POND_CYCLE)
            self.pond["next"] = t + M.POND_PERIOD
            level = POND_CYCLE[self.pond["idx"]]
            self.emit("pond_level", None, [], f"the pond is {('low', 'middling', 'high')[level]}", {"level": level})
            if level == 0:
                for s in (self.sources[k] for k in sorted(self.sources)):
                    if s["kind"] == "stone_pile":
                        s["amount"] = min(M.SOURCE_KINDS["stone_pile"]["max"], s["amount"] + M.WASH_STONES)
                        self.emit("stones_washed_ashore", None, [s["id"]], "stones washed ashore",
                                  {"amount": M.WASH_STONES})

    def _rain_clock(self) -> None:
        r, t = self.rain, self.tick
        if not r["raining"] and r["start_at"] is not None:
            if not r["noticed"] and t >= r["notice_at"]:
                r["noticed"] = True
                self.emit("rain_notice", None, [], "rain is coming", {"in_s": (r["start_at"] - t) // TICK_HZ})
            if t >= r["start_at"]:
                r["raining"] = True
                self.emit("rain_started", None, [], "it started to rain")
        elif r["raining"] and t >= r["end_at"]:
            r.update({"raining": False, "start_at": None, "end_at": None, "notice_at": None})
            self.emit("rain_stopped", None, [], "the rain stopped")
        r["wetness"] = min(100, r["wetness"] + 1) if r["raining"] else max(0, r["wetness"] - 1)
        if r["raining"] and t % M.RAIN_WEAR_EVERY == 0:
            for oid in sorted(self.objects):
                o = self.objects[oid]
                if o["ready"] and not self.covered(o["x"], o["y"]):
                    self._add_wear(o, 1)

    # --- the tick -------------------------------------------------------------------------------

    def step(self, dt: float = TICK_S) -> list[Event]:
        if abs(dt - TICK_S) > 1e-9:
            raise ValueError(f"dt must be {TICK_S}")
        if self.mode == "frozen":
            return self.drain_events()
        self.tick += 1
        self._clocks()
        order = sorted(self.actors, key=lambda i: (self.actors[i]["kind"] != "character", i))
        for aid in order:
            a = self.actors[aid]
            tasks.advance(self, a)
            if a["cart"] in self.objects:
                self.objects[a["cart"]].update(x=a["x"], y=a["y"])
            if a["carry_obj"] in self.objects:
                self.objects[a["carry_obj"]].update(x=a["x"], y=a["y"])
        for fn in self.systems:
            fn(self)
        if self.tick % HEARTBEAT_EVERY_TICKS == 0:
            self.problems += self.check_invariants()
            self.emit("heartbeat", None, [], None, {"state_hash": self.state_hash()})
        return self.drain_events()

    # --- commands --------------------------------------------------------------------------------

    def apply(self, command: Command) -> CommandResult:
        act = command.action
        if act in SYSTEM_ACTIONS or act in ("rain", "planner_status"):
            if command.source not in ("system", "admin"):
                return CommandResult("refused", f"'{act}' is not available to players")
            return self._system(act, command.args)
        a = self.actors.get(command.persona or "")
        if a is None:
            return CommandResult("refused", f"nobody called '{command.persona}' in the village")
        if self.mode == "frozen":
            return CommandResult("refused", "the village is quiet until the next session")
        args = command.args
        if act == "edit_queue":
            return tasks.edit_queue(self, a, args)
        if act == "set_goals":
            a["goals"] = list(args["goals"])
            self.emit("goals_set", a["id"], [], args.get("public_line") or f"{a['name']} set new goals")
            return CommandResult("applied")
        if act == "set_habits":
            return self._set_habits(a, args)
        if act == "consent":
            ok, why, data = self.add_consent(a, args["to"], args["scope"])
            return CommandResult("applied" if ok else "refused", why, data)
        if act == "say":
            self.emit("say", a["id"], [], args["line"][:LIMITS["say"]], {"line": args["line"]})
            return CommandResult("applied")
        if act == "keepsake":
            obj = self.object_for(args["object_id"])
            if obj is None:
                return CommandResult("refused", f"no object '{args['object_id']}'")
            self.add_keepsake(a, obj, args["sentence"])
            return CommandResult("applied")
        handler = self.handlers.get(act)
        if handler is not None:
            ok, why, data = handler(self, a, args)
            return CommandResult("applied" if ok else "refused", why, data)
        return CommandResult("refused", f"'{act}' is not handled by the world")

    def _set_habits(self, a: dict, args: dict) -> CommandResult:
        who = self.actors.get(str(args.get("target", "")).lower())
        if who is None:
            return CommandResult("refused", f"nobody called '{args.get('target')}'")
        if who["id"] != a["id"] and who.get("owner") != a["id"]:
            return CommandResult("refused", "you can only change your own or your residents' habits")
        if self.rules["habit_edits"] == "between_sessions" and self.mode == "live":
            return CommandResult("refused", "habit changes wait until the session ends")
        errs = H.validate(args["habits"])
        if errs:
            return CommandResult("refused", "; ".join(errs))
        who["habits"] = copy.deepcopy(args["habits"])
        who["habit_steps"], who["habit"] = [], None
        self.emit("habits_edited", a["id"], [who["id"]], f"{a['name']} changed {who['name']}'s habits",
                  {"target": who["id"], "count": len(who["habits"])})
        return CommandResult("applied")

    def _system(self, act: str, args: dict) -> CommandResult:
        if act == "session_open":
            mins = args.get("length_min")
            self.session = {"end_tick": self.tick + int(mins) * 60 * TICK_HZ} if mins else {}
            self.mode, self.closing = "live", {"no_big": False, "square": False}
            for a in self.actors.values():
                a["gathered"] = False
            self.emit("session_opened", None, [], "the session began", {"length_min": mins})
        elif act == "no_big_jobs":
            self.closing["no_big"] = True
            self.emit("no_big_jobs", None, [], "no new big jobs from here on")
        elif act in ("walk_to_square", "quota_close"):
            if act == "quota_close":
                self.closing["no_big"] = True
                self.emit("quota_close", None, [], "closing early to save the weekly budget", dict(args))
            self._to_square()
        elif act == "freeze":
            hook = self.handlers.get("before_freeze")   # glue: e.g. tomorrow's-list fallback
            if hook is not None:
                hook(self)
            for a in self.actors.values():
                tasks.release(self, a)
            self.consents = []
            self.emit("session_closed", None, [], "the session ended")
            self.mode = "frozen"
        elif act == "admin_deplete":
            hit = [s for s in self.sources.values() if args.get("source") in ("all", s["id"], s["m"])]
            if not hit:
                return CommandResult("refused", f"no source '{args.get('source')}'")
            for s in hit:
                s["amount"] = 0
                s["next"] = None if self._timer_free(s) else self.tick + M.SOURCE_KINDS[s["kind"]]["regrow"]
        elif act == "rain":
            self.schedule_rain(args.get("in_s", 0), args.get("duration_s", 60))
        elif act == "planner_status":
            a = self.actors.get(args.get("persona", ""))
            if a is None:
                return CommandResult("refused", "no such character")
            a["planner"] = args.get("status", "idle")
        return CommandResult("applied")

    def _to_square(self) -> None:
        self.closing["square"] = True
        for aid in sorted(self.actors):
            a = self.actors[aid]
            tasks.prepend(self, a, [{"op": "walk", "target": "square", "id": f"sq{self.tick}"},
                                    {"op": "say", "say": "Everyone gathers in the square.",
                                     "id": f"sq{self.tick}b"}])
        self.emit("walk_to_square", None, [], "everyone is heading to the square")

    def frame(self) -> dict[str, Any]:
        """The 2 Hz `village.frames` payload: positions and captions only (public)."""
        return {"characters": [{"id": aid, "x": a["x"], "y": a["y"], "now": a["caption"]}
                               for aid, a in sorted(self.actors.items())]}

    # --- invariants ---------------------------------------------------------------------------------

    def check_invariants(self) -> list[str]:
        """Every piece has exactly one location, and the containers agree."""
        bad: list[str] = []
        seen: dict[str, int] = {}
        for a in self.actors.values():
            for pid in a["carry"]:
                seen[pid] = seen.get(pid, 0) + 1
                if self.pieces.get(pid, {}).get("loc") != f"carry:{a['id']}":
                    bad.append(f"{pid} carried by {a['id']} but loc disagrees")
        for o in self.objects.values():
            for i, s in enumerate(o["slots"]):
                if s["piece"]:
                    seen[s["piece"]] = seen.get(s["piece"], 0) + 1
                    if self.pieces.get(s["piece"], {}).get("loc") != f"slot:{o['id']}:{i}":
                        bad.append(f"{s['piece']} in slot {o['id']}:{i} but loc disagrees")
            for pid in o["rack"]["pieces"]:
                seen[pid] = seen.get(pid, 0) + 1
                if self.pieces.get(pid, {}).get("loc") != f"rack:{o['id']}":
                    bad.append(f"{pid} on rack {o['id']} but loc disagrees")
        for pid, p in self.pieces.items():
            if p["loc"].startswith("tile:"):
                seen[pid] = seen.get(pid, 0) + 1
        for pid in self.pieces:
            if seen.get(pid, 0) != 1:
                bad.append(f"{pid} is in {seen.get(pid, 0)} places")
        for pid in seen:
            if pid not in self.pieces:
                bad.append(f"{pid} is held but does not exist")
        return bad

    def material_counts(self) -> dict[str, int]:
        """Pieces plus source stock per material (for conservation checks)."""
        out = {m: 0 for m in MATERIALS}
        for p in self.pieces.values():
            out[p["m"]] += 1
        for s in self.sources.values():
            out[s["m"]] += s["amount"]
        return out


def new_world(rules: dict | None = None, seed: int = 1) -> World:
    """The starting village (main.default_make_world's fresh world)."""
    return World(seed=seed, rules=rules)
