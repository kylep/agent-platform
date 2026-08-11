"""Parse + validate the `running` agent's inbound payload.

Everything here is UNTRUSTED, agent-produced text. The agent pulls activities
through the strava tool and writes an encouraging weekly note; both arrive as a
JSON blob we must not trust. Parse defensively, clamp every number, allow only
known tags, sanitize anything bound for Discord. A hostile payload can produce
bounded activity rows and bounded text — nothing else.

Payload shape:
    {
      "activities": [ {id, date, type, name, distance_m, moving_time_s,
                        elevation_m, avg_hr, max_hr}, ... ],
      "brief": { "body": "...", "highlights": ["...", ...], "tags": ["pr", ...] }
    }
Either key may be absent — a pure sync sends only `activities`.
"""
from __future__ import annotations

import json
import re

# A small closed vocabulary of reasons a running week is notable, like the
# stockmarket brief's fixed tags. An invented tag is dropped, not created.
TAGS: list[str] = [
    "pr", "long-run", "speed", "recovery", "streak", "comeback",
    "race", "consistency", "big-week", "rest",
]
MAX_TAGS = 4
MAX_ACTIVITIES = 300
MAX_HIGHLIGHTS = 6
KNOWN_TYPES = {"Run", "TrailRun", "VirtualRun", "Walk", "Hike", "Ride",
               "VirtualRide", "Swim", "Workout", "WeightTraining"}

_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MENTION_RE = re.compile(r"<@[!&]?\d+>")


def parse_payload(result_text: str | None) -> dict | None:
    """Extract the payload JSON from a run result. Tolerates ```json fences and
    surrounding prose; returns None when no JSON object is found."""
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


def sanitize(s, limit: int = 600) -> str:
    """Neutralize Discord control sequences in agent text and clamp length."""
    zwsp = "​"
    s = str(s).replace("\n", " ").replace("\r", " ")
    s = s.replace("@everyone", f"@{zwsp}everyone").replace("@here", f"@{zwsp}here")
    s = _MENTION_RE.sub("", s)
    return " ".join(s.split())[:limit]


def _num(v, limit: float) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")) or f < 0 or f > limit:
        return None
    return f


def _int(v, limit: int) -> int | None:
    f = _num(v, limit)
    return int(round(f)) if f is not None else None


def clean_activities(raw) -> list[dict]:
    """Validate the activity rows into exactly what gets stored. Drops any row
    without a real id + date; clamps distances/times/HR to sane bands (a
    marathon is ~42km; nobody moves for 24h; HR over 260 is a sensor glitch)."""
    if not isinstance(raw, list):
        return []
    out, seen = [], set()
    for row in raw:
        if not isinstance(row, dict):
            continue
        try:
            aid = int(row.get("id"))
        except (TypeError, ValueError):
            continue
        day = str(row.get("date", "")).strip()
        if aid in seen or not _DAY_RE.match(day):
            continue
        seen.add(aid)
        typ = str(row.get("type", "Run")).strip()
        if typ not in KNOWN_TYPES:
            typ = "Run"
        out.append({
            "id": aid,
            "day": day,
            "name": sanitize(row.get("name", ""), 200),
            "type": typ,
            "distance_m": _int(row.get("distance_m"), 500_000) or 0,
            "moving_time_s": _int(row.get("moving_time_s"), 86_400) or 0,
            "elevation_m": _num(row.get("elevation_m"), 30_000),
            "avg_hr": _num(row.get("avg_hr"), 260),
            "max_hr": _num(row.get("max_hr"), 260),
        })
        if len(out) >= MAX_ACTIVITIES:
            break
    return out


def clean_tags(raw) -> list[str]:
    if not isinstance(raw, list):
        return []
    out = []
    for t in raw:
        slug = str(t).strip().lower()
        if slug in TAGS and slug not in out:
            out.append(slug)
    return out[:MAX_TAGS]


def clean_brief(raw) -> dict | None:
    """Validate the coach's note. None when there's nothing worth storing."""
    if not isinstance(raw, dict):
        return None
    body = sanitize(raw.get("body", ""), 2000)
    highlights = []
    if isinstance(raw.get("highlights"), list):
        for h in raw["highlights"]:
            hs = sanitize(h, 200)
            if hs:
                highlights.append(hs)
            if len(highlights) >= MAX_HIGHLIGHTS:
                break
    tags = clean_tags(raw.get("tags"))
    if not body and not highlights:
        return None
    return {"body": body, "highlights": highlights, "tags": tags}


def format_post(week_start: str, brief: dict, stats: dict) -> str:
    """The Discord post for a stored weekly brief: a headline stat line, the
    coach's note, highlights, then tags."""
    km = stats.get("distance_km") or 0
    runs = stats.get("runs") or 0
    head = f"**\U0001f3c3 Running — week of {week_start}**"
    line = f"{km:.1f} km across {runs} run{'s' if runs != 1 else ''}"
    lines = [head, line]
    if brief["body"]:
        lines.append(brief["body"])
    for h in brief["highlights"]:
        lines.append(f"• {h}")
    if brief["tags"]:
        lines.append(" ".join(f"`{t}`" for t in brief["tags"]))
    return "\n".join(lines)
