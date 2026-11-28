"""U1: the hands (plan §3): picking steps, blocking, habits, the queue edits."""
import random

from agentplatform.village import mapdata
from agentplatform.village.contract import Command
from agentplatform.village.world import World
from .test_village_world import build, cmd, edit, give, kinds, project, run, sysc

TWO = {"players": ["pai", "kai"]}


def blocked(a):
    return [s for s in a["queue"] if s["state"] == "blocked"]


def test_blocked_does_not_stop_independent_steps():
    w = World()
    oid = project(w, size=2)
    a = w.actors["pai"]
    edit(w, [{"op": "place", "target": oid, "say": "roof"}, {"op": "walk", "target": "dock", "say": "to the dock"}])
    ev = run(w, 120)
    assert a["finished"]["s2"] == "done" and a["queue"][0]["id"] == "s1"
    assert a["queue"][0]["state"] == "blocked"
    assert a["queue"][0]["blocked_reason"] == "waiting for wood"
    # the caption says why the order changed
    assert any(e.kind == "step_blocked" and e.data["reason"] == "waiting for wood" for e in ev)


def test_caption_names_the_skipped_step():
    w = World()
    oid = project(w, size=2)
    a = w.actors["pai"]
    edit(w, [{"op": "place", "target": oid, "say": "roof"}, {"op": "walk", "target": "dock", "say": "to the dock"}])
    run(w, 6)
    assert a["caption"] == "skipping roof until waiting for wood, to the dock first"


def test_blocked_resumes_when_the_need_arrives():
    w = World()
    oid = project(w, size=1)
    a = w.actors["pai"]
    edit(w, [{"op": "place", "target": oid}])
    run(w, 40)
    assert a["queue"][0]["state"] == "blocked"
    give(w, "pai", "wood", 1)
    ev = run(w, 40)
    assert a["queue"] == [] and a["finished"]["s1"] == "done"
    assert "step_unblocked" in kinds(ev) and w.objects[oid]["ready"]


def test_blocked_waits_for_another_character_and_resumes_when_it_arrives():
    w = World(rules=TWO)
    pai = w.actors["pai"]
    w.apply(sysc("admin_deplete", source="wood"))
    edit(w, [{"op": "pickup", "target": "wood"}])
    run(w, 20)
    step = pai["queue"][0]
    assert step["state"] == "blocked" and "no wood left" in step["blocked_reason"]
    w.new_piece("wood", pai["x"], pai["y"])          # kai drops some at the pile
    ev = run(w, 60)
    assert len(pai["carry"]) == 1 and pai["queue"] == [] and "step_unblocked" in kinds(ev)


def test_every_blocked_step_has_a_reason_across_a_busy_run():
    rnd = random.Random(11)
    w = World(rules={"players": ["pai", "kai", "olu"]})
    oid = project(w, size=6)
    pool = [{"op": "pickup", "target": "wood"}, {"op": "pickup", "target": "stone"},
            {"op": "place", "target": oid}, {"op": "use", "target": oid},
            {"op": "wait_for", "target": "kai"}, {"op": "wait_for", "target": "olu"},
            {"op": "carry_to", "target": "pile"}, {"op": "walk", "target": "far_bank"},
            {"op": "repair", "target": oid}, {"op": "drop"}]
    w.apply(sysc("admin_deplete", source="stone"))
    seen = 0
    for rnd_i in range(40):
        for who in ("pai", "kai", "olu"):
            if rnd.random() < 0.7:
                edit(w, [dict(rnd.choice(pool))], persona=who)
        for _ in range(20):
            for e in w.step():
                if e.kind == "step_blocked":
                    assert e.data["reason"]
            for a in w.actors.values():
                for s in a["queue"]:
                    if s["state"] == "blocked":
                        seen += 1
                        assert s["blocked_reason"] and s["blocked_since"] is not None, s
                    else:
                        assert s["blocked_reason"] is None
    assert seen > 100


def test_two_characters_on_the_same_wood_first_pickup_wins():
    w = World(rules=TWO)
    pai, kai = w.actors["pai"], w.actors["kai"]
    tree = w.sources["tree1"]
    tree["amount"] = 1
    for who in (pai, kai):
        who["x"], who["y"] = tree["x"], tree["y"]
    for who in ("pai", "kai"):
        edit(w, [{"op": "pickup", "target": "tree1"}], persona=who)
    run(w, 30)
    winner, loser = (pai, kai) if pai["carry"] else (kai, pai)
    assert len(winner["carry"]) == 1 and loser["carry"] == []
    assert loser["queue"][0]["state"] == "blocked"
    assert loser["queue"][0]["blocked_reason"] in (f"{winner['name']} took that wood",
                                                    "no wood left at tree1, waiting for it to regrow")


def test_give_up_after_three_minutes_and_the_planner_is_told_why():
    w = World()
    edit(w, [{"op": "pickup", "target": "clay"}, {"op": "walk", "target": "dock"}])
    run(w, 170 * 4)
    s1 = w.actors["pai"]["queue"][0]
    assert s1["id"] == "s1" and s1["state"] == "blocked"      # still trying at 170 s
    assert "can't cross the stream" in s1["blocked_reason"]
    assert w.actors["pai"]["finished"]["s2"] == "done"        # the rest of the list carried on
    ev = run(w, 20 * 4)
    failed = [e for e in ev if e.kind == "step_failed"]
    assert failed and "blocked too long: can't cross the stream" in failed[0].data["reason"]
    assert w.actors["pai"]["finished"]["s1"] == "failed"


def test_rain_pause_never_counts_toward_giving_up():
    w = World()
    oid = project(w, size=1)
    give(w, "pai", "wood", 1)
    w.apply(Command("s1", "admin", "rain", {"in_s": 0, "duration_s": 400}))
    edit(w, [{"op": "place", "target": oid}])
    run(w, 4 * 300)
    s = w.actors["pai"]["queue"][0]
    assert s["state"] == "blocked" and s["blocked_reason"] == "paused for rain"


def test_waiting_character_walks_over_to_the_one_they_wait_on():
    w = World(rules=TWO)
    pai, kai = w.actors["pai"], w.actors["kai"]
    kai.update(x=44, y=20)
    edit(w, [{"op": "wait_for", "target": "kai"}])
    run(w, 4 * 20)
    assert max(abs(pai["x"] - kai["x"]), abs(pai["y"] - kai["y"])) <= 2
    assert pai["finished"].get("s1") == "done"


def test_habit_shapes_work_with_no_planner_input():
    w = World()
    habit = {"when": {"kind": "pile_below", "material": "wood", "n": 2},
             "do": {"kind": "fetch", "material": "wood", "to": "pile"}, "say": "keeping the wood pile up"}
    res = w.apply(cmd("set_habits", "pai", "reflection", target="pai", habits=[habit]))
    assert res.state == "applied"
    a = w.actors["pai"]
    ev = run(w, 4 * 400)
    from agentplatform.village import habits
    assert habits.pile_count(w, "wood") == 2
    assert a["queue"] == [] and "habit_started" in kinds(ev)
    started = [e for e in ev if e.kind == "habit_started"]
    assert started[0].data["label"] == "keeping the wood pile up"
    assert w.check_invariants() == []


def test_the_label_is_the_running_habits_say_and_idle_is_resting():
    w = World()
    habit = {"when": {"kind": "idle"}, "do": {"kind": "rest_at", "place": "dock"}, "say": "sitting by the water"}
    w.apply(cmd("set_habits", "pai", "reflection", target="pai", habits=[habit]))
    run(w, 10)
    assert w.actors["pai"]["caption"] == "sitting by the water"
    assert w.actors["pai"]["idle"].get("habit", 0) > 0


def test_a_queued_step_takes_over_from_a_habit_at_the_next_step_boundary():
    w = World()
    habit = {"when": {"kind": "idle"}, "do": {"kind": "walk_path", "a": "dock", "b": "square"}}
    w.apply(cmd("set_habits", "pai", "reflection", target="pai", habits=[habit]))
    run(w, 8)
    assert w.actors["pai"]["habit_steps"]
    edit(w, [{"op": "walk", "target": "workshop"}])
    run(w, 400)
    assert w.actors["pai"]["finished"]["s1"] == "done"


def test_habits_never_use_set_aside_pieces():
    w = World()
    pid = w.new_piece("wood", 32, 21)
    w.pieces[pid]["aside"] = "pai"
    w.apply(sysc("admin_deplete", source="wood"))
    habit = {"when": {"kind": "pile_below", "material": "wood", "n": 5},
             "do": {"kind": "fetch", "material": "wood", "to": "pile"}}
    w.apply(cmd("set_habits", "pai", "reflection", target="pai", habits=[habit]))
    run(w, 200)
    assert w.pieces[pid]["loc"] == "tile:32,21" and w.actors["pai"]["carry"] == []


def test_a_habit_waits_for_the_rain_to_stop_and_stacks_loose_wood():
    w = World()
    hab = {"when": {"kind": "loose_near_work", "material": "wood"}, "do": {"kind": "stack_loose"}}
    w.apply(cmd("set_habits", "pai", "reflection", target="pai", habits=[hab]))
    project(w, size=3, place="tile:30,24")
    pid = w.new_piece("wood", 31, 24)
    run(w, 4 * 60)
    assert w.pieces[pid]["loc"].startswith("tile:") and w.pieces[pid]["loc"] != "tile:31,24"


def test_invalid_habits_are_refused_with_the_reason():
    w = World()
    bad = {"when": {"kind": "weather_is_nice"}, "do": {"kind": "rest_at", "place": "dock"}}
    res = w.apply(cmd("set_habits", "pai", "reflection", target="pai", habits=[bad]))
    assert res.state == "refused" and "must be one of" in res.reason
    assert w.actors["pai"]["habits"] == []
    strict = World(rules={"habit_edits": "between_sessions"})
    ok = {"when": {"kind": "idle"}, "do": {"kind": "stack_loose"}}
    res = strict.apply(cmd("set_habits", "pai", "reflection", target="pai", habits=[ok]))
    assert res.state == "refused" and "wait until the session ends" in res.reason


def test_edit_queue_ops_and_versions():
    w = World()
    a = w.actors["pai"]
    assert edit(w, [{"op": "walk", "target": "dock", "id": "a"}, {"op": "walk", "target": "square", "id": "c"}]).state == "applied"
    assert a["queue_version"] == 1
    stale = w.apply(cmd("edit_queue", base_version=0, ops=[{"op": "append", "steps": [{"op": "rest"}]}]))
    assert stale.state == "refused" and stale.data["queue_version"] == 1
    assert edit(w, ops=[{"op": "insert_after", "after": "a", "steps": [{"op": "walk", "target": "pond", "id": "b"}]}]).state == "applied"
    assert [s["id"] for s in a["queue"]] == ["a", "b", "c"]
    res = edit(w, ops=[{"op": "replace", "step_id": "b", "steps": [{"op": "rest", "id": "b2"}]},
                       {"op": "remove", "step_id": "zzz"},
                       {"op": "append", "steps": [{"op": "walk", "target": "square", "id": "c"}]}])
    assert res.state == "applied"
    assert [r["state"] for r in res.data["ops"]] == ["applied", "refused", "refused"]   # dup id refused
    assert "no step 'zzz'" in res.data["ops"][1]["reason"]
    assert [s["id"] for s in a["queue"]] == ["a", "b2", "c"]
    edit(w, ops=[{"op": "clear_from", "step_id": "b2"}])
    assert [s["id"] for s in a["queue"]] == ["a"]
    assert a["finished"]["c"] == "skipped"
    refused = edit(w, ops=[{"op": "remove", "step_id": "nope"}])
    assert refused.state == "refused" and a["queue_version"] == 4


def test_a_step_whose_dependency_failed_is_skipped_and_deps_order_work():
    w = World()
    a = w.actors["pai"]
    edit(w, [{"op": "walk", "target": "dock", "id": "late", "deps": ["first"]},
             {"op": "walk", "target": "square", "id": "first"},
             {"op": "use", "target": "nothing", "id": "bad"},
             {"op": "walk", "target": "workshop", "id": "dependent", "deps": ["bad"]}])
    ev = run(w, 4 * 90)
    assert a["finished"]["first"] == "done" and a["finished"]["late"] == "done"
    assert a["finished"]["bad"] == "failed" and a["finished"]["dependent"] == "skipped"
    order = [e.subjects[0] for e in ev if e.kind == "step_done"]
    assert order.index("first") < order.index("late")


def test_walking_speed_is_one_and_a_half_tiles_a_second():
    w = World()
    a = w.actors["pai"]
    goal = (44, 20)
    path = mapdata.find_path((a["x"], a["y"]), goal, 0, w.bridge, w.paths)
    edit(w, [{"op": "walk", "target": "tile:44,20"}])
    ticks = 0
    while not a["finished"].get("s1") and ticks < 1000:
        run(w, 1)
        ticks += 1
    assert abs(ticks - len(path) * 1000 / 375) <= 3


def test_carrying_slows_the_walk():
    def walk_ticks(carry):
        w = World()
        if carry:
            give(w, "pai", "wood", 1)
        edit(w, [{"op": "walk", "target": "tile:44,20"}])
        n = 0
        while not w.actors["pai"]["finished"].get("s1"):
            run(w, 1)
            n += 1
        return n

    assert walk_ticks(True) > walk_ticks(False) * 1.2


def test_a_walk_chosen_by_a_character_wears_the_path():
    w = World()
    edit(w, [{"op": "walk", "target": "tile:30,26"}, {"op": "walk", "target": "square"}] * 3)
    run(w, 4 * 80)
    assert max(w.wear.values()) >= 3


def test_idle_attribution_bins_every_second():
    w = World(rules=TWO)
    pai, kai = w.actors["pai"], w.actors["kai"]
    run(w, 8)
    assert pai["idle"] == {"resting": 8}
    w.apply(sysc("planner_status", persona="pai", status="thinking"))
    run(w, 8)
    assert pai["idle"]["thinking_no_steps"] == 8
    kai.update(x=60, y=36)
    edit(w, [{"op": "wait_for", "target": "kai"}])
    pai["x"], pai["y"] = 2, 36
    run(w, 4)
    assert any(k.startswith("waiting_for:kai") for k in pai["idle"])
    oid = project(w, size=1, persona="kai")
    edit(w, [{"op": "place", "target": oid}], persona="kai")
    run(w, 8)
    assert any(k.startswith("blocked:waiting for wood") for k in kai["idle"])
    edit(w, [{"op": "walk", "target": "square"}], persona="kai")
    run(w, 8)
    assert kai["idle"].get("working", 0) > 0


def test_hammer_wears_out_and_building_waits_for_the_repair():
    w = World(rules=TWO)
    oid = project(w, size=1)
    w.tools["hammer"].update(wear=11)
    give(w, "pai", "wood", 1)
    edit(w, [{"op": "place", "target": oid}])
    run(w, 40)
    assert w.tools["hammer"]["broken"] and w.objects[oid]["ready"]      # that placement broke it
    oid2 = project(w, size=1)
    give(w, "pai", "wood", 1)
    edit(w, [{"op": "place", "target": oid2}])
    run(w, 20)
    assert "hammer is broken" in w.actors["pai"]["queue"][0]["blocked_reason"]
    edit(w, [{"op": "repair", "target": "tool:hammer"}], persona="kai")
    run(w, 4 * 30)
    assert not w.tools["hammer"]["broken"] and w.objects[oid2]["ready"]


def test_one_repair_has_one_owner():
    w = World(rules=TWO)
    oid = build(w, size=2)
    w.objects[oid]["wear"] = 6
    for who in ("pai", "kai"):
        w.actors[who].update(x=w.objects[oid]["x"], y=w.objects[oid]["y"])
    edit(w, [{"op": "repair", "target": oid}], persona="pai")
    run(w, 2)
    edit(w, [{"op": "repair", "target": oid}], persona="kai")
    run(w, 10)
    kq = w.actors["kai"]["queue"]
    assert kq and "Pai is already repairing that" == kq[0]["blocked_reason"]
    run(w, 4 * 20)
    assert w.objects[oid]["wear"] == 0
    assert w.actors["kai"]["finished"].get("s1") == "failed" or w.actors["kai"]["queue"] == []


def test_say_tell_and_unavailable_ops():
    w = World(rules=TWO)
    res = edit(w, ops=[{"op": "say", "line": "hello"},
                       {"op": "tell", "line": "careful", "claim": "reed rope snaps under a cart"},
                       {"op": "pull", "target": "kai", "toward": "dock", "line": "come"}])
    assert [r["state"] for r in res.data["ops"]] == ["applied", "applied", "refused"]
    ev = w.drain_events()
    tell = next(e for e in ev if e.kind == "tell")
    assert tell.data["claim"] == "reed rope snaps under a cart" and tell.data["witnesses"]
    w.register("op:pull", lambda world, actor, op: (True, None, {}))
    assert edit(w, ops=[{"op": "pull", "target": "kai", "toward": "dock", "line": "come"}]).state == "applied"


def test_habits_can_only_be_set_on_yourself_or_your_own_residents():
    w = World(rules=TWO)
    w.add_actor("mara", "Mara", "resident", 31, 21, owner="pai")
    ok = [{"when": {"kind": "idle"}, "do": {"kind": "rest_at", "place": "dock"}}]
    res = w.apply(cmd("set_habits", "pai", "reflection", target="kai", habits=ok))
    assert res.state == "refused" and "only change your own or your residents' habits" in res.reason
    assert w.actors["kai"]["habits"] == []
    assert w.apply(cmd("set_habits", "kai", "reflection", target="mara", habits=ok)).state == "refused"
    assert w.apply(cmd("set_habits", "pai", "reflection", target="mara", habits=ok)).state == "applied"
    assert w.actors["mara"]["habits"] == ok
    assert w.apply(cmd("set_habits", "pai", "reflection", target="pai", habits=ok)).state == "applied"
