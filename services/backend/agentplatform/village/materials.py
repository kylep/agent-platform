"""Materials, sources, object kinds, durations and the decor menu (plan §3).

Constants and tiny pure helpers only. Durations are in ticks (4 per second).
"""
import re

from .contract import MATERIALS, TICK_HZ


def secs(s: float) -> int:
    return max(1, int(round(s * TICK_HZ)))


MATERIAL_SET = frozenset(MATERIALS)

# source kind -> material, stock cap, regrow interval. `stone_pile` refills on the
# pond cycle when water_cycle is on, on this timer when it is off.
SOURCE_KINDS = {
    "tree": {"m": "wood", "max": 3, "regrow": secs(120), "label": "the trees"},
    "reed_patch": {"m": "reeds", "max": 4, "regrow": secs(90), "label": "the reed beds"},
    "stone_pile": {"m": "stone", "max": 12, "regrow": secs(180), "label": "the stone pile"},
    "seed_spot": {"m": "seeds", "max": 2, "regrow": secs(150), "label": "the seed spots"},
    "clay_bank": {"m": "clay", "max": 8, "regrow": secs(240), "label": "the clay bank"},
}
SOURCE_OF = {v["m"]: k for k, v in SOURCE_KINDS.items()}
assert set(SOURCE_OF) == set(MATERIALS) - {"rope"}      # rope is made, not found

# Work durations by step op (ticks).
WORK = {"pickup": secs(2), "drop": secs(0.5), "place": secs(1.5), "dismantle": secs(3),
        "repair": secs(5), "use": secs(3), "rest": secs(8), "look": secs(1), "visit": secs(6),
        "plant": secs(3), "harvest": secs(3), "keepsake": secs(1), "disassemble": secs(3)}

# Walking speed in thousandths of a tile per second; a tick adds speed * dt.
SPEED = {"walk": 1500, "carry": 1200, "cart": 1000}

WET_DRY_TICKS = secs(120)       # sunlight dries a soaked piece after this long
CLAY_DRY_TICKS = secs(120)      # fresh clay must dry before it holds weight
RACK_TICKS = secs(90)           # reeds -> rope
RACK_CAP = 4
GARDEN_RIPE_TICKS = secs(8 * 60)
POND_PERIOD = secs(20 * 60)     # one pond level change every 20 min (3 per session)
POND_CYCLE = (1, 2, 1, 0)       # low (0) is when stones wash ashore
WASH_STONES = 4
RAIN_WEAR_EVERY = secs(60)      # rain adds a crack per minute to exposed objects

WEAR_WORN = 5                   # "cracked": object_worn event, shown on the object
WEAR_BROKEN = 10                # unusable until repaired
TOOL_BREAK = 12
TOOLS = ("hammer", "saw")

# function -> default object kind; caption keywords refine it.
KIND_BY_FUNCTION = {"sit": "bench", "shelter": "shelter", "store": "chest", "carry": "handcart",
                    "dry": "drying_rack", "grow": "garden_bed", "look": "ornament"}
KIND_KEYWORDS = (("bridge", "footbridge"), ("gate", "gate"), ("fence", "gate"), ("cart", "handcart"))
INDOOR_FUNCTIONS = ("shelter",)

# "Make it your own" [A25]: three options per kind; anything else is built plain.
FACINGS = ("north", "east", "south", "west")
COLOURS = ("red", "blue", "green", "yellow", "white", "grey")
_NAME_RE = re.compile(r"^name:[A-Za-z][A-Za-z '-]{0,23}$")


def kind_for(function: str, caption: str) -> str:
    low = caption.lower()
    if function == "grow" and "bridge" not in low:
        return "garden_bed"             # anything built to grow is a bed, whatever it is called
    for word, kind in KIND_KEYWORDS:
        if word in low:
            return kind
    return KIND_BY_FUNCTION[function]


def decor_ok(option: str) -> bool:
    """The menu is facing:<dir>, colour:<name>, name:<carved text>; same for every kind."""
    if option.startswith("facing:"):
        return option[7:] in FACINGS
    if option.startswith("colour:"):
        return option[7:] in COLOURS
    return bool(_NAME_RE.match(option))


def split_decor(options: list[str]) -> tuple[list[str], list[str]]:
    """(applied, skipped); at most one option per menu key."""
    applied, skipped, used = [], [], set()
    for opt in options or []:
        key = opt.split(":", 1)[0]
        if decor_ok(opt) and key not in used:
            applied.append(opt)
            used.add(key)
        else:
            skipped.append(opt)
    return applied, skipped


def make_slots(materials: list[str], size: int) -> list[dict]:
    """An outline: `size` slots cycling through the project's materials."""
    return [{"m": materials[i % len(materials)], "piece": None} for i in range(size)]
