import { useMemo, useState } from "react";
import type { BacktestSeriesPoint, SeriesView } from "./api";
import { fmtMetric, fmtMoney, groupThousands, numOrNull } from "./api";

// Overlaid index chart. Hand-rolled SVG, no chart dependency — the same
// approach as the console's DurationChart. Colors are ALWAYS design tokens
// (var(--ds-chart-N)); the platform's no-raw-hex gate scans app frontends too.
//
// Two decisions worth knowing:
//
// 1. Series are normalized to PERCENT CHANGE from the first session in the
//    range, not plotted at their prices. SPY near $560 and XIU near $38 share
//    no useful linear axis; what you actually want to compare is how far each
//    one moved, and 0% is the shared baseline.
//
// 2. The x domain is the UNION of every session present across the selected
//    symbols, indexed rather than time-scaled. Indexing closes the weekend and
//    holiday gaps that make a 5D time-scaled chart mostly empty, and taking
//    the union keeps the TSX aligned with the NYSE when only one of them was
//    open (Thanksgiving, Victoria Day, and so on).

const W = 900, H = 320, PAD_L = 52, PAD_R = 16, PAD_T = 14, PAD_B = 30;
const CHART_SERIES = 8;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export const seriesColor = (i: number) =>
  `var(--ds-chart-${(i % CHART_SERIES) + 1})`;

type Normalized = {
  symbol: string;
  color: string;
  /** (x index into the union domain, percent change from the range start) */
  points: [number, number][];
  first: number;
  last: number;
  changePct: number | null;
};

export function niceTicks(lo: number, hi: number, count = 5): number[] {
  const span = hi - lo || 1;
  const rough = span / count;
  const pow = 10 ** Math.floor(Math.log10(rough));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * pow).find((s) => s >= rough) ?? 10 * pow;
  const start = Math.ceil(lo / step) * step;
  const out: number[] = [];
  for (let v = start; v <= hi + step / 1000; v += step) out.push(Number(v.toFixed(6)));
  return out;
}

export function IndexChart({ series, hidden, onToggle, colorIndex, onBrush }: {
  series: SeriesView[];
  hidden: Set<string>;
  onToggle: (symbol: string) => void;
  /** Palette slot for a symbol. Supplied by the page so a line and its stat
   * tile always agree, even when a pending symbol has a tile but no series. */
  colorIndex: (symbol: string) => number;
  /** Called on release of a click-drag across the plot with the two ISO
   * session dates (oldest first). The page turns it into a custom range. */
  onBrush?: (from: string, to: string) => void;
}) {
  const [cursor, setCursor] = useState<number | null>(null);
  // A drag in progress: the session index where it started (`a`) and where the
  // pointer is now (`b`). Either order — normalized on release.
  const [drag, setDrag] = useState<{ a: number; b: number } | null>(null);

  const { days, normalized, lo, hi } = useMemo(() => {
    const shown = series.filter((s) => !hidden.has(s.symbol) && s.points.length > 0);
    // Union of every session any shown symbol traded, oldest first.
    const days = [...new Set(shown.flatMap((s) => s.points.map(([d]) => d)))].sort();
    const at = new Map(days.map((d, i) => [d, i]));
    const normalized: Normalized[] = shown.map((s) => {
      const base = s.points[0][1];
      const points = s.points.map(([d, close]) =>
        [at.get(d)!, base ? (close / base - 1) * 100 : 0] as [number, number]);
      const last = s.points[s.points.length - 1][1];
      return {
        symbol: s.symbol,
        color: seriesColor(colorIndex(s.symbol)),
        points, first: base, last,
        changePct: base ? (last / base - 1) * 100 : null,
      };
    });
    const values = normalized.flatMap((n) => n.points.map(([, v]) => v));
    // Always keep the 0% baseline in frame — it is the reference the whole
    // chart is read against.
    const lo = Math.min(0, ...values), hi = Math.max(0, ...values);
    const pad = (hi - lo || 2) * 0.08;
    return { days, normalized, lo: lo - pad, hi: hi + pad };
  }, [series, hidden, colorIndex]);

  if (days.length === 0) {
    return <p className="muted sm-chart-empty">No price history yet for these symbols.</p>;
  }

  const x = (i: number) =>
    PAD_L + (i * (W - PAD_L - PAD_R)) / Math.max(days.length - 1, 1);
  const y = (v: number) =>
    PAD_T + ((hi - v) * (H - PAD_T - PAD_B)) / Math.max(hi - lo, 0.0001);
  const ticks = niceTicks(lo, hi);

  // Evenly spaced date ticks across the x axis. The count scales to the plot
  // width (~one per 130px) and the label format follows the range — day for
  // short spans, month + 2-digit year out to a year, bare year beyond.
  const spanDays = days.length > 1
    ? (Date.parse(days[days.length - 1]) - Date.parse(days[0])) / 86_400_000
    : 0;
  const fmtDate = (iso: string) => {
    const [yr, mo, dy] = iso.split("-");
    const mon = MONTHS[parseInt(mo, 10) - 1];
    if (spanDays <= 70) return `${mon} ${parseInt(dy, 10)}`;
    if (spanDays <= 800) return `${mon} '${yr.slice(2)}`;
    return yr;
  };
  const tickCount = Math.max(2, Math.min(days.length,
    Math.floor((W - PAD_L - PAD_R) / 130) + 1));
  const xTicks = [...new Set(Array.from({ length: tickCount }, (_, k) =>
    Math.round((k * (days.length - 1)) / (tickCount - 1))))];

  // Pointer x → nearest session index, clamped to the plot so a drag that
  // runs off either edge still selects the first/last session.
  function idxAt(e: React.PointerEvent<SVGSVGElement>): number {
    const box = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - box.left) / box.width) * W;
    const i = Math.round(
      ((px - PAD_L) / Math.max(W - PAD_L - PAD_R, 1)) * Math.max(days.length - 1, 1));
    return Math.max(0, Math.min(days.length - 1, i));
  }

  function onPointerDown(e: React.PointerEvent<SVGSVGElement>) {
    if (!onBrush || e.button !== 0) return;
    // Capture so the drag keeps tracking even when the pointer leaves the SVG.
    e.currentTarget.setPointerCapture(e.pointerId);
    const i = idxAt(e);
    setDrag({ a: i, b: i });
  }

  function onPointerMove(e: React.PointerEvent<SVGSVGElement>) {
    const i = idxAt(e);
    setCursor(i);
    setDrag((d) => (d ? { a: d.a, b: i } : d));
  }

  function onPointerUp() {
    if (drag) {
      const from = Math.min(drag.a, drag.b), to = Math.max(drag.a, drag.b);
      // Ignore a plain click (no span) — that's a hover, not a selection.
      if (to > from && onBrush) onBrush(days[from], days[to]);
      setDrag(null);
    }
  }

  const readout = (n: Normalized) => {
    if (cursor === null) return n.changePct;
    // The value at the cursor, or the last one before it when this symbol did
    // not trade that session.
    const prior = n.points.filter(([i]) => i <= cursor);
    return prior.length ? prior[prior.length - 1][1] : null;
  };

  return (
    <div className="sm-chart">
      <svg viewBox={`0 0 ${W} ${H}`}
           className={`sm-chart-svg${onBrush ? " sm-chart-brushable" : ""}`}
           role="img"
           aria-label={`Percent change over the selected range for ${
             normalized.map((n) => n.symbol).join(", ")}`}
           onPointerDown={onPointerDown} onPointerMove={onPointerMove}
           onPointerUp={onPointerUp}
           onPointerLeave={() => { if (!drag) setCursor(null); }}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={PAD_L} x2={W - PAD_R} y1={y(t)} y2={y(t)}
                  className={t === 0 ? "sm-grid sm-grid-zero" : "sm-grid"} />
            <text x={PAD_L - 8} y={y(t) + 4} className="sm-axis" textAnchor="end">
              {t > 0 ? "+" : ""}{t.toFixed(t % 1 === 0 ? 0 : 1)}%
            </text>
          </g>
        ))}
        {xTicks.map((idx) => {
          const first = idx === 0, last = idx === days.length - 1;
          return (
            <g key={idx}>
              {!first && !last && (
                <line x1={x(idx)} x2={x(idx)} y1={PAD_T} y2={H - PAD_B}
                      className="sm-grid" />
              )}
              <text x={x(idx)} y={H - 8} className="sm-axis"
                    textAnchor={first ? "start" : last ? "end" : "middle"}>
                {fmtDate(days[idx])}
              </text>
            </g>
          );
        })}
        {drag && drag.a !== drag.b && (() => {
          const from = Math.min(drag.a, drag.b), to = Math.max(drag.a, drag.b);
          return (
            <g className="sm-brush">
              <rect x={x(from)} y={PAD_T} width={x(to) - x(from)}
                    height={H - PAD_T - PAD_B} className="sm-brush-band" />
              <line x1={x(from)} x2={x(from)} y1={PAD_T} y2={H - PAD_B}
                    className="sm-brush-edge" />
              <line x1={x(to)} x2={x(to)} y1={PAD_T} y2={H - PAD_B}
                    className="sm-brush-edge" />
              <text x={x(from) + 4} y={PAD_T + 12} className="sm-axis sm-brush-label"
                    textAnchor="start">{days[from]}</text>
              <text x={x(to) - 4} y={PAD_T + 12} className="sm-axis sm-brush-label"
                    textAnchor="end">{days[to]}</text>
            </g>
          );
        })()}
        {cursor !== null && !drag && (
          <>
            <line x1={x(cursor)} x2={x(cursor)} y1={PAD_T} y2={H - PAD_B}
                  className="sm-cursor" />
            <text x={x(cursor)} y={H - 8} className="sm-axis sm-cursor-label"
                  textAnchor="middle">{days[cursor]}</text>
          </>
        )}
        {normalized.map((n) => (
          <polyline key={n.symbol} fill="none" stroke={n.color} strokeWidth={1.8}
                    className="sm-line"
                    points={n.points.map(([i, v]) => `${x(i)},${y(v)}`).join(" ")} />
        ))}
      </svg>

      <ul className="sm-legend">
        {series.map((s) => {
          const off = hidden.has(s.symbol);
          const n = normalized.find((x2) => x2.symbol === s.symbol);
          const value = n ? readout(n) : null;
          // Annualized (CAGR) alongside the cumulative return, but only for
          // spans of ~a year or more — annualizing a one-month move implies a
          // precision that isn't there. Suppressed while hovering (the value
          // then shows the cursor's point-in-time change, not the full range).
          const cagr = (!off && n && cursor === null && n.first > 0
                        && spanDays >= 300)
            ? (Math.pow(n.last / n.first, 365.25 / spanDays) - 1) * 100 : null;
          return (
            <li key={s.symbol}>
              <button type="button" onClick={() => onToggle(s.symbol)}
                      aria-pressed={!off}
                      className={`sm-legend-item${off ? " off" : ""}`}>
                <span className="sm-swatch" aria-hidden
                      style={{ background: seriesColor(colorIndex(s.symbol)) }} />
                <span className="sm-legend-sym">{s.symbol}</span>
                <span className="sm-legend-val">
                  {off || value === null ? "—"
                    : `${value > 0 ? "+" : ""}${value.toFixed(2)}%`}
                </span>
                {cagr !== null && (
                  <span className="sm-legend-cagr">
                    {cagr > 0 ? "+" : ""}{cagr.toFixed(1)}%/yr
                  </span>
                )}
              </button>
            </li>
          );
        })}
      </ul>
      {series.some((s) => s.downsampled) && (
        <p className="muted sm-note">
          Long ranges are thinned for display; daily closes are all stored.
        </p>
      )}
    </div>
  );
}

// --- backtest series charts (docs/design/35) --------------------------------
//
// Both charts below plot POSITIONS parsed from the API's decimal strings
// (`numOrNull`) — never a value a person reads as a number. Every label on
// them is either the exact string the API returned (fmtMetric) or is plainly
// marked as derived/approximate, per the plan's "never invent" rule.

const BT_W = 760, BT_H = 220, BT_PAD_L = 56, BT_PAD_R = 12, BT_PAD_T = 12, BT_PAD_B = 26;

function monthLabel(iso: string): string {
  const [yr, mo] = iso.split("-");
  return `${MONTHS[parseInt(mo, 10) - 1]} '${yr.slice(2)}`;
}

function useBtCursor(count: number) {
  const [cursor, setCursor] = useState<number | null>(null);
  function idxAt(e: React.PointerEvent<SVGSVGElement>): number {
    const box = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - box.left) / box.width) * BT_W;
    const i = Math.round(((px - BT_PAD_L) / Math.max(BT_W - BT_PAD_L - BT_PAD_R, 1))
      * Math.max(count - 1, 0));
    return Math.max(0, Math.min(count - 1, i));
  }
  return { cursor, setCursor, idxAt };
}

/** Value vs contributed, monthly. Two lines sharing one money axis. Every
 * displayed number is the API's own digits, grouped with thousands
 * separators by string manipulation (`groupThousands`/`fmtMoney`) — never a
 * float re-parsed and re-formatted; only pixel position comes from a parsed
 * number. `currency` (when the experiment's base currency is known) is
 * shown once per legend line rather than on every axis tick. */
export function ValueContributedChart({ points, currency }: {
  points: BacktestSeriesPoint[]; currency?: string | null;
}) {
  const days = points.map((p) => p.day);
  const values = points.map((p) => numOrNull(p.value));
  const contributed = points.map((p) => numOrNull(p.contributed));
  const { cursor, setCursor, idxAt } = useBtCursor(points.length);

  if (points.length === 0) {
    return <p className="muted sm-chart-empty">No value series recorded for this strategy.</p>;
  }
  const nums = [...values, ...contributed].filter((v): v is number => v !== null);
  const lo0 = Math.min(0, ...nums), hi0 = Math.max(0, ...nums);
  const pad = (hi0 - lo0 || 1) * 0.08;
  const lo = lo0 - pad, hi = hi0 + pad;
  const x = (i: number) => BT_PAD_L + (i * (BT_W - BT_PAD_L - BT_PAD_R)) / Math.max(points.length - 1, 1);
  const y = (v: number) => BT_PAD_T + ((hi - v) * (BT_H - BT_PAD_T - BT_PAD_B)) / Math.max(hi - lo, 0.0001);
  const ticks = niceTicks(lo, hi);
  const path = (arr: (number | null)[]) => arr.map((v, i) => (v === null ? null : `${x(i)},${y(v)}`))
    .filter((p): p is string => p !== null).join(" ");
  const tickCount = Math.max(2, Math.min(points.length,
    Math.floor((BT_W - BT_PAD_L - BT_PAD_R) / 110) + 1));
  const xTicks = [...new Set(Array.from({ length: tickCount }, (_, k) =>
    Math.round((k * (points.length - 1)) / Math.max(tickCount - 1, 1))))];
  const at = cursor ?? points.length - 1;

  return (
    <div className="sm-chart">
      <svg viewBox={`0 0 ${BT_W} ${BT_H}`} className="sm-chart-svg" role="img"
           aria-label="Portfolio value versus contributed capital, by month"
           onPointerMove={(e) => setCursor(idxAt(e))} onPointerLeave={() => setCursor(null)}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={BT_PAD_L} x2={BT_W - BT_PAD_R} y1={y(t)} y2={y(t)} className="sm-grid" />
            <text x={BT_PAD_L - 8} y={y(t) + 4} className="sm-axis" textAnchor="end">
              {groupThousands(t) ?? fmtMetric(t)}
            </text>
          </g>
        ))}
        {xTicks.map((idx) => (
          <text key={idx} x={x(idx)} y={BT_H - 8} className="sm-axis"
                textAnchor={idx === 0 ? "start" : idx === points.length - 1 ? "end" : "middle"}>
            {monthLabel(days[idx])}
          </text>
        ))}
        {cursor !== null && (
          <line x1={x(cursor)} x2={x(cursor)} y1={BT_PAD_T} y2={BT_H - BT_PAD_B} className="sm-cursor" />
        )}
        <polyline fill="none" stroke="var(--ds-chart-8)" strokeDasharray="4 3" strokeWidth={1.6}
                  className="sm-line" points={path(contributed)} />
        <polyline fill="none" stroke="var(--ds-chart-1)" strokeWidth={1.8}
                  className="sm-line" points={path(values)} />
      </svg>
      <ul className="sm-bt-legend">
        <li className="sm-bt-legend-row">
          <span className="sm-swatch" aria-hidden style={{ background: "var(--ds-chart-1)" }} />
          <span className="sm-legend-sym">Value</span>
          <span className="sm-legend-val">{fmtMoney(points[at]?.value, currency)}</span>
        </li>
        <li className="sm-bt-legend-row">
          <span className="sm-swatch sm-swatch-dash" aria-hidden />
          <span className="sm-legend-sym">Contributed</span>
          <span className="sm-legend-val">{fmtMoney(points[at]?.contributed, currency)}</span>
        </li>
      </ul>
      {cursor !== null && <p className="muted sm-note">{days[cursor]}</p>}
    </div>
  );
}

/** Drawdown DERIVED from the monthly value series: percent below the
 * running peak-to-date at each sampled month. This is an approximation —
 * monthly sampling can miss a deeper intra-month trough — never a
 * substitute for the strategy's own `max_drawdown` metric (shown in the
 * stat row), which the engine computes at daily resolution. */
export function DrawdownChart({ points }: { points: BacktestSeriesPoint[] }) {
  const days = points.map((p) => p.day);
  const values = points.map((p) => numOrNull(p.value));
  const { cursor, setCursor, idxAt } = useBtCursor(points.length);

  const drawdown: (number | null)[] = [];
  let peak: number | null = null;
  for (const v of values) {
    if (v === null) { drawdown.push(null); continue; }
    peak = peak === null ? v : Math.max(peak, v);
    drawdown.push(peak > 0 ? (v / peak - 1) * 100 : 0);
  }
  const known = drawdown.filter((v): v is number => v !== null);
  if (known.length === 0) {
    return <p className="muted sm-chart-empty">No value series recorded for this strategy.</p>;
  }
  const lo = Math.min(0, ...known) - 1, hi = 0;
  const x = (i: number) => BT_PAD_L + (i * (BT_W - BT_PAD_L - BT_PAD_R)) / Math.max(points.length - 1, 1);
  const y = (v: number) => BT_PAD_T + ((hi - v) * (BT_H - BT_PAD_T - BT_PAD_B)) / Math.max(hi - lo, 0.0001);
  const ticks = niceTicks(lo, hi, 4);
  const path = drawdown.map((v, i) => (v === null ? null : `${x(i)},${y(v)}`))
    .filter((p): p is string => p !== null).join(" ");
  const at = cursor ?? points.length - 1;

  return (
    <div className="sm-chart">
      <svg viewBox={`0 0 ${BT_W} ${BT_H}`} className="sm-chart-svg" role="img"
           aria-label="Drawdown from the running peak, derived from the monthly value series"
           onPointerMove={(e) => setCursor(idxAt(e))} onPointerLeave={() => setCursor(null)}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={BT_PAD_L} x2={BT_W - BT_PAD_R} y1={y(t)} y2={y(t)}
                  className={t === 0 ? "sm-grid sm-grid-zero" : "sm-grid"} />
            <text x={BT_PAD_L - 8} y={y(t) + 4} className="sm-axis" textAnchor="end">{t.toFixed(0)}%</text>
          </g>
        ))}
        {cursor !== null && (
          <line x1={x(cursor)} x2={x(cursor)} y1={BT_PAD_T} y2={BT_H - BT_PAD_B} className="sm-cursor" />
        )}
        <polyline fill="none" stroke="var(--ds-danger)" strokeWidth={1.6}
                  className="sm-line" points={path} />
      </svg>
      <p className="muted sm-note">
        {cursor !== null && drawdown[at] !== null
          ? `${monthLabel(days[at])}: ${drawdown[at]!.toFixed(1)}% below peak (derived, approximate)`
          : "Approximate — from month-end values, not the engine's daily max_drawdown above."}
      </p>
    </div>
  );
}
