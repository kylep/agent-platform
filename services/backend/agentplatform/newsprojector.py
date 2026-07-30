"""News projector — trusted code that turns a gatherer run's JSON digest into a
sanitized, deduped post for the Discord channel.

The gatherer agent is credential-less and only emits a structured digest as its
run result. This module (run inside the recorder, NOT an agent) parses that
digest, drops stories already posted (server-owned `shared_news` dedup),
neutralizes anything dangerous in the agent-controlled text, formats the post,
and records what was posted. Because it is deterministic code — not an LLM —
an injected digest cannot make it do anything but produce bounded text.
"""
from __future__ import annotations

import json
import re
from datetime import timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import delete, select

from agentplatform.db import SharedNews, utcnow

_SECTION_ORDER = ["AI industry", "AI tooling", "Open source", "Security",
                  "World", "Local", "Weather"]


def parse_digest(result_text: str | None) -> dict | None:
    """Extract the digest JSON from a run result. Tolerates ```json fences and
    surrounding prose; returns None if no JSON object is found (fail-safe: an
    unparseable/garbage result posts nothing)."""
    if not result_text:
        return None
    text = result_text.strip()
    # Strip a ```json … ``` fence if present.
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        # Fall back to the largest {...} span in the text.
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return obj if isinstance(obj, dict) else None


# Discord control sequences an agent-controlled string must not smuggle through.
_MENTION_RE = re.compile(r"<@[!&]?\d+>")


def sanitize(s: str) -> str:
    """Neutralize Discord mentions in agent-controlled text: @everyone/@here are
    defanged with a zero-width space, and raw <@…>/<@&…> mention tokens removed.
    Newlines collapsed so a headline can't inject extra lines."""
    zwsp = "\u200b"   # breaks @everyone/@here without visibly changing the text
    s = str(s).replace("\n", " ").replace("\r", " ")
    s = s.replace("@everyone", f"@{zwsp}everyone").replace("@here", f"@{zwsp}here")
    s = _MENTION_RE.sub("", s)
    return s.strip()


# Query params that are tracking/analytics noise, not story identity — dropped
# so the same article with a utm tag dedups against the plain URL. Anything else
# (e.g. ?id=123, ?story=…) is kept, since it can be part of the real identity.
_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "utm_name", "utm_reader", "utm_brand", "utm_social",
    "fbclid", "gclid", "dclid", "gbraid", "wbraid", "msclkid", "yclid",
    "mc_cid", "mc_eid", "igshid", "ncid", "cmpid", "ito", "at_medium",
    "at_campaign", "ref", "ref_src", "ref_url", "source", "spm", "s_cid",
}


def _norm_url(url: str) -> str:
    """Canonicalize a story URL for dedup: lowercase scheme+host, drop the
    fragment and tracking params, and trim a trailing slash — so the same story
    arriving with a utm tag, a #anchor, or http/https variance is recognized as
    already-shared. Falls back to a plain trim if the URL doesn't parse."""
    raw = str(url).strip()
    try:
        parts = urlsplit(raw)
        if not parts.scheme or not parts.netloc:
            return raw.rstrip("/")[:512]
        query = urlencode([(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                           if k.lower() not in _TRACKING_PARAMS])
        path = parts.path.rstrip("/")
        canon = urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, query, ""))
        return canon[:512]
    except ValueError:
        return raw.rstrip("/")[:512]


def format_post(date: str, items: list[dict]) -> str:
    """Group items by section (known sections first) into a skimmable digest."""
    by_section: dict[str, list[dict]] = {}
    for it in items:
        by_section.setdefault(sanitize(it.get("section", "") or "Other"), []).append(it)
    ordered = ([s for s in _SECTION_ORDER if s in by_section]
               + [s for s in by_section if s not in _SECTION_ORDER])
    lines = [f"**📰 News — {sanitize(date)}**" if date else "**📰 News**"]
    for section in ordered:
        lines.append(f"\n__{section}__")
        for it in by_section[section]:
            head = sanitize(it.get("headline", ""))
            why = sanitize(it.get("why", ""))
            url = _norm_url(it.get("url", ""))
            bullet = f"• **{head}**" + (f" — {why}" if why else "")
            if url:
                bullet += f" <{url}>"
            lines.append(bullet)
    return "\n".join(lines)


async def build_candidate(session, result_text: str | None) -> dict | None:
    """Parse the gatherer's digest and drop already-posted / intra-batch
    duplicate URLs, WITHOUT recording anything. Returns
    `{"date", "post_text", "items"}` for the new stories, or None when there is
    nothing new (or the result wasn't a valid digest).

    Read-only on purpose: dedup is only *committed* (record_shared) once the
    digest is going out — either straight away (ungated) or on approval — so a
    held-then-rejected digest doesn't burn its stories."""
    digest = parse_digest(result_text)
    if digest is None:
        return None
    raw_items = digest.get("items") if isinstance(digest.get("items"), list) else []
    valid = [it for it in raw_items
             if isinstance(it, dict) and it.get("url") and it.get("headline")]
    urls = [_norm_url(it["url"]) for it in valid]
    if not urls:
        return None
    seen = set((await session.execute(
        select(SharedNews.url).where(SharedNews.url.in_(urls)))).scalars())
    new_items = []
    for it in valid:
        u = _norm_url(it["url"])
        if u in seen:      # already posted, or a duplicate earlier in this batch
            continue
        seen.add(u)
        new_items.append(it)
    if not new_items:
        return None
    date = str(digest.get("date") or "")
    return {"date": date, "post_text": format_post(date, new_items), "items": new_items}


async def record_shared(session, items: list[dict], *, days: int = 14) -> None:
    """Mark a digest's stories as posted (dedup ledger) and prune old records.
    Called when a digest actually goes out — immediately (ungated) or on
    approval of a held one."""
    for it in items:
        session.add(SharedNews(url=_norm_url(it["url"]),
                               title=str(it.get("headline", ""))[:512],
                               section=str(it.get("section", ""))[:64]))
    await session.execute(delete(SharedNews).where(
        SharedNews.posted_at < utcnow() - timedelta(days=days)))
    await session.commit()


async def project(session, result_text: str | None, *, days: int = 14) -> str | None:
    """Ungated path: build the candidate, record its stories, and return the
    post text — or None when there is nothing new. Used when approval is off."""
    candidate = await build_candidate(session, result_text)
    if candidate is None:
        return None
    await record_shared(session, candidate["items"], days=days)
    return candidate["post_text"]
