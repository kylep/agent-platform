import { useMemo } from "react";
import type { HeatDay } from "./api";

// A GitHub-contributions-style calendar of daily distance. The API hands back
// a dense day-by-day grid that starts on a Monday, so we chunk it into columns
// of seven (one column per week, weekday down the rows) and shade each cell by
// how far that day's runs went. Colour is always a design token — the cell is
// var(--ds-accent) at a stepped opacity, so light/dark both work and the
// no-raw-hex gate stays happy.

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const DOW = ["Mon", "", "Wed", "", "Fri", "", "Sun"];

// Distance (km) → intensity bucket 0..4. 0 is "ran nothing".
function level(km: number): number {
  if (km <= 0) return 0;
  if (km < 5) return 1;
  if (km < 10) return 2;
  if (km < 16) return 3;
  return 4;
}

export function Heatmap({ days }: { days: HeatDay[] }) {
  const weeks = useMemo(() => {
    const cols: HeatDay[][] = [];
    for (let i = 0; i < days.length; i += 7) cols.push(days.slice(i, i + 7));
    return cols;
  }, [days]);

  // A month label sits above the first column whose Monday falls in a new month.
  const monthLabels = weeks.map((w, i) => {
    const first = w[0];
    if (!first) return "";
    const m = Number(first.day.slice(5, 7)) - 1;
    const prev = i > 0 && weeks[i - 1][0]
      ? Number(weeks[i - 1][0].day.slice(5, 7)) - 1 : -1;
    return m !== prev ? MONTHS[m] : "";
  });

  const total = days.reduce((s, d) => s + d.distance_km, 0);
  const activeDays = days.filter((d) => d.count > 0).length;

  return (
    <section className="rn-card rn-heat" aria-label="Run calendar">
      <div className="rn-card-head">
        <h2>Calendar</h2>
        <span className="muted">
          {activeDays} active days · {total.toFixed(0)} km
        </span>
      </div>
      <div className="rn-heat-scroll">
        <div className="rn-heat-grid">
          <div className="rn-heat-dow" aria-hidden>
            {DOW.map((d, i) => <span key={i}>{d}</span>)}
          </div>
          <div className="rn-heat-cols">
            <div className="rn-heat-months" aria-hidden>
              {monthLabels.map((m, i) => <span key={i}>{m}</span>)}
            </div>
            <div className="rn-heat-weeks">
              {weeks.map((w, ci) => (
                <div className="rn-heat-col" key={ci}>
                  {Array.from({ length: 7 }, (_, ri) => {
                    const d = w[ri];
                    if (!d) return <span key={ri} className="rn-cell rn-cell-empty" />;
                    return (
                      <span key={ri}
                            className={`rn-cell rn-l${level(d.distance_km)}`}
                            title={`${d.day}: ${d.distance_km.toFixed(1)} km`
                                   + (d.count > 1 ? ` · ${d.count} activities` : "")} />
                    );
                  })}
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
      <div className="rn-heat-legend">
        <span className="muted">less</span>
        {[0, 1, 2, 3, 4].map((l) => <span key={l} className={`rn-cell rn-l${l}`} />)}
        <span className="muted">more</span>
      </div>
    </section>
  );
}
