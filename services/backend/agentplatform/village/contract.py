"""The village's shared contract (plan U0.5): the World protocol, the value
types that cross unit boundaries, the closed JSON schema for planner ops and
tool actions, the action -> role table, the event kinds and the config keys.

Pure stdlib, no I/O. Every later unit imports from here; changing anything in
this file goes through the orchestrator. Behaviour lives in the units
(world.py, hands.py, planner_ops.py, commands.py, ...), not here.
"""
import hashlib
import json
from dataclasses import asdict, dataclass, field, fields
from typing import Any, ClassVar, Protocol, runtime_checkable

# --- time and space ---------------------------------------------------------

TICK_S = 0.25                 # fixed step: every tick advances sim time by exactly this
TICK_HZ = 4
FRAME_EVERY_TICKS = 2         # village.frames at 2 Hz
HEARTBEAT_EVERY_TICKS = 20    # heartbeat event carrying state_hash (L7)
MAP_W, MAP_H = 64, 40
VIEW_RADIUS = 6               # tiles: the "view circle"

# --- limits (design caps and ambiguity-register defaults) -------------------

LIMITS: dict[str, int] = {
    "carry": 2,                    # pieces a character carries
    "cart_carry": 4,
    "steps_per_edit": 12,          # steps per edit_queue chunk
    "ops_per_edit": 20,
    "project_size": 12,            # [A20] pieces
    "goals": 3,
    "habits": 8,
    "residents_live": 2,           # per character
    "pulls_active": 3,
    "marks_per_session": 3,
    "deps_per_step": 12,
    "decor_options": 3,
    "tomorrow_steps": 40,          # item cap; "≤10 min of work" is checked by the engine
    "give_up_blocked_s": 180,      # [A17]
    "invite_wait_s": 60,           # [A19]
    "request_id": 100,
    # text caps (characters)
    "say": 140, "step_say": 100, "caption": 100, "guess": 140, "goal": 140,
    "public_line": 140, "note": 1000, "mark": 200, "keepsake": 140,
    "section": 1500, "prompt_note": 140, "line": 140, "description": 200,
    "away_note": 280, "reason": 140, "name": 32, "ref": 64, "decor": 40,
    "public_text": 200, "query": 200,
}

# --- vocabularies -----------------------------------------------------------

MATERIALS = ("wood", "reeds", "rope", "stone", "seeds", "clay")
# Design "Proposing new objects": one function from a fixed list.
FUNCTIONS = ("sit", "shelter", "store", "carry", "dry", "grow", "look")

STEP_OPS = ("walk", "pickup", "carry_to", "drop", "place", "dismantle", "repair", "use",
            "visit", "rest", "look", "say", "wait_for", "plant", "harvest", "set_aside",
            "keepsake")
STEP_STATES = ("queued", "active", "blocked", "done", "failed", "skipped")

COMMAND_SOURCES = ("planner", "reflection", "admin", "system")
COMMAND_STATES = ("accepted", "applied", "refused")

SESSION_KINDS = ("daily", "test")
SESSION_STATES = ("scheduled", "live", "closing", "closed", "rest", "skipped")
SNAPSHOT_KINDS = ("periodic", "open", "close")

RUN_ROLES = ("planner", "reflection")
VILLAGE_RUN_STATES = ("launching", "running", "done", "skipped", "failed", "cancelled")

KNOWLEDGE_KINDS = ("tried", "saw", "told")
PROMPT_CHANGE_KINDS = ("permanent", "trial")
PROMPT_CHANGE_STATES = ("pending", "applied", "reverted", "undone", "lapsed", "kept", "renewed")

IDLE_BINS = ("working", "blocked", "habit", "resting", "thinking_no_steps", "waiting_for")

# Why a `wait` returned (§5.2); recorded on village_planner_cycles.wake_reason.
WAKE_REASONS = ("runway", "step_done", "step_failed", "step_blocked", "nearby", "addressed",
                "notice", "brief_changed", "session_state", "heartbeat")

# System commands the clock journals so replay matches (§3 Closing, §4).
SYSTEM_ACTIONS = ("session_open", "no_big_jobs", "walk_to_square", "freeze",
                  "quota_close", "admin_deplete")

# Every `village_events.kind`. Units emit only these.
EVENT_KINDS = (
    # session lifecycle and clock
    "session_opened", "session_closing", "session_closed", "no_big_jobs", "walk_to_square",
    "quota_close", "heartbeat",
    # steps and queues
    "step_started", "step_done", "step_failed", "step_blocked", "step_unblocked",
    "step_skipped", "queue_edited", "command_refused", "plan_dropped",
    # materials
    "pickup", "drop", "piece_placed", "set_aside", "consent_given", "source_refilled",
    "stones_washed_ashore",
    # projects and objects
    "project_started", "project_cancelled", "object_first_ready", "object_first_use",
    "object_used", "object_moved", "object_dismantled", "object_worn", "object_repaired",
    "decor_skipped", "keepsake_added", "keepsake_removed", "tool_broken", "tool_repaired",
    "rack_done", "garden_ripe", "shared_meal",
    # hidden rules and knowledge
    "guess_made", "guess_resolved", "rule_outcome",
    # speech and social
    "say", "tell", "invite", "invite_answered", "visit_started", "visit_ended",
    "pull_set", "moment_marked",
    # residents
    "habit_started", "habits_edited", "goals_set", "resident_created", "resident_edited",
    "resident_retired", "chatter", "wish_created", "wish_granted", "wish_expired",
    # weather and water
    "rain_notice", "rain_started", "rain_stopped", "pond_level",
    # minds (runs)
    "planner_status", "reflection_started", "reflection_done", "reflection_skipped",
    "reflection_failed", "tomorrow_written",
    # self-evolution
    "prompt_change_pending", "prompt_change_applied", "prompt_change_reverted",
    "prompt_change_undone", "prompt_change_lapsed", "trial_kept", "trial_renewed",
)

# --- config (village_config.rules, §13) -------------------------------------

# Pai's seven open rules (plan "Open for Pai"): key -> allowed values, default first.
OPEN_FOR_PAI: dict[str, tuple[str, ...]] = {
    "between_sessions": ("frozen", "ambient"),
    "water_cycle": ("on", "off"),
    "prompt_apply": ("next_session", "next_planner_call"),
    "shared_pile": ("shared", "per_character"),
    "conflict": ("first_pickup", "reserve_on_plan", "yield_to_owner_project"),
    "habit_edits": ("live", "between_sessions"),
    "peer_queue_visibility": ("none", "next1", "full"),
}

RULE_CHOICES: dict[str, tuple[str, ...]] = {
    **OPEN_FOR_PAI,
    "spectator_queue_visibility": ("next3_over_head,next5_on_click", "now_only"),
    "play_section_scope": ("village_only", "everywhere"),
}

DEFAULT_RULES: dict[str, Any] = {
    **{k: v[0] for k, v in OPEN_FOR_PAI.items()},
    "spectator_queue_visibility": "next3_over_head,next5_on_click",
    "closing": {"no_big_jobs_min": 10, "walk_to_square_min": 3},
    "auto_schedule": False,
    "session_start_local": "12:00",
    "session_tz": "America/Toronto",
    "lengths": {"normal": 60, "reduced": 30, "reduce_above": 70, "rest_above": 85},
    "five_hour_skip_at": 85,
    "five_hour_close_at": 95,
    "quota_stale_min": 15,
    "planner_agent": "village-planner",
    "planner_min_s": 10,
    "planner_heartbeat_s": 30,
    "runway_s": 20,
    "planner_max_restarts": 20,
    "reflection_slots": {"pai": "5:00", "kai": "8:20", "olu": "11:40"},
    "reflection_every_min": 10,
    "reflection_timeout_s": 480,
    "final_slot_before_end_min": 12,
    # [A13] Kyle 2026-11-27 "learning was the point": reflections save to memory.
    "reflection_memory_writes": True,
    "play_section_scope": "village_only",
    "play_section_max_chars": 1500,
    "prompt_edits_per_session": 3,
    "rain_notice_s": 60,
    # Phase 1 is Pai only; Phase 2 sets ["pai", "kai", "olu"] in the config row.
    "players": ["pai"],
}


def merged_rules(stored: dict[str, Any] | None) -> dict[str, Any]:
    """DEFAULT_RULES overlaid with the stored row; nested dicts merge one level."""
    out = json.loads(json.dumps(DEFAULT_RULES))
    for key, value in (stored or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = {**out[key], **value}
        else:
            out[key] = value
    return out


# --- hashing ----------------------------------------------------------------

def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      default=str)


def args_hash(args: dict[str, Any]) -> str:
    """sha256 of a call's canonical args without request_id (§7 idempotency)."""
    return hashlib.sha256(canonical_json(
        {k: v for k, v in args.items() if k != "request_id"}).encode("utf-8")).hexdigest()


def hash_state(state: dict[str, Any]) -> str:
    """The one way a world state is hashed (World.state_hash, heartbeats, snapshots)."""
    return hashlib.sha256(canonical_json(state).encode("utf-8")).hexdigest()


# --- value types --------------------------------------------------------------

class _Record:
    """dict round-trip for the dataclasses below; unknown keys are refused."""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)  # type: ignore[call-overload]

    @classmethod
    def from_dict(cls, data: dict[str, Any]):
        names = {f.name for f in fields(cls)}  # type: ignore[arg-type]
        extra = set(data) - names
        if extra:
            raise ValueError(f"{cls.__name__}: unknown fields {sorted(extra)}")
        return cls(**data)


@dataclass
class Step(_Record):
    """One to-do list item (§2 World model). Planner input is STEP_SCHEMA;
    state/blocked_* are the engine's."""
    op: str
    id: str = ""
    target: str | None = None
    say: str | None = None
    deps: list[str] = field(default_factory=list)
    needs: dict[str, Any] | None = None       # {"material": str, "n": int}
    big: bool = False
    guess: str | None = None
    decor: list[str] = field(default_factory=list)
    state: str = "queued"
    blocked_reason: str | None = None
    blocked_since: int | None = None          # tick


@dataclass
class Command(_Record):
    """A journal row (mirrors village_commands). `World.apply` takes it."""
    session_id: str
    source: str
    action: str
    args: dict[str, Any] = field(default_factory=dict)
    seq: int | None = None
    run_id: str | None = None
    persona: str | None = None
    request_id: str | None = None
    args_hash: str | None = None
    state: str = "accepted"
    reason: str | None = None
    apply_tick: int | None = None


@dataclass
class CommandResult(_Record):
    state: str                                # "applied" or "refused"
    reason: str | None = None
    data: dict[str, Any] = field(default_factory=dict)  # e.g. per-op refusals, new queue_version


@dataclass
class Event(_Record):
    """What World.step/apply emit; the store adds seq, session_id and epoch."""
    tick: int
    kind: str
    actor: str | None = None
    subjects: list[str] = field(default_factory=list)
    public_text: str | None = None            # public by construction, ≤200
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class Snapshot(_Record):
    """A village_snapshots row; `state` is World.to_snapshot()."""
    session_id: str
    tick: int
    last_applied_seq: int
    state_hash: str
    state: dict[str, Any]
    kind: str = "periodic"


@runtime_checkable
class World(Protocol):
    """The in-memory world, single writer of game state. Deterministic: the
    same snapshot plus the same commands at the same ticks give the same hash."""
    tick: int

    def step(self, dt: float = TICK_S) -> list[Event]:
        """Advance one fixed tick (dt must equal TICK_S) and return its events."""
        ...

    def apply(self, command: Command) -> CommandResult:
        """Apply one journaled command at the current tick."""
        ...

    def state_hash(self) -> str:
        """hash_state(self.to_snapshot())."""
        ...

    def to_snapshot(self) -> dict[str, Any]:
        """JSON-safe full state (the snapshot's `state` column)."""
        ...

    @classmethod
    def from_snapshot(cls, state: dict[str, Any]) -> "World":
        ...


# --- closed JSON schema ------------------------------------------------------
# A subset of JSON Schema (type, enum, const, properties, required,
# additionalProperties: false, items, min/maxItems, min/maxLength,
# minimum/maximum, oneOf) so planner_ops.py and the tool share one definition.

def _str(cap: str | int, min_len: int = 1) -> dict[str, Any]:
    n = cap if isinstance(cap, int) else LIMITS[cap]
    return {"type": "string", "minLength": min_len, "maxLength": n}


def _int(lo: int, hi: int) -> dict[str, Any]:
    return {"type": "integer", "minimum": lo, "maximum": hi}


def _enum(values: tuple[str, ...]) -> dict[str, Any]:
    return {"type": "string", "enum": list(values)}


def _obj(props: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": list(required),
            "additionalProperties": False}


def _list(item: dict[str, Any], max_items: int, min_items: int = 0) -> dict[str, Any]:
    return {"type": "array", "items": item, "minItems": min_items, "maxItems": max_items}


REF = _str("ref")                              # ids, names, places, targets
MATERIAL = _enum(MATERIALS)
FUNCTION = _enum(FUNCTIONS)
REQUEST_ID = _str("request_id")
DECOR = _list(_str("decor"), LIMITS["decor_options"])   # outside the menu → built plain

STEP_SCHEMA = _obj({
    "id": REF,
    "op": _enum(STEP_OPS),
    "target": REF,
    "say": _str("step_say"),
    "deps": _list(REF, LIMITS["deps_per_step"]),
    "needs": _obj({"material": MATERIAL, "n": _int(1, LIMITS["project_size"])}, ("material",)),
    "big": {"type": "boolean"},
    "guess": _str("guess"),
    "decor": DECOR,
}, ("op",))
STEPS = _list(STEP_SCHEMA, LIMITS["steps_per_edit"], 1)

# §7.1 habit grammar: kind -> its argument schemas. Anything else is refused.
HABIT_WHEN: dict[str, dict[str, Any]] = {
    "pile_below": {"material": MATERIAL, "n": _int(0, 50)},
    "loose_near_work": {"material": MATERIAL},
    "hammering_nearby": {},
    "raining": {},
    "ripe_garden": {},
    "broken_object_within": {"r": _int(1, 20)},
    "idle": {},
    "time_left_under": {"min": _int(1, 60)},
}
HABIT_DO: dict[str, dict[str, Any]] = {
    "fetch": {"material": MATERIAL, "to": REF},
    "stack_loose": {},
    "move_away": {},
    "repair_nearest": {},
    "harvest": {},
    "walk_path": {"a": REF, "b": REF},
    "rest_at": {"place": REF},
    "carry_tools_to_broken": {},
}


def _kinded(table: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {"oneOf": [_obj({"kind": {"const": k}, **args}, ("kind", *args))
                      for k, args in table.items()]}


HABIT_SCHEMA = _obj({"when": _kinded(HABIT_WHEN), "do": _kinded(HABIT_DO),
                     "say": _str("step_say")}, ("when", "do"))

# edit_queue ops (§5.2): op name -> (properties, required).
_OPS: dict[str, tuple[dict[str, Any], tuple[str, ...]]] = {
    "append": ({"steps": STEPS}, ("steps",)),
    "insert_after": ({"after": REF, "steps": STEPS}, ("after", "steps")),
    "replace": ({"step_id": REF, "steps": STEPS}, ("step_id", "steps")),
    "remove": ({"step_id": REF}, ("step_id",)),
    "clear_from": ({"step_id": REF}, ("step_id",)),
    "say": ({"line": _str("say")}, ("line",)),
    "tell": ({"line": _str("say"), "claim": _str("say"), "rule_key": REF}, ("line", "claim")),
    "drop_plan": ({"reason": _str("reason"), "project_id": REF}, ("reason",)),
    "start_project": ({"caption": _str("caption"), "function": FUNCTION,
                       "materials": _list(MATERIAL, len(MATERIALS), 1),
                       "size": _int(1, LIMITS["project_size"]), "place": REF, "decor": DECOR},
                      ("caption", "function", "materials", "size")),
    "set_aside": ({"material": MATERIAL, "n": _int(1, LIMITS["project_size"]),
                   "pieces": _list(REF, LIMITS["project_size"])}, ()),
    "allow_set_aside": ({"to": REF, "scope": _enum(("once", "session"))}, ("to", "scope")),
    "invite": ({"to": REF, "place": REF, "line": _str("line")}, ("to", "place", "line")),
    "answer_invite": ({"invite_id": REF, "reply": _enum(("accept", "decline", "later"))},
                      ("invite_id", "reply")),
    "pull": ({"target": REF, "toward": REF, "line": _str("line")}, ("target", "toward", "line")),
    "mark_moment": ({"sentence": _str("mark"), "shared": {"type": "boolean"}}, ("sentence",)),
    "keepsake": ({"object_id": REF, "sentence": _str("keepsake")}, ("object_id", "sentence")),
}
OP_NAMES = tuple(_OPS)
OP_SCHEMA = {"oneOf": [_obj({"op": {"const": name}, **props}, ("op", *req))
                       for name, (props, req) in _OPS.items()]}

# --- tool actions (§7) -------------------------------------------------------

PLANNER, REFLECTION = "planner", "reflection"
BOTH = frozenset({PLANNER, REFLECTION})

# action -> roles allowed (checked against the caller's village_runs.role).
ACTION_ROLES: dict[str, frozenset[str]] = {
    "help": BOTH, "look": BOTH, "wait": frozenset({PLANNER}), "edit_queue": BOTH,
    "set_goals": frozenset({REFLECTION}), "set_habits": frozenset({REFLECTION}),
    "resident": frozenset({REFLECTION}), "note": frozenset({REFLECTION}),
    "recall_notes": frozenset({REFLECTION}), "mark": BOTH, "keepsake": BOTH,
    "prompt": frozenset({REFLECTION}), "trial": frozenset({REFLECTION}), "pull": BOTH,
    "invite": BOTH, "answer": BOTH, "consent": BOTH,
    "propose_object": frozenset({REFLECTION}), "tomorrow": frozenset({REFLECTION}),
    "say": BOTH,
}
READ_ACTIONS = frozenset({"help", "look", "wait", "recall_notes"})   # no request_id
WRITE_ACTIONS = frozenset(ACTION_ROLES) - READ_ACTIONS               # request_id required
FINAL_SLOT_ONLY = frozenset({"tomorrow"})                            # reflection slot "final"


def _action(props: dict[str, Any], required: tuple[str, ...] = (), write: bool = True):
    if write:
        props, required = {"request_id": REQUEST_ID, **props}, ("request_id", *required)
    return _obj(props, required)


ACTION_ARGS: dict[str, dict[str, Any]] = {
    "help": _action({}, write=False),
    "look": _action({}, write=False),
    "wait": _action({"cursor": REF}, write=False),
    "recall_notes": _action({"q": _str("query", 0)}, write=False),
    "edit_queue": _action({"base_version": _int(0, 2**31 - 1),
                           "ops": _list(OP_SCHEMA, LIMITS["ops_per_edit"], 1)},
                          ("base_version", "ops")),
    "set_goals": _action({"goals": _list(_str("goal"), LIMITS["goals"]),
                          "public_line": _str("public_line")}, ("goals",)),
    "set_habits": _action({"target": REF, "habits": _list(HABIT_SCHEMA, LIMITS["habits"])},
                          ("target", "habits")),
    "resident": _action({"op": _enum(("create", "edit", "retire")), "name": _str("name"),
                         "role": REF, "look": REF}, ("op", "name")),
    "note": _action({"text": _str("note"), "links": _list(REF, 10)}, ("text",)),
    "mark": _action({"sentence": _str("mark"), "shared": {"type": "boolean"}}, ("sentence",)),
    "keepsake": _action({"object_id": REF, "sentence": _str("keepsake")},
                        ("object_id", "sentence")),
    "prompt": _action({"section": _str("section", 0), "kind": _enum(PROMPT_CHANGE_KINDS),
                       "note": _str("prompt_note"), "mark_id": REF}, ("section", "kind")),
    "trial": _action({"change_id": REF, "op": _enum(("keep", "renew"))}, ("change_id", "op")),
    "pull": _action({"target": REF, "toward": REF, "line": _str("line")},
                    ("target", "toward", "line")),
    "invite": _action({"to": REF, "place": REF, "line": _str("line")}, ("to", "place", "line")),
    "answer": _action({"invite_id": REF, "reply": _enum(("accept", "decline", "later"))},
                      ("invite_id", "reply")),
    "consent": _action({"to": REF, "scope": _enum(("once", "session"))}, ("to", "scope")),
    "propose_object": _action({"description": _str("description"),
                               "materials": _list(MATERIAL, len(MATERIALS), 1),
                               "function": FUNCTION, "size": _int(1, LIMITS["project_size"]),
                               "decor": DECOR}, ("description", "materials", "function", "size")),
    "tomorrow": _action({"steps": _list(STEP_SCHEMA, LIMITS["tomorrow_steps"], 1),
                         "away_note": _str("away_note")}, ("steps",)),
    "say": _action({"line": _str("say")}, ("line",)),
}
ACTIONS = tuple(ACTION_ARGS)


def tool_params() -> dict[str, Any]:
    """The flat `params` schema for tools/village/tool.yaml: `action` plus the
    union of every action's arguments (same-named enums merge, same-named
    strings take the widest length bounds). Per-action
    shape is enforced by the engine with ACTION_ARGS[action]."""
    props: dict[str, Any] = {"action": _enum(ACTIONS)}
    for schema in ACTION_ARGS.values():
        for name, sub in schema["properties"].items():
            have = props.get(name)
            if have is None:
                props[name] = sub
            elif have != sub:
                if "enum" in have and "enum" in sub:
                    props[name] = {**have, "enum": have["enum"] + [
                        v for v in sub["enum"] if v not in have["enum"]]}
                elif have.get("type") == sub.get("type") == "string" and \
                        "enum" not in have and "enum" not in sub:
                    props[name] = {**have, "minLength": min(have["minLength"], sub["minLength"]),
                                   "maxLength": max(have["maxLength"], sub["maxLength"])}
                else:
                    raise ValueError(f"tool param {name!r} has conflicting schemas")
    return _obj(props, ("action",))


# --- validator ---------------------------------------------------------------

_TYPES: dict[str, tuple[type, ...]] = {"string": (str,), "integer": (int,), "boolean": (bool,),
                                       "object": (dict,), "array": (list,)}


def check(schema: dict[str, Any], value: Any, path: str = "$") -> list[str]:
    """Validate `value` against the schema subset above; [] means valid.
    Messages are plain words a model can act on."""
    if "oneOf" in schema:
        results = [check(s, value, path) for s in schema["oneOf"]]
        if any(not r for r in results):
            return []
        # Point at the variant whose discriminator (op/kind const) matched, if any.
        for sub, errs in zip(schema["oneOf"], results):
            key = next((k for k, p in sub.get("properties", {}).items() if "const" in p), None)
            if key and isinstance(value, dict) and value.get(key) == sub["properties"][key]["const"]:
                return errs
        names = [p.get("const") for s in schema["oneOf"]
                 for p in s.get("properties", {}).values() if "const" in p]
        return [f"{path}: must be one of {names}" if names else f"{path}: matches no option"]
    if "const" in schema:
        return [] if value == schema["const"] else [f"{path}: must be {schema['const']!r}"]
    kind = schema.get("type")
    if kind:
        ok = isinstance(value, _TYPES[kind]) and not (kind == "integer" and isinstance(value, bool))
        if not ok:
            return [f"{path}: must be {kind}"]
    errs: list[str] = []
    if "enum" in schema and value not in schema["enum"]:
        errs.append(f"{path}: must be one of {schema['enum']}")
    if kind == "string":
        if len(value) < schema.get("minLength", 0):
            errs.append(f"{path}: must not be empty")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errs.append(f"{path}: longer than {schema['maxLength']} characters")
    elif kind == "integer":
        if "minimum" in schema and value < schema["minimum"]:
            errs.append(f"{path}: below {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errs.append(f"{path}: above {schema['maximum']}")
    elif kind == "array":
        if len(value) < schema.get("minItems", 0):
            errs.append(f"{path}: needs at least {schema['minItems']} item(s)")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errs.append(f"{path}: at most {schema['maxItems']} items")
        for i, item in enumerate(value):
            errs += check(schema["items"], item, f"{path}[{i}]")
    elif kind == "object":
        props = schema.get("properties", {})
        for name in schema.get("required", ()):
            if name not in value:
                errs.append(f"{path}.{name}: required")
        for name, item in value.items():
            if name in props:
                errs += check(props[name], item, f"{path}.{name}")
            elif schema.get("additionalProperties") is False:
                errs.append(f"{path}.{name}: unknown field")
    return errs
