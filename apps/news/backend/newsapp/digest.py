"""Digest handling, ported from the platform's newsprojector (which this app
replaces — news presentation belongs to the news app, not the recorder).

The gatherer agent is credential-less and only emits a structured digest as
its run result; the platform recorder publishes that text to app.news.inbound
verbatim. Everything here is deterministic code operating on UNTRUSTED,
agent-produced text: parse defensively, sanitize anything that reaches
Discord, canonicalize URLs for dedup. An injected digest can't make this code
do anything but produce bounded text and rows.

(Deliberate duplication: this logic used to live in
agentplatform/newsprojector.py. Apps depend only on public contracts — never
platform internals — so the code moved here wholesale; the platform side was
deleted.)"""
from __future__ import annotations

import json
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SECTION_ORDER = ["AI industry", "AI tooling", "Open source", "Security",
                 "World", "Local", "Weather"]

# Section label → topic slug. Anything unmapped slugifies as-is (auto-topic).
def slugify(section: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(section).lower()).strip("-")
    return s[:64] or "other"


def parse_digest(result_text: str | None) -> dict | None:
    """Extract the digest JSON from a run result. Tolerates ```json fences and
    surrounding prose; returns None if no JSON object is found (fail-safe: an
    unparseable/garbage result ingests nothing)."""
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


_MENTION_RE = re.compile(r"<@[!&]?\d+>")


def sanitize(s: str) -> str:
    """Neutralize Discord control sequences in agent-controlled text:
    @everyone/@here defanged with a zero-width space, raw mention tokens
    removed, newlines collapsed."""
    zwsp = "​"
    s = str(s).replace("\n", " ").replace("\r", " ")
    s = s.replace("@everyone", f"@{zwsp}everyone").replace("@here", f"@{zwsp}here")
    s = _MENTION_RE.sub("", s)
    return s.strip()


_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "utm_name", "utm_reader", "utm_brand", "utm_social",
    "fbclid", "gclid", "dclid", "gbraid", "wbraid", "msclkid", "yclid",
    "mc_cid", "mc_eid", "igshid", "ncid", "cmpid", "ito", "at_medium",
    "at_campaign", "ref", "ref_src", "ref_url", "source", "spm", "s_cid",
}


def norm_url(url: str) -> str:
    """Canonicalize a story URL for dedup: lowercase scheme+host, drop the
    fragment and tracking params, trim a trailing slash."""
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


def valid_items(digest: dict) -> list[dict]:
    raw_items = digest.get("items") if isinstance(digest.get("items"), list) else []
    return [it for it in raw_items
            if isinstance(it, dict) and it.get("url") and it.get("headline")]


def format_post(date: str, items: list[dict]) -> str:
    """Group items by section (known sections first) into a skimmable Discord
    digest. Only NEW (just-ingested) items are passed here."""
    by_section: dict[str, list[dict]] = {}
    for it in items:
        by_section.setdefault(sanitize(it.get("section", "") or "Other"), []).append(it)
    ordered = ([s for s in SECTION_ORDER if s in by_section]
               + [s for s in by_section if s not in SECTION_ORDER])
    lines = [f"**📰 News — {sanitize(date)}**" if date else "**📰 News**"]
    for section in ordered:
        lines.append(f"\n__{section}__")
        for it in by_section[section]:
            head = sanitize(it.get("headline", ""))
            why = sanitize(it.get("why", ""))
            url = norm_url(it.get("url", ""))
            bullet = f"• **{head}**" + (f" — {why}" if why else "")
            if url:
                bullet += f" <{url}>"
            lines.append(bullet)
    return "\n".join(lines)
