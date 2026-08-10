import { useMemo, useState } from "react";
import type { SeriesView } from "./api";

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

function niceTicks(lo: number, hi: number, count = 5): number[] {
  const span = hi - lo || 1;
  const rough = span / count;
  const pow = 10 ** Math.floor(Math.log10(rough));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * pow).find((s) => s >= rough) ?? 10 * pow;
  const start = Math.ceil(lo / step) * step;
  const out: number[] = [];
  for (let v = start; v <= hi + step / 1000; v += step) out.push(Number(v.toFixed(6)));
  return out;
}

export function IndexChart({ series, hidden, onToggle, colorIndex }: {
  series: SeriesView[];
  hidden: Set<string>;
  onToggle: (symbol: string) => void;
  /** Palette slot for a symbol. Supplied by the page so a line and its stat
   * tile always agree, even when a pending symbol has a tile but no series. */
  colorIndex: (symbol: string) => number;
}) {
  const [cursor, setCursor] = useState<number | null>(null);

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

  function onMove(e: React.MouseEvent<SVGSVGElement>) {
    const box = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - box.left) / box.width) * W;
    const i = Math.round(
      ((px - PAD_L) / Math.max(W - PAD_L - PAD_R, 1)) * Math.max(days.length - 1, 1));
    setCursor(i >= 0 && i < days.length ? i : null);
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
      <svg viewBox={`0 0 ${W} ${H}`} className="sm-chart-svg" role="img"
           aria-label={`Percent change over the selected range for ${
             normalized.map((n) => n.symbol).join(", ")}`}
           onMouseMove={onMove} onMouseLeave={() => setCursor(null)}>
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
        {cursor !== null && (
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
