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

Price basis. Yahoo is asked for history not adjusted for dividends
(auto_adjust=False, actions=True), where `Close` is split-adjusted only and
`Adj Close` is split+dividend adjusted. Both are stored (`close_split_adj`,
`adj_close`) along with the day's `dividend` and `split_ratio`, so a backtest
can build its own total return. `close` keeps its old meaning — the fully
adjusted close the chart draws — and OHLC is scaled onto that same basis.

Adjusted history is back-adjusted: every new dividend or split rewrites all
earlier adjusted prices. A short top-up (5d) that meets a new action would
leave older rows on the old basis — a step in the series. So when a fetched
window carries an action the archive hasn't stored and there are stored rows
older than the window, the symbol's full history is refetched in the same
call (`refetched` in the output).

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
# Yahoo tickers: letters, digits, dot (XIU.TO), dash (BRK-B), caret (^GSPC),
# equals (CAD=X, the FX series).
SYMBOL_RE = re.compile(r"^[A-Z0-9.^=-]{1,12}$")
# Shortest first: plan_targets compares ranges by position.
RANGES = ("5d", "1mo", "6mo", "1y", "5y", "10y", "max")
# Kinds this tool may mint. index (seed_indexes) and watch (/watchlist) are
# the app's to grant: they put a symbol on everyone's chart, which a
# backtest load must not be able to do.
KINDS = ("research", "fx")
# Symbols this tool adds itself are backtest material, not chart material.
DEFAULT_KIND = "research"
# Columns this tool writes; the app adds them (design 35) at startup.
REQUIRED_COLUMNS = {
    "bars": ("symbol", "day", "open", "high", "low", "close", "volume",
             "close_split_adj", "adj_close", "dividend", "split_ratio"),
    "symbols": ("symbol", "label", "kind", "status", "error",
                "last_synced_at", "added_at", "currency"),
}
CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
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


def check_schema(conn) -> None:
    """Refuse to run against an app that predates these columns: writing a
    partial row set would look like success and leave the backtest data
    silently empty."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name IN ('bars', 'symbols')",
            (SCHEMA,))
        have = {(r[0], r[1]) for r in cur.fetchall()}
    missing = [f"{t}.{c}" for t, cols in REQUIRED_COLUMNS.items()
               for c in cols if (t, c) not in have]
    if missing:
        raise RuntimeError(
            f"{SCHEMA} is missing {', '.join(missing)} — the stockmarket app "
            f"creates these at startup; deploy the current app version first")


def tracked_symbols(conn) -> list[tuple[str, str]]:
    """Every symbol the app tracks, as (symbol, status)."""
    with conn.cursor() as cur:
        cur.execute("SELECT symbol, status FROM symbols ORDER BY symbol")
        return [(r[0], r[1]) for r in cur.fetchall()]


def frame_to_rows(symbol: str, df) -> list[dict]:
    """Yahoo daily history (auto_adjust=False, actions=True) → bar rows.

    `close` and OHLC are on the fully adjusted basis (each scaled by
    Adj Close / Close, as yfinance's own auto_adjust does), so the chart is
    unchanged. `close_split_adj` is Yahoo's raw `Close`, which is already
    split-adjusted. Prices are rounded to 4 decimals to shed float32 noise;
    actions to 6, since dividends can be fractions of a cent.
    """
    rows = []
    for ts, row in df.iterrows():
        def raw(col):
            v = row.get(col)
            if v is None or (isinstance(v, float) and math.isnan(v)):
                return None
            return float(v)
        close, adj = raw("Close"), raw("Adj Close")
        if close is None or close <= 0:
            continue                      # a bar with no close is not a bar
        if adj is None or adj <= 0:
            adj = close                   # FX pairs have no dividend adjustment
        factor = adj / close

        def scaled(col):
            v = raw(col)
            return None if v is None else round(v * factor, 4)
        vol = raw("Volume")
        split = raw("Stock Splits")
        rows.append({
            "symbol": symbol, "day": str(ts.date()),
            "open": scaled("Open"), "high": scaled("High"), "low": scaled("Low"),
            "close": round(adj, 4), "volume": int(vol) if vol is not None else None,
            "close_split_adj": round(close, 4), "adj_close": round(adj, 4),
            "dividend": round(raw("Dividends") or 0.0, 6),
            # Yahoo reports "no split" as 0; NULL says what that means.
            "split_ratio": round(split, 6) if split else None,
        })
    return rows


def fetch_bars(symbol: str, rng: str) -> tuple[list[dict], str | None]:
    """Rows plus the listing currency (None when Yahoo doesn't say — a
    currency is never inferred from the ticker)."""
    import yfinance
    ticker = yfinance.Ticker(symbol)
    rows = frame_to_rows(
        symbol, ticker.history(period=rng, auto_adjust=False, actions=True))
    try:
        currency = (ticker.history_metadata or {}).get("currency")
    except Exception:
        currency = None
    currency = str(currency).strip().upper() if currency else None
    return rows, currency if currency and CURRENCY_RE.match(currency) else None


def new_events(rows: list[dict], stored: dict) -> list[str]:
    """Days in `rows` carrying a dividend or split the archive doesn't hold
    with the same value. `stored` maps day → (dividend, split_ratio); a
    missing day or a NULL (pre-design-35) dividend counts as not held."""
    days = []
    for r in rows:
        if not r["dividend"] and r["split_ratio"] is None:
            continue
        have = stored.get(r["day"])
        if (have is None or have[0] is None
                or round(float(have[0]), 6) != r["dividend"]
                or (round(float(have[1]), 6) if have[1] else None)
                != r["split_ratio"]):
            days.append(r["day"])
    return days


def needs_refetch(conn, symbol: str, rows: list[dict]) -> bool:
    """A new action in the window only makes the archive incoherent if rows
    older than the window exist — those are on the old adjustment basis and
    this fetch won't overwrite them."""
    event_days = [r["day"] for r in rows
                  if r["dividend"] or r["split_ratio"] is not None]
    if not event_days:
        return False
    with conn.cursor() as cur:
        cur.execute("SELECT day, dividend, split_ratio FROM bars "
                    "WHERE symbol = %s AND day = ANY(%s)", (symbol, event_days))
        stored = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
        if not new_events(rows, stored):
            return False
        cur.execute("SELECT 1 FROM bars WHERE symbol = %s AND day < %s LIMIT 1",
                    (symbol, rows[0]["day"]))
        return cur.fetchone() is not None


BAR_COLUMNS = ("open", "high", "low", "close", "volume", "close_split_adj",
               "adj_close", "dividend", "split_ratio")


def upsert_bars(conn, rows: list[dict]) -> int:
    if not rows:
        return 0
    with conn.cursor() as cur:
        # Identifiers quoted: "open" and "close" are non-reserved in Postgres
        # today, and not worth betting a nightly sync on.
        cur.executemany(
            "INSERT INTO bars (symbol, day, "
            + ", ".join(f'"{c}"' for c in BAR_COLUMNS)
            + ") VALUES (%(symbol)s, %(day)s, "
            + ", ".join(f"%({c})s" for c in BAR_COLUMNS) + ") "
            "ON CONFLICT (symbol, day) DO UPDATE SET "
            + ", ".join(f'"{c}" = EXCLUDED."{c}"' for c in BAR_COLUMNS), rows)
    return len(rows)


def ensure_symbol(conn, symbol: str, kind: str) -> None:
    """Track a symbol this tool loaded. An existing row keeps its kind — a
    backtest loading ^GSPC must never demote the index to research."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO symbols (symbol, label, kind, status, error, added_at) "
            "VALUES (%s, '', %s, 'pending', '', %s) "
            "ON CONFLICT (symbol) DO NOTHING",
            (symbol, kind, datetime.now(timezone.utc)))


def mark_symbol(conn, symbol: str, status: str, error: str | None,
                currency: str | None = None) -> None:
    """Record the outcome on the tracked-symbol row. A watchlist add lands as
    `pending`; this is what flips it to ok (chart is ready) or invalid (bad
    ticker), which is the state the UI renders. A fetch that didn't report a
    currency leaves a known one in place rather than blanking it."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE symbols SET status = %s, error = %s, last_synced_at = %s, "
            "currency = COALESCE(%s, currency) WHERE symbol = %s",
            (status, (error or "")[:500], datetime.now(timezone.utc),
             currency, symbol))


def sync_one(conn, symbol: str, rng: str, kind: str) -> dict:
    rows, currency = fetch_bars(symbol, rng)
    if not rows:
        mark_symbol(conn, symbol, "invalid",
                    "no price data from Yahoo — check the ticker")
        conn.commit()
        return {"symbol": symbol, "error": "no price data — check the ticker "
                "(Yahoo conventions; TSX tickers end in .TO)"}
    refetched = rng != "max" and needs_refetch(conn, symbol, rows)
    if refetched:
        rows, full_currency = fetch_bars(symbol, "max")
        currency = full_currency or currency
        if not rows:
            raise RuntimeError("full-history refetch returned no data")
    written = upsert_bars(conn, rows)
    ensure_symbol(conn, symbol, kind)
    mark_symbol(conn, symbol, "ok", None, currency)
    conn.commit()
    out = {"symbol": symbol, "rows": written,
           "first_day": rows[0]["day"], "last_day": rows[-1]["day"]}
    if refetched:
        out["refetched"] = True
    return out


def plan_targets(requested, known: dict[str, str], rng: str) -> list[tuple[str, str]]:
    """(symbol, range) pairs to sync, honouring the never-loaded rule.

    `known` maps tracked symbol → status. A symbol that has never loaded
    takes the full backfill (or `rng`, if that reaches further back), so a
    watchlist add that lands during the daily 5d sync doesn't leave a ticker
    with five days of chart forever.
    """
    if rng not in RANGES:
        raise ValueError(f"invalid range {rng!r} (one of {', '.join(RANGES)})")
    backfill = max(BACKFILL_RANGE, rng, key=RANGES.index)
    if requested:
        # An explicit symbol the app doesn't track yet still gets loaded — and
        # tracked, with the requested kind (research by default).
        return [(s, backfill if known.get(s, "pending") == "pending" else rng)
                for s in (clean_symbol(x) for x in requested)]
    return [(s, backfill if status == "pending" else rng)
            for s, status in known.items()]


def resolve_targets(conn, args: dict) -> list[tuple[str, str]]:
    return plan_targets(args.get("symbols"), dict(tracked_symbols(conn)),
                        args.get("range") or "5d")


def resolve_kind(args: dict) -> str:
    kind = args.get("kind") or DEFAULT_KIND
    if kind not in KINDS:
        raise ValueError(f"invalid kind {kind!r} (one of {', '.join(KINDS)})")
    return kind


def main() -> int:
    args = json.load(sys.stdin)
    try:
        conn = connect()
    except Exception as e:
        print(str(e), file=sys.stderr)
        return 2
    try:
        try:
            check_schema(conn)
        except Exception as e:
            print(str(e), file=sys.stderr)
            return 2
        try:
            kind = resolve_kind(args)
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
                              "refetched": [], "note": "no symbols tracked yet"}))
            return 0

        done, errors = [], []
        for symbol, rng in targets:
            try:
                out = sync_one(conn, symbol, rng, kind)
            except Exception as e:                  # one bad ticker must not
                conn.rollback()                     # sink the whole batch
                errors.append({"symbol": symbol, "error": str(e)[:200]})
                continue
            (errors if "error" in out else done).append(out)

        if errors and not done:
            print("; ".join(f"{e['symbol']}: {e['error']}" for e in errors),
                  file=sys.stderr)
            return 1
        refetched = [d["symbol"] for d in done if d.pop("refetched", False)]
        print(json.dumps({"written": sum(d["rows"] for d in done),
                          "symbols": done, "errors": errors,
                          "refetched": refetched}))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
