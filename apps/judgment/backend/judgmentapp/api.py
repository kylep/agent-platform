"""Kyle's API, served under /apps/judgment/api/ (design 38, "The App").

Only Kyle's own admin session gets in. nginx has vetted the session or key
and stamps X-AP-User / X-AP-Role (the NetworkPolicy keeps anyone else from
forging them); this router then demands role `admin` AND a principal in
`schema.owner_principals()`. That refuses reader logins, `query_app` (always
`reader`), app keys and admin API keys, whose principal is the key's name.

The reads and the shared writes are the statements in `schema.py`, so the page
and the tool agree by construction. What is here alone is what only the page
does: confirming feedback, and deleting. Deletes are planned in memory from the
reference columns, then run as explicit statements in one transaction; the
same plan answers the delete preview, so what the page lists is what goes.
"""
from __future__ import annotations

import contextlib
import json
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from judgmentapp import schema
from judgmentapp.db import execute, query


def require_owner(x_ap_user: str = Header(default=""),
                  x_ap_role: str = Header(default="")) -> str:
    if x_ap_role != "admin" or not x_ap_user or x_ap_user not in schema.owner_principals():
        raise HTTPException(403, "the judgment app answers only its owner's admin session")
    return x_ap_user


router = APIRouter(prefix="/apps/judgment/api", dependencies=[Depends(require_owner)])

# --- the page's own statements ---------------------------------------------------

LIST_BELIEFS = ("SELECT " + ", ".join(schema.COLUMNS["beliefs"]) + " FROM beliefs "
                "ORDER BY created_at DESC")
LIST_BELIEFS_BY_STATUS = ("SELECT " + ", ".join(schema.COLUMNS["beliefs"]) + " FROM beliefs "
                          "WHERE status = %s ORDER BY created_at DESC")
ALL_VERSIONS = ("SELECT " + ", ".join(schema.COLUMNS["belief_versions"]) +
                " FROM belief_versions ORDER BY belief_id, version")

CONFIRM_FEEDBACK = "UPDATE feedback SET confirmed_at = %s WHERE id = %s AND confirmed_at IS NULL"
SET_FEEDBACK_WORDS = "UPDATE feedback SET kyle_words = %s WHERE id = %s"

# What a deletion plan reads: every reference, none of the text but the claims
# the preview shows. Locking the belief rows first serialises with the tool's
# new versions (they take LOCK_BELIEF), so no version slips in mid-plan.
LOCK_ALL_BELIEFS = "SELECT id FROM beliefs FOR UPDATE"
PLAN_BELIEFS = "SELECT id, status, current_version FROM beliefs"
PLAN_VERSIONS = "SELECT belief_id, version, feedback_id, claim FROM belief_versions"
PLAN_FEEDBACK = "SELECT id, prediction_id, belief_id FROM feedback"
PLAN_LINKS = "SELECT prediction_id, belief_id FROM prediction_beliefs"

UNLINK_FEEDBACK_BELIEF = ("UPDATE feedback SET belief_id = NULL, belief_version = NULL "
                          "WHERE id = %s")
UNLINK_FEEDBACK_PREDICTION = "UPDATE feedback SET prediction_id = NULL WHERE id = %s"
DELETE_FEEDBACK = "DELETE FROM feedback WHERE id = %s"
DELETE_VERSION = "DELETE FROM belief_versions WHERE belief_id = %s AND version = %s"
DELETE_LINK = "DELETE FROM prediction_beliefs WHERE prediction_id = %s AND belief_id = %s"
DELETE_BELIEF = "DELETE FROM beliefs WHERE id = %s"
DELETE_PREDICTION = "DELETE FROM predictions WHERE id = %s"


def _sf(request: Request):
    return request.app.state.sf


@contextlib.contextmanager
def _validating():
    try:
        yield
    except schema.ValidationError as e:
        raise HTTPException(422, str(e)) from None


def _path_id(value: str, what: str) -> str:
    if not schema.ID_RE.match(value):
        raise HTTPException(404, f"no such {what}")
    return value


# --- row shaping --------------------------------------------------------------
# asyncpg hands back datetimes, sqlite the ISO text the app stored. Normalise.

_TIMESTAMPS = ("created_at", "source_at", "confirmed_at")


def _iso(v) -> str | None:
    if v is None:
        return None
    return v.isoformat() if isinstance(v, datetime) else str(v)


def _alternatives(v) -> list:
    try:
        v = json.loads(v) if isinstance(v, str) else v
    except ValueError:
        return []
    return v if isinstance(v, list) else []


def _row(table: str, row: tuple) -> dict:
    d = dict(zip(schema.COLUMNS[table], row))
    for c in _TIMESTAMPS:
        if c in d:
            d[c] = _iso(d[c])
    if table == "predictions":
        d["alternatives"] = _alternatives(d["alternatives"])
    return d


def _latest_confirmed(versions: list[dict]) -> dict | None:
    confirmed = [v for v in versions if v["provenance"] == "kyle_confirmed"]
    return confirmed[-1] if confirmed else None


def _summary(belief: dict, versions: list[dict]) -> dict:
    current = next((v for v in versions if v["version"] == belief["current_version"]), None)
    return {**belief, "current": current, "confirmed": _latest_confirmed(versions)}


def _flags(prediction: dict, feedback: list[dict]) -> list[str]:
    # Timing is only ever in doubt for a prospective claim (design 38).
    if prediction["timing"] != "prospective":
        return []
    return schema.prediction_flags(prediction, feedback)


async def _belief(s, belief_id: str) -> dict:
    rows = await query(s, schema.BELIEF, (belief_id,))
    if not rows:
        raise HTTPException(404, "no such belief")
    return _row("beliefs", rows[0])


async def _versions(s, belief_id: str) -> list[dict]:
    return [_row("belief_versions", r) for r in await query(s, schema.VERSIONS, (belief_id,))]


async def _prediction(s, prediction_id: str) -> dict:
    rows = await query(s, schema.PREDICTION, (prediction_id,))
    if not rows:
        raise HTTPException(404, "no such prediction")
    return _row("predictions", rows[0])


async def _feedback(s, feedback_id: str) -> dict:
    rows = await query(s, schema.FEEDBACK_ONE, (feedback_id,))
    if not rows:
        raise HTTPException(404, "no such feedback")
    return _row("feedback", rows[0])


async def _feedback_by_prediction(s) -> tuple[list[dict], dict[str, list[dict]]]:
    rows = [_row("feedback", r) for r in await query(s, schema.ALL_FEEDBACK)]
    by_prediction: dict[str, list[dict]] = {}
    for f in rows:
        if f["prediction_id"]:
            by_prediction.setdefault(f["prediction_id"], []).append(f)
    return rows, by_prediction


# --- bodies -----------------------------------------------------------------------
# `extra="forbid"`: the page never sends `author`, `confirmed_at` or
# `provenance`; the server stamps them, and a body that tries is refused.

class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Confirm(_Body):
    expected_version: int


class Correct(_Body):
    expected_version: int
    claim: str
    scope: str | None = None
    reason: str | None = None


class Reject(_Body):
    expected_version: int
    reason: str


class NewFeedback(_Body):
    kyle_words: str
    outcome: str
    belief_id: str | None = None
    belief_version: int | None = None


class ConfirmFeedback(_Body):
    kyle_words: str | None = None


# --- me --------------------------------------------------------------------------------

@router.get("/me")
async def me(principal: str = Depends(require_owner)):
    return {"principal": principal}


# --- beliefs -----------------------------------------------------------------------------

@router.get("/beliefs")
async def beliefs(request: Request,
                  status: Literal["active", "superseded", "rejected", "all"] = "all"):
    async with _sf(request)() as s:
        if status == "all":
            rows = await query(s, LIST_BELIEFS)
        else:
            rows = await query(s, LIST_BELIEFS_BY_STATUS, (status,))
        versions: dict[str, list[dict]] = {}
        for r in await query(s, ALL_VERSIONS):
            v = _row("belief_versions", r)
            versions.setdefault(v["belief_id"], []).append(v)
    out = []
    for r in rows:
        b = _row("beliefs", r)
        out.append(_summary(b, versions.get(b["id"], [])))
    return out


@router.get("/beliefs/{belief_id}")
async def belief_detail(request: Request, belief_id: str):
    _path_id(belief_id, "belief")
    async with _sf(request)() as s:
        belief = await _belief(s, belief_id)
        versions = await _versions(s, belief_id)
        predictions = []
        for (pid,) in await query(s, schema.PREDICTIONS_FOR_BELIEF, (belief_id,)):
            rows = await query(s, schema.PREDICTION, (pid,))
            if rows:
                p = _row("predictions", rows[0])
                predictions.append({k: p[k] for k in
                                    ("id", "scenario", "predicted_choice", "timing", "created_at")})
        feedback = [_row("feedback", r)
                    for r in await query(s, schema.FEEDBACK_FOR_BELIEF, (belief_id,))]
    predictions.sort(key=lambda p: p["created_at"], reverse=True)
    return {"belief": belief, "versions": versions, "predictions": predictions,
            "feedback": feedback}


async def _new_version(request: Request, belief_id: str, expected_version: int,
                       principal: str, make) -> dict:
    """Append a version under the belief's row lock, as the tool does: check
    `expected_version`, write version + 1 and move the head, one transaction.
    `make(current)` returns (fields for the new version, the belief's status)."""
    _path_id(belief_id, "belief")
    async with _sf(request).begin() as s:
        rows = await query(s, schema.LOCK_BELIEF, (belief_id,))
        if not rows:
            raise HTTPException(404, "no such belief")
        _, _, current_version = rows[0]
        if current_version != expected_version:
            raise HTTPException(409, f"the belief is at version {current_version}, not "
                                     f"{expected_version}; reload it and try again")
        cur = await query(s, schema.VERSION, (belief_id, current_version))
        if not cur:
            raise HTTPException(409, "the belief has no current version; reload it")
        with _validating():
            fields, status = make(_row("belief_versions", cur[0]))
        version = current_version + 1
        row = {"id": schema.new_id(), "belief_id": belief_id, "version": version,
               "feedback_id": None, "author": schema.user_author(principal),
               "created_at": schema.now(), **fields}
        await execute(s, schema.INSERT_VERSION,
                      [row[c] for c in schema.COLUMNS["belief_versions"]])
        await execute(s, schema.SET_BELIEF_HEAD, (version, status, belief_id))
        stored = await query(s, schema.VERSION, (belief_id, version))
    return {**_row("belief_versions", stored[0]), "status": status}


def _copy(current: dict, **changes) -> dict:
    keep = ("claim", "scope", "evidence", "provenance", "source_ref", "confidence")
    return {**{k: current[k] for k in keep}, **changes}


@router.post("/beliefs/{belief_id}/confirm")
async def confirm_belief(request: Request, belief_id: str, body: Confirm,
                         principal: str = Depends(require_owner)):
    def make(cur):
        return _copy(cur, provenance="kyle_confirmed",
                     reason=f"Kyle confirmed version {cur['version']} on his page"), "active"
    return await _new_version(request, belief_id, body.expected_version, principal, make)


@router.post("/beliefs/{belief_id}/correct")
async def correct_belief(request: Request, belief_id: str, body: Correct,
                         principal: str = Depends(require_owner)):
    def make(cur):
        # Kyle's own words: none of Kai's evidence or source carries over.
        scope = (schema.text_field("scope", body.scope, required=False)
                 if body.scope is not None else cur["scope"])
        reason = (schema.text_field("reason", body.reason, required=False)
                  or "Kyle corrected the claim on his page")
        return _copy(cur, claim=schema.text_field("claim", body.claim), scope=scope,
                     evidence=None, source_ref=None, provenance="kyle_confirmed",
                     reason=reason), "active"
    return await _new_version(request, belief_id, body.expected_version, principal, make)


@router.post("/beliefs/{belief_id}/reject")
async def reject_belief(request: Request, belief_id: str, body: Reject,
                        principal: str = Depends(require_owner)):
    def make(cur):
        # The claim as it stood, with Kyle's reason; the provenance is the
        # claim's, so a rejection never mints a new kyle_confirmed claim.
        return _copy(cur, reason=schema.text_field("reason", body.reason)), "rejected"
    return await _new_version(request, belief_id, body.expected_version, principal, make)


@router.delete("/beliefs/{belief_id}")
async def delete_belief(request: Request, belief_id: str):
    _path_id(belief_id, "belief")
    async with _sf(request).begin() as s:
        plan = await _plan(s)
        if belief_id not in plan.belief_rows:
            raise HTTPException(404, "no such belief")
        plan.belief(belief_id)
        return {"deleted": await plan.run(s)}


# --- predictions ------------------------------------------------------------------------

@router.get("/predictions")
async def predictions(request: Request,
                      state: Literal["pending", "resolved", "all"] = "all"):
    async with _sf(request)() as s:
        rows = [_row("predictions", r) for r in await query(s, schema.ALL_PREDICTIONS)]
        _, by_prediction = await _feedback_by_prediction(s)
    out = []
    for p in reversed(rows):
        fb = by_prediction.get(p["id"], [])
        resolution = schema.resolution(fb)
        if (state == "pending" and resolution is not None) or \
                (state == "resolved" and resolution is None):
            continue
        out.append({**p, "resolution": resolution, "flags": _flags(p, fb),
                    "feedback_count": len(fb)})
    return out


@router.get("/predictions/{prediction_id}")
async def prediction_detail(request: Request, prediction_id: str):
    _path_id(prediction_id, "prediction")
    async with _sf(request)() as s:
        p = await _prediction(s, prediction_id)
        links = []
        for _, belief_id, version in await query(s, schema.PREDICTION_LINKS, (prediction_id,)):
            # A link survives a deleted version (feedback deletion takes
            # versions, not links), so the claim can be gone: say so with null.
            v = await query(s, schema.VERSION, (belief_id, version))
            links.append({"belief_id": belief_id, "belief_version": version,
                          "claim": _row("belief_versions", v[0])["claim"] if v else None})
        fb = [_row("feedback", r)
              for r in await query(s, schema.FEEDBACK_FOR_PREDICTION, (prediction_id,))]
    return {"prediction": p, "links": links, "feedback": fb,
            "resolution": schema.resolution(fb), "flags": _flags(p, fb)}


@router.post("/predictions/{prediction_id}/feedback")
async def add_feedback(request: Request, prediction_id: str, body: NewFeedback,
                       principal: str = Depends(require_owner)):
    _path_id(prediction_id, "prediction")
    with _validating():
        words = schema.text_field("kyle_words", body.kyle_words)
        outcome = schema.choice("outcome", body.outcome, schema.OUTCOMES)
        belief_id = schema.record_id("belief_id", body.belief_id, required=False)
        if body.belief_version is not None and belief_id is None:
            raise schema.ValidationError("belief_version needs belief_id")
    async with _sf(request).begin() as s:
        await _prediction(s, prediction_id)
        version = None
        if belief_id is not None:
            rows = await query(s, schema.BELIEF, (belief_id,))
            if not rows:
                raise HTTPException(422, "belief_id names no belief")
            version = body.belief_version if body.belief_version is not None else rows[0][3]
            if not await query(s, schema.VERSION, (belief_id, version)):
                raise HTTPException(422, f"the belief has no version {version}")
        # Written on Kyle's page, so confirmed on creation (design 38).
        at = schema.now()
        fid = schema.new_id()
        await execute(s, schema.INSERT_FEEDBACK,
                      (fid, at, prediction_id, belief_id, version, words, None, None,
                       outcome, None, schema.user_author(principal), at))
        return await _feedback(s, fid)


@router.delete("/predictions/{prediction_id}")
async def delete_prediction(request: Request, prediction_id: str):
    _path_id(prediction_id, "prediction")
    async with _sf(request).begin() as s:
        await _prediction(s, prediction_id)
        plan = await _plan(s)
        plan.prediction(prediction_id)
        return {"deleted": await plan.run(s)}


# --- feedback ------------------------------------------------------------------------------

@router.get("/feedback")
async def feedback(request: Request, confirmed: Literal["false", "true", "all"] = "all"):
    async with _sf(request)() as s:
        rows = [_row("feedback", r) for r in await query(s, schema.ALL_FEEDBACK)]
    if confirmed != "all":
        want = confirmed == "true"
        rows = [f for f in rows if schema.is_confirmed(f) == want]
    return list(reversed(rows))


@router.post("/feedback/{feedback_id}/confirm")
async def confirm_feedback(request: Request, feedback_id: str,
                           body: ConfirmFeedback | None = None):
    """Confirms Kyle's words and the outcome, never Kai's interpretation.
    New words replace the relayed ones; the first confirmation time stands."""
    _path_id(feedback_id, "feedback")
    with _validating():
        words = schema.text_field("kyle_words", body.kyle_words if body else None,
                                  required=False)
    async with _sf(request).begin() as s:
        await _feedback(s, feedback_id)
        if words is not None:
            await execute(s, SET_FEEDBACK_WORDS, (words, feedback_id))
        await execute(s, CONFIRM_FEEDBACK, (schema.now(), feedback_id))
        return await _feedback(s, feedback_id)


@router.get("/feedback/{feedback_id}/delete-preview")
async def feedback_delete_preview(request: Request, feedback_id: str):
    _path_id(feedback_id, "feedback")
    async with _sf(request)() as s:
        await _feedback(s, feedback_id)
        plan = await _plan(s, lock=False)
    plan.feedback(feedback_id)
    return {
        "versions": [{"belief_id": b, "version": v, "claim": plan.version_rows[(b, v)][1]}
                     for b, v in sorted(plan.versions)],
        "beliefs_emptied": sorted(plan.beliefs),
        # Feedback left with no target once those beliefs go; usually empty.
        "feedback": sorted(plan.feedback_ids - {feedback_id}),
    }


@router.delete("/feedback/{feedback_id}")
async def delete_feedback(request: Request, feedback_id: str):
    _path_id(feedback_id, "feedback")
    async with _sf(request).begin() as s:
        await _feedback(s, feedback_id)
        plan = await _plan(s)
        plan.feedback(feedback_id)
        return {"deleted": await plan.run(s)}


# --- review --------------------------------------------------------------------------------

@router.get("/review")
async def review(request: Request):
    """Counts, then each resolved prospective prediction. No percentage: the
    counts are the whole score (design 38)."""
    async with _sf(request)() as s:
        preds = [_row("predictions", r) for r in await query(s, schema.ALL_PREDICTIONS)]
        rows, by_prediction = await _feedback_by_prediction(s)
    items = []
    for p in reversed(preds):
        if p["timing"] != "prospective":
            continue
        fb = by_prediction.get(p["id"], [])
        resolution = schema.resolution(fb)
        if resolution is None:
            continue
        items.append({"prediction": p, "feedback": fb, "resolution": resolution,
                      "flags": _flags(p, fb)})
    return {"counts": schema.review_counts(preds, by_prediction, rows), "items": items}


# --- deletion (design 38, "Deletion policy") ---------------------------------------------

async def _plan(s, *, lock: bool = True) -> "_Deletion":
    if lock:
        await query(s, LOCK_ALL_BELIEFS)
    return _Deletion(await query(s, PLAN_BELIEFS), await query(s, PLAN_VERSIONS),
                     await query(s, PLAN_FEEDBACK), await query(s, PLAN_LINKS))


class _Deletion:
    """Everything one delete removes, worked out before a row goes.

    - belief: its versions and prediction links; feedback that targeted it
      loses that target, and goes if it has none left.
    - prediction: its links; feedback that targeted it the same way.
    - feedback: every version citing it; a belief left without versions goes
      (and so on through the belief rule), otherwise its head falls back to
      the newest remaining version.

    Predictions are never updated: a deleted belief or version leaves the
    prediction's own text and its link (whose claim then reads as null).
    """

    def __init__(self, beliefs, versions, feedback, links):
        self.belief_rows = {bid: (status, current) for bid, status, current in beliefs}
        self.version_rows = {(bid, v): (fid, claim) for bid, v, fid, claim in versions}
        self.feedback_rows = {fid: (pid, bid) for fid, pid, bid in feedback}
        self.link_rows = [(pid, bid) for pid, bid in links]
        self.beliefs: set[str] = set()
        self.predictions: set[str] = set()
        self.feedback_ids: set[str] = set()
        self.versions: set[tuple[str, int]] = set()

    def belief(self, bid: str) -> None:
        if bid in self.beliefs:
            return
        self.beliefs.add(bid)
        self.versions |= {k for k in self.version_rows if k[0] == bid}
        for fid, (_, target) in self.feedback_rows.items():
            if target == bid:
                self._lost_target(fid)

    def prediction(self, pid: str) -> None:
        if pid in self.predictions:
            return
        self.predictions.add(pid)
        for fid, (target, _) in self.feedback_rows.items():
            if target == pid:
                self._lost_target(fid)

    def feedback(self, fid: str) -> None:
        if fid in self.feedback_ids:
            return
        self.feedback_ids.add(fid)
        citing = {k for k, (cite, _) in self.version_rows.items() if cite == fid}
        self.versions |= citing
        for bid in sorted({b for b, _ in citing}):
            if not self._remaining(bid):
                self.belief(bid)

    def _lost_target(self, fid: str) -> None:
        pid, bid = self.feedback_rows[fid]
        if (pid is None or pid in self.predictions) and (bid is None or bid in self.beliefs):
            self.feedback(fid)

    def _remaining(self, bid: str) -> list[int]:
        return sorted(v for b, v in self.version_rows if b == bid and (b, v) not in self.versions)

    async def run(self, s) -> dict:
        """The plan as explicit statements, inside the caller's transaction."""
        for fid, (pid, bid) in sorted(self.feedback_rows.items()):
            if fid in self.feedback_ids:
                continue
            if bid is not None and bid in self.beliefs:
                await execute(s, UNLINK_FEEDBACK_BELIEF, (fid,))
            if pid is not None and pid in self.predictions:
                await execute(s, UNLINK_FEEDBACK_PREDICTION, (fid,))
        for fid in sorted(self.feedback_ids):
            await execute(s, DELETE_FEEDBACK, (fid,))
        for bid, v in sorted(self.versions):
            await execute(s, DELETE_VERSION, (bid, v))
        links = sorted({(p, b) for p, b in self.link_rows
                        if p in self.predictions or b in self.beliefs})
        for pid, bid in links:
            await execute(s, DELETE_LINK, (pid, bid))
        for bid in sorted(self.beliefs):
            await execute(s, DELETE_BELIEF, (bid,))
        for pid in sorted(self.predictions):
            await execute(s, DELETE_PREDICTION, (pid,))
        for bid, (status, current) in sorted(self.belief_rows.items()):
            if bid not in self.beliefs and (bid, current) in self.versions:
                await execute(s, schema.SET_BELIEF_HEAD, (self._remaining(bid)[-1], status, bid))
        return {"beliefs": len(self.beliefs), "versions": len(self.versions),
                "links": len(links), "feedback": len(self.feedback_ids),
                "predictions": len(self.predictions)}
