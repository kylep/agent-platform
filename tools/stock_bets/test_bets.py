"""The Tool's actions against an in-memory App and fixed bars."""
from __future__ import annotations

import io
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import bets  # noqa: E402
import engine  # noqa: E402

FRI = date(2026, 11, 20)
MON = date(2026, 11, 23)


class FakeStore:
    VIEWS = {"bets_all": "bets", "playbook_all": "playbook", "research_all": "research"}

    def __init__(self):
        self.rows = {"bets": [], "playbook": [], "research": []}
        self.request_ids = {}
        self.n = 0

    def query(self, view, params=None):
        return [dict(r) for r in self.rows[self.VIEWS[view]]]

    def create(self, collection, values, request_id):
        if request_id in self.request_ids:
            return self.request_ids[request_id]
        if collection == "bets" and any(r["session"] == values["session"]
                                        for r in self.rows["bets"]):
            raise bets.ToolError("duplicate_session: a bet for this session already exists")
        self.n += 1
        row = {**values, "id": f"r{self.n}", "version": 1,
               "created_at": f"2026-11-20T00:00:{self.n:02d}+00:00"}
        self.rows[collection].append(row)
        receipt = {"collection": collection, "id": row["id"], "version": 1}
        self.request_ids[request_id] = receipt
        return receipt

    def update(self, collection, record_id, version, values, request_id):
        row = next(r for r in self.rows[collection] if r["id"] == record_id)
        if row["version"] != version:
            raise bets.ToolError("AD-VERSION-CONFLICT")
        row.update(values)
        row["version"] += 1
        return {"id": record_id, "version": row["version"]}


def _bar(o, h, low, c):
    return {"open": o, "high": h, "low": low, "close": c}


def fixed_bars(calls=None):
    """Every symbol closes Friday at 100 and trades Monday 100.5/101/99.5/100.8,
    except SPY (Fri 500, Mon close 505) and NVDA (Fri 200; Mon 204/206/203/205)."""
    def source(symbols, start, end):
        if calls is not None:
            calls.append((tuple(symbols), start, end))
        out = {s: {FRI: _bar(99, 101, 98, 100), MON: _bar(100.5, 101, 99.5, 100.8)}
               for s in symbols}
        out["SPY"] = {FRI: _bar(498, 501, 497, 500), MON: _bar(501, 506, 500, 505)}
        out["NVDA"] = {FRI: _bar(199, 201, 198, 200), MON: _bar(204, 206, 203, 205)}
        return out
    return source


def at(day, hh, mm):
    """An Eastern wall time in November (UTC-5) as aware UTC."""
    from datetime import timedelta
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=timezone.utc) \
        + timedelta(hours=5)


def ctx(store, now, caller="stock-bets", bars=None):
    return bets.Ctx(store=store, bars=bars or fixed_bars(), caller=caller, now=now)


BET = {"action": "bet", "symbol": "NVDA", "side": "long", "exit": {"kind": "close"},
       "confidence": 0.55, "basis": "catalyst", "thesis": "Earnings beat carries.",
       "sources": ["https://example.com/a"]}


def test_bet_is_stamped_by_app_clock_and_records_session():
    store = FakeStore()
    out = bets.execute(ctx(store, at(FRI, 15, 30)), BET)
    assert out["status"] == "accepted" and out["late"] is False
    row = store.rows["bets"][0]
    assert row["session"] == "2026-11-20" and row["exit_session"] == "2026-11-23"
    assert row["taken_at"] == "2026-11-20T20:30:00+00:00"
    assert row["playbook"] == "v1" and row["status"] == "open" and row["origin"] == "pet"
    assert "entry" not in row and "net_pct" not in row


def test_caller_supplied_numbers_are_ignored():
    store = FakeStore()
    bets.execute(ctx(store, at(FRI, 15, 30)),
                 {**BET, "entry": 1.0, "net_pct": 99, "win": True})
    row = store.rows["bets"][0]
    assert "entry" not in row and "net_pct" not in row and "win" not in row


def test_second_bet_same_session_refused():
    store = FakeStore()
    bets.execute(ctx(store, at(FRI, 15, 30)), BET)
    with pytest.raises(bets.ToolError, match="duplicate_session"):
        bets.execute(ctx(store, at(FRI, 15, 45)), {**BET, "symbol": "SPY"})


def test_bet_after_close_is_late():
    store = FakeStore()
    out = bets.execute(ctx(store, at(FRI, 16, 5)), BET)
    assert out["late"] is True


def test_holiday_answers_closed_without_error():
    store = FakeStore()
    out = bets.execute(ctx(store, at(date(2026, 11, 26), 15, 30)), BET)
    assert out["status"] == "closed" and store.rows["bets"] == []


def test_only_the_pet_bets_and_only_olu_tunes():
    with pytest.raises(bets.ToolError, match="bet is for stock-bets"):
        bets.execute(ctx(FakeStore(), at(FRI, 15, 30), caller="olu"), BET)
    with pytest.raises(bets.ToolError, match="playbook is for olu"):
        bets.execute(ctx(FakeStore(), at(FRI, 15, 30)), {"action": "playbook"})


def test_settle_waits_for_exit_session_close_plus_grace():
    store = FakeStore()
    bets.execute(ctx(store, at(FRI, 15, 30)), BET)
    calls = []
    out = bets.execute(ctx(store, at(MON, 16, 10), bars=fixed_bars(calls)),
                       {"action": "settle"})
    assert out["settled"] == [] and out["waiting"][0]["exit_session"] == "2026-11-23"
    assert calls == []  # nothing fetched before the bar is final


def test_settle_scores_from_bars_and_is_idempotent():
    store = FakeStore()
    bets.execute(ctx(store, at(FRI, 15, 30)), BET)
    out = bets.execute(ctx(store, at(MON, 16, 25)), {"action": "settle"})
    row = store.rows["bets"][0]
    # NVDA long 200 -> 205: gross 2.5%, net 2.45%, $24.50
    assert (row["entry"], row["exit"], row["exit_reason"]) == (200.0, 205.0, "close")
    assert (row["gross_pct"], row["net_pct"], row["pnl_usd"]) == (2.5, 2.45, 24.5)
    assert row["win"] is True and row["direction_hit"] is True and row["win_pct"] == 100
    # SPY 500 -> 505 = 1%, minus the same cost
    assert (row["spy_pct"], row["spy_net_pct"], row["spy_pnl_usd"]) == (1.0, 0.95, 9.5)
    assert row["edge_pct"] == 1.5
    assert row["field_n"] == 25
    assert out["settled"][0]["net_pct"] == 2.45
    version = row["version"]
    again = bets.execute(ctx(store, at(MON, 17, 0)), {"action": "settle"})
    assert again["settled"] == [] and row["version"] == version


def test_short_bracket_gap_and_missing_bar():
    store = FakeStore()
    bets.execute(ctx(store, at(FRI, 15, 30)),
                 {**BET, "side": "short", "exit": {"kind": "bracket", "stop_pct": 1}})
    # NVDA opens 204, past a short's stop at 202: exits at the open
    bets.execute(ctx(store, at(MON, 16, 30)), {"action": "settle"})
    row = store.rows["bets"][0]
    assert row["exit_reason"] == "gap_stop" and row["exit"] == 204.0
    assert row["net_pct"] == -2.05 and row["win"] is False

    store2 = FakeStore()
    bets.execute(ctx(store2, at(FRI, 15, 30)), BET)

    def no_monday(symbols, start, end):
        return {s: {FRI: _bar(1, 1, 1, 1)} for s in symbols}
    out = bets.execute(ctx(store2, at(MON, 16, 30), bars=no_monday), {"action": "settle"})
    assert out["missing"] and store2.rows["bets"][0]["status"] == "open"


def test_lesson_once_after_scoring():
    store = FakeStore()
    bets.execute(ctx(store, at(FRI, 15, 30)), BET)
    with pytest.raises(bets.ToolError, match="not scored"):
        bets.execute(ctx(store, at(FRI, 15, 31)),
                     {"action": "lesson", "session": "2026-11-20", "lesson": "x"})
    bets.execute(ctx(store, at(MON, 16, 30)), {"action": "settle"})
    bets.execute(ctx(store, at(MON, 16, 31)),
                 {"action": "lesson", "session": "2026-11-20", "lesson": "It carried."})
    with pytest.raises(bets.ToolError, match="already"):
        bets.execute(ctx(store, at(MON, 16, 32)),
                     {"action": "lesson", "session": "2026-11-20", "lesson": "again"})


def test_check_rescores_without_writing():
    store = FakeStore()
    bets.execute(ctx(store, at(FRI, 15, 30)), BET)
    bets.execute(ctx(store, at(MON, 16, 30)), {"action": "settle"})
    before = dict(store.rows["bets"][0])
    out = bets.execute(ctx(store, at(MON, 22, 0), caller="olu"), {"action": "check"})
    assert out["ok"] is True and store.rows["bets"][0] == before


def test_playbook_version_is_recorded_on_later_bets():
    store = FakeStore()
    bets.execute(ctx(store, at(FRI, 21, 30), caller="olu"),
                 {"action": "playbook", "version": "v2", "text": "t", "reason": "r",
                  "prediction": "p", "refuter": "f"})
    bets.execute(ctx(store, at(MON, 15, 30)), BET)
    assert store.rows["bets"][0]["playbook"] == "v2"
    with pytest.raises(bets.ToolError, match="already exists"):
        bets.execute(ctx(store, at(MON, 21, 30), caller="olu"),
                     {"action": "playbook", "version": "v2", "text": "t", "reason": "r",
                      "prediction": "p", "refuter": "f"})


def test_import_keeps_made_time_and_flags_pet_arithmetic():
    store = FakeStore()
    item = {k: v for k, v in BET.items() if k != "action"}
    out = bets.execute(ctx(store, at(MON, 21, 30), caller="olu"),
                       {"action": "import", "bets": [
                           {**item, "made": "2026-11-20T15:31:00-05:00",
                            "playbook": "v1", "pet_net_pct": 2.6}]})
    assert out["imported"] == ["2026-11-20"]
    row = store.rows["bets"][0]
    assert row["origin"] == "import" and row["late"] is False
    settled = bets.execute(ctx(store, at(MON, 21, 31), caller="olu"), {"action": "settle"})
    assert settled["settled"][0]["pet_agrees"] is False
    again = bets.execute(ctx(store, at(MON, 21, 32), caller="olu"),
                         {"action": "import", "bets": [
                             {**item, "made": "2026-11-20T15:31:00-05:00"}]})
    assert again["imported"] == [] and again["skipped"][0]["reason"] == "already in the App"


def test_scoreboard_reports_baselines_and_blocks():
    store = FakeStore()
    bets.execute(ctx(store, at(FRI, 15, 30)), BET)
    bets.execute(ctx(store, at(MON, 16, 30)), {"action": "settle"})
    board = bets.execute(ctx(store, at(MON, 22, 0), caller="pai"), {"action": "scoreboard"})
    overall = board["overall"]
    assert overall["settled"] == 1 and overall["wins"] == 1 and overall["win_rate"] == 1.0
    assert overall["spy_avg_net_pct"] == 0.95 and "field_avg_net_pct" in overall
    assert board["by_playbook"]["v1"]["settled"] == 1
    assert board["blocks_of_20"][0]["bets"] == "1-20"
    assert board["lessons_missing"] == ["2026-11-20"] and board["bet_today"] is False


def test_run_main_refuses_without_endpoint(monkeypatch, capsys):
    import run
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"action": "scoreboard"})))
    monkeypatch.setenv("TOOL_CALLER_AGENT", "pai")
    assert run.main() == 2
    assert "unavailable" in capsys.readouterr().err


def test_engine_symbols_match_tool_description():
    manifest = (Path(__file__).parent / "tool.yaml").read_text()
    for symbol in engine.SYMBOLS:
        assert f" {symbol}" in manifest


def test_sized_stake_follows_confidence():
    assert [bets.sized_stake(c) for c in (0.5, 0.55, 0.6, 0.7, 0.9)] == [0, 125, 250, 500, 1000]


def test_scoreboard_sizes_pnl_by_confidence():
    store = FakeStore()
    bets.execute(ctx(store, at(FRI, 15, 30)), {**BET, "confidence": 0.7})
    bets.execute(ctx(store, at(MON, 16, 30)), {"action": "settle"})
    overall = bets.execute(ctx(store, at(MON, 22, 0), caller="olu"),
                           {"action": "scoreboard"})["overall"]
    # flat $1,000 x 2.45% = 24.50; sized $500 x 2.45% = 12.25
    assert overall["pnl_usd"] == 24.5 and overall["sized_pnl_usd"] == 12.25
    assert overall["sized_avg_stake_usd"] == 500
