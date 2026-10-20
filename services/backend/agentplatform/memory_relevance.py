"""Small deterministic lexical policy shared by ranking and excerpt selection."""

import re
from collections import Counter

# Remove grammatical query words, not domain labels or receipt vocabulary.
STOPWORDS = frozenset(
    "a an and are as at be been by can did do does for from had has have how i if in is it its me my of on or our should that the their them then there these they this to us was we were what when where which who why will with would you your".split()
)


def query_terms(query):
    return list(
        dict.fromkeys(
            t
            for t in re.findall(r"\w+(?:[.-]\w+)*", query.lower())
            if t not in STOPWORDS
        )
    )[:32]


def coverage(content, terms, weights):
    return sum(
        weight
        for term, weight in zip(terms, weights)
        if re.search(re.escape(term), content, re.IGNORECASE)
    )


def lexical_start(content, terms, weights):
    """Best 2,000-character window by distinct weighted term coverage.

    Bound repeated matches per term; one long repeated receipt cannot create
    an unbounded number of candidate windows. Offsets are Python codepoints.
    """
    events = []
    for term, weight in zip(terms, weights):
        for i, match in enumerate(re.finditer(re.escape(term), content, re.IGNORECASE)):
            if i == 128:
                break
            events.append((match.start(), term, weight))
    if not events:
        return 0
    events.sort()
    counts, score, best, start, left, right = Counter(), 0.0, -1.0, 0, 0, 0
    for position, _, _ in events:
        lo = max(0, position - 160)
        # Reserve trailing context as well as the leading 160 characters.
        # Otherwise a window can end just after a matching label ("code:")
        # while clipping the value that follows it.
        while right < len(events) and events[right][0] < lo + 1840:
            _, term, weight = events[right]
            if not counts[term]:
                score += weight
            counts[term] += 1
            right += 1
        while left < right and events[left][0] < lo:
            _, term, weight = events[left]
            counts[term] -= 1
            if not counts[term]:
                score -= weight
            left += 1
        if score > best + 1e-9:
            best, start = score, lo
    return start


def lexical_window(content, terms, weights, size):
    """Reanchor to the actual budget around a complete literal match.

    An immediately following colon binds its value through the next structural
    boundary to the label. Punctuation inside a non-whitespace token (a URL,
    decimal, version or dotted identifier) never ends that value. Other matches
    require their entire whitespace-delimited token, even for substring queries.
    These are structural units, not a claim of natural-language answerability.
    Returns (start, required_start, required_end), or None when none fits.
    """
    # Inspect only enough context per bounded match to establish a fitting unit.
    # Canonical notes have no storage size cap; never tokenize the entire note.
    boundary = re.compile(r"[\r\n]|[.!?;](?=\s|$)")
    anchors = []
    for term, weight in zip(terms, weights):
        for i, match in enumerate(re.finditer(re.escape(term), content, re.I)):
            if i == 128:
                break
            lo = match.start()
            while lo and not content[lo - 1].isspace() and match.end() - lo <= size:
                lo -= 1
            if match.end() - lo > size:
                continue
            stop = lo + size
            cursor = match.end()
            while cursor < len(content) and content[cursor].isspace() and content[cursor] not in '\r\n' and cursor <= stop:
                cursor += 1
            # An over-budget horizontal gap cannot establish whether the label
            # binds a value. Conservatively omit this unresolved unit.
            if cursor > stop:
                continue
            if cursor < len(content) and content[cursor] == ':':
                value_start = cursor + 1
                # Sentence punctuation must be followed by whitespace/end.
                # In particular '?' and ';' within URL queries are not stops.
                found = boundary.search(content, value_start, min(len(content), stop + 1))
                end = found.end() if found else len(content)
            else:
                end = match.end()
                while end < len(content) and not content[end].isspace() and end <= stop:
                    end += 1
            if end > stop:
                continue
            anchors.append((lo, end, term, weight))
    windows, events = [], []
    for lo, hi, term, weight in anchors:
        if hi - lo > size:
            continue
        padding = min(160, (size - (hi - lo)) // 2)
        start = min(max(0, lo - padding), max(0, len(content) - size))
        windows.append((start, lo, hi))
        # A window contains this complete anchor iff end-size <= start <= lo.
        # Sweep those intervals instead of comparing every pair of matches.
        events.extend([(max(0, hi-size), 1, term, weight), (lo+1, -1, term, weight)])
    events.sort()
    counts, score, cursor, best = Counter(), 0.0, 0, None
    for start, lo, hi in sorted(windows):
        while cursor < len(events) and events[cursor][0] <= start:
            _, delta, term, weight = events[cursor]
            before = bool(counts[term])
            counts[term] += delta
            score += weight * (bool(counts[term]) - before)
            cursor += 1
        if best is None or score > best[0] + 1e-9:
            best = (score, start, lo, hi)
    return best[1:] if best else None
