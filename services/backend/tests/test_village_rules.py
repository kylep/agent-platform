"""U1: hidden rules (plan §3): pure, deterministic, resolved when a step completes."""
import pytest

from agentplatform.village import rules
from agentplatform.village.world import World
from .test_village_world import build, edit, give, kinds, project, run

CASES = [
    ("place_piece", {"material": "wood", "wet": True}, "wet_wood_peg", False),
    ("place_piece", {"material": "wood", "wet": False}, None, None),
    ("place_piece", {"material": "reeds", "wet": True}, None, None),
    ("use_object", {"kind": "gate", "slots": ["rope"], "loaded": False}, "reed_rope", True),
    ("use_object", {"kind": "handcart", "slots": ["rope", "wood"], "loaded": True}, "reed_rope", False),
    ("use_object", {"kind": "handcart", "slots": ["rope"], "loaded": False}, None, None),
    ("use_object", {"kind": "bench", "slots": ["wood"], "wet_clay": True}, "clay_dry", False),
    ("use_object", {"kind": "bench", "slots": ["clay"], "wet_clay": False}, None, None),
    ("path_tile", {"stones": 3}, "stone_path", True),
    ("path_tile", {"stones": 4}, "stone_path", True),
    ("path_tile", {"stones": 2}, "stone_path", False),
]


@pytest.mark.parametrize("trigger,ctx,key,ok", CASES)
def test_rules_are_deterministic_for_same_inputs(trigger, ctx, key, ok):
    first = rules.evaluate(trigger, dict(ctx))
    for _ in range(5):
        assert rules.evaluate(trigger, dict(ctx)) == first
    if key is None:
        assert first is None
    else:
        assert first.rule_key == key and first.ok is ok and first.text and first.claim


def test_unknown_trigger_has_no_outcome():
    assert rules.evaluate("nothing", {"material": "wood", "wet": True}) is None


def test_every_outcome_text_fits_an_event():
    for trigger, ctx, key, _ in CASES:
        out = rules.evaluate(trigger, dict(ctx))
        if out:
            assert len(out.text) <= 200 and out.rule_key in rules.RULE_KEYS


def _outcomes(events):
    return [e for e in events if e.kind == "rule_outcome"]


def test_wet_wood_splits_when_pegged_and_the_guess_is_resolved():
    w = World()
    oid = project(w, size=1)
    give(w, "pai", "wood", 1, wet=True)
    w.pieces[w.actors["pai"]["carry"][0]]["dry_in"] = 99999
    edit(w, [{"op": "place", "target": oid, "guess": "I think wet wood is fine"}])
    ev = run(w, 40)
    out = _outcomes(ev)
    assert len(out) == 1 and out[0].public_text == "the wet wood split when it was pegged"
    assert out[0].data["rule_key"] == "wet_wood_peg" and out[0].data["claim"] == "wet wood splits when pegged"
    guess = next(e for e in ev if e.kind == "guess_resolved")
    assert guess.data["ok"] is False and guess.data["guess"] == "I think wet wood is fine"
    assert "pai" in guess.data["witnesses"] and "guess_made" in kinds(ev)
    assert not w.objects[oid]["ready"] and w.actors["pai"]["finished"]["s1"] == "failed"
    split = [p for p in w.pieces.values() if p["split"]]
    assert len(split) == 1 and split[0]["loc"].startswith("tile:")
    assert w.check_invariants() == []


def test_dry_wood_pegs_fine_and_an_unrelated_guess_says_nothing_settled_it():
    w = World()
    oid = project(w, size=1)
    give(w, "pai", "wood", 1)
    edit(w, [{"op": "place", "target": oid, "guess": "the bench will be sturdy"}])
    ev = run(w, 40)
    assert _outcomes(ev) == [] and w.objects[oid]["ready"]
    guess = next(e for e in ev if e.kind == "guess_resolved")
    assert guess.data["ok"] is None


def test_the_same_inputs_give_the_same_outcome_in_two_worlds():
    def play():
        w = World()
        oid = project(w, size=1)
        give(w, "pai", "wood", 1, wet=True)
        edit(w, [{"op": "place", "target": oid}])
        return [(e.kind, e.public_text, e.data) for e in run(w, 40) if e.kind == "rule_outcome"]

    assert play() == play() != []


def test_rope_holds_a_gate_but_snaps_under_a_loaded_cart():
    w = World()
    gate = build(w, material="rope", size=2, function="shelter", caption="A gate")
    assert w.objects[gate]["kind"] == "gate"
    edit(w, [{"op": "use", "target": gate, "guess": "reed rope holds this gate"}])
    ev = run(w, 40)
    out = _outcomes(ev)[0]
    assert out.data["ok"] is True and out.data["claim"] == "reed rope holds a gate"
    cart = build(w, material="rope", size=2, function="carry", caption="A handcart")
    give(w, "pai", "wood", 1)                         # loaded
    edit(w, [{"op": "use", "target": cart}])
    ev = run(w, 40)
    out = _outcomes(ev)[0]
    assert out.public_text == "the rope snapped under the cart" and out.data["ok"] is False
    assert w.actors["pai"]["cart"] is None and w.actors["pai"]["carry"] == []     # load dropped on site
    assert w.objects[cart]["wear"] >= 10 and w.check_invariants() == []
    # an unloaded cart is fine
    other = build(w, material="rope", size=2, function="carry", caption="A handcart")
    edit(w, [{"op": "use", "target": other}])
    ev = run(w, 40)
    assert _outcomes(ev) == [] and w.actors["pai"]["cart"] == other


def test_clay_must_dry_before_it_holds_weight():
    w = World()
    oid = build(w, material="clay", size=2)
    for s in w.objects[oid]["slots"]:
        w.pieces[s["piece"]].update(wet=True, dry_in=100)
    edit(w, [{"op": "use", "target": oid}])
    ev = run(w, 30)
    assert _outcomes(ev)[0].data["rule_key"] == "clay_dry" and w.objects[oid]["wear"] == 3
    run(w, 4 * 30)                                    # dries in the sun
    assert not any(w.pieces[s["piece"]]["wet"] for s in w.objects[oid]["slots"])
    edit(w, [{"op": "use", "target": oid}])
    assert _outcomes(run(w, 30)) == []


def test_a_stone_path_needs_three_stones_per_tile():
    w = World()
    a = w.actors["pai"]
    a["x"], a["y"] = 31, 25
    give(w, "pai", "stone", 2)
    edit(w, [{"op": "place", "target": "tile:31,25", "guess": "two stones will do"}])
    ev = run(w, 30)
    assert _outcomes(ev)[0].data["ok"] is False and (31, 25) not in w.paths
    assert len(w.stones_at(31, 25)) == 2              # nothing was lost
    give(w, "pai", "stone", 1)
    edit(w, [{"op": "place", "target": "tile:31,25"}])
    ev = run(w, 30)
    assert _outcomes(ev)[0].data["ok"] is True and (31, 25) in w.paths
    assert w.stones_at(31, 25) == [] and w.check_invariants() == []
    # a built path tile makes the cart route possible there
    from agentplatform.village import mapdata
    assert mapdata.passable(31, 25, w.bridge, w.paths, cart=True)
