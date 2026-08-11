import type { Activity, Brief, Prs, Totals } from "./api";

// Shared display pieces for the running page. Numbers are tabular; colours are
// design tokens only.

function hms(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h) return `${h}h ${m}m`;
  return `${m}m`;
}

/** The cumulative "fun totals" banner — the delightful at-a-glance line. */
export function TotalsCard({ totals }: { totals: Totals }) {
  return (
    <section className="rn-card rn-totals" aria-label="All-time totals">
      <div className="rn-totals-hero">
        <span className="rn-totals-km">{totals.total_km.toLocaleString()} km</span>
        {totals.comparison && <span className="rn-totals-cmp">{totals.comparison}</span>}
      </div>
      <div className="rn-totals-row">
        <div><b>{totals.runs}</b><span>runs</span></div>
        <div><b>{hms(totals.total_moving_time_s)}</b><span>moving</span></div>
        <div><b>{totals.total_elevation_m.toLocaleString()} m</b><span>climbed</span></div>
        {totals.everests > 0 && (
          <div><b>{totals.everests}×</b><span>Everest</span></div>
        )}
      </div>
    </section>
  );
}

function PrTile({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rn-pr">
      <span className="rn-pr-label">{label}</span>
      <span className="rn-pr-value">{value}</span>
      {sub && <span className="rn-pr-sub muted">{sub}</span>}
    </div>
  );
}

export function PRBoard({ prs }: { prs: Prs }) {
  return (
    <section className="rn-card" aria-label="Personal records">
      <div className="rn-card-head"><h2>Records</h2></div>
      <div className="rn-pr-grid">
        <PrTile label="Longest run"
                value={prs.longest_run ? `${prs.longest_run.km} km` : "—"}
                sub={prs.longest_run?.day} />
        <PrTile label="Fastest 5K+"
                value={prs.fastest_5k?.pace ?? "—"} sub={prs.fastest_5k?.day} />
        <PrTile label="Fastest 10K+"
                value={prs.fastest_10k?.pace ?? "—"} sub={prs.fastest_10k?.day} />
        <PrTile label="Biggest week"
                value={prs.biggest_week ? `${prs.biggest_week.km} km` : "—"}
                sub={prs.biggest_week?.week_start} />
        <PrTile label="Current streak"
                value={`${prs.current_streak} day${prs.current_streak === 1 ? "" : "s"}`} />
        <PrTile label="Longest streak"
                value={`${prs.longest_streak} day${prs.longest_streak === 1 ? "" : "s"}`} />
      </div>
    </section>
  );
}

export function BriefCard({ brief }: { brief: Brief | null }) {
  if (!brief) {
    return (
      <section className="rn-card" aria-label="Weekly brief">
        <div className="rn-card-head"><h2>Coach's brief</h2></div>
        <p className="muted">
          No brief yet — the first weekly recap posts after the running agent's
          next run (and lands in Discord #running).
        </p>
      </section>
    );
  }
  return (
    <section className="rn-card" aria-label="Weekly brief">
      <div className="rn-card-head">
        <h2>Coach's brief</h2>
        <span className="muted">week of {brief.week_start}</span>
      </div>
      <p className="rn-brief-stat">
        <b>{brief.distance_km.toFixed(1)} km</b> across{" "}
        <b>{brief.runs}</b> run{brief.runs === 1 ? "" : "s"}
      </p>
      {brief.body && <p className="rn-brief-body">{brief.body}</p>}
      {brief.highlights.length > 0 && (
        <ul className="rn-brief-highlights">
          {brief.highlights.map((h, i) => <li key={i}>{h}</li>)}
        </ul>
      )}
      {brief.tags.length > 0 && (
        <div className="rn-tags">
          {brief.tags.map((t) => <span key={t} className="rn-tag">{t}</span>)}
        </div>
      )}
      <a className="rn-brief-report" href={`/reports/weekly-running/${brief.week_start}`}>
        Full weekly report →
      </a>
    </section>
  );
}

export function ActivityList({ activities }: { activities: Activity[] }) {
  if (activities.length === 0) {
    return (
      <section className="rn-card" aria-label="Recent activities">
        <div className="rn-card-head"><h2>Recent</h2></div>
        <p className="muted">
          No activities yet. Once the strava secret is set, the running agent
          backfills your recent runs.
        </p>
      </section>
    );
  }
  return (
    <section className="rn-card" aria-label="Recent activities">
      <div className="rn-card-head"><h2>Recent</h2></div>
      <ul className="rn-acts">
        {activities.map((a) => (
          <li key={a.id}>
            <span className="rn-act-day muted">{a.day.slice(5)}</span>
            <span className="rn-act-name">{a.name || a.type}</span>
            <span className="rn-act-dist">{a.distance_km.toFixed(1)} km</span>
            <span className="rn-act-pace muted">{a.pace ?? hms(a.moving_time_s)}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
