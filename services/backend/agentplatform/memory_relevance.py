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
        while right < len(events) and events[right][0] < lo + 2000:
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
