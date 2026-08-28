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
from datetime import date as date_cls
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SECTION_ORDER = ["AI industry", "AI tooling", "Open source", "Security",
                 "World", "Local", "Weather"]

# Rejection reasons, in the order the Discord footer lists them.
REASON_ORDER = ("stale", "undated", "hub-url", "duplicate-story", "duplicate-url")

# Sections whose item is new every day at the same URL (the forecast):
# dedup by (url, day) and skip the story-similarity check.
DAILY_SECTIONS = {"weather"}


def is_daily_section(section: str) -> bool:
    return slugify(section) in DAILY_SECTIONS

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


# --- freshness gates ---------------------------------------------------------
# Why these exist: the gatherer reads web-search snippets that carry no
# publication dates, and month-scoped queries land on roundup pages — so a
# May CVE or last year's funding round arrives labelled "today". The agent
# can't be trusted to notice; the trusted side has to check. Every gate is
# deterministic and cheap, and a rejection is data (an event + a footer
# count), never a crash.

def parse_day(s) -> date_cls | None:
    """Strict YYYY-MM-DD → date; anything else is None (the agent's `published`
    field is untrusted text)."""
    s = str(s or "").strip()
    if len(s) != 10 or s[4] != "-" or s[7] != "-":
        return None
    try:
        return date_cls.fromisoformat(s)
    except ValueError:
        return None


# Rolling aggregators whose pages change under a stable URL: a "story" cited
# to one of these is really a pointer at a listicle, and the URL-based dedup
# would either let the same page through under a new headline or lock the
# page out forever after its first use.
AGGREGATOR_HOSTS = {
    "releasebot.io", "releases.sh", "sharkstriker.com", "aiweekly.co",
    "llm-stats.com", "aitoolsrecap.com", "claudelog.com", "aiagentstore.ai",
    "agentic.ai", "ground.news", "wikipedia.org", "x.com", "twitter.com",
}

_HUB_SEGMENT = re.compile(r"[a-z]+")


def is_hub_url(url: str) -> bool:
    """Is this a site section/landing page rather than an article? A news
    article URL virtually always carries a slug (hyphens) or a date/id
    (digits); a bare root or one-or-two plain-word segments (`/news/`,
    `/support/security`, `/local/toronto/`) is a hub. Aggregator hosts are
    hubs regardless of path."""
    try:
        parts = urlsplit(str(url).strip())
    except ValueError:
        return True
    host = parts.netloc.lower().split(":")[0]
    if any(host == h or host.endswith("." + h) for h in AGGREGATOR_HOSTS):
        return True
    segments = [s for s in parts.path.split("/") if s]
    if not segments:
        return True
    return len(segments) <= 2 and all(_HUB_SEGMENT.fullmatch(s) for s in segments)


# Words that carry no story identity: English function words plus the verbs
# and adjectives every headline reaches for. Stripped before comparing.
_STOPWORDS = {
    "a", "an", "the", "of", "in", "on", "for", "to", "as", "at", "by", "and",
    "or", "its", "it", "is", "are", "was", "with", "from", "amid", "after",
    "over", "into", "up", "out", "via", "than", "that", "this", "his", "her",
    "their", "has", "have", "been", "not", "but", "all", "yet", "now", "still",
    "new", "major", "critical", "big", "first", "latest", "today", "week",
    "launches", "launch", "launched", "ships", "shipped", "releases",
    "released", "release", "patches", "patched", "fixes", "fixed", "announces",
    "announced", "unveils", "unveiled", "confirms", "confirmed", "reports",
    "reported", "says", "said", "adds", "added", "gets", "hits", "warns",
    "faces", "heats", "ahead", "more", "including", "includes", "across",
    "under", "against", "amid", "ahead", "set", "top", "key",
}
_CVE_RE = re.compile(r"cve-\d{4}-\d{4,}")
_WORD_RE = re.compile(r"[a-z0-9][a-z0-9.\-]*[a-z0-9]|[a-z0-9]")


def story_tokens(title: str) -> tuple[set[str], set[str]]:
    """(identity tokens, CVE ids) for a headline. Tokens keep inner dots and
    hyphens so `18.6`, `zero-day`, `gpt-5.3-codex` survive as single terms."""
    t = str(title).lower()
    cves = set(_CVE_RE.findall(t))
    words = {w for w in _WORD_RE.findall(t) if len(w) >= 3 and w not in _STOPWORDS}
    return words, cves


def same_story(a: str, b: str) -> bool:
    """Two headlines describe one story when they share a CVE id, or when at
    least two identity tokens are shared and they cover half of the shorter
    headline (overlap coefficient — robust to one side being much wordier,
    which Jaccard is not)."""
    ta, ca = story_tokens(a)
    tb, cb = story_tokens(b)
    if ca & cb:
        return True
    if not ta or not tb:
        return False
    shared = ta & tb
    return len(shared) >= 2 and len(shared) / min(len(ta), len(tb)) >= 0.5


def format_post(date: str, items: list[dict], filtered: dict[str, int] | None = None) -> str:
    """Group items by section (known sections first) into a skimmable Discord
    digest. Only NEW (just-ingested) items are passed here; `filtered` is the
    per-reason count of what the gates dropped, shown as a subtext footer so
    a thin digest is visibly the filter's doing rather than a quiet day."""
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
    counts = {k: v for k, v in (filtered or {}).items() if v}
    if counts:
        ordered_reasons = ([r for r in REASON_ORDER if r in counts]
                           + sorted(r for r in counts if r not in REASON_ORDER))
        lines.append("\n-# filtered: " + " · ".join(f"{counts[r]} {r}" for r in ordered_reasons))
    return "\n".join(lines)
