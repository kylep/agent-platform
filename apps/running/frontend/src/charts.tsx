import type { WeekBar } from "./api";

// Weekly mileage as a hand-rolled SVG bar chart — same no-dependency approach
// as the stockmarket chart. Bars are var(--ds-chart-1); the tallest week and
// a light average line give the shape at a glance.

const W = 720, H = 200, PAD_L = 34, PAD_R = 8, PAD_T = 12, PAD_B = 26;

function fmtWeek(iso: string): string {
  const [, m, d] = iso.split("-");
  return `${Number(m)}/${Number(d)}`;
}

export function WeeklyBars({ weeks }: { weeks: WeekBar[] }) {
  const max = Math.max(1, ...weeks.map((w) => w.distance_km));
  const avg = weeks.length
    ? weeks.reduce((s, w) => s + w.distance_km, 0) / weeks.length : 0;
  const plotW = W - PAD_L - PAD_R, plotH = H - PAD_T - PAD_B;
  const bw = plotW / Math.max(weeks.length, 1);
  const y = (v: number) => PAD_T + (1 - v / max) * plotH;
  // A few round gridline values across the range.
  const ticks = [0, max / 2, max].map((v) => Math.round(v));

  return (
    <section className="rn-card" aria-label="Weekly mileage">
      <div className="rn-card-head">
        <h2>Weekly mileage</h2>
        <span className="muted">avg {avg.toFixed(1)} km/wk</span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="rn-bars" role="img"
           aria-label="Kilometres run per week">
        {ticks.map((t, i) => (
          <g key={i}>
            <line x1={PAD_L} x2={W - PAD_R} y1={y(t)} y2={y(t)} className="rn-grid" />
            <text x={PAD_L - 6} y={y(t) + 3} className="rn-axis" textAnchor="end">{t}</text>
          </g>
        ))}
        {avg > 0 && (
          <line x1={PAD_L} x2={W - PAD_R} y1={y(avg)} y2={y(avg)} className="rn-avg" />
        )}
        {weeks.map((w, i) => {
          const x = PAD_L + i * bw;
          const h = Math.max(0, plotH - (y(w.distance_km) - PAD_T));
          const showLabel = weeks.length <= 14 || i % 2 === 0;
          return (
            <g key={w.week_start}>
              <rect x={x + bw * 0.15} y={y(w.distance_km)} width={bw * 0.7} height={h}
                    className="rn-bar" rx={2}>
                <title>{`Week of ${w.week_start}: ${w.distance_km.toFixed(1)} km · ${w.runs} runs`}</title>
              </rect>
              {showLabel && (
                <text x={x + bw / 2} y={H - 8} className="rn-axis" textAnchor="middle">
                  {fmtWeek(w.week_start)}
                </text>
              )}
            </g>
          );
        })}
      </svg>
    </section>
  );
}
