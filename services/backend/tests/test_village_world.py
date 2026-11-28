"""U1: the village world (plan §2-3): sources, pieces, objects, rain, snapshots."""
import json
import random

import pytest

from agentplatform.village import mapdata
from agentplatform.village.contract import World as WorldProtocol
from agentplatform.village.contract import Command, hash_state
from agentplatform.village.world import World


def cmd(action, persona="pai", source="planner", **args):
    return Command("s1", source, action, args, persona=persona)


def sysc(action, **args):
    return Command("s1", "system", action, args)


def edit(w, steps=None, ops=None, persona="pai"):
    a = w.actors[persona]
    ops = ops or [{"op": "append", "steps": steps}]
    return w.apply(cmd("edit_queue", persona, base_version=a["queue_version"], ops=ops))


def run(w, ticks):
    out = []
    for _ in range(ticks):
        out += w.step()
    return out


def kinds(events):
    return [e.kind for e in events]


def give(w, persona, material, n=1, wet=False):
    for _ in range(n):
        pid = w.new_piece(material, w.actors[persona]["x"], w.actors[persona]["y"],
                          f"carry:{persona}")
        w.pieces[pid]["wet"] = wet
        w.pieces[pid]["dry_in"] = 480 if wet else 0


def project(w, caption="A bench", function="sit", materials=("wood",), size=2, place=None, persona="pai"):
    op = {"op": "start_project", "caption": caption, "function": function,
          "materials": list(materials), "size": size}
    if place:
        op["place"] = place
    res = edit(w, ops=[op], persona=persona)
    assert res.state == "applied", res
    return res.data["ops"][0]["object_id"]


def build(w, material="wood", size=2, **kw):
    """Build a finished object next to pai; returns its id."""
    oid = project(w, materials=(material,), size=size, **kw)
    a = w.actors["pai"]
    obj = w.objects[oid]
    obj["x"], obj["y"] = a["x"], a["y"]
    for i in range(size):
        pid = w.new_piece(material, a["x"], a["y"], f"slot:{oid}:{i}")
        w.pieces[pid]["wet"] = False
    w.finish_object(a, obj, {"decor": []})
    w.drain_events()
    return oid


def test_world_satisfies_protocol_and_round_trips():
    w = World(seed=3)
    assert isinstance(w, WorldProtocol)
    edit(w, [{"op": "pickup", "target": "wood"}, {"op": "carry_to", "target": "pile"}, {"op": "drop"}])
    run(w, 120)
    snap = w.to_snapshot()
    json.dumps(snap)                      # JSON-safe
    assert w.state_hash() == hash_state(snap)
    w2 = World.from_snapshot(snap)
    assert w2.state_hash() == w.state_hash()
    # the same future from the same snapshot
    run(w, 200)
    run(w2, 200)
    assert w2.state_hash() == w.state_hash()


def test_replay_is_deterministic_with_commands_at_recorded_ticks():
    def play():
        w = World(seed=9)
        for t in range(600):
            if t == 5:
                edit(w, [{"op": "pickup", "target": "wood"}, {"op": "walk", "target": "square"}])
            if t == 200:
                w.apply(sysc("walk_to_square"))
            run(w, 1)
        return w.state_hash()

    assert play() == play()


def test_step_requires_the_fixed_dt():
    with pytest.raises(ValueError):
        World().step(0.5)


def test_map_sources_are_on_walkable_ground_and_reachable():
    w = World()
    sq = mapdata.LANDMARKS["square"]
    for s in w.sources.values():
        assert mapdata.passable(s["x"], s["y"]), s
        if s["kind"] != "clay_bank":
            assert mapdata.find_path(sq, (s["x"], s["y"])) is not None, s
    clay = w.sources["clay_bank1"]
    assert mapdata.find_path(sq, (clay["x"], clay["y"])) is None      # far bank needs a bridge


def test_sources_run_down_and_refill():
    w = World()
    tree = w.sources["tree1"]
    tree["amount"] = 1
    a = w.actors["pai"]
    a["x"], a["y"] = tree["x"], tree["y"]
    edit(w, [{"op": "pickup", "target": "tree1"}])
    run(w, 20)
    assert tree["amount"] == 0 and len(a["carry"]) == 1
    edit(w, [{"op": "pickup", "target": "tree1"}])
    run(w, 5)
    assert w.actors["pai"]["queue"][0]["state"] == "blocked"
    assert "regrow" in w.actors["pai"]["queue"][0]["blocked_reason"]
    ev = run(w, 120 * 4)
    assert "source_refilled" in kinds(ev)
    assert len(a["carry"]) == 2 and a["queue"] == []        # the waiting pickup carried on once it grew back


def test_stone_pile_refills_on_pond_cycle_not_a_timer():
    w = World()
    pile = w.sources["stone_pile1"]
    w.apply(sysc("admin_deplete", source="stone_pile1"))
    run(w, 400)
    assert pile["amount"] == 0                       # no timer while water_cycle is on
    ev = run(w, 3 * 1200 * 4)
    assert "stones_washed_ashore" in kinds(ev) and pile["amount"] > 0
    off = World(rules={"water_cycle": "off"})
    off.apply(sysc("admin_deplete", source="stone_pile1"))
    run(off, 180 * 4 + 5)
    assert off.sources["stone_pile1"]["amount"] >= 1


def test_piece_single_location_through_a_busy_run():
    w = World()
    oid = project(w, size=4)
    give(w, "pai", "wood", 2)
    edit(w, [{"op": "place", "target": oid}, {"op": "place", "target": oid},
             {"op": "pickup", "target": "wood"}, {"op": "drop"}])
    for _ in range(40):
        run(w, 10)
        assert w.check_invariants() == []
    assert all(p["loc"].startswith(("tile:", "carry:", "slot:")) for p in w.pieces.values())


def test_dismantled_pieces_reusable_once():
    w = World()
    oid = build(w, size=3)
    a = w.actors["pai"]
    edit(w, [{"op": "dismantle", "target": oid}])
    ev = run(w, 40)
    assert oid not in w.objects and "object_dismantled" in kinds(ev)
    loose = [p for p in w.pieces.values() if p["loc"].startswith("tile:")]
    assert len(loose) == 3 and w.check_invariants() == []
    # reuse them in a new project: each piece fills exactly one slot
    new = project(w, size=3)
    edit(w, [{"op": "pickup", "target": p["id"]} for p in loose[:2]]
         + [{"op": "place", "target": new}, {"op": "place", "target": new}])
    run(w, 200)
    placed = [s["piece"] for s in w.objects[new]["slots"] if s["piece"]]
    assert len(placed) == 2 and len(set(placed)) == 2
    assert w.check_invariants() == []
    assert a["carry"] == []


def test_small_object_move_keeps_id_and_history():
    w = World()
    oid = build(w, size=2)
    obj = w.objects[oid]
    w.actors["pai"].update(x=38, y=27)                # not at the object yet
    edit(w, [{"op": "carry_to", "target": f"{oid}>garden"}])
    run(w, 4)
    assert w.usable(obj)                              # still usable until someone arrives
    run(w, 600)
    assert oid in w.objects and obj["ready"] and obj["carried_by"] is None
    assert (obj["x"], obj["y"]) != (32, 20) and abs(obj["x"] - 22) <= 1
    assert any("Moved by Pai" in h for h in obj["history"]) and obj["history"][0].startswith("Built by Pai")
    assert w.check_invariants() == []


def test_large_object_is_taken_apart_carried_and_rebuilt_with_same_id():
    w = World()
    oid = build(w, size=6)
    obj = w.objects[oid]
    first = [s["piece"] for s in obj["slots"]]
    edit(w, [{"op": "carry_to", "target": f"{oid}>garden"}])
    run(w, 6)
    assert w.usable(obj)
    saw_taken_apart = False
    for _ in range(100):
        run(w, 10)
        saw_taken_apart = saw_taken_apart or obj["moving"]
        assert w.check_invariants() == []
        if obj["ready"] and not obj["moving"] and saw_taken_apart:
            break
    assert saw_taken_apart and obj["ready"] and w.objects[oid] is obj
    assert sorted(s["piece"] for s in obj["slots"]) == sorted(first)       # the same pieces
    assert abs(obj["x"] - 22) <= 1 and any("Moved by" in h for h in obj["history"])


def test_cancelled_pieces_stay_on_site_until_carried():
    w = World()
    oid = project(w, size=3)
    give(w, "pai", "wood", 2)
    edit(w, [{"op": "place", "target": oid}, {"op": "place", "target": oid}])
    run(w, 60)
    obj = w.objects[oid]
    x, y = obj["x"], obj["y"]
    placed = [s["piece"] for s in obj["slots"] if s["piece"]]
    assert len(placed) == 2
    pid = obj["project"]
    res = edit(w, ops=[{"op": "drop_plan", "reason": "changed my mind", "project_id": pid}])
    assert res.state == "applied" and oid not in w.objects
    assert w.projects[pid]["state"] == "cancelled"
    assert w.projects[pid]["caption"] == "Stopped building; collecting the wood"
    for p in placed:
        assert w.pieces[p]["loc"] == f"tile:{x},{y}"
    assert w.check_invariants() == []


def test_cart_refused_off_paths_and_allowed_on_them():
    w = World()
    cart = build(w, size=2, function="carry", caption="A handcart")
    assert w.objects[cart]["kind"] == "handcart"
    a = w.actors["pai"]
    edit(w, [{"op": "use", "target": cart}, {"op": "walk", "target": "woods"},
             {"op": "walk", "target": "workshop"}])
    run(w, 400)
    assert a["cart"] == cart
    done = a["finished"]
    assert done.get("s2") != "done"                    # woods are off the stone paths
    blocked = next(s for s in a["queue"] if s["id"] == "s2")
    assert blocked["state"] == "blocked" and "stone paths" in blocked["blocked_reason"]
    assert (a["x"], a["y"]) != mapdata.LANDMARKS["woods"]
    # the independent step after it still ran, entirely on built ground
    assert done.get("s3") == "done"


def test_cart_carries_four_and_walks_slower():
    w = World()
    cart = build(w, size=2, function="carry", caption="A handcart")
    a = w.actors["pai"]
    edit(w, [{"op": "use", "target": cart}])
    run(w, 40)
    assert a["cart"] == cart
    from agentplatform.village import hands
    assert hands.cap(a) == 4 and hands.speed(a) == 1000
    a["cart"] = None
    give(w, "pai", "wood", 1)
    assert hands.speed(a) == 1200
    a["carry"].clear()
    assert hands.speed(a) == 1500


def test_unknown_material_and_function_are_refused():
    w = World()
    res = edit(w, [{"op": "pickup", "target": "gold"}])
    assert res.state == "refused" and "unknown material 'gold'" in res.reason
    res = edit(w, ops=[{"op": "start_project", "caption": "x", "function": "fly",
                        "materials": ["wood"], "size": 2}])
    assert res.state == "refused" and "unknown function" in res.reason
    res = edit(w, ops=[{"op": "start_project", "caption": "x", "function": "sit",
                        "materials": ["gold"], "size": 2}])
    assert res.state == "refused" and "unknown material" in res.reason
    res = edit(w, ops=[{"op": "start_project", "caption": "x", "function": "sit",
                        "materials": ["wood"], "size": 13}])
    assert res.state == "refused"
    assert w.actors["pai"]["queue"] == []


def test_material_counts_conserved_over_random_ticks():
    rnd = random.Random(7)
    w = World(rules={"players": ["pai", "kai", "olu"]})
    oid = project(w, size=6)
    opts = [{"op": "pickup", "target": "wood"}, {"op": "pickup", "target": "reeds"},
            {"op": "pickup", "target": "stone"}, {"op": "carry_to", "target": "pile"},
            {"op": "drop"}, {"op": "place", "target": oid}, {"op": "walk", "target": "dock"},
            {"op": "rest"}, {"op": "set_aside", "target": "wood"}]
    for round_ in range(60):
        for who in ("pai", "kai", "olu"):
            edit(w, [dict(rnd.choice(opts))], persona=who)
        before = w.material_counts()
        run(w, 40)
        after = w.material_counts()
        for m, n in before.items():
            # pickups, drops and placing only move material; regrowth and washed
            # stones are the only way a count rises, and nothing is consumed here
            assert after[m] >= n, (m, before, after)
        assert w.check_invariants() == [], w.check_invariants()
        assert w.problems == []


def test_set_aside_refused_without_consent_then_allowed():
    w = World(rules={"players": ["pai", "kai"]})
    pai, kai = w.actors["pai"], w.actors["kai"]
    pai["x"] = pai["y"] = 0
    pai["x"], pai["y"] = 30, 20
    kai["x"], kai["y"] = 30, 20
    pid = w.new_piece("wood", 30, 20)
    give(w, "pai", "wood", 1)
    res = edit(w, ops=[{"op": "set_aside", "material": "wood", "n": 5}])
    assert res.state == "applied"
    assert w.pieces[pid]["aside"] == "pai"
    pai_piece = pai["carry"][0]
    assert w.pieces[pai_piece]["aside"] == "pai"
    edit(w, [{"op": "drop"}])
    run(w, 10)
    assert w.pieces[pai_piece]["loc"].startswith("tile:") and w.pieces[pai_piece]["aside"] == "pai"
    edit(w, [{"op": "pickup", "target": pai_piece}], persona="kai")
    run(w, 40)
    step = kai["queue"][0]
    assert step["state"] == "blocked" and step["blocked_reason"] == "Pai set that aside"
    assert kai["carry"] == []
    # consent: once -> the pickup goes through and the consent is spent
    res = w.apply(cmd("consent", "pai", to="kai", scope="once"))
    assert res.state == "applied"
    run(w, 80)
    assert kai["carry"] == [pai_piece] and w.pieces[pai_piece]["aside"] is None
    assert w.consents == []


def test_rain_pauses_building_but_not_carrying_and_never_removes_anything():
    w = World()
    oid = project(w, size=2)
    a = w.actors["pai"]
    give(w, "pai", "wood", 2)
    w.apply(Command("s1", "admin", "rain", {"in_s": 0, "duration_s": 30}))
    run(w, 2)
    assert w.is_raining()
    n_pieces, n_objects = len(w.pieces), len(w.objects)
    edit(w, [{"op": "place", "target": oid}, {"op": "carry_to", "target": "dock"},
             {"op": "place", "target": oid}])
    run(w, 60)
    q = {s["id"]: s for s in a["queue"]}
    assert q["s1"]["state"] == "blocked" and q["s1"]["blocked_reason"] == "paused for rain"
    assert a["finished"].get("s2") == "done"          # carrying continued
    assert all(p["loc"].startswith("carry:") for p in w.pieces.values())      # nothing placed
    assert len(w.pieces) == n_pieces and len(w.objects) == n_objects
    ev = run(w, 30 * 4)
    assert "rain_stopped" in kinds(ev)
    run(w, 200)
    # building resumed once it stopped raining; the carried wood was soaked, so it
    # split when pegged (the hidden rule), but nothing was ever removed
    assert a["queue"] == [] and a["finished"]["s1"] in ("done", "failed")
    assert len(w.pieces) == n_pieces and len(w.objects) == n_objects


def test_rain_is_announced_ahead_and_soaks_then_sun_dries_loose_wood():
    w = World()
    pid = w.new_piece("wood", 5, 30)
    w.apply(Command("s1", "admin", "rain", {"in_s": 100, "duration_s": 20}))
    ev = run(w, 4 * 41)
    notice = next(e for e in ev if e.kind == "rain_notice")
    assert notice.tick <= 4 * 41 and not w.pieces[pid]["wet"]
    ev += run(w, 4 * 70)
    assert "rain_started" in kinds(ev) and w.pieces[pid]["wet"]
    run(w, 4 * 30)
    assert w.pieces[pid]["wet"] and not w.is_raining()
    run(w, 4 * 130)
    assert not w.pieces[pid]["wet"]


def test_a_finished_shelter_keeps_work_going_in_the_rain():
    w = World()
    shelter = build(w, size=2, function="shelter", caption="A shelter")
    oid = project(w, size=1, place=f"{shelter}")
    assert w.covered(w.objects[oid]["x"], w.objects[oid]["y"])
    give(w, "pai", "wood", 1)
    w.apply(Command("s1", "admin", "rain", {"in_s": 0, "duration_s": 600}))
    edit(w, [{"op": "place", "target": oid}])
    run(w, 80)
    assert w.actors["pai"]["finished"].get("s1") == "done"


def test_decor_menu_and_plain_fallback():
    w = World()
    oid = project(w, size=1)
    obj = w.objects[oid]
    give(w, "pai", "wood", 1)
    edit(w, [{"op": "place", "target": oid, "decor": ["facing:east", "shell inlay", "colour:blue"]}])
    ev = run(w, 60)
    assert obj["ready"] and obj["decor"] == ["facing:east", "colour:blue"]
    skipped = next(e for e in ev if e.kind == "decor_skipped")
    assert skipped.public_text == "built plain: 'shell inlay' isn't an option"


def test_footbridge_opens_the_far_bank():
    w = World()
    sq = mapdata.LANDMARKS["square"]
    clay = w.sources["clay_bank1"]
    assert mapdata.find_path(sq, (clay["x"], clay["y"]), 0, w.bridge, w.paths) is None
    res = edit(w, ops=[{"op": "start_project", "caption": "A footbridge", "function": "carry",
                        "materials": ["wood"], "size": 1}])
    oid = res.data["ops"][0]["object_id"]
    assert (w.objects[oid]["x"], w.objects[oid]["y"]) == mapdata.BRIDGE_SITE
    give(w, "pai", "wood", 1)
    edit(w, [{"op": "place", "target": oid}])
    run(w, 200)
    assert w.objects[oid]["ready"] and w.bridge
    assert mapdata.find_path(sq, (clay["x"], clay["y"]), 0, w.bridge, w.paths) is not None
    edit(w, [{"op": "pickup", "target": "clay"}])
    run(w, 800)
    assert any(p["m"] == "clay" for p in w.pieces.values())


def test_footbridge_refused_away_from_the_stream():
    w = World()
    res = edit(w, ops=[{"op": "start_project", "caption": "A footbridge", "function": "carry",
                        "materials": ["wood"], "size": 2, "place": "square"}])
    assert res.state == "refused" and "stream" in res.reason


def test_frozen_world_does_not_change_and_refuses_commands():
    w = World()
    edit(w, [{"op": "pickup", "target": "wood"}])
    run(w, 20)
    w.apply(sysc("freeze"))
    h = w.state_hash()
    ev = run(w, 50)
    assert kinds(ev) == ["session_closed"] and w.state_hash() == h
    assert edit(w, [{"op": "rest"}]).state == "refused"
    w.apply(sysc("session_open", length_min=60))
    assert w.mode == "live" and w.session["end_tick"] == w.tick + 60 * 60 * 4


def test_closing_commands_stop_big_jobs_and_send_everyone_to_the_square():
    w = World(rules={"players": ["pai", "kai"]})
    edit(w, [{"op": "walk", "target": "woods"}, {"op": "walk", "target": "hillside"}])
    run(w, 40)
    w.apply(sysc("no_big_jobs"))
    res = edit(w, [{"op": "walk", "target": "dock", "big": True}])
    assert res.state == "refused" and "no new big jobs" in res.reason
    res = edit(w, ops=[{"op": "start_project", "caption": "x", "function": "sit",
                        "materials": ["wood"], "size": 2}])
    assert res.state == "refused"
    w.apply(sysc("walk_to_square"))
    run(w, 4 * 90)
    for who in ("pai", "kai"):
        a = w.actors[who]
        assert max(abs(a["x"] - 32), abs(a["y"] - 20)) <= 1, who
    # the step that was interrupted is still on the list, in order
    assert [s["op"] for s in w.actors["pai"]["queue"]][:2] == ["walk", "walk"]


def test_state_is_json_stable_after_many_events():
    w = World(rules={"players": ["pai", "kai", "olu"]})
    for who in ("pai", "kai", "olu"):
        edit(w, [{"op": "pickup", "target": "reeds"}, {"op": "carry_to", "target": "pile"}, {"op": "drop"}],
             persona=who)
    run(w, 800)
    snap = w.to_snapshot()
    assert json.loads(json.dumps(snap)) == snap
    assert World.from_snapshot(snap).state_hash() == w.state_hash()
