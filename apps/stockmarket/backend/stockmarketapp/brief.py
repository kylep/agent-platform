"""Brief handling: parsing, validating and formatting the agent's market brief.

Everything here operates on UNTRUSTED, agent-produced text. The `stockmarket`
agent reads the open web to explain a session, so its output must be treated
as something an injected web page may have influenced: parse defensively,
clamp every number, allow only known tags, and sanitize anything that reaches
Discord. A hostile brief can produce bounded text and one row — nothing else.
"""
from __future__ import annotations

import json
import re

# The brief's tag vocabulary. Fixed, unlike the news app's auto-created
# topics: news sections are open-ended discovery, whereas these are a small
# closed set of reasons a market moves, and an agent inventing a nineteenth
# one is a mistake to drop rather than a lane to create.
TAGS: list[str] = [
    "earnings", "macro", "central-bank", "rates", "geopolitics",
    "commodities", "sector-rotation", "broad-market",
]
MAX_TAGS = 3
MAX_MOVERS = 5
SYMBOL_RE = re.compile(r"^[A-Z0-9.^-]{1,12}$")
_MENTION_RE = re.compile(r"<@[!&]?\d+>")
_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_brief(result_text: str | None) -> dict | None:
    """Extract the brief JSON from a run result. Tolerates ```json fences and
    surrounding prose; returns None when no JSON object is found, so an
    unparseable result ingests nothing rather than a half-brief."""
    if not result_text:
        return None
    text = result_text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return obj if isinstance(obj, dict) else None


def sanitize(s: str, limit: int = 600) -> str:
    """Neutralize Discord control sequences in agent-controlled text:
    @everyone/@here defanged with a zero-width space, raw mention tokens
    removed, newlines collapsed, length clamped."""
    zwsp = "​"
    s = str(s).replace("\n", " ").replace("\r", " ")
    s = s.replace("@everyone", f"@{zwsp}everyone").replace("@here", f"@{zwsp}here")
    s = _MENTION_RE.sub("", s)
    return " ".join(s.split())[:limit]


def _number(v, limit: float) -> float | None:
    """A finite number inside a sane band, or None. Guards the display against
    a garbage percentage stretching an axis to infinity."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")) or abs(f) > limit:
        return None
    return round(f, 2)


def clean_symbol(v) -> str:
    s = str(v).strip().upper()
    return s if SYMBOL_RE.match(s) else ""


def clean_tags(raw) -> list[str]:
    """Known tags only, deduplicated, order preserved, capped."""
    if not isinstance(raw, list):
        return []
    out = []
    for t in raw:
        slug = str(t).strip().lower()
        if slug in TAGS and slug not in out:
            out.append(slug)
    return out[:MAX_TAGS]


def clean_indexes(raw) -> list[dict]:
    if not isinstance(raw, list):
        return []
    out = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        sym = clean_symbol(row.get("symbol", ""))
        # A daily index move beyond +/-50% is a data error, not a session.
        ret = _number(row.get("return_pct"), 50)
        if not sym or ret is None:
            continue
        out.append({"symbol": sym, "return_pct": ret,
                    "note": sanitize(row.get("note", ""), 300)})
    return out


def clean_movers(raw) -> list[dict]:
    if not isinstance(raw, list):
        return []
    out = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        sym = clean_symbol(row.get("symbol", ""))
        if not sym:
            continue
        # A single holding cannot contribute more than the whole index moved;
        # 10,000bp (100%) is a generous ceiling on an honest number.
        out.append({"symbol": sym,
                    "index": clean_symbol(row.get("index", "")),
                    "contrib_bps": _number(row.get("contrib_bps"), 10_000),
                    "note": sanitize(row.get("note", ""), 300)})
    return out[:MAX_MOVERS]


def clean_brief(raw: dict) -> dict | None:
    """Validate a parsed brief into exactly what gets stored, or None if it
    has nothing worth storing. A brief needs a real session date and either a
    body or some index numbers — an empty shell is not a brief."""
    day = str(raw.get("day", "")).strip()
    if not _DAY_RE.match(day):
        return None
    out = {
        "day": day,
        "body": sanitize(raw.get("body", ""), 2000),
        "tags": clean_tags(raw.get("tags")),
        "indexes": clean_indexes(raw.get("indexes")),
        "movers": clean_movers(raw.get("movers")),
    }
    if not out["body"] and not out["indexes"]:
        return None
    return out


def format_post(brief: dict) -> str:
    """The Discord post for a stored brief: the numbers as a scannable line,
    then the prose, then the tags."""
    head = f"**📈 Markets — {brief['day']}**"
    moves = "  ".join(
        f"{i['symbol']} {i['return_pct']:+.2f}%" for i in brief["indexes"])
    lines = [head]
    if moves:
        lines.append(moves)
    if brief["body"]:
        lines.append(brief["body"])
    if brief["tags"]:
        lines.append(" ".join(f"`{t}`" for t in brief["tags"]))
    return "\n".join(lines)
