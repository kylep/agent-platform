"""Plain-words glossary of every village game term (plan §9, U5).

One sentence per term. Served at /api/village/glossary and used by the
help action. Pure data, no I/O.
"""

GLOSSARY: dict[str, str] = {
    "character": "One of the AI players (Pai, Kai or Olu) who lives in the village and does its work.",
    "resident": "A small helper a character raises, who follows its habits and needs no model to act.",
    "session": "The daily stretch of play, about an hour from noon, when the village is awake.",
    "quiet hours": "The time between sessions when the village is frozen and nothing changes.",
    "rest day": "A day with no session because the shared AI budget was too low to play.",
    "to-do list": "The ordered steps a character is working through, which its planner can rewrite.",
    "step": "One small job on a to-do list, like walking somewhere or picking up wood.",
    "blocked": "A step that cannot go on yet, with a short reason such as waiting for stone.",
    "habit": "A when-then rule that a character or resident follows on its own, with no model involved.",
    "thinking": "The planner is deciding what the character does next while the hands keep working.",
    "look-back": "A short pause about every ten minutes where a character reflects and sets goals, habits and notes.",
    "project": "A thing a character has decided to build, made of pieces gathered and placed over time.",
    "outline": "The marked spot on the ground that shows where a project will stand.",
    "piece": "One unit of material, carried and placed one at a time.",
    "wood": "A building material cut from the woods, used for benches, carts and frames.",
    "reeds": "A light material cut at the water's edge, used for shelter, roofs and ropes.",
    "rope": "A material twisted from reeds, used for tying and carrying.",
    "stone": "A heavy material from the quarry and the shore, used for paths and foundations.",
    "seeds": "A material planted in garden beds to grow food.",
    "clay": "A material dug near the pond, used for pots and storage.",
    "source": "A place where a material is gathered and that slowly refills.",
    "pile": "A heap of one material kept in a shared spot for anyone to use.",
    "set-aside supplies": "Pieces a character keeps for itself, which others may take only with consent.",
    "handcart": "A cart that carries more pieces at once but only along paths.",
    "drying rack": "A frame that dries wet wood and reeds while nobody is near.",
    "garden bed": "A patch where seeds are planted and, once ripe, harvested for a shared meal.",
    "tool": "A working item that wears out with use and must be repaired.",
    "wear and repair": "Objects and tools slowly wear down with use and are fixed with a repair step.",
    "footbridge": "A walkway built over water so characters can cross without a detour.",
    "stone path": "A line of laid stones that makes walking and cart travel faster.",
    "square": "The open meeting place in the middle of the village.",
    "workshop": "The building where tools are kept and repaired.",
    "dock": "The landing on the pond where goods arrive and boats tie up.",
    "pond water level": "How high the pond is, which rises and falls once each session and uncovers or hides stones.",
    "rain": "Weather that pauses outdoor building, warns first, and then passes without breaking anything.",
    "wish": "Something a resident wants after the same trouble repeats, granted when a built object answers it.",
    "chatter": "Short, templated lines residents say to each other when they stand close.",
    "now-doing label": "The words over a character showing what it is doing right now.",
    "view circle": "The small area around a character, which is all it can see.",
    "guess": "A character's own bet about how a hidden rule works, attached to a step to test it.",
    "untold rule": "A rule of the world nobody is told, which characters learn by trying things.",
    "news (told)": "A claim one character tells another, remembered only by those who were close enough to hear it.",
    "pull": "A gentle, visible nudge toward a person or place that a character can set and change.",
    "invitation": "A request to meet somewhere that the other character may accept, decline or put off.",
    "shared visit": "Two characters spending time at the same place because one invited the other.",
    "keepsake": "A short public note a character leaves on an object, removed if the object is taken apart.",
    "marked moment": "A moment a character chooses to remember, kept with a short clip of what happened.",
    "clip": "A few seconds of the village replayed around a marked moment.",
    "prompt": "The written instructions that describe how a character thinks and plays.",
    "how I play": "The one section of its prompt a character may rewrite about its own play tactics.",
    "core purpose": "What a character is for, which no edit to how it plays may change.",
    "trial": "A change to how a character plays that lasts one session and then reverts unless kept or renewed.",
    "make it your own": "The invitation for a character to adjust its play style to suit itself.",
    "Since last time": "A strip showing what was built, moved or taken apart in the previous session.",
    "Today so far": "A short list of the biggest things that have happened in the current session.",
    "Today in one minute": "A one-minute replay of the day's events, ending on a slider from noon to close.",
    "a moment from each character": "The moment each character chose to share, replayed in its own words.",
}

# Terms the design requires; the tests check every one is defined.
REQUIRED_TERMS: tuple[str, ...] = tuple(GLOSSARY)


def glossary_entries() -> list[dict[str, str]]:
    """The glossary as the API serves it, alphabetical."""
    return [{"term": t, "meaning": GLOSSARY[t]} for t in sorted(GLOSSARY, key=str.lower)]


def define(term: str) -> str | None:
    """A term's sentence, matched case-insensitively."""
    low = term.strip().lower()
    for t, sentence in GLOSSARY.items():
        if t.lower() == low:
            return sentence
    return None


def undefined_terms(terms) -> list[str]:
    """Which of `terms` have no glossary entry (for the caption/label coverage test)."""
    return [t for t in terms if define(t) is None]
