"""U0.5: the village shared contract (plan 2026-11-27-village-game §12)."""
import json
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from agentplatform.db import (VillageCommand, VillageConfig, VillageEvent, VillageNote,
                              VillageRun, VillageSession, VillageSnapshot)
from agentplatform.village import contract as c

TOOL_YAML = Path(__file__).resolve().parents[3] / "tools" / "village" / "tool.yaml"

STEP = {"op": "pickup", "id": "s1", "target": "woods", "say": "getting wood",
        "deps": [], "needs": {"material": "wood", "n": 2}, "big": False,
        "guess": "wet wood holds", "decor": ["faces the reeds"]}

OP_EXAMPLES = {
    "append": {"steps": [STEP]},
    "insert_after": {"after": "s1", "steps": [{"op": "walk", "target": "square"}]},
    "replace": {"step_id": "s1", "steps": [{"op": "rest"}]},
    "remove": {"step_id": "s1"},
    "clear_from": {"step_id": "s1"},
    "say": {"line": "hello"},
    "tell": {"line": "careful", "claim": "reed rope snaps under a cart"},
    "drop_plan": {"reason": "rain"},
    "start_project": {"caption": "A bench", "function": "sit", "materials": ["wood"], "size": 6},
    "set_aside": {"material": "wood", "n": 3},
    "allow_set_aside": {"to": "kai", "scope": "once"},
    "invite": {"to": "kai", "place": "square", "line": "come sit"},
    "answer_invite": {"invite_id": "i1", "reply": "accept"},
    "pull": {"target": "bench", "toward": "garden", "line": "I want company"},
    "mark_moment": {"sentence": "the rope held", "shared": True},
    "keepsake": {"object_id": "o1", "sentence": "for Kai"},
}


def test_every_op_has_a_valid_example():
    assert set(OP_EXAMPLES) == set(c.OP_NAMES)
    for name, args in OP_EXAMPLES.items():
        assert c.check(c.OP_SCHEMA, {"op": name, **args}) == [], name


@pytest.mark.parametrize("op, why", [
    ({"op": "teleport"}, "must be one of"),
    ({"op": "say", "line": "x", "volume": 11}, "unknown field"),
    ({"op": "say", "line": "x" * 141}, "longer than 140"),
    ({"op": "say"}, "required"),
    ({"op": "start_project", "caption": "c", "function": "fly", "materials": ["wood"],
      "size": 3}, "must be one of"),
    ({"op": "start_project", "caption": "c", "function": "sit", "materials": ["gold"],
      "size": 3}, "must be one of"),
    ({"op": "start_project", "caption": "c", "function": "sit", "materials": ["wood"],
      "size": 13}, "above 12"),
    ({"op": "append", "steps": [{"op": "fly"}]}, "must be one of"),
    ({"op": "append", "steps": [{"op": "rest", "state": "done"}]}, "unknown field"),
    ({"op": "append", "steps": [{"op": "rest"}] * 13}, "at most 12"),
    ({"op": "append", "steps": []}, "at least 1"),
])
def test_ops_are_closed(op, why):
    errs = c.check(c.OP_SCHEMA, op)
    assert errs and any(why in e for e in errs), errs


def test_habit_grammar_is_closed():
    args = {"material": "stone", "n": 3, "r": 4, "min": 5, "to": "workshop", "a": "square",
            "b": "dock", "place": "square"}
    for when, wargs in c.HABIT_WHEN.items():
        for do, dargs in c.HABIT_DO.items():
            habit = {"when": {"kind": when, **{k: args[k] for k in wargs}},
                     "do": {"kind": do, **{k: args[k] for k in dargs}}, "say": "on it"}
            assert c.check(c.HABIT_SCHEMA, habit) == [], habit
    assert c.check(c.HABIT_SCHEMA, {"when": {"kind": "bored"}, "do": {"kind": "harvest"}})
    assert c.check(c.HABIT_SCHEMA, {"when": {"kind": "pile_below", "material": "stone"},
                                    "do": {"kind": "harvest"}})  # n missing
    assert c.check(c.HABIT_SCHEMA, {"when": {"kind": "idle", "n": 1}, "do": {"kind": "harvest"}})


def test_action_tables_agree():
    assert set(c.ACTION_ROLES) == set(c.ACTION_ARGS)
    assert c.ACTION_ROLES["wait"] == {"planner"}
    assert c.ACTION_ROLES["edit_queue"] == {"planner", "reflection"}
    for action in ("set_goals", "set_habits", "resident", "note", "recall_notes", "prompt",
                   "trial", "propose_object", "tomorrow"):
        assert c.ACTION_ROLES[action] == {"reflection"}, action
    assert c.FINAL_SLOT_ONLY <= c.ACTION_ROLES.keys()
    assert c.READ_ACTIONS == {"help", "look", "wait", "recall_notes"}
    for action, schema in c.ACTION_ARGS.items():
        assert ("request_id" in schema["required"]) == (action in c.WRITE_ACTIONS), action


def test_action_args_validate():
    assert c.check(c.ACTION_ARGS["wait"], {"cursor": "abc"}) == []
    assert c.check(c.ACTION_ARGS["wait"], {"request_id": "r"})  # reads take none
    ok = {"request_id": "r1", "base_version": 3, "ops": [{"op": "append", **OP_EXAMPLES["append"]}]}
    assert c.check(c.ACTION_ARGS["edit_queue"], ok) == []
    assert c.check(c.ACTION_ARGS["edit_queue"], {**ok, "request_id": "x" * 101})
    assert c.check(c.ACTION_ARGS["edit_queue"], {k: v for k, v in ok.items() if k != "request_id"})
    assert c.check(c.ACTION_ARGS["edit_queue"], {**ok, "agent": "kai"})  # no addressing others
    assert c.check(c.ACTION_ARGS["edit_queue"], {**ok, "base_version": True})
    assert c.check(c.ACTION_ARGS["set_habits"], {"request_id": "r", "target": "self", "habits": [
        {"when": {"kind": "raining"}, "do": {"kind": "rest_at", "place": "workshop"}}]}) == []
    assert c.check(c.ACTION_ARGS["prompt"], {"request_id": "r", "section": "x" * 1501,
                                             "kind": "trial"})


def test_tool_params_is_a_superset_of_every_action():
    params = c.tool_params()
    json.dumps(params)
    assert params["properties"]["action"]["enum"] == list(c.ACTIONS)
    for schema in c.ACTION_ARGS.values():
        assert set(schema["properties"]) <= set(params["properties"])
    assert set(params["properties"]["op"]["enum"]) == {"create", "edit", "retire", "keep", "renew"}
    assert params["properties"]["sentence"]["maxLength"] == 200
    assert c.check(params, {"action": "say", "request_id": "r", "line": "hi"}) == []
    assert c.check(params, {"action": "shout"})


def _strip(node):
    if isinstance(node, dict):
        return {k: _strip(v) for k, v in node.items() if k != "description"}
    if isinstance(node, list):
        return [_strip(v) for v in node]
    return node


def test_tool_yaml_params_match_the_contract():
    if not TOOL_YAML.exists():
        pytest.skip("tools/village/tool.yaml is U4's; checked once it exists")
    yaml = pytest.importorskip("yaml")
    shipped = yaml.safe_load(TOOL_YAML.read_text())["params"]
    assert _strip(shipped) == _strip(c.tool_params())


def test_records_round_trip():
    step = c.Step.from_dict(STEP)
    assert step.state == "queued"
    assert c.Step.from_dict(json.loads(json.dumps(step.to_dict()))) == step
    cmd = c.Command(session_id="s", source="planner", action="edit_queue",
                    args={"ops": []}, seq=7, run_id="r", persona="pai", request_id="q",
                    args_hash=c.args_hash({"ops": [], "request_id": "q"}))
    assert c.Command.from_dict(json.loads(json.dumps(cmd.to_dict()))) == cmd
    snap = c.Snapshot(session_id="s", tick=40, last_applied_seq=7,
                      state_hash=c.hash_state({"a": 1}), state={"a": 1}, kind="open")
    assert c.Snapshot.from_dict(snap.to_dict()) == snap
    ev = c.Event(tick=1, kind="step_blocked", actor="pai", public_text="waiting for reeds")
    assert c.Event.from_dict(ev.to_dict()) == ev
    res = c.CommandResult(state="refused", reason="that step already finished")
    assert c.CommandResult.from_dict(res.to_dict()) == res
    with pytest.raises(ValueError):
        c.Step.from_dict({"op": "rest", "colour": "red"})
    assert set(c.Command.__dataclass_fields__) >= {
        col.name for col in VillageCommand.__table__.columns} - {"created_at"}


def test_hashes_are_canonical():
    assert c.args_hash({"a": 1, "b": [2], "request_id": "x"}) == c.args_hash({"b": [2], "a": 1})
    assert c.args_hash({"a": 1}) != c.args_hash({"a": 2})
    assert c.hash_state({"x": 1, "y": {"z": 2}}) == c.hash_state({"y": {"z": 2}, "x": 1})


def test_config_defaults():
    keys = {"between_sessions", "water_cycle", "shared_pile", "conflict", "prompt_apply",
            "habit_edits", "peer_queue_visibility", "spectator_queue_visibility", "closing",
            "auto_schedule", "session_start_local", "session_tz", "lengths",
            "five_hour_skip_at", "five_hour_close_at", "quota_stale_min", "planner_agent",
            "planner_min_s", "planner_heartbeat_s", "runway_s", "planner_max_restarts",
            "reflection_slots", "reflection_every_min", "reflection_timeout_s",
            "final_slot_before_end_min", "reflection_memory_writes", "play_section_scope",
            "play_section_max_chars", "prompt_edits_per_session", "rain_notice_s", "players"}
    assert set(c.DEFAULT_RULES) == keys
    assert len(c.OPEN_FOR_PAI) == 7
    assert {k: c.DEFAULT_RULES[k] for k in c.OPEN_FOR_PAI} == {
        "between_sessions": "frozen", "water_cycle": "on", "prompt_apply": "next_session",
        "shared_pile": "shared", "conflict": "first_pickup", "habit_edits": "live",
        "peer_queue_visibility": "none"}
    for key, choices in c.RULE_CHOICES.items():
        assert c.DEFAULT_RULES[key] in choices
    assert c.DEFAULT_RULES["auto_schedule"] is False
    assert c.DEFAULT_RULES["reflection_memory_writes"] is True


def test_merged_rules_overlays_without_mutating_defaults():
    rules = c.merged_rules({"closing": {"walk_to_square_min": 5}, "players": ["pai", "kai"]})
    assert rules["closing"] == {"no_big_jobs_min": 10, "walk_to_square_min": 5}
    assert rules["players"] == ["pai", "kai"]
    rules["lengths"]["normal"] = 1
    assert c.DEFAULT_RULES["lengths"]["normal"] == 60
    assert c.DEFAULT_RULES["closing"]["walk_to_square_min"] == 3
    assert c.merged_rules(None) == c.DEFAULT_RULES


def test_event_kinds():
    assert len(c.EVENT_KINDS) == len(set(c.EVENT_KINDS))
    assert {"step_blocked", "piece_placed", "object_first_ready", "object_first_use",
            "heartbeat", "guess_resolved", "wish_granted"} <= set(c.EVENT_KINDS)


class _CounterWorld:
    def __init__(self, n=0):
        self.tick, self.n = 0, n

    def step(self, dt=c.TICK_S):
        assert dt == c.TICK_S
        self.tick += 1
        return []

    def apply(self, command):
        self.n += command.args.get("by", 1)
        return c.CommandResult(state="applied")

    def state_hash(self):
        return c.hash_state(self.to_snapshot())

    def to_snapshot(self):
        return {"tick": self.tick, "n": self.n}

    @classmethod
    def from_snapshot(cls, state):
        w = cls(state["n"]); w.tick = state["tick"]
        return w


def test_world_protocol_shape():
    w = _CounterWorld()
    assert isinstance(w, c.World)
    w.step(); w.apply(c.Command(session_id="s", source="system", action="admin_deplete",
                                args={"by": 2}))
    again = _CounterWorld.from_snapshot(json.loads(json.dumps(w.to_snapshot())))
    assert again.state_hash() == w.state_hash()


async def test_village_models_create_and_constrain(sf):
    async with sf() as s:
        s.add(VillageConfig(rules={"players": ["pai"]}))
        s.add(VillageSession(day="2026-11-27", kind="daily", state="live"))
        s.add(VillageSession(day="2026-11-27", kind="test"))
        s.add(VillageSession(day="2026-11-27", kind="test"))
        s.add(VillageRun(session_id="s1", persona="pai", role="planner", run_id="r1"))
        s.add(VillageSnapshot(session_id="s1", tick=0, state_hash="h", state={"a": 1},
                              kind="open"))
        s.add(VillageEvent(session_id="s1", epoch="e1", tick=1, kind="say",
                           public_text="hi"))
        s.add(VillageNote(agent="pai", session_id="s1", text="private"))
        for rid in ("q1", "q2"):
            s.add(VillageCommand(session_id="s1", source="planner", run_id="r1",
                                 request_id=rid, action="say", args={"line": "hi"}))
        await s.commit()
        seqs = (await s.execute(select(VillageCommand.seq).order_by(VillageCommand.seq))).scalars()
        a, b = list(seqs)
        assert b > a
    async with sf() as s:
        s.add(VillageCommand(session_id="s1", source="planner", run_id="r1", request_id="q1",
                             action="say", args={}))
        with pytest.raises(IntegrityError):
            await s.commit()
    async with sf() as s:
        s.add(VillageSession(day="2026-11-27", kind="daily"))
        with pytest.raises(IntegrityError):
            await s.commit()
