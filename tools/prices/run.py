"""prices tool: fetch daily bars from Yahoo and upsert them into the
stockmarket app's archive (schema app_stockmarket).

This is the *writer* half of the stockmarket data path. The point of writing
from inside the tool is volume: a five-year backfill of three indexes is ~3800
rows, which is fine for Postgres and ruinous for a model's context. The agent
asks for a symbol and a range; it gets back counts.

The app owns every table here — it runs the DDL at startup (create_all). This
tool deliberately creates nothing: if the tables are missing, that means the
app was never deployed, and inventing a schema behind its back would just
produce two disagreeing definitions later.

Executor contract: JSON args on stdin, JSON result on stdout, non-zero exit +
stderr message on failure. Env comes from the app's provisioned DB secret
(APP_DB_*) — nothing else is available.
"""
import json
import math
import os
import re
import sys
from datetime import datetime, timezone

SCHEMA = "app_stockmarket"
# Yahoo tickers: letters, digits, dot (XIU.TO), dash (BRK-B), caret (^GSPC).
SYMBOL_RE = re.compile(r"^[A-Z0-9.^-]{1,12}$")
# A symbol the app has recorded but never loaded gets the full history no
# matter what range was asked for — otherwise a watchlist add during the
# daily 5d sync would leave a ticker with five days of chart.
BACKFILL_RANGE = "5y"


def clean_symbol(raw: str) -> str:
    sym = str(raw).strip().upper()
    if not SYMBOL_RE.match(sym):
        raise ValueError(f"invalid ticker {raw!r}")
    return sym


def connect():
    """Connect as the app's role. Built from the secret's components rather
    than APP_DB_URL, which carries SQLAlchemy's `+asyncpg` driver suffix."""
    import psycopg
    missing = [k for k in ("APP_DB_HOST", "APP_DB_USER", "APP_DB_PASSWORD",
                           "APP_DB_NAME") if not os.environ.get(k)]
    if missing:
        raise RuntimeError(
            f"missing {', '.join(missing)} — the app-stockmarket-db secret is "
            f"not bound; is the stockmarket app provisioned?")
    conn = psycopg.connect(
        host=os.environ["APP_DB_HOST"], port=int(os.environ.get("APP_DB_PORT", "5432")),
        user=os.environ["APP_DB_USER"], password=os.environ["APP_DB_PASSWORD"],
        dbname=os.environ["APP_DB_NAME"], connect_timeout=10,
        options=f"-c search_path={SCHEMA}")
    return conn


def tracked_symbols(conn) -> list[tuple[str, str]]:
    """Every symbol the app tracks, as (symbol, status)."""
    with conn.cursor() as cur:
        cur.execute("SELECT symbol, status FROM symbols ORDER BY symbol")
        return [(r[0], r[1]) for r in cur.fetchall()]


def frame_to_rows(symbol: str, df) -> list[tuple]:
    """Yahoo daily history → rows ready for the bars upsert.

    Prices are rounded to 4 decimals to shed float32 noise (same treatment as
    the `stocks` tool) and auto-adjusted for splits and dividends, so a
    re-fetch of an old window can legitimately rewrite old closes — which is
    exactly why the write is an upsert rather than an insert.
    """
    rows = []
    for ts, row in df.iterrows():
        def num(col):
            v = row.get(col)
            if v is None or (isinstance(v, float) and math.isnan(v)):
                return None
            return round(float(v), 4)
        close = num("Close")
        if close is None or close <= 0:
            continue                      # a bar with no close is not a bar
        vol = num("Volume")
        rows.append((symbol, str(ts.date()), num("Open"), num("High"),
                     num("Low"), close, int(vol) if vol is not None else None))
    return rows


def fetch_bars(symbol: str, rng: str) -> list[tuple]:
    import yfinance
    return frame_to_rows(
        symbol, yfinance.Ticker(symbol).history(period=rng, auto_adjust=True))


def upsert_bars(conn, rows: list[tuple]) -> int:
    if not rows:
        return 0
    with conn.cursor() as cur:
        # Identifiers quoted: "open" and "close" are non-reserved in Postgres
        # today, and not worth betting a nightly sync on.
        cur.executemany(
            'INSERT INTO bars (symbol, day, "open", "high", "low", "close", volume) '
            "VALUES (%s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (symbol, day) DO UPDATE SET "
            '"open" = EXCLUDED."open", "high" = EXCLUDED."high", '
            '"low" = EXCLUDED."low", "close" = EXCLUDED."close", '
            "volume = EXCLUDED.volume", rows)
    return len(rows)


def mark_symbol(conn, symbol: str, status: str, error: str | None) -> None:
    """Record the outcome on the tracked-symbol row. A watchlist add lands as
    `pending`; this is what flips it to ok (chart is ready) or invalid (bad
    ticker), which is the state the UI renders."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE symbols SET status = %s, error = %s, last_synced_at = %s "
            "WHERE symbol = %s",
            (status, (error or "")[:500], datetime.now(timezone.utc), symbol))


def sync_one(conn, symbol: str, rng: str) -> dict:
    rows = fetch_bars(symbol, rng)
    if not rows:
        mark_symbol(conn, symbol, "invalid",
                    "no price data from Yahoo — check the ticker")
        conn.commit()
        return {"symbol": symbol, "error": "no price data — check the ticker "
                "(Yahoo conventions; TSX tickers end in .TO)"}
    written = upsert_bars(conn, rows)
    mark_symbol(conn, symbol, "ok", None)
    conn.commit()
    return {"symbol": symbol, "rows": written,
            "first_day": rows[0][1], "last_day": rows[-1][1]}


def plan_targets(requested, known: dict[str, str], rng: str) -> list[tuple[str, str]]:
    """(symbol, range) pairs to sync, honouring the never-loaded rule.

    `known` maps tracked symbol → status. A symbol that has never loaded
    ignores `rng` and takes the full backfill instead, so a watchlist add that
    lands during the daily 5d sync doesn't leave a ticker with five days of
    chart forever.
    """
    if requested:
        # An explicit symbol the app doesn't track yet still gets loaded — the
        # bars are useful and the app picks them up when it does track it.
        return [(s, BACKFILL_RANGE if known.get(s, "pending") == "pending" else rng)
                for s in (clean_symbol(x) for x in requested)]
    return [(s, BACKFILL_RANGE if status == "pending" else rng)
            for s, status in known.items()]


def resolve_targets(conn, args: dict) -> list[tuple[str, str]]:
    return plan_targets(args.get("symbols"), dict(tracked_symbols(conn)),
                        args.get("range") or "5d")


def main() -> int:
    args = json.load(sys.stdin)
    try:
        conn = connect()
    except Exception as e:
        print(str(e), file=sys.stderr)
        return 2
    try:
        try:
            targets = resolve_targets(conn, args)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 2
        except Exception as e:
            print(f"could not read the tracked-symbol list ({e}) — the "
                  f"stockmarket app owns these tables and creates them at "
                  f"startup; is it deployed?", file=sys.stderr)
            return 2
        if not targets:
            print(json.dumps({"written": 0, "symbols": [], "errors": [],
                              "note": "no symbols tracked yet"}))
            return 0

        done, errors = [], []
        for symbol, rng in targets:
            try:
                out = sync_one(conn, symbol, rng)
            except Exception as e:                  # one bad ticker must not
                conn.rollback()                     # sink the whole batch
                errors.append({"symbol": symbol, "error": str(e)[:200]})
                continue
            (errors if "error" in out else done).append(out)

        if errors and not done:
            print("; ".join(f"{e['symbol']}: {e['error']}" for e in errors),
                  file=sys.stderr)
            return 1
        print(json.dumps({"written": sum(d["rows"] for d in done),
                          "symbols": done, "errors": errors}))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
