"""Tests for the stock-bets engine (ENG-17). Each done-when line of the
ticket has a test named after it; the calendar tests pin the dates the pet's
interim prompt carries by hand."""
from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import engine  # noqa: E402
from engine import Bar, BetRefused, ExitPlan, score, take_bet  # noqa: E402


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


def bet(**overrides):
    payload = {
        "symbol": "QQQ", "side": "long", "exit": {"kind": "close"},
        "confidence": 0.55, "basis": "momentum",
        "thesis": "Tech led into the close. Momentum tends to carry one session.",
        "sources": ["https://example.com/a"],
    }
    payload.update(overrides)
    return payload


# 2026-11-20 is a Friday in EST (UTC-5): 15:30 ET is 20:30 UTC, close 21:00 UTC.
BEFORE_CLOSE = utc(2026, 11, 20, 20, 30)
AFTER_CLOSE = utc(2026, 11, 20, 21, 5)


# ------------------------------------------------------------ calendar

@pytest.mark.parametrize("day", ["2026-11-26", "2026-12-25", "2027-01-01",
                                 "2027-01-18", "2027-02-15", "2027-03-26"])
def test_calendar_holidays_match_the_pets_interim_list(day):
    assert not engine.is_session(date.fromisoformat(day))


@pytest.mark.parametrize("day", ["2026-11-27", "2026-12-24"])
def test_calendar_early_closes_match_the_pets_interim_list(day):
    d = date.fromisoformat(day)
    assert engine.is_session(d)
    assert engine.close_time(d) == engine.EARLY_CLOSE


def test_calendar_has_no_other_holidays_or_early_closes_in_the_interim_window():
    day, holidays, early = date(2026, 11, 20), [], []
    while day <= date(2027, 3, 31):
        if day.weekday() < 5 and not engine.is_session(day):
            holidays.append(day.isoformat())
        elif engine.is_session(day) and engine.close_time(day) == engine.EARLY_CLOSE:
            early.append(day.isoformat())
        day = date.fromordinal(day.toordinal() + 1)
    assert holidays == ["2026-11-26", "2026-12-25", "2027-01-01",
                        "2027-01-18", "2027-02-15", "2027-03-26"]
    assert early == ["2026-11-27", "2026-12-24"]


def test_calendar_weekend_observance_and_saturday_new_year():
    # 2026-07-04 is a Saturday: observed Friday 07-03, and no July 3 early close.
    assert not engine.is_session(date(2026, 7, 3))
    assert date(2026, 7, 2) not in engine.early_closes(2026)
    # 2027-12-25 is a Saturday: observed Friday 12-24.
    assert not engine.is_session(date(2027, 12, 24))
    # 2022-01-01 was a Saturday: NYSE stayed open on Friday 2021-12-31.
    assert engine.is_session(date(2021, 12, 31))
    # 2025-07-03 (a Thursday) closed early.
    assert engine.close_time(date(2025, 7, 3)) == engine.EARLY_CLOSE


def test_next_session_skips_weekends_and_holidays():
    assert engine.next_session(date(2026, 11, 20)) == date(2026, 11, 23)
    assert engine.next_session(date(2026, 11, 25)) == date(2026, 11, 27)
    assert engine.next_session(date(2026, 12, 31)) == date(2027, 1, 4)


def test_eastern_time_follows_daylight_saving():
    assert engine.to_eastern(utc(2026, 7, 1, 20, 0)) == datetime(2026, 7, 1, 16, 0)
    assert engine.to_eastern(utc(2026, 11, 20, 21, 0)) == datetime(2026, 11, 20, 16, 0)
    # DST ends 2026-11-01 06:00 UTC (02:00 EDT -> 01:00 EST).
    assert engine.to_eastern(utc(2026, 11, 1, 5, 59)) == datetime(2026, 11, 1, 1, 59)
    assert engine.to_eastern(utc(2026, 11, 1, 6, 0)) == datetime(2026, 11, 1, 1, 0)


# --------------------------------------------------------------- intake

def test_second_bet_for_one_session_is_refused():
    first = take_bet(bet(), BEFORE_CLOSE, set())
    assert first.status == "accepted"
    with pytest.raises(BetRefused) as err:
        take_bet(bet(symbol="SPY"), BEFORE_CLOSE, {first.bet.session})
    assert err.value.code == "duplicate_session"


def test_bet_after_the_close_is_marked_late():
    on_time = take_bet(bet(), BEFORE_CLOSE, set()).bet
    late = take_bet(bet(), AFTER_CLOSE, set()).bet
    assert (on_time.late, late.late) == (False, True)
    assert late.session == date(2026, 11, 20)
    assert late.taken_at == AFTER_CLOSE


def test_bet_after_an_early_close_is_late():
    # 2026-11-27 closes 13:00 ET = 18:00 UTC; the pet runs at 15:30 ET.
    assert take_bet(bet(), utc(2026, 11, 27, 20, 30), set()).bet.late is True
    assert take_bet(bet(), utc(2026, 11, 27, 17, 30), set()).bet.late is False


def test_a_holiday_gives_no_bet_and_no_error():
    out = take_bet(bet(), utc(2026, 11, 26, 20, 30), set())
    assert out.status == "closed" and out.bet is None
    assert out.session == date(2026, 11, 26)
    # Even a malformed payload on a holiday is not an error: nothing is taken.
    assert take_bet({"symbol": "NOPE"}, utc(2026, 11, 26, 20, 30), set()).status == "closed"


def test_a_weekend_gives_no_bet_and_no_error():
    assert take_bet(bet(), utc(2026, 11, 21, 20, 30), set()).status == "closed"


@pytest.mark.parametrize("overrides,code", [
    ({"symbol": "GME"}, "bad_symbol"),
    ({"symbol": "qqq"}, "bad_symbol"),
    ({"side": "flat"}, "bad_side"),
    ({"exit": {"kind": "noon"}}, "bad_exit"),
    ({"exit": {"kind": "bracket"}}, "bad_exit"),
    ({"exit": {"kind": "bracket", "target_pct": 0.1}}, "bad_exit"),
    ({"exit": {"kind": "bracket", "stop_pct": 5.5}}, "bad_exit"),
    ({"exit": {"kind": "bracket", "stop_pct": True}}, "bad_exit"),
    ({"exit": {"kind": "bracket", "target_pct": "nan"}}, "bad_exit"),
    ({"exit": {"kind": "close", "target_pct": 1}}, "bad_exit"),
    ({"exit": {"kind": "close", "at": "11:00"}}, "bad_exit"),
    ({"exit": "close"}, "bad_exit"),
    ({"confidence": 0.95}, "bad_confidence"),
    ({"confidence": 0.49}, "bad_confidence"),
    ({"basis": "vibes"}, "bad_basis"),
    ({"thesis": " "}, "bad_thesis"),
    ({"thesis": "x" * 601}, "bad_thesis"),
    ({"sources": ["ok"] * 11}, "bad_sources"),
    ({"sources": [""]}, "bad_sources"),
])
def test_malformed_bets_are_refused(overrides, code):
    with pytest.raises(BetRefused) as err:
        take_bet(bet(**overrides), BEFORE_CLOSE, set())
    assert err.value.code == code


def test_bracket_edges_are_accepted():
    plan = take_bet(bet(exit={"kind": "bracket", "target_pct": 0.2, "stop_pct": 5}),
                    BEFORE_CLOSE, set()).bet.exit
    assert plan == ExitPlan("bracket", Decimal("0.2"), Decimal("5"))
    target_only = take_bet(bet(exit={"kind": "bracket", "target_pct": 1}),
                           BEFORE_CLOSE, set()).bet.exit
    assert target_only.stop_pct is None


def test_naive_timestamp_is_rejected():
    with pytest.raises(ValueError):
        take_bet(bet(), datetime(2026, 11, 20, 20, 30), set())


# -------------------------------------------------------------- scoring

BRACKET = {"kind": "bracket", "target_pct": 1, "stop_pct": 1}


def test_bracket_whose_day_touches_both_levels_scores_as_the_stop():
    # entry 100: target 101, stop 99; the day trades 98.5..101.5.
    r = score("long", BRACKET, 100, {"open": 100, "high": 101.5, "low": 98.5, "close": 100.5},
              500, 505)
    assert (r.exit_reason, r.exit) == ("stop", Decimal("99"))
    assert r.gross_pct == Decimal("-1.0000")
    assert r.net_pct == Decimal("-1.0500")
    assert r.pnl == Decimal("-10.50")
    assert r.win is False


def test_gap_through_the_target_scores_at_the_open():
    r = score("long", BRACKET, 100, {"open": 102, "high": 103, "low": 98, "close": 99}, 500, 500)
    assert (r.exit_reason, r.exit) == ("gap_target", Decimal("102"))
    assert r.gross_pct == Decimal("2.0000") and r.win is True


def test_gap_through_the_stop_scores_at_the_open():
    r = score("long", BRACKET, 100, {"open": 97, "high": 102, "low": 96, "close": 101}, 500, 500)
    assert (r.exit_reason, r.exit) == ("gap_stop", Decimal("97"))
    assert r.gross_pct == Decimal("-3.0000")


def test_target_only_and_untouched_brackets():
    hit = score("long", {"kind": "bracket", "target_pct": 1}, 100,
                {"open": 100, "high": 101.2, "low": 90, "close": 95}, 1, 1)
    assert (hit.exit_reason, hit.exit) == ("target", Decimal("101"))
    neither = score("long", BRACKET, 100,
                    {"open": 100, "high": 100.9, "low": 99.1, "close": 100.4}, 1, 1)
    assert (neither.exit_reason, neither.exit) == ("close", Decimal("100.4"))


def test_open_and_close_exits():
    bar = {"open": 101, "high": 103, "low": 99, "close": 102}
    assert score("long", {"kind": "open"}, 100, bar, 1, 1).exit == Decimal("101")
    assert score("long", {"kind": "close"}, 100, bar, 1, 1).exit == Decimal("102")


def test_net_is_gross_minus_five_basis_points_and_a_tiny_gain_loses():
    r = score("long", {"kind": "close"}, 100, {"open": 100, "high": 100, "low": 100,
                                               "close": 100.04}, 1, 1)
    assert r.gross_pct == Decimal("0.0400")
    assert r.net_pct == Decimal("-0.0100")
    assert r.win is False


def _mirror(bar, entry):
    """Reflect a bar through the entry price: a long on the mirror is the
    short on the original."""
    m = {k: 2 * entry - v for k, v in bar.items()}
    return {"open": m["open"], "high": m["low"], "low": m["high"], "close": m["close"]}


@pytest.mark.parametrize("bar", [
    {"open": 100, "high": 101.5, "low": 98.5, "close": 100.5},   # both touched
    {"open": 98, "high": 99, "low": 97, "close": 98.5},          # gap
    {"open": 100.2, "high": 100.6, "low": 99.6, "close": 100.3},  # neither
    {"open": 100, "high": 100.3, "low": 98.7, "close": 99},      # one level
])
@pytest.mark.parametrize("plan", [BRACKET, {"kind": "bracket", "target_pct": 1.2},
                                  {"kind": "open"}, {"kind": "close"}])
def test_a_short_scores_as_the_mirror_of_a_long(bar, plan):
    short = score("short", plan, 100, bar, 500, 505)
    long_ = score("long", plan, 100, _mirror(bar, 100), 500, 505)
    assert short.exit_reason == long_.exit_reason
    assert short.gross_pct == long_.gross_pct
    assert short.net_pct == long_.net_pct
    assert short.pnl == long_.pnl
    assert short.win == long_.win
    assert short.exit + long_.exit == 200


def test_short_profits_when_price_falls():
    r = score("short", BRACKET, 100, {"open": 100, "high": 100.5, "low": 98.8, "close": 99},
              1, 1)
    assert (r.exit_reason, r.exit, r.gross_pct) == ("target", Decimal("99"), Decimal("1.0000"))
    gap = score("short", BRACKET, 100, {"open": 101.5, "high": 102, "low": 101, "close": 101},
                1, 1)
    assert (gap.exit_reason, gap.exit) == ("gap_stop", Decimal("101.5"))


def test_spy_comparison_is_close_to_close_over_the_held_session():
    r = score("long", {"kind": "open"}, 100, {"open": 100, "high": 100, "low": 100,
                                              "close": 100}, 600, 603)
    assert r.spy_pct == Decimal("0.5000")


def test_scoring_the_same_bet_twice_gives_the_same_numbers():
    args = ("long", BRACKET, 187.33, {"open": 187.9, "high": 189.21, "low": 186.02,
                                      "close": 188.4}, 590.12, 588.77)
    first, second = score(*args), score(*args)
    assert first == second
    # Floats and their string form give the same Decimal arithmetic.
    as_text = score("long", BRACKET, "187.33", {"open": "187.9", "high": "189.21",
                                                "low": "186.02", "close": "188.4"},
                    "590.12", "588.77")
    assert as_text == first
    # stop 185.4567 is above the low 186.02; target 189.2033 is under the high.
    assert (first.exit_reason, first.exit) == ("target", Decimal("189.2033"))
    assert first.gross_pct == Decimal("1.0000")
    assert first.pnl == Decimal("9.50")


def test_score_takes_no_symbol_so_a_session_can_be_scored_across_the_list():
    bars = {"SPY": (500, Bar.of(501, 503, 499, 502)), "QQQ": (400, Bar.of(398, 401, 395, 396))}
    out = {sym: score("long", BRACKET, entry, bar, 500, 502) for sym, (entry, bar) in bars.items()}
    assert out["SPY"].exit_reason == "close"
    assert out["QQQ"].exit_reason == "stop"


def test_bad_prices_are_rejected():
    with pytest.raises(ValueError):
        score("long", BRACKET, 0, {"open": 1, "high": 1, "low": 1, "close": 1}, 1, 1)
    with pytest.raises(ValueError):
        score("long", BRACKET, 100, {"open": "nan", "high": 1, "low": 1, "close": 1}, 1, 1)
