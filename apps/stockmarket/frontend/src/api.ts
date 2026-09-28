export type SymbolView = {
  symbol: string;
  label: string;
  kind: "index" | "watch";
  status: "pending" | "ok" | "invalid";
  error: string;
  latest_day: string | null;
  latest_close: number | null;
  change_pct: number | null;      // the latest session's move
};

export type SeriesView = {
  symbol: string;
  points: [string, number][];     // (day, close), oldest first
  downsampled: boolean;
};

export type MoverView = {
  symbol: string;
  index: string;
  contrib_bps: number | null;
  note: string;
};

export type IndexNote = { symbol: string; return_pct: number; note: string };

export type BriefView = {
  day: string;
  body: string;
  tags: string[];
  indexes: IndexNote[];
  movers: MoverView[];
  run_id: string | null;
};

export type Summary = {
  indexes: SymbolView[];
  watchlist: SymbolView[];
  latest_day: string | null;
  latest_brief_day: string | null;
  tags: string[];
};

// --- backtests (docs/design/35) -------------------------------------------
//
// Metric values are whatever the read API returned: money and ratios come
// back as decimal STRINGS (the engine's canonical form), trade counts as
// numbers, and an unsupported metric as null. The UI never parses one back
// into a float and reformats it — that would silently change the digits a
// person reads. `unknown` here is deliberate: render with `fmtMetric`, never
// with arithmetic.
export type MetricValue = string | number | null;

export type BacktestListItem = {
  id: string;
  name: string;
  created_at: string;
  strategies: { id: string; label: string; final_value: MetricValue; xirr: MetricValue }[];
};

export type MaxDrawdown = {
  depth: MetricValue; peak: MetricValue; trough: MetricValue; recovery: MetricValue;
} | null;

export type MaxWeight = { weight: MetricValue; symbol: string; day: string } | null;

// Only the keys the views actually read are typed; the rest ride through
// `[key: string]: unknown` so a metric the engine adds later still renders
// (via fmtMetric) instead of vanishing.
export type BacktestMetrics = {
  final_value: MetricValue; contributed: MetricValue; profit: MetricValue;
  xirr: MetricValue; twr: MetricValue; twr_annualized: MetricValue;
  volatility: MetricValue; sharpe: MetricValue; risk_free: MetricValue;
  turnover: MetricValue; max_drawdown: MaxDrawdown; trades: MetricValue;
  commissions: MetricValue; slippage: MetricValue; costs: MetricValue;
  fx_paid: MetricValue; dividends: MetricValue;
  max_weight: MaxWeight; max_single_stock_weight: MaxWeight;
  [key: string]: unknown;
};

// The bounded subset `/backtests/{id}` carries per strategy — the same
// numbers the `backtest` tool's own summary reports (final_value,
// contributed, profit, xirr, twr_annualized, a flat max_drawdown_depth
// instead of the nested object, volatility, sharpe, trades, costs). The
// full dict (everything `BacktestMetrics` types) is a separate fetch to
// `/backtests/{id}/metrics` — the detail response can't afford it at
// the spec's worst case (8 strategies, ~19 caveats measured 20+ KB
// uncapped).
export type BacktestHeadlineMetrics = {
  final_value: MetricValue; contributed: MetricValue; profit: MetricValue;
  xirr: MetricValue; twr_annualized: MetricValue; max_drawdown_depth: MetricValue;
  volatility: MetricValue; sharpe: MetricValue; trades: MetricValue; costs: MetricValue;
};

export type BacktestStrategyDetail = { id: string; label: string; metrics: BacktestHeadlineMetrics };

export type BacktestDetail = {
  id: string;
  name: string;
  description: string;
  assumed: { path: string; value: unknown }[];
  caveats: { code: string; text: string }[];
  exclusions_summary: { count: number; symbols: string[] };
  report_id: string | null;
  created_at: string;
  strategies: BacktestStrategyDetail[];
};

// Full per-strategy metrics (minus the unbounded pick timeline) and the
// stored spec, each split out of the bounded detail response above.
export type BacktestMetricsView = { strategy_id: string | null; metrics: BacktestMetrics };
export type BacktestSpecView = { id: string; spec: Record<string, unknown> };

export type BacktestEvent = {
  day: string; strategy_id: string; kind: string;
  symbol: string | null; detail: unknown;
};

export type BacktestEventsPage = {
  page: number; page_size: number; has_more: boolean; events: BacktestEvent[];
};

export type BacktestSeriesPoint = { day: string; value: MetricValue; contributed: MetricValue };

export type BacktestSeriesView = {
  strategy_id: string; sample: "monthly" | "daily"; points: BacktestSeriesPoint[];
};

export type RerunOut = { run_id: string | null; requested: boolean };

export type WhoAmI = { principal: string; role: string };

/** The API's own decimal strings/numbers, exactly as returned — never
 * `.toFixed()`'d or `parseFloat()`'d back into a different-looking number. */
export function fmtMetric(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "string" || typeof v === "number") return String(v);
  return "—";
}

/** Best-effort numeric read for chart POSITIONING only (pixel placement,
 * never a label). Falls back to null, which callers must treat as a gap. */
export function numOrNull(v: MetricValue): number | null {
  if (v === null || v === undefined) return null;
  const n = typeof v === "number" ? v : parseFloat(v);
  return Number.isFinite(n) ? n : null;
}

// --- display-only formatting (string manipulation, never parseFloat) -------
//
// Both helpers below work on the DIGITS of the API's own string (or a
// number's default `String()` form) — inserting separators or moving the
// decimal point — never on a float reparsed from it. A shape that isn't a
// plain (possibly signed, possibly decimal) number — scientific notation,
// "n/a", etc. — returns null and the caller falls back to the exact string.
const DECIMAL_RE = /^(-)?(\d+)(?:\.(\d+))?$/;

/** "48213.06" -> "48,213.06". Grouping never touches a digit's value. */
export function groupThousands(raw: unknown): string | null {
  const m = DECIMAL_RE.exec(String(raw).trim());
  if (!m) return null;
  const [, sign, intPart, fracPart] = m;
  const grouped = intPart.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return (sign ?? "") + grouped + (fracPart ? `.${fracPart}` : "");
}

/** "0.1187" -> "11.87": the decimal point moved two places right, exactly —
 * padding with a zero when a digit isn't there yet is exact (0.1 == 10.0
 * once shifted), never a guess at a digit that might be nonzero. */
export function shiftDecimalPercent(raw: unknown): string | null {
  const m = DECIMAL_RE.exec(String(raw).trim());
  if (!m) return null;
  const [, sign, intPart, fracPartRaw] = m;
  const fracPart = fracPartRaw ?? "";
  const frac2 = `${fracPart}00`.slice(0, 2);
  const rest = fracPart.slice(2);
  const newInt = (intPart + frac2).replace(/^0+(?=\d)/, "");
  return (sign ?? "") + newInt + (rest ? `.${rest}` : "");
}

/** Money display: thousands separators, plus the base currency code when the
 * caller actually knows it (never guessed — see `baseCurrency`). */
export function fmtMoney(v: unknown, currency?: string | null): string {
  if (v === null || v === undefined || v === "") return "—";
  const grouped = groupThousands(v);
  if (grouped === null) return fmtMetric(v);
  return currency ? `${grouped} ${currency}` : grouped;
}

/** Percent display for a ratio metric, via exact decimal-string shifting.
 * Falls back to the untouched string when the shape can't be shifted safely
 * ("leave them", per the plan) — e.g. a metric the engine reports in
 * scientific notation. */
export function fmtPercent(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  const pct = shiftDecimalPercent(v);
  return pct === null ? fmtMetric(v) : `${pct}%`;
}

// One shared vocabulary for every metric key the Backtests view shows,
// so the experiment page's stat labels and the compare table's row labels
// never diverge (docs review: "never raw snake_case keys"). `kind` decides
// which of the formatters above applies; anything absent renders plain via
// `fmtMetric`.
type MetricKind = "money" | "percent" | "plain";

export const METRIC_LABELS: Record<string, string> = {
  value: "Value", final_value: "Final value", contributed: "Contributed",
  profit: "Profit", xirr: "XIRR", twr: "TWR", twr_annualized: "TWR (annualized)",
  volatility: "Volatility", sharpe: "Sharpe", risk_free: "Risk-free rate",
  turnover: "Turnover", max_drawdown_depth: "Max drawdown", trades: "Trades",
  commissions: "Commissions", slippage: "Slippage", costs: "Costs",
  fx_paid: "FX paid", dividends: "Dividends",
};

const METRIC_KIND: Record<string, MetricKind> = {
  value: "money", final_value: "money", contributed: "money", profit: "money",
  commissions: "money", slippage: "money", costs: "money",
  fx_paid: "money", dividends: "money",
  xirr: "percent", twr: "percent", twr_annualized: "percent",
  volatility: "percent", risk_free: "percent", max_drawdown_depth: "percent",
};

/** Metric label + value display in one call — the single place that decides
 * money vs. percent vs. plain for a given key, used by both the stat rows
 * and the compare table so they can never disagree. */
export function fmtMetricByKey(key: string, v: unknown, currency?: string | null): string {
  const kind = METRIC_KIND[key] ?? "plain";
  if (kind === "money") return fmtMoney(v, currency);
  if (kind === "percent") return fmtPercent(v);
  return fmtMetric(v);
}

export function metricLabel(key: string): string {
  return METRIC_LABELS[key] ?? key;
}

/** The experiment's base currency, ONLY when the detail response actually
 * says so (`assumed: [{path: "base_currency", value: "CAD"}, ...]`) — never
 * inferred from a symbol or guessed from context. */
export function baseCurrency(assumed: { path: string; value: unknown }[]): string | null {
  const hit = assumed.find((a) => a.path === "base_currency");
  return typeof hit?.value === "string" ? hit.value : null;
}

// The date ranges, matching what every finance site offers minus 1D: the
// archive holds daily bars only, so an intraday range would be one point
// pretending to be a line. The latest session's move is the big number
// instead.
export const RANGES = ["5D", "1M", "6M", "YTD", "1Y", "5Y"] as const;
export type Range = (typeof RANGES)[number];

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/apps/stockmarket/api${path}`, {
    credentials: "include",
    headers: init?.body ? { "Content-Type": "application/json" } : undefined,
    ...init,
  });
  if (res.status === 401) {
    // The platform session guards this app — bounce to its login.
    window.location.href = "/login";
    throw new Error("401");
  }
  if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
  return res.status === 204 ? (undefined as T) : (res.json() as Promise<T>);
}
