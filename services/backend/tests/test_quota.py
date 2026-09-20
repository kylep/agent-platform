"""The pure half of Quota (docs/design/22): header strings in, numbers and
datetimes out, and the sentences the tool reads back."""
from datetime import datetime, timedelta, timezone

from agentplatform.db import QuotaSnapshot
from agentplatform.quota import (H_5H_RESET, H_5H_UTILIZATION, H_7D_RESET,
                                 H_7D_UTILIZATION, H_STATUS, changed,
                                 humanize_delta, is_stale, parse_observation,
                                 parse_codex_usage, parse_observed_at, render_text)

NOW = datetime(2026, 9, 14, 15, 2, 11, tzinfo=timezone.utc)
RESET_5H = datetime(2026, 9, 14, 19, 0, tzinfo=timezone.utc)
RESET_7D = datetime(2026, 9, 17, 23, 0, tzinfo=timezone.utc)


def parse(**headers):
    return parse_observation(headers, NOW, "proxy")


def snapshot(**fields):
    fields.setdefault("observed_at", NOW)
    fields.setdefault("source", "refresh")
    return QuotaSnapshot(id=1, raw={}, **fields)


# --- utilization: fraction or percent ----------------------------------------

def test_fraction_is_kept_as_is():
    o = parse(**{H_5H_UTILIZATION: "0.22"})
    assert o.five_hour_utilization == 0.22


def test_percent_is_divided():
    assert parse(**{H_5H_UTILIZATION: "22"}).five_hour_utilization == 0.22
    assert parse(**{H_5H_UTILIZATION: "22.5"}).five_hour_utilization == 0.225


def test_one_is_a_full_fraction_and_one_hundred_is_a_full_percent():
    assert parse(**{H_5H_UTILIZATION: "1.0"}).five_hour_utilization == 1.0
    assert parse(**{H_5H_UTILIZATION: "1"}).five_hour_utilization == 1.0
    assert parse(**{H_5H_UTILIZATION: "100"}).five_hour_utilization == 1.0


def test_out_of_range_values_are_clamped():
    assert parse(**{H_5H_UTILIZATION: "-5"}).five_hour_utilization == 0.0
    assert parse(**{H_5H_UTILIZATION: "-0.2"}).five_hour_utilization == 0.0
    assert parse(**{H_5H_UTILIZATION: "150"}).five_hour_utilization == 1.0


def test_unparsable_utilization_is_none_but_the_observation_survives():
    o = parse(**{H_5H_UTILIZATION: "n/a", H_7D_UTILIZATION: "0.81"})
    assert o.five_hour_utilization is None
    assert o.seven_day_utilization == 0.81


def test_empty_and_nonfinite_utilization_are_none():
    for value in ("", "   ", "NaN", "inf", "-inf"):
        assert parse(**{H_5H_UTILIZATION: value}).five_hour_utilization is None


# --- reset: epoch seconds or ISO-8601 ----------------------------------------

def test_reset_as_epoch_seconds():
    o = parse(**{H_5H_RESET: "1757880000"})
    assert o.five_hour_resets_at == datetime.fromtimestamp(1757880000, timezone.utc)


def test_reset_as_iso_with_zulu_and_offset():
    for value in ("2026-09-14T19:00:00Z", "2026-09-14T19:00:00+00:00"):
        assert parse(**{H_5H_RESET: value}).five_hour_resets_at == RESET_5H


def test_reset_without_an_offset_is_read_as_utc():
    assert parse(**{H_5H_RESET: "2026-09-14T19:00:00"}).five_hour_resets_at == RESET_5H


def test_unparsable_reset_is_none():
    for value in ("", "soon", "2026-13-99"):
        assert parse(**{H_5H_RESET: value}).five_hour_resets_at is None


# --- the observation as a whole ----------------------------------------------

def test_no_known_header_is_no_observation():
    assert parse_observation({}, NOW, "proxy") is None
    assert parse_observation({"content-type": "application/json"}, NOW, "proxy") is None
    # Same prefix, none of the five the platform reads: nothing to record.
    assert parse_observation(
        {"anthropic-ratelimit-unified-overage-status": "off"}, NOW, "proxy") is None


def test_codex_windows_are_named_by_duration_and_missing_five_hour_is_ok():
    obs = parse_codex_usage({"plan_type": "pro", "rate_limit": {
        "allowed": True,
        "primary_window": {"used_percent": 95, "limit_window_seconds": 604800,
                           "reset_at": int(RESET_7D.timestamp())},
    }}, NOW, "refresh")
    assert obs is not None
    assert obs.five_hour_utilization is None
    assert obs.seven_day_utilization == 0.95
    assert obs.seven_day_resets_at == RESET_7D
    assert obs.status == "allowed"


def test_headers_match_case_insensitively():
    o = parse_observation({H_5H_UTILIZATION.upper(): "0.22"}, NOW, "proxy")
    assert o.five_hour_utilization == 0.22


def test_raw_keeps_every_unified_header_verbatim():
    o = parse_observation({
        H_5H_UTILIZATION: "0.22",
        "Anthropic-RateLimit-Unified-Overage-Status": "off",
        "anthropic-ratelimit-unified-grace": "whatever",
        "content-type": "application/json",
    }, NOW, "proxy")
    assert o.raw == {H_5H_UTILIZATION: "0.22",
                     "anthropic-ratelimit-unified-overage-status": "off",
                     "anthropic-ratelimit-unified-grace": "whatever"}


def test_observed_at_and_source_come_from_the_caller():
    o = parse(**{H_5H_UTILIZATION: "0.22"})
    assert (o.observed_at, o.source) == (NOW, "proxy")


def test_status_is_capped_to_a_safe_token():
    assert parse(**{H_5H_UTILIZATION: "0.1", H_STATUS: "allowed"}).status == "allowed"
    assert parse(**{H_5H_UTILIZATION: "0.1", H_STATUS: " ALLOWED "}).status == "allowed"
    # Anything that could carry markup or a sentence into a prompt is dropped.
    assert parse(**{H_5H_UTILIZATION: "0.1", H_STATUS: "<b>allowed</b>"}).status is None
    assert parse(**{H_5H_UTILIZATION: "0.1", H_STATUS: "a" * 33}).status is None
    assert parse(**{H_5H_UTILIZATION: "0.1", H_STATUS: ""}).status is None


def test_a_full_observation():
    o = parse(**{H_5H_UTILIZATION: "0.22", H_5H_RESET: "2026-09-14T19:00:00Z",
                 H_7D_UTILIZATION: "0.81", H_7D_RESET: "2026-09-17T23:00:00Z",
                 H_STATUS: "allowed"})
    assert (o.five_hour_utilization, o.five_hour_resets_at) == (0.22, RESET_5H)
    assert (o.seven_day_utilization, o.seven_day_resets_at) == (0.81, RESET_7D)
    assert o.status == "allowed"


# --- staleness ---------------------------------------------------------------

def test_no_row_is_stale():
    assert is_stale(None, NOW) is True


def test_stale_at_the_earliest_reset_boundary():
    row = snapshot(five_hour_resets_at=RESET_5H, seven_day_resets_at=RESET_7D)
    assert is_stale(row, RESET_5H - timedelta(seconds=1)) is False
    assert is_stale(row, RESET_5H) is False
    assert is_stale(row, RESET_5H + timedelta(seconds=1)) is True


def test_the_earliest_non_null_reset_decides():
    # 7d only: the 5h null must not make the row look permanently fresh.
    row = snapshot(seven_day_resets_at=RESET_7D)
    assert is_stale(row, RESET_5H + timedelta(seconds=1)) is False
    assert is_stale(row, RESET_7D + timedelta(seconds=1)) is True


def test_a_row_with_no_reset_at_all_is_not_stale():
    assert is_stale(snapshot(), NOW + timedelta(days=30)) is False


def test_staleness_tolerates_a_naive_now_and_a_naive_row():
    row = snapshot(five_hour_resets_at=RESET_5H.replace(tzinfo=None))
    assert is_stale(row, (RESET_5H + timedelta(seconds=1)).replace(tzinfo=None)) is True


# --- change detection --------------------------------------------------------

def test_first_observation_is_always_a_change():
    assert changed(None, parse(**{H_5H_UTILIZATION: "0.22"})) is True


def test_identical_values_are_not_a_change():
    o = parse(**{H_5H_UTILIZATION: "0.22", H_5H_RESET: "2026-09-14T19:00:00Z"})
    row = snapshot(five_hour_utilization=0.22, five_hour_resets_at=RESET_5H)
    assert changed(row, o) is False


def test_any_utilization_or_reset_difference_is_a_change():
    row = snapshot(five_hour_utilization=0.22, five_hour_resets_at=RESET_5H,
                   seven_day_utilization=0.81, seven_day_resets_at=RESET_7D)
    base = {H_5H_UTILIZATION: "0.22", H_5H_RESET: "2026-09-14T19:00:00Z",
            H_7D_UTILIZATION: "0.81", H_7D_RESET: "2026-09-17T23:00:00Z"}
    assert changed(row, parse(**base)) is False
    for header, value in ((H_5H_UTILIZATION, "0.23"),
                          (H_5H_RESET, "2026-09-14T20:00:00Z"),
                          (H_7D_UTILIZATION, "0.82"),
                          (H_7D_RESET, "2026-09-18T23:00:00Z")):
        assert changed(row, parse(**{**base, header: value})) is True


def test_status_alone_is_not_a_change():
    row = snapshot(five_hour_utilization=0.22, status="allowed")
    assert changed(row, parse(**{H_5H_UTILIZATION: "0.22",
                                 H_STATUS: "allowed_warning"})) is False


# --- humanize_delta ----------------------------------------------------------

def test_humanize_delta_reads_the_top_two_units():
    assert humanize_delta(timedelta(hours=3, minutes=54)) == "3h 54m"
    assert humanize_delta(timedelta(days=3, hours=8, minutes=20)) == "3d 8h"
    assert humanize_delta(timedelta(seconds=2)) == "2s"
    assert humanize_delta(timedelta(minutes=1, seconds=30)) == "1m 30s"
    assert humanize_delta(timedelta(hours=3)) == "3h"
    assert humanize_delta(timedelta(0)) == "0s"
    assert humanize_delta(timedelta(seconds=-90)) == "0s"


# --- render_text -------------------------------------------------------------

def test_render_text_with_no_snapshot():
    assert render_text(None, NOW) == "No usage observation yet."


def test_render_text_matches_the_designed_shape():
    row = snapshot(five_hour_utilization=0.22, five_hour_resets_at=RESET_5H,
                   seven_day_utilization=0.81, seven_day_resets_at=RESET_7D,
                   status="allowed", observed_at=NOW - timedelta(seconds=2))
    assert render_text(row, NOW) == (
        "5-hour window: 22% used, resets in 3h 57m (2026-09-14 19:00 UTC).\n"
        "7-day window: 81% used, resets in 3d 7h (2026-09-17 23:00 UTC).\n"
        "Observed 2s ago (refresh). Status: allowed.")


def test_render_text_advisory_appears_only_above_ninety_percent():
    def text(fraction):
        return render_text(snapshot(five_hour_utilization=fraction,
                                    seven_day_utilization=0.1), NOW)

    for fraction, shown in ((0.0, "0%"), (0.5, "50%"), (0.91, "91%"), (1.0, "100%")):
        assert f"5-hour window: {shown} used." in text(fraction)
    assert "defer heavy work" not in text(0.0)
    assert "defer heavy work" not in text(0.5)
    assert "defer heavy work" not in text(0.90)
    assert "defer heavy work" in text(0.91)
    assert "defer heavy work" in text(1.0)


def test_render_text_advisory_fires_on_the_seven_day_window_too():
    row = snapshot(five_hour_utilization=0.1, seven_day_utilization=0.95)
    assert "defer heavy work" in render_text(row, NOW)


def test_render_text_with_null_windows():
    row = snapshot(observed_at=NOW - timedelta(seconds=5), source="proxy")
    assert render_text(row, NOW) == ("5-hour window: unknown.\n"
                                     "7-day window: unknown.\n"
                                     "Observed 5s ago (proxy).")


def test_render_text_without_a_reset_omits_the_reset_clause():
    row = snapshot(five_hour_utilization=0.22, observed_at=NOW)
    assert render_text(row, NOW).startswith("5-hour window: 22% used.\n")


def test_epoch_at_or_before_the_unix_zero_is_unparsable():
    for value in ("0", "-1", "-1757880000"):
        assert parse(**{H_5H_RESET: value}).five_hour_resets_at is None


def test_raw_is_bounded_in_count_and_value_length():
    from agentplatform.quota import RAW_MAX_HEADERS, RAW_MAX_VALUE
    headers = {f"anthropic-ratelimit-unified-junk-{i}": "x" * 900
               for i in range(500)}
    headers[H_5H_UTILIZATION] = "0.22"
    o = parse_observation(headers, NOW, "proxy")
    assert len(o.raw) == RAW_MAX_HEADERS
    assert all(len(v) <= RAW_MAX_VALUE for v in o.raw.values())
    # The headers with a column are never the ones the cap drops.
    assert o.raw[H_5H_UTILIZATION] == "0.22"
    assert o.five_hour_utilization == 0.22


def test_percent_rounds_half_up():
    row = snapshot(five_hour_utilization=0.225)
    assert "5-hour window: 23% used." in render_text(row, NOW)


# --- the reporter's clock -----------------------------------------------------

def test_parse_observed_at_keeps_a_plausible_past_timestamp():
    """A report that queued behind something is still a report about when it
    was taken, not about when it arrived."""
    earlier = NOW - timedelta(minutes=10)
    assert parse_observed_at(earlier.isoformat(), NOW) == earlier


def test_parse_observed_at_falls_back_to_now_when_it_cannot_parse():
    for value in (None, "", "not-a-time", "0"):
        assert parse_observed_at(value, NOW) == NOW


def test_parse_observed_at_clamps_a_future_timestamp():
    """The dangerous input. An observation dated 9999 can never be superseded,
    so every later real observation would be dropped and the snapshot would be
    frozen at whatever that one report said — forever."""
    assert parse_observed_at("9999-01-01T00:00:00Z", NOW) == NOW
    assert parse_observed_at((NOW + timedelta(days=365)).isoformat(), NOW) == NOW


def test_parse_observed_at_tolerates_small_clock_skew():
    """Two machines' clocks disagree; that is not a lie worth rewriting."""
    skewed = NOW + timedelta(seconds=5)
    assert parse_observed_at(skewed.isoformat(), NOW) == skewed


def test_http_status_is_kept_in_raw_as_a_string():
    """Diagnostic only, but the difference between a 0.99 off a 429 and the
    same number off a 200 is the difference between reading the snapshot and
    guessing at it."""
    obs = parse_observation({H_5H_UTILIZATION: "0.99"}, NOW, "proxy", http_status=429)
    assert obs.raw["http_status"] == "429"
    # Absent unless the reporter said so — nothing invents a status.
    assert "http_status" not in parse_observation(
        {H_5H_UTILIZATION: "0.99"}, NOW, "proxy").raw


def test_http_status_survives_a_flood_of_unknown_headers():
    """It is added after the cap, so headers nothing reads cannot displace it."""
    headers = {H_5H_UTILIZATION: "0.5"}
    headers.update({f"anthropic-ratelimit-unified-junk-{i}": "x" for i in range(200)})
    obs = parse_observation(headers, NOW, "proxy", http_status=200)
    assert obs.raw["http_status"] == "200"
