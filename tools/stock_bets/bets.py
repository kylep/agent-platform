"""The stock-bets Tool's actions over the `stock-bets` state App.

`engine.py` owns the rules (calendar, intake, scoring) as pure functions.
This module owns the I/O around them: the clock, the daily bars (fetched by
this trusted code, never supplied by a model), and the App's records. Every
price and result field on a bet is written here; the App's `writers` rule
makes this Tool the only path, so no agent can set a number.

The store and the bar source are injected, so the tests drive the same code
with fakes.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from statistics import fmean
from typing import Callable, Protocol

import engine

APP = "stock-bets"
PET = "stock-bets"
TUNER = "olu"
# Who may call which action. The App's own access rules are the real fence;
# this table keeps a reader from attempting a write it would be refused.
CALLERS = {
    "bet": {PET}, "lesson": {PET},
    "settle": {PET, TUNER}, "check": {PET, TUNER, "pai"},
    "scoreboard": {PET, TUNER, "pai"}, "history": {PET, TUNER, "pai"},
    "playbook": {TUNER}, "research": {TUNER}, "import": {TUNER},
}
# A session's daily bar is read only this long after its close, so Yahoo's
# bar is final rather than the live one.
SETTLE_GRACE = timedelta(minutes=20)
LESSON_MAX = 500
SCOREBOARD_RECENT = 20
FIELD = sorted(engine.SYMBOLS)
# Confidence-sized stake (Kyle, 2026-11-20): $0 at 0.50, $1,000 at 0.90,
# linear between. The flat $1,000 P&L stays beside it; sized beating flat
# over many bets is the evidence that confidence carries information.
SIZED_MAX = Decimal("1000")


def sized_stake(confidence: float) -> float:
    c = Decimal(str(confidence))
    frac = (c - engine.CONFIDENCE_MIN) / (engine.CONFIDENCE_MAX - engine.CONFIDENCE_MIN)
    return float(round(SIZED_MAX * min(max(frac, Decimal(0)), Decimal(1)), 2))


class ToolError(ValueError):
    """An answer for the model: exits non-zero with this message."""


class Store(Protocol):
    def query(self, view: str, params: dict | None = None) -> list[dict]: ...
    def create(self, collection: str, values: dict, request_id: str) -> dict: ...
    def update(self, collection: str, record_id: str, version: int,
               values: dict, request_id: str) -> dict: ...


# Bars: {symbol: {date: {"open","high","low","close"}}}
BarSource = Callable[[list[str], date, date], dict]


@dataclass
class Ctx:
    store: Store
    bars: BarSource
    caller: str
    now: datetime          # aware UTC; the App's clock, never the caller's


def _rid(*parts: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_URL, "stock-bets:" + ":".join(parts)).hex


def _f(value: Decimal, places: int) -> float:
    return float(round(value, places))


def _exit_dict(values: dict) -> dict:
    out = {"kind": values["exit_kind"]}
    for key in ("target_pct", "stop_pct"):
        if values.get(key) is not None:
            out[key] = values[key]
    return out


def _all_bets(ctx: Ctx) -> list[dict]:
    return sorted(ctx.store.query("bets_all"), key=lambda b: b["session"])


def _current_playbook(ctx: Ctx) -> str:
    rows = ctx.store.query("playbook_all")
    if not rows:
        return "v1"
    return max(rows, key=lambda r: (r["starts"], r["created_at"]))["name"]


def _settle_ready(session: date, now: datetime) -> bool:
    """True once the session's close (13:00 or 16:00 ET) plus grace has passed."""
    local = engine.to_eastern(now)
    close = datetime.combine(session, engine.close_time(session))
    return local >= close + SETTLE_GRACE


def _bet_values(taken: engine.Bet, playbook: str, origin: str) -> dict:
    values = {
        "session": taken.session.isoformat(), "symbol": taken.symbol,
        "side": taken.side, "exit_kind": taken.exit.kind,
        "confidence": float(taken.confidence), "basis": taken.basis,
        "thesis": taken.thesis, "sources": list(taken.sources),
        "playbook": playbook, "taken_at": taken.taken_at.isoformat(),
        "late": taken.late, "origin": origin, "status": "open",
        "exit_session": engine.next_session(taken.session).isoformat(),
    }
    if taken.exit.target_pct is not None:
        values["target_pct"] = float(taken.exit.target_pct)
    if taken.exit.stop_pct is not None:
        values["stop_pct"] = float(taken.exit.stop_pct)
    return values


# ------------------------------------------------------------------ actions

def bet(ctx: Ctx, args: dict) -> dict:
    """Take today's bet, stamped by the App's clock."""
    bets = _all_bets(ctx)
    taken_sessions = {date.fromisoformat(b["session"]) for b in bets}
    payload = {k: args.get(k) for k in ("symbol", "side", "exit", "confidence",
                                        "basis", "thesis", "sources")}
    try:
        taken = engine.take_bet(payload, ctx.now, taken_sessions)
    except engine.BetRefused as exc:
        raise ToolError(f"{exc.code}: {exc}") from None
    if taken.status == "closed":
        return {"status": "closed", "session": taken.session.isoformat(),
                "message": "The US market is closed today; no bet is taken or needed."}
    playbook = _current_playbook(ctx)
    values = _bet_values(taken.bet, playbook, "pet")
    receipt = ctx.store.create("bets", values, _rid("bet", values["session"]))
    return {"status": "accepted", "id": receipt.get("id"), "session": values["session"],
            "sized_stake_usd": sized_stake(values["confidence"]),
            "exit_session": values["exit_session"], "late": values["late"],
            "playbook": playbook, "taken_at": values["taken_at"],
            "scores_after": "the exit session's close; call settle after that"}


def _score_bet(values: dict, bars: dict) -> dict:
    """Score one stored bet from fetched bars. Raises KeyError when a bar is
    missing, so the caller can report it and leave the bet open."""
    session = date.fromisoformat(values["session"])
    exit_session = date.fromisoformat(values["exit_session"])
    plan = engine.parse_exit(_exit_dict(values))
    sym = values["symbol"]
    entry_close = bars[sym][session]["close"]
    nxt = bars[sym][exit_session]
    spy = bars["SPY"]
    result = engine.score(values["side"], plan, entry_close, nxt,
                          spy[session]["close"], spy[exit_session]["close"])
    moved = Decimal(str(nxt["close"])) - Decimal(str(entry_close))
    direction_hit = moved > 0 if values["side"] == "long" else moved < 0
    field = []
    for other in FIELD:
        try:
            field.append(engine.score(values["side"], plan, bars[other][session]["close"],
                                      bars[other][exit_session], spy[session]["close"],
                                      spy[exit_session]["close"]).net_pct)
        except (KeyError, ValueError):
            continue
    spy_net = result.spy_pct - engine.COST_PCT
    out = {
        "status": "scored", "entry": _f(result.entry, 6), "exit": _f(result.exit, 6),
        "exit_reason": result.exit_reason, "gross_pct": _f(result.gross_pct, 4),
        "net_pct": _f(result.net_pct, 4), "pnl_usd": _f(result.pnl, 2),
        "win": result.win, "win_pct": 100 if result.win else 0,
        "direction_hit": direction_hit, "direction_pct": 100 if direction_hit else 0,
        "spy_pct": _f(result.spy_pct, 4), "spy_net_pct": _f(spy_net, 4),
        "spy_pnl_usd": _f(engine.STAKE * spy_net / 100, 2),
        "edge_pct": _f(result.net_pct - spy_net, 4),
        "bar_open": float(nxt["open"]), "bar_high": float(nxt["high"]),
        "bar_low": float(nxt["low"]), "bar_close": float(nxt["close"]),
    }
    if field:
        out["field_net_pct"] = _f(Decimal(str(fmean(float(x) for x in field))), 4)
        out["field_n"] = len(field)
    return out


def _fetch_for(ctx: Ctx, bets: list[dict]) -> dict:
    lo = min(date.fromisoformat(b["session"]) for b in bets)
    hi = max(date.fromisoformat(b["exit_session"]) for b in bets)
    return ctx.bars(FIELD, lo, hi)


def settle(ctx: Ctx, args: dict) -> dict:
    """Score every open bet whose exit session has closed. Idempotent."""
    bets = _all_bets(ctx)
    due = [b for b in bets if b["status"] == "open"
           and _settle_ready(date.fromisoformat(b["exit_session"]), ctx.now)]
    waiting = [{"session": b["session"], "exit_session": b["exit_session"]}
               for b in bets if b["status"] == "open" and b not in due]
    if not due:
        return {"settled": [], "waiting": waiting, "missing": []}
    bars = _fetch_for(ctx, due)
    settled, missing = [], []
    for b in due:
        try:
            scored = _score_bet(b, bars)
        except KeyError:
            missing.append({"session": b["session"], "symbol": b["symbol"],
                            "reason": "a needed daily bar is not available yet"})
            continue
        stamp = {**scored, "scored_at": ctx.now.isoformat()}
        ctx.store.update("bets", b["id"], b["version"], stamp,
                         _rid("settle", b["session"], str(b["version"])))
        row = {"session": b["session"], "symbol": b["symbol"], "side": b["side"],
               **{k: scored[k] for k in ("entry", "exit", "exit_reason", "net_pct",
                                         "pnl_usd", "win", "direction_hit",
                                         "spy_net_pct")}}
        if b.get("pet_net_pct") is not None:
            row["pet_net_pct"] = b["pet_net_pct"]
            row["pet_agrees"] = abs(b["pet_net_pct"] - scored["net_pct"]) < 0.01
        settled.append(row)
    return {"settled": settled, "waiting": waiting, "missing": missing}


SCORED_FIELDS = ("entry", "exit", "exit_reason", "gross_pct", "net_pct", "pnl_usd",
                 "win", "direction_hit", "spy_pct", "field_net_pct")


def check(ctx: Ctx, args: dict) -> dict:
    """Re-score a settled bet from freshly fetched bars, write nothing, and
    list any field that differs from what is stored."""
    session = args.get("session")
    bets = [b for b in _all_bets(ctx) if b["status"] == "scored"]
    if not bets:
        raise ToolError("no scored bet to check yet")
    target = next((b for b in bets if b["session"] == session), None) if session \
        else bets[-1]
    if target is None:
        raise ToolError(f"no scored bet for session {session}")
    try:
        fresh = _score_bet(target, _fetch_for(ctx, [target]))
    except KeyError:
        raise ToolError("a needed daily bar is not available") from None
    diffs = {}
    for key in SCORED_FIELDS:
        a, b = target.get(key), fresh.get(key)
        if isinstance(a, float) and isinstance(b, float):
            same = abs(a - b) < 0.0001 if key != "field_net_pct" else abs(a - b) < 0.01
        else:
            same = a == b
        if not same:
            diffs[key] = {"stored": a, "now": b}
    return {"session": target["session"], "symbol": target["symbol"],
            "ok": not diffs, "differences": diffs,
            "note": ("Yahoo revises bars rarely; a small difference in entry or "
                     "exit is a data revision, not a scoring bug.") if diffs else ""}


def lesson(ctx: Ctx, args: dict) -> dict:
    text = (args.get("lesson") or "").strip()
    if not text or len(text) > LESSON_MAX:
        raise ToolError(f"lesson must be 1 to {LESSON_MAX} characters")
    session = args.get("session")
    bets = _all_bets(ctx)
    target = next((b for b in bets if b["session"] == session), None)
    if target is None:
        raise ToolError(f"no bet for session {session}")
    if target["status"] != "scored":
        raise ToolError("that bet is not scored yet; a lesson comes after its result")
    if target.get("lesson"):
        raise ToolError("that bet already has its lesson; lessons are written once")
    ctx.store.update("bets", target["id"], target["version"], {"lesson": text},
                     _rid("lesson", session))
    return {"session": session, "saved": True}


def _block(rows: list[dict]) -> dict:
    n = len(rows)
    if not n:
        return {"settled": 0}
    wins = [r for r in rows if r["win"]]
    losses = [r for r in rows if not r["win"]]
    avg = lambda key, rs: round(fmean(r[key] for r in rs), 4) if rs else None  # noqa: E731
    out = {
        "settled": n, "wins": len(wins), "win_rate": round(len(wins) / n, 4),
        "direction_hits": sum(1 for r in rows if r["direction_hit"]),
        "direction_rate": round(sum(1 for r in rows if r["direction_hit"]) / n, 4),
        "avg_net_pct": avg("net_pct", rows), "avg_win_net_pct": avg("net_pct", wins),
        "avg_loss_net_pct": avg("net_pct", losses),
        "pnl_usd": round(sum(r["pnl_usd"] for r in rows), 2),
        "spy_avg_net_pct": avg("spy_net_pct", rows),
        "spy_pnl_usd": round(sum(r["spy_pnl_usd"] for r in rows), 2),
        "sized_pnl_usd": round(sum(sized_stake(r["confidence"]) * r["net_pct"] / 100
                                   for r in rows), 2),
        "sized_avg_stake_usd": round(fmean(sized_stake(r["confidence"]) for r in rows), 2),
    }
    field = [r for r in rows if r.get("field_net_pct") is not None]
    if field:
        out["field_avg_net_pct"] = avg("field_net_pct", field)
    return out


def scoreboard(ctx: Ctx, args: dict) -> dict:
    bets = _all_bets(ctx)
    scored = [b for b in bets if b["status"] == "scored"]
    by = lambda key: {k: _block([b for b in scored if b[key] == k])  # noqa: E731
                      for k in sorted({b[key] for b in scored})}
    blocks = [_block(scored[i:i + 20]) | {"bets": f"{i + 1}-{i + 20}"}
              for i in range(0, len(scored), 20)]
    brackets = [{"session": b["session"], "target_pct": b.get("target_pct"),
                 "stop_pct": b.get("stop_pct"), "exit_reason": b["exit_reason"]}
                for b in scored if b["exit_kind"] == "bracket"]
    today = engine.to_eastern(ctx.now).date().isoformat()
    return {
        "as_of": ctx.now.isoformat(), "total_bets": len(bets),
        "open": [{"session": b["session"], "symbol": b["symbol"], "side": b["side"],
                  "exit_session": b["exit_session"]} for b in bets if b["status"] == "open"],
        "bet_today": any(b["session"] == today for b in bets),
        "overall": _block(scored), "last_20": _block(scored[-SCOREBOARD_RECENT:]),
        "blocks_of_20": blocks, "by_playbook": by("playbook"), "by_basis": by("basis"),
        "by_side": by("side"), "brackets": brackets[-SCOREBOARD_RECENT:],
        "lessons_missing": [b["session"] for b in scored if not b.get("lesson")],
        "late_bets": [b["session"] for b in bets if b["late"]],
        "notes": ("win = net % > 0 after 0.05% cost. direction_hit ignores the exit plan. "
                  "spy_* is always-long SPY for the same sessions with the same cost; "
                  "field_* is the same side and exit plan on all 25 symbols, averaged "
                  "(the random-pick baseline). pnl_usd is a flat $1,000 stake; sized_pnl_usd stakes "
                  "$1,000 x (confidence - 0.50) / 0.40, so it beats flat only if higher "
                  "confidence really wins more. ~150 settled bets are needed to tell a "
                  "60% hit rate from 50%."),
    }


def history(ctx: Ctx, args: dict) -> dict:
    """The last N bets with their results and lessons (default 10)."""
    n = max(1, min(int(args.get("limit") or 10), 60))
    keep = ("session", "symbol", "side", "exit_kind", "target_pct", "stop_pct",
            "confidence", "basis", "thesis", "playbook", "late", "status",
            "sized_stake_usd",
            "exit_reason", "net_pct", "win", "direction_hit", "spy_net_pct",
            "field_net_pct", "lesson")
    return {"bets": [{k: v for k, v in ({**b, "sized_stake_usd": sized_stake(b["confidence"])}
                                        .items()) if k in keep and v is not None}
                     for b in _all_bets(ctx)[-n:]]}


def playbook(ctx: Ctx, args: dict) -> dict:
    version = (args.get("version") or "").strip()
    if not version or len(version) > 16:
        raise ToolError("version is required, e.g. v2")
    if any(r["name"] == version for r in ctx.store.query("playbook_all")):
        raise ToolError(f"playbook {version} already exists; versions are immutable")
    values = {"name": version, "starts": engine.to_eastern(ctx.now).date().isoformat()}
    for key in ("text", "reason", "prediction", "refuter"):
        if not (args.get(key) or "").strip():
            raise ToolError(f"{key} is required: a playbook change states its "
                            "reason, prediction and refuter")
        values[key] = args[key].strip()
    receipt = ctx.store.create("playbook", values, _rid("playbook", version))
    return {"version": version, "starts": values["starts"], "id": receipt.get("id"),
            "next": "bets taken from now on record this version"}


def research(ctx: Ctx, args: dict) -> dict:
    values = {"day": engine.to_eastern(ctx.now).date().isoformat()}
    for key in ("source", "claim", "status", "idea"):
        value = args.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ToolError(f"{key} is required")
        values[key] = value.strip()
    if values["status"] not in ("READ", "TESTED"):
        raise ToolError("status is READ or TESTED")
    receipt = ctx.store.create("research", values, uuid.uuid4().hex)
    return {"saved": True, "id": receipt.get("id"), "day": values["day"]}


def import_bets(ctx: Ctx, args: dict) -> dict:
    """Bring wiki-ledger bets into the App. Each keeps the time it was made,
    is re-scored by `settle` with the App's arithmetic, and carries the pet's
    own net % so the difference is listed."""
    items = args.get("bets")
    if not isinstance(items, list) or not 1 <= len(items) <= 60:
        raise ToolError("bets must be a list of 1 to 60 ledger bets")
    existing = {b["session"] for b in _all_bets(ctx)}
    imported, skipped = [], []
    for item in items:
        if not isinstance(item, dict):
            raise ToolError("each bet is an object")
        try:
            made = datetime.fromisoformat(str(item.get("made", "")).replace("Z", "+00:00"))
        except ValueError:
            raise ToolError("made must be an ISO time with offset (the wiki append time)") \
                from None
        if made.tzinfo is None or made > ctx.now:
            raise ToolError("made must carry an offset and be in the past")
        try:
            taken = engine.take_bet(item, made, set())
        except engine.BetRefused as exc:
            raise ToolError(f"{item.get('made')}: {exc.code}: {exc}") from None
        if taken.status == "closed":
            skipped.append({"made": item.get("made"), "reason": "market closed that day"})
            continue
        session = taken.bet.session.isoformat()
        if session in existing:
            skipped.append({"session": session, "reason": "already in the App"})
            continue
        values = _bet_values(taken.bet, str(item.get("playbook") or "v1")[:16], "import")
        if item.get("pet_net_pct") is not None:
            values["pet_net_pct"] = float(item["pet_net_pct"])
        ctx.store.create("bets", values, _rid("bet", session))
        existing.add(session)
        imported.append(session)
    return {"imported": imported, "skipped": skipped,
            "next": "call settle: it scores the imported bets and lists any whose "
                    "pet_net_pct differs"}


ACTIONS = {"bet": bet, "settle": settle, "check": check, "lesson": lesson,
           "scoreboard": scoreboard, "history": history, "playbook": playbook,
           "research": research, "import": import_bets}


def execute(ctx: Ctx, args: dict) -> dict:
    action = args.get("action")
    fn = ACTIONS.get(action)
    if fn is None:
        raise ToolError(f"unknown action {action!r}")
    if ctx.caller not in CALLERS[action]:
        raise ToolError(f"{action} is for {', '.join(sorted(CALLERS[action]))}, "
                        f"not {ctx.caller or 'an unknown caller'}")
    return fn(ctx, args)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


