import { useEffect, useState } from "react";
import { api, type Activity, type Brief, type Calendar, type Health, type Prs,
         type Summary, type WeekBar } from "./api";
import { Heatmap } from "./heatmap";
import { WeeklyBars } from "./charts";
import { ActivityList, BriefCard, PRBoard, TotalsCard } from "./components";
import { SideNav, buildPlatformNav, type AppNavInfo } from "@ap/ui/sidenav";

// The running page (one route): all-time totals, a run calendar, weekly
// mileage, personal records, the latest coach's brief, and recent activities.

function Shell({ children }: { children: React.ReactNode }) {
  const [apps, setApps] = useState<AppNavInfo[]>([{ name: "running", icon: "🏃", display_name: "Running Coach" }]);
  useEffect(() => {
    fetch("/api/apps", { credentials: "include" })
      .then((r) => (r.ok ? r.json() : []))
      .then((all: (AppNavInfo & { ui: boolean; ready: boolean | null })[]) =>
        setApps(all.filter((a) => a.ui && a.ready)))
      .catch(() => {});
  }, []);
  return (
    <div className="layout">
      <SideNav entries={buildPlatformNav(apps)} activePath="/apps/running/" />
      <main className="main">
        <div className="rn-shell">
          <header className="rn-top">
            <span className="rn-brand">🏃 Running Coach</span>
          </header>
          {children}
        </div>
      </main>
    </div>
  );
}

export default function App() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [calendar, setCalendar] = useState<Calendar | null>(null);
  const [weekly, setWeekly] = useState<WeekBar[]>([]);
  const [prs, setPrs] = useState<Prs | null>(null);
  const [brief, setBrief] = useState<Brief | null>(null);
  const [activities, setActivities] = useState<Activity[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    Promise.all([
      api<Summary>("/summary"),
      api<Calendar>("/calendar?weeks=26"),
      api<{ weeks: WeekBar[] }>("/weekly?weeks=12"),
      api<Prs>("/prs"),
      api<Brief[]>("/briefs?limit=1"),
      api<Activity[]>("/activities?limit=12"),
      api<Health>("/health"),
    ]).then(([s, c, w, p, b, a, h]) => {
      setSummary(s); setCalendar(c); setWeekly(w.weeks); setPrs(p);
      setBrief(b[0] ?? null); setActivities(a); setHealth(h); setError(null);
    }).catch((e) => setError(e instanceof Error ? e.message : "Failed to load."))
      .finally(() => setLoaded(true));
  }, []);

  const empty = loaded && summary && summary.totals.activities === 0;

  return (
    <Shell>
      {error && <div className="error">{error}</div>}
      {!loaded && <p className="muted">Loading…</p>}

      {empty && (
        <div className="rn-card rn-empty">
          <div className="rn-card-head">
            <h2>{health?.ok ? "Ready for the first sync" : "Activity sync is offline"}</h2>
            <span className={`rn-state ${health?.ok ? "ok" : "bad"}`}>
              {health?.ok ? "CONNECTED" : "DEGRADED"}
            </span>
          </div>
          <p className="muted">
            {health?.ok
              ? "The ingestion pipeline is connected and waiting for Running Coach to sync Strava."
              : "The app cannot currently consume activity updates. The dashboard is hidden because zeroes would be misleading."}
          </p>
          {health?.pipeline.last_error && <p className="error">{health.pipeline.last_error}</p>}
        </div>
      )}

      {!empty && summary && <TotalsCard totals={summary.totals} />}
      {!empty && calendar && calendar.days.length > 0 && <Heatmap days={calendar.days} />}

      {!empty && <div className="rn-cols">
        {weekly.length > 0 && <WeeklyBars weeks={weekly} />}
        {prs && <PRBoard prs={prs} />}
      </div>}

      {!empty && <div className="rn-cols rn-coach-row">
        <BriefCard brief={brief} />
        <ActivityList activities={activities} />
      </div>}
    </Shell>
  );
}
