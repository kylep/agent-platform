"""The `app_judgment` tables as both writers see them (design 38).

The `judgment` tool (`tools/judgment/run.py`) and this app share one home for
column lists, enums, limits, validation, every SQL statement, and the pure
rules both sides apply (resolution, review flags). The app owns the DDL; the
tool creates nothing. The tool loads this file by path from the synced
checkout, so it must stay **stdlib only**: the tool-executor image has no
SQLAlchemy.

Every statement uses `%s` placeholders (psycopg, the tool's driver). The app
runs the same text through SQLAlchemy `text()` after `db.translate()` rewrites
placeholders and drops the Postgres-only `FOR UPDATE` on sqlite. JSON is
stored as TEXT (`alternatives`, `requests.result`) so both drivers bind it as
a plain string. Ids are 32-hex strings minted in code, never sequences, so
both dialects agree.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone

# --- who ------------------------------------------------------------------------

# The one agent the tool serves. Moving ownership is a reviewed one-line change.
OWNER_AGENT = "kai"


def owner_principals(env=None) -> frozenset[str]:
    """The login principals whose admin session may use the App (Kyle's).
    Comma-separated `JUDGMENT_OWNER_PRINCIPALS`, default `admin` (the
    principal the login form uses)."""
    raw = (env if env is not None else os.environ).get("JUDGMENT_OWNER_PRINCIPALS", "admin")
    return frozenset(p.strip() for p in raw.split(",") if p.strip())


AGENT_AUTHOR = "agent:" + OWNER_AGENT


def user_author(principal: str) -> str:
    return "user:" + principal


# --- vocabularies -----------------------------------------------------------------

BELIEF_STATUSES = ("active", "superseded", "rejected")
PROVENANCES = ("kyle_confirmed", "kyle_relayed", "observed", "inference", "imported")
# What the tool may write. `kyle_confirmed` is the App's alone (a user author).
AGENT_PROVENANCES = ("kyle_relayed", "observed", "inference", "imported")
CONFIDENCES = ("low", "medium", "high")
TIMINGS = ("prospective", "retrospective")
OUTCOMES = ("supported", "contradicted", "mixed", "context_changed", "unresolved")
RESOLVING_OUTCOMES = ("supported", "contradicted", "mixed", "context_changed")

# --- limits -----------------------------------------------------------------------

LIMITS = {
    "claim": 1000,
    "scope": 4000,
    "evidence": 4000,
    "reason": 4000,
    "scenario": 4000,
    "predicted_choice": 1000,
    "rationale": 4000,
    "alternative": 500,
    "kyle_words": 4000,
    "interpretation": 4000,
    "source_ref": 200,
    "request_id": 100,
    "query": 200,
}
MAX_ALTERNATIVES = 10
MAX_LINKED_BELIEFS = 20
# Feedback this soon after its prediction is flagged in the review.
QUICK_FEEDBACK = timedelta(minutes=10)

# `relay:<32 hex>` or `discord:<channel id>/<message id>` (snowflakes).
SOURCE_REF_RE = re.compile(r"^(relay:[0-9a-f]{32}|discord:\d{1,25}/\d{1,25})$")
ID_RE = re.compile(r"^[0-9a-f]{32}$")
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,100}$")


class ValidationError(ValueError):
    """One line a model (or the page) can act on."""


def new_id() -> str:
    return uuid.uuid4().hex


def now() -> datetime:
    return datetime.now(timezone.utc)


def text_field(name: str, value, *, required: bool = True) -> str | None:
    """Strip, require, bound. Control characters other than newline/tab go."""
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise ValidationError(f"{name} is required")
        return None
    if not isinstance(value, str):
        raise ValidationError(f"{name} must be a string")
    value = "".join(ch for ch in value.strip() if ch in "\n\t" or ord(ch) >= 32)
    limit = LIMITS[name]
    if len(value) > limit:
        raise ValidationError(f"{name} is {len(value)} characters; the limit is {limit} — "
                              "keep the minimal evidence, not a transcript")
    return value


def choice(name: str, value, allowed: tuple, *, required: bool = True) -> str | None:
    if value is None:
        if required:
            raise ValidationError(f"{name} is required: one of {', '.join(allowed)}")
        return None
    if value not in allowed:
        raise ValidationError(f"{name} must be one of {', '.join(allowed)}, not {value!r}")
    return value


def source_ref(value, *, required: bool) -> str | None:
    value = text_field("source_ref", value, required=required)
    if value is not None and not SOURCE_REF_RE.match(value):
        raise ValidationError("source_ref must be relay:<32-hex message id> or "
                              "discord:<channel id>/<message id>")
    return value


def record_id(name: str, value, *, required: bool = True) -> str | None:
    if value is None:
        if required:
            raise ValidationError(f"{name} is required")
        return None
    if not isinstance(value, str) or not ID_RE.match(value):
        raise ValidationError(f"{name} must be a 32-hex id")
    return value


def timestamp(name: str, value, *, required: bool = False) -> datetime | None:
    if value is None:
        if required:
            raise ValidationError(f"{name} is required (ISO 8601)")
        return None
    try:
        ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        raise ValidationError(f"{name} must be an ISO 8601 timestamp") from None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    if ts > now() + timedelta(minutes=5):
        raise ValidationError(f"{name} is in the future")
    return ts


def alternatives(value) -> str:
    """A JSON text list of 0..10 short strings."""
    if value is None:
        value = []
    if not isinstance(value, list) or len(value) > MAX_ALTERNATIVES:
        raise ValidationError(f"alternatives must be a list of at most {MAX_ALTERNATIVES} strings")
    return json.dumps([text_field("alternative", v) for v in value])


# --- tables -------------------------------------------------------------------------

COLUMNS = {
    "beliefs": ["id", "created_at", "status", "current_version"],
    "belief_versions": [
        "id", "belief_id", "version", "claim", "scope", "evidence", "provenance",
        "source_ref", "confidence", "reason", "feedback_id", "author", "created_at",
    ],
    "predictions": [
        "id", "created_at", "scenario", "alternatives", "predicted_choice", "rationale",
        "confidence", "timing", "question_ref", "author",
    ],
    "prediction_beliefs": ["prediction_id", "belief_id", "belief_version"],
    "feedback": [
        "id", "created_at", "prediction_id", "belief_id", "belief_version", "kyle_words",
        "source_ref", "source_at", "outcome", "interpretation", "author", "confirmed_at",
    ],
    # Idempotency for the tool's writes: a retried call returns the receipt.
    "requests": ["request_id", "action", "result", "created_at"],
}

UNIQUE = {"belief_versions": ["belief_id", "version"]}
PRIMARY_KEYS = {
    "beliefs": ["id"], "belief_versions": ["id"], "predictions": ["id"],
    "prediction_beliefs": ["prediction_id", "belief_id"], "feedback": ["id"],
    "requests": ["request_id"],
}

# --- writes ----------------------------------------------------------------------------

INSERT_BELIEF = ("INSERT INTO beliefs (id, created_at, status, current_version) "
                 "VALUES (%s, %s, %s, %s)")

INSERT_VERSION = (
    "INSERT INTO belief_versions (id, belief_id, version, claim, scope, evidence, "
    "provenance, source_ref, confidence, reason, feedback_id, author, created_at) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)

# Params: (id,). Taken before a new version; sqlite drops FOR UPDATE.
LOCK_BELIEF = "SELECT id, status, current_version FROM beliefs WHERE id = %s FOR UPDATE"

# Params: (current_version, status, id).
SET_BELIEF_HEAD = "UPDATE beliefs SET current_version = %s, status = %s WHERE id = %s"

INSERT_PREDICTION = (
    "INSERT INTO predictions (id, created_at, scenario, alternatives, predicted_choice, "
    "rationale, confidence, timing, question_ref, author) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)

INSERT_PREDICTION_BELIEF = ("INSERT INTO prediction_beliefs (prediction_id, belief_id, "
                            "belief_version) VALUES (%s, %s, %s)")

INSERT_FEEDBACK = (
    "INSERT INTO feedback (id, created_at, prediction_id, belief_id, belief_version, "
    "kyle_words, source_ref, source_at, outcome, interpretation, author, confirmed_at) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)

INSERT_REQUEST = ("INSERT INTO requests (request_id, action, result, created_at) "
                  "VALUES (%s, %s, %s, %s)")
GET_REQUEST = "SELECT action, result FROM requests WHERE request_id = %s"

# --- reads ------------------------------------------------------------------------------

BELIEF = "SELECT id, created_at, status, current_version FROM beliefs WHERE id = %s"
VERSIONS = ("SELECT " + ", ".join(COLUMNS["belief_versions"]) + " FROM belief_versions "
            "WHERE belief_id = %s ORDER BY version")
VERSION = ("SELECT " + ", ".join(COLUMNS["belief_versions"]) + " FROM belief_versions "
           "WHERE belief_id = %s AND version = %s")
HAS_CONFIRMED = ("SELECT COUNT(*) FROM belief_versions WHERE belief_id = %s "
                 "AND provenance = 'kyle_confirmed'")

# Params: (pattern, pattern, limit) with pattern = '%' + lowercased query + '%'.
# Matches the CURRENT version's claim or scope.
SEARCH_BELIEFS = (
    "SELECT b.id FROM beliefs b JOIN belief_versions v "
    "ON v.belief_id = b.id AND v.version = b.current_version "
    "WHERE LOWER(v.claim) LIKE %s OR LOWER(COALESCE(v.scope, '')) LIKE %s "
    "ORDER BY b.created_at DESC LIMIT %s"
)
ALL_BELIEFS = "SELECT id FROM beliefs ORDER BY created_at DESC LIMIT %s"

PREDICTION = ("SELECT " + ", ".join(COLUMNS["predictions"]) + " FROM predictions WHERE id = %s")
PREDICTION_LINKS = ("SELECT prediction_id, belief_id, belief_version FROM prediction_beliefs "
                    "WHERE prediction_id = %s ORDER BY belief_id")
PREDICTIONS_FOR_BELIEF = ("SELECT prediction_id FROM prediction_beliefs WHERE belief_id = %s")
ALL_PREDICTIONS = ("SELECT " + ", ".join(COLUMNS["predictions"]) + " FROM predictions "
                   "ORDER BY created_at")

FEEDBACK_ONE = ("SELECT " + ", ".join(COLUMNS["feedback"]) + " FROM feedback WHERE id = %s")
FEEDBACK_FOR_PREDICTION = ("SELECT " + ", ".join(COLUMNS["feedback"]) + " FROM feedback "
                           "WHERE prediction_id = %s ORDER BY created_at")
FEEDBACK_FOR_BELIEF = ("SELECT " + ", ".join(COLUMNS["feedback"]) + " FROM feedback "
                       "WHERE belief_id = %s ORDER BY created_at")
ALL_FEEDBACK = ("SELECT " + ", ".join(COLUMNS["feedback"]) + " FROM feedback "
                "ORDER BY created_at")
# Contradicted/mixed feedback that no belief version cites yet.
AWAITING_REVISION = (
    "SELECT " + ", ".join("f." + c for c in COLUMNS["feedback"]) + " FROM feedback f "
    "WHERE f.outcome IN ('contradicted', 'mixed') AND NOT EXISTS ("
    "SELECT 1 FROM belief_versions v WHERE v.feedback_id = f.id) ORDER BY f.created_at"
)

# --- pure rules (both sides) ------------------------------------------------------------


def _as_dt(value) -> datetime | None:
    if value is None or isinstance(value, datetime):
        if isinstance(value, datetime) and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    return timestamp("ts", value)


def resolution(feedback_rows: list[dict]) -> str | None:
    """A prediction's outcome from its feedback, or None while unresolved.
    `unresolved` feedback resolves nothing; disagreeing resolved items are
    `mixed` (the review shows them all)."""
    outcomes = {f["outcome"] for f in feedback_rows if f["outcome"] in RESOLVING_OUTCOMES}
    if not outcomes:
        return None
    return outcomes.pop() if len(outcomes) == 1 else "mixed"


def prediction_flags(prediction: dict, feedback_rows: list[dict]) -> list[str]:
    """Why a prospective prediction deserves a second look. Timing is Kai's
    attestation (design 38), so the review shows the patterns that undercut
    it instead of trusting it silently."""
    flags = []
    created = _as_dt(prediction["created_at"])
    for f in feedback_rows:
        src = _as_dt(f.get("source_at"))
        if src is not None and src < created:
            flags.append("feedback predates the prediction")
        fb_created = _as_dt(f.get("created_at"))
        if fb_created is not None and fb_created - created < QUICK_FEEDBACK:
            flags.append("feedback within 10 minutes of the prediction")
    return sorted(set(flags))


def is_confirmed(feedback_row: dict) -> bool:
    return feedback_row.get("confirmed_at") is not None


def review_counts(predictions: list[dict], feedback_by_prediction: dict[str, list[dict]],
                  feedback_rows: list[dict]) -> dict:
    """The review's counts (design 38): no accuracy percentage, ever."""
    counts = {
        "prospective_resolved": {o: 0 for o in RESOLVING_OUTCOMES},
        "prospective_resolved_confirmed": 0,
        "prospective_resolved_relayed_only": 0,
        "prospective_pending": 0,
        "retrospective": 0,
        "flagged": 0,
        "feedback_unconfirmed": sum(1 for f in feedback_rows if not is_confirmed(f)),
    }
    for p in predictions:
        if p["timing"] == "retrospective":
            counts["retrospective"] += 1
            continue
        fb = feedback_by_prediction.get(p["id"], [])
        state = resolution(fb)
        if state is None:
            counts["prospective_pending"] += 1
        else:
            counts["prospective_resolved"][state] += 1
            resolving = [f for f in fb if f["outcome"] in RESOLVING_OUTCOMES]
            if any(is_confirmed(f) for f in resolving):
                counts["prospective_resolved_confirmed"] += 1
            else:
                counts["prospective_resolved_relayed_only"] += 1
        if prediction_flags(p, fb):
            counts["flagged"] += 1
    return counts
