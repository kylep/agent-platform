"""Hidden rules (plan §3): a pure table, never shown to models.

`evaluate(trigger, ctx)` is a deterministic function of its arguments, so "hidden
rules give the same result every time". The world calls it when a step
completes and turns the Outcome into a `rule_outcome` event; the outcome's
`claim` is the sentence a `tried`/`saw` knowledge row carries.
"""
from dataclasses import dataclass

STONES_PER_PATH_TILE = 3


@dataclass(frozen=True)
class Outcome:
    rule_key: str
    ok: bool
    text: str      # public line for the event ("the rope snapped under the cart")
    claim: str     # the claim a knowledge row stores


def _wet_wood(ctx: dict) -> Outcome | None:
    if ctx.get("material") != "wood":
        return None
    if ctx.get("wet"):
        return Outcome("wet_wood_peg", False, "the wet wood split when it was pegged",
                       "wet wood splits when pegged")
    return None


def _rope(ctx: dict) -> Outcome | None:
    if "rope" not in ctx.get("slots", ()):
        return None
    if ctx.get("kind") == "gate":
        return Outcome("reed_rope", True, "the reed rope held the gate shut",
                       "reed rope holds a gate")
    if ctx.get("kind") == "handcart" and ctx.get("loaded"):
        return Outcome("reed_rope", False, "the rope snapped under the cart",
                       "reed rope does not hold a loaded cart")
    return None


def _clay(ctx: dict) -> Outcome | None:
    if ctx.get("wet_clay"):
        return Outcome("clay_dry", False, "the soft clay sagged under the weight",
                       "clay must dry before it holds weight")
    return None


def _path(ctx: dict) -> Outcome | None:
    n = ctx.get("stones", 0)
    if n >= STONES_PER_PATH_TILE:
        return Outcome("stone_path", True, "three stones made a path tile",
                       "a stone path needs 3 stones per tile")
    return Outcome("stone_path", False, f"{n} stone{'s' if n != 1 else ''} did not make a path tile",
                   "a stone path needs 3 stones per tile")


# trigger -> rule functions tried in order; the first that fires is the outcome.
RULE_TABLE = {
    "place_piece": (_wet_wood,),
    "use_object": (_rope, _clay),
    "path_tile": (_path,),
}
RULE_KEYS = ("wet_wood_peg", "reed_rope", "clay_dry", "stone_path")


def evaluate(trigger: str, ctx: dict) -> Outcome | None:
    for fn in RULE_TABLE.get(trigger, ()):
        out = fn(ctx)
        if out is not None:
            return out
    return None
