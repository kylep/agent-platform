"""Cron preview — what an expression means, and when it will next fire.

The schedule builder in the UI (and every cron tooltip beside it) reads its
English from here rather than describing crons in the browser, so the sentence
an operator is shown and the schedule the scheduler fires come from one
implementation. The fire times come from `scheduler.next_fires`, which is the
function the scheduler itself uses: a preview that disagreed with the scheduler
about a daylight-saving boundary would be worse than no preview at all.
"""
from fastapi import APIRouter, Depends, Query

from agentplatform import cronenglish
from agentplatform.api.auth import READ_ROLES, require_role
from agentplatform.db import utcnow
from agentplatform.scheduler import is_valid_cron, is_valid_timezone, next_fires

from agentplatform.api import schemas as S
router = APIRouter()

PREVIEW_COUNT = 3


@router.get("/api/cron/preview", response_model=S.CronPreview,
            dependencies=[Depends(require_role(*READ_ROLES))])
async def cron_preview(
    # A cron is a handful of characters and an IANA zone is a short name. The
    # caps are what stops an unauthenticated-shaped mistake — a paste, a loop —
    # from handing the parser an arbitrarily long string to walk.
    expr: str = Query("", max_length=256),
    tz: str = Query("", max_length=64),
):
    """Describe a 5-field cron and list its next fires in `tz` (blank = UTC).

    Always 200: this is called as the operator types, and an expression that is
    half-written is an answer ("not valid yet"), not a failure.
    """
    expr, tz = expr.strip(), tz.strip()
    if not expr:
        return {"error": "a cron expression is required"}
    if not is_valid_timezone(tz):
        return {"error": f"unknown timezone {tz!r} — use an IANA name like America/Toronto"}
    # The renderer parses first because it can say *why* — "99 is outside 0-23"
    # is a fix; croniter's yes/no is only a verdict. croniter still gets the
    # last word, since it is what will run the schedule.
    try:
        english = cronenglish.describe(expr)
    except ValueError as e:
        return {"error": str(e)}
    expanded = cronenglish.expand_alias(expr)
    if not is_valid_cron(expanded):
        return {"error": "not a valid cron expression"}
    return {"english": english,
            "next": next_fires(expanded, utcnow(), tz, PREVIEW_COUNT)}
