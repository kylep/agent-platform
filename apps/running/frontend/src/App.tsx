import { useEffect, useState } from "react";
import { api, type Activity, type Brief, type Calendar, type Prs,
         type Summary, type WeekBar } from "./api";
import { Heatmap } from "./heatmap";
import { WeeklyBars } from "./charts";
import { ActivityList, BriefCard, PRBoard, TotalsCard } from "./components";
import { SideNav, buildPlatformNav, type AppNavInfo } from "@ap/ui/sidenav";

// The running page (one route): all-time totals, a run calendar, weekly
// mileage, personal records, the latest coach's brief, and recent activities.

function Shell({ children }: { children: React.ReactNode }) {
  const [apps, setApps] = useState<AppNavInfo[]>([{ name: "running", icon: "🏃" }]);
  useEffect(() => {
    fetch("/api/apps", { credentials: "include" })
      .then((r) => (r.ok ? r.json() : []))
      .then((all: { name: string; icon: string; ui: boolean; ready: boolean | null }[]) =>
        setApps(all.filter((a) => a.ui && a.ready)))
      .catch(() => {});
  }, []);
  return (
    <div className="layout">
      <SideNav entries={buildPlatformNav(apps)} activePath="/apps/running/" />
      <main className="main">
        <div className="rn-shell">
          <header className="rn-top">
            <span className="rn-brand">🏃 Running</span>
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
    ]).then(([s, c, w, p, b, a]) => {
      setSummary(s); setCalendar(c); setWeekly(w.weeks); setPrs(p);
      setBrief(b[0] ?? null); setActivities(a); setError(null);
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
          <h2>No runs yet</h2>
          <p className="muted">
            Once the <code>strava</code> secret is set, the running agent
            backfills your recent activities and this fills in — a calendar,
            weekly mileage, your records, and a weekly coach's brief. Ask{" "}
            <b>pai</b> about your runs in Discord any time.
          </p>
        </div>
      )}

      {summary && <TotalsCard totals={summary.totals} />}
      {calendar && calendar.days.length > 0 && <Heatmap days={calendar.days} />}

      <div className="rn-cols">
        {weekly.length > 0 && <WeeklyBars weeks={weekly} />}
        {prs && <PRBoard prs={prs} />}
      </div>

      <div className="rn-cols">
        <BriefCard brief={brief} />
        <ActivityList activities={activities} />
      </div>
    </Shell>
  );
}
