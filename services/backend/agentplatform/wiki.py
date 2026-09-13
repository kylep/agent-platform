"""The wiki (docs/design/21) is the platform's shared, citable knowledge: a page
is a row, a `[[slug]]` is how anything points at it, and every write is a diff
posted into `#wiki`. This module is its pure layer — the slug grammar, link
extraction, the summary, the diff, staleness, and every sentence the platform
writes into a room about a page — with no I/O.

It is separate from the store for the same reason `tickets.py` is: the API, the
`wiki` tool, the prompt block, the UI and the tests must agree on what a slug
is, what counts as a link and what the room was told, and a rule that lives in
one place cannot drift between them. The strings here are read by humans in the
web UI and, unrendered, in the Discord mirror, so they are golden-tested."""
import difflib
import re
import unicodedata
from datetime import datetime, timedelta, timezone

# Somebody else's words, flattened onto one line with room mentions defanged and
# a visible cap — the wiki quotes titles, reasons and authors into sentences it
# writes in its own voice exactly as Tickets does, and one sanitiser is one
# answer to what those words may contain.
from .tickets import one_line, participant_label
# Code is quoted text, never a link — a page explaining the `[[slug]]` syntax
# in a snippet (the seeded Home page does exactly that) must not create a wanted
# page for its example. Relay already decided what counts as code, fenced and
# inline, including the unclosed fence an LLM truncated mid-block; borrowing its
# patterns keeps "what is code" one answer across the platform.
from .relay import _CODE_RE, _FENCE

# The slug grammar (docs/design/21). Anchored with \A..\Z rather than ^..$: the
# slug is interpolated into URLs and into `[[...]]`, and `$` would let
# "home\nrm -rf" through a caller that reached for `.match`. 64 characters is
# the column width.
SLUG_RE = re.compile(r"\A[a-z0-9][a-z0-9-]{0,63}\Z")
SLUG_MAX = 64
# Caps matching the columns these fields are stored in, and the point past
# which none of them is information any more: a title is a headline, a reason
# is a sentence, a summary is the first paragraph.
TITLE_LIMIT, REASON_LIMIT, SUMMARY_LIMIT, LABEL_LIMIT = 120, 200, 280, 64
# Same shape as `relay_router.BUDGET_PREFIX` and Tickets': the store finds "did
# we already say this in this room this hour?" with a LIKE on the prefix, so the
# variable part has to come last.
BUDGET_PREFIX = "⏸️ paused: an agent has used its hourly wiki-write budget"

_FENCE_RE = re.compile(_FENCE, re.DOTALL)
# A link target is whatever is between the brackets — it is validated against
# the grammar afterwards rather than matched loosely here, so `[[not a slug]]`
# is text and not a link to `not-a-slug` nobody typed.
_LINK_RE = re.compile(r"\[\[([^\[\]\n]*)\]\]")
# `[text](url)` unwrapped to `text` for a summary. The url is allowed one level
# of nested parentheses, which both a wikipedia link and `javascript:alert(1)`
# have — stopping at the first `)` would leave the tail of the url in the
# summary, which is the one string that is never rendered as markdown.
_MD_LINK_RE = re.compile(r"\[([^\[\]]*)\]\((?:[^()]|\([^()]*\))*\)")
# Heading markers at the start of any line of the paragraph a summary is taken
# from: `## Wanted` summarises as `Wanted`.
_HEADING_RE = re.compile(r"^[ \t]*#{1,6}[ \t]*", re.MULTILINE)
_PARA_RE = re.compile(r"\n[ \t]*\n")
# Apostrophes vanish rather than becoming separators, so `Kyle's` slugs as
# `kyles` and not `kyle-s`. Both the typewriter and the typographic one: the
# ASCII fold below would drop the curly one anyway, but only by accident.
_APOSTROPHE_RE = re.compile(r"['’ʼ]")
_NON_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _text(body) -> str:
    r"""A body as the parsers see it. CRLF is normalised because a paragraph
    break is a blank line and `\r\n\r\n` is not one to any regex that says
    `\n`: a body pasted from Windows or fetched over HTTP would be one
    unsplittable paragraph, and its summary the whole page."""
    return (body or "").replace("\r\n", "\n").replace("\r", "\n")


def _is_heading_only(para: str) -> bool:
    """A paragraph that is nothing but headings — the shape a summary skips
    past. A heading with prose under it in the same paragraph is prose."""
    lines = [line for line in para.splitlines() if line.strip()]
    return bool(lines) and all(line.lstrip().startswith("#") for line in lines)


def slugify(title_or_key) -> str:
    """The slug for a title or a memory key: `Kyle's Location!` → `kyles-location`.

    Deterministic and lossy on purpose — promotion derives a slug from a memory
    key and a human creating a page gets one offered from the title, and both
    have to land on the same answer for the same words. Accents fold to ASCII
    (a slug is typed and grepped, not read aloud) and anything that is not a
    letter or a digit becomes a single `-`. Raises when nothing usable is left:
    a page identified by the empty string is a page nobody can link to."""
    folded = unicodedata.normalize("NFKD", str(title_or_key or ""))
    ascii_only = folded.encode("ascii", "ignore").decode("ascii").lower()
    slug = _NON_SLUG_RE.sub("-", _APOSTROPHE_RE.sub("", ascii_only)).strip("-")
    # Trimmed again after the cap: cutting mid-word can land on a separator, and
    # a trailing `-` is not a slug the grammar accepts.
    slug = slug[:SLUG_MAX].strip("-")
    if not SLUG_RE.fullmatch(slug):
        raise ValueError(f"no usable wiki slug in {title_or_key!r}")
    return slug


def find_links(body: str) -> list[str]:
    """The `[[slug]]` targets a body points at: first-seen order, lower-cased,
    no duplicates. A target that is not a slug is not a link — it is text that
    happens to sit in brackets, and turning it into a wanted page would fill the
    red-link list with prose nobody meant to write.

    A target that does not exist yet IS a link: that dangling row is the wanted
    page, which is the feature.

    Inline spans count as code here, unlike `tickets.find_ticket_refs` where
    naming a ticket in backticks is still naming it: a `[[slug]]` in backticks
    is someone showing the syntax, and the web renderer's chip pass already
    treats an inline span as inert, so linking it would disagree with what the
    reader is looking at."""
    out: list[str] = []
    for m in _LINK_RE.finditer(_CODE_RE.sub(" ", _text(body))):
        target = m.group(1).lower()
        if SLUG_RE.fullmatch(target) and target not in out:
            out.append(target)
    return out


def summary_of(body: str) -> str:
    """The page's first paragraph, on one line — what search results, the
    citation chip and every agent's `<wiki>` prompt block show instead of a
    64 KB body.

    A page that opens with its own title as a heading would summarise as that
    title, which says nothing the slug did not, so the heading is skipped when
    there is a paragraph behind it. Markdown that only means something rendered
    is unwrapped (`[[deploying]]` → `deploying`, `[the docs](url)` → `the docs`)
    because this string is shown as plain text everywhere it appears — and it
    goes through the same flattening as any other quoted words: the body is
    agent-written, and the summary is replayed into other agents' prompts."""
    # Fenced code is dropped before anything else: a page that opens with a
    # snippet would otherwise summarise as ``` plus a shell command, in search
    # results and in every other agent's prompt block. Inline spans stay — they
    # read as ordinary words in a one-line summary.
    paras = [p for p in _PARA_RE.split(_FENCE_RE.sub(" ", _text(body))) if p.strip()]
    if not paras:
        return ""
    # Every leading heading, not just the first: `# Page` over `## Section` over
    # the prose is a common shape, and stopping at the first hop summarises the
    # page as the word "Section".
    i = 0
    while i < len(paras) - 1 and _is_heading_only(paras[i]):
        i += 1
    text = _HEADING_RE.sub("", paras[i])
    text = _LINK_RE.sub(lambda m: m.group(1), text)
    text = _MD_LINK_RE.sub(lambda m: m.group(1), text)
    return one_line(text, SUMMARY_LIMIT)


def unified_diff(old: str, new: str, slug: str, v_old, v_new) -> str:
    """The text diff between two versions of a page, headed with the versions it
    is between. Versions store the full body precisely so this can be computed
    on read: nothing about a page's history depends on a patch having been
    stored correctly at write time."""
    return "\n".join(difflib.unified_diff(
        (old or "").splitlines(), (new or "").splitlines(),
        fromfile=f"{slug} v{v_old}", tofile=f"{slug} v{v_new}", lineterm=""))


def line_counts(old: str, new: str) -> tuple[int, int]:
    """`(added, removed)` for one write — the `(+12 −1)` on the diff card and in
    the history list. The two header lines are dropped by position rather than
    by prefix: a page that quotes a diff has body lines starting with `+++`, and
    they are content."""
    diff = list(difflib.unified_diff((old or "").splitlines(),
                                     (new or "").splitlines(), lineterm=""))
    added = sum(1 for line in diff[2:] if line.startswith("+"))
    removed = sum(1 for line in diff[2:] if line.startswith("-"))
    return added, removed


def card_for(page, version_row, *, url_base: str, added: int = 0,
             removed: int = 0) -> dict:
    """The `card` payload on a write's event message in `#wiki`. Kept small and
    flat: the Relay client renders it directly, and the Discord bridge falls
    back to the body below when it cannot. The line counts are passed in
    because they are a fact about two bodies and the card only has one."""
    return {"type": "wiki", "slug": page.slug,
            "title": one_line(page.title, TITLE_LIMIT),
            "version": version_row.version,
            # Kept namespaced (`agent:pai`) so the client can resolve a face,
            # but flattened all the same: a connector id is not the platform's
            # own text either.
            "author": one_line(version_row.author, LABEL_LIMIT),
            "reason": one_line(version_row.reason, REASON_LIMIT),
            "added": added, "removed": removed,
            "url": f"{(url_base or '').rstrip('/')}/wiki/{page.slug}"}


def card_body(page, version_row, *, added: int = 0, removed: int = 0) -> str:
    """The card message's plain text. It exists because the Discord mirror (and
    any other bridge) gets the body and not the card, so the sentence has to
    carry the edit on its own — and it keeps the `[[slug]]` so the mirror reads
    as a link back into the wiki. An empty reason drops the quotes rather than
    printing a pair with nothing between them."""
    who = one_line(participant_label(version_row.author), LABEL_LIMIT)
    reason = one_line(version_row.reason, REASON_LIMIT)
    said = f'{who}: "{reason}"' if reason else who
    return (f"📖 [[{page.slug}]] v{version_row.version} · {said} "
            f"(+{added} −{removed})")


def budget_body(limit: int) -> str:
    return f"{BUDGET_PREFIX} ({limit}/hour); try again later"


def promoted_body(memory_content: str, agent: str, key, when) -> str:
    """A promoted memory's page body: the note, then where it came from. The
    provenance line is the whole point of promotion — the memory stays the
    agent's private note and the page becomes the shared fact, so the page has
    to say whose note it was and when it hardened. An unnamed memory (promotion
    by id) simply drops the key rather than quoting an empty one."""
    named = f" `{one_line(key, LABEL_LIMIT)}`" if key else ""
    line = (f"Promoted from {one_line(participant_label(agent), LABEL_LIMIT)}'s"
            f" memory{named} on {when:%Y-%m-%d}")
    content = (memory_content or "").rstrip()
    return f"{content}\n\n{line}" if content else line


def _aware(ts: datetime) -> datetime:
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts


def is_stale(page, now: datetime, days: int) -> bool:
    """A live page nobody has touched for `days` — the gardener's list and the
    stats. Archived pages are out of the garden by definition: archiving is how
    a page stops being something anyone has to tend."""
    if page.archived_at is not None or page.updated_at is None:
        return False
    # sqlite hands back naive datetimes and postgres does not, and a caller
    # reaching for `utcnow()` passes a naive `now`. Either mismatch is a
    # TypeError that would take down a whole gardener pass over one row, so both
    # operands are normalised rather than one.
    return (_aware(now) - _aware(page.updated_at)) >= timedelta(days=days)
