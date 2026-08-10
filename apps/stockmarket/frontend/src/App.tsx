import { useCallback, useEffect, useMemo, useState } from "react";
import { api, RANGES, type BriefView, type Range, type SeriesView,
         type Summary } from "./api";
import { IndexChart } from "./chart";
import { AddSymbol, BriefCard, StatTile } from "./components";
import { ChipButton } from "@ap/ui/chip";
import { buildPlatformNav, SideNav, type AppNavInfo } from "@ap/ui/sidenav";

// The stockmarket page (one route, deliberately): three pinned indexes and
// your watchlist overlaid on one percent-change chart, the latest session as
// numbers above it, and the day's brief below.

function Shell({ children }: { children: React.ReactNode }) {
  // The shared platform sidebar (from @ap/ui) wraps the app — same chrome as
  // the console, with this app active under the Apps accordion.
  const [apps, setApps] = useState<AppNavInfo[]>([{ name: "stockmarket", icon: "📈" }]);
  useEffect(() => {
    fetch("/api/apps", { credentials: "include" })
      .then((r) => (r.ok ? r.json() : []))
      .then((all: { name: string; icon: string; ui: boolean; ready: boolean | null }[]) =>
        setApps(all.filter((a) => a.ui && a.ready)))
      .catch(() => {});
  }, []);
  return (
    <div className="layout">
      <SideNav entries={buildPlatformNav(apps)} activePath="/apps/stockmarket/" />
      <main className="main">
        <div className="sm-shell">
          <header className="sm-top">
            <span className="sm-brand">📈 Stockmarket</span>
          </header>
          {children}
        </div>
      </main>
    </div>
  );
}

export default function App() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [series, setSeries] = useState<SeriesView[]>([]);
  const [brief, setBrief] = useState<BriefView | null>(null);
  const [range, setRange] = useState<Range>("1M");
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [loaded, setLoaded] = useState(false);

  const load = useCallback(() => {
    Promise.all([
      api<Summary>("/summary"),
      api<BriefView[]>("/briefs?limit=1"),
    ]).then(([s, briefs]) => {
      setSummary(s);
      setBrief(briefs[0] ?? null);
      setError(null);
    }).catch((e) => setError(e instanceof Error ? e.message : "Failed to load."))
      .finally(() => setLoaded(true));
  }, []);
  useEffect(load, [load]);

  // Palette slots are assigned over EVERY tracked symbol, not just the charted
  // ones, so a pending backfill's tile keeps the color its line will get.
  const tracked = useMemo(
    () => [...(summary?.indexes ?? []), ...(summary?.watchlist ?? [])],
    [summary]);
  const order = useMemo(() => tracked.map((s) => s.symbol), [tracked]);
  const colorIndex = useCallback(
    (symbol: string) => Math.max(order.indexOf(symbol), 0), [order]);

  // Only symbols with bars go on the chart — a pending backfill has nothing to
  // draw yet, and an invalid ticker never will.
  const symbolKey = tracked.filter((s) => s.status === "ok")
    .map((s) => s.symbol).join(",");

  useEffect(() => {
    if (!symbolKey) { setSeries([]); return; }
    api<SeriesView[]>(`/series?symbols=${encodeURIComponent(symbolKey)}&range=${range}`)
      .then(setSeries)
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load prices."));
  }, [symbolKey, range]);

  function toggle(symbol: string) {
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(symbol)) next.delete(symbol); else next.add(symbol);
      return next;
    });
  }

  async function add(symbol: string) {
    setAdding(true); setError(null);
    try {
      await api("/watchlist", { method: "POST", body: JSON.stringify({ symbol }) });
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to add symbol.");
    } finally { setAdding(false); }
  }

  async function remove(symbol: string) {
    setError(null);
    try {
      await api(`/watchlist/${encodeURIComponent(symbol)}`, { method: "DELETE" });
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to remove symbol.");
    }
  }

  const watching = summary?.watchlist ?? [];
  // A pending backfill lands within a run or two; poll while one is in flight
  // so the tile flips from "backfilling…" on its own.
  const anyPending = tracked.some((s) => s.status === "pending");
  useEffect(() => {
    if (!anyPending) return;
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, [anyPending, load]);

  return (
    <Shell>
      {error && <div className="error">{error}</div>}
      {!loaded && <p className="muted">Loading…</p>}

      <section className="sm-stats" aria-label="Latest session">
        {(summary?.indexes ?? []).map((s) => (
          <StatTile key={s.symbol} view={s} colorIndex={colorIndex(s.symbol)} />
        ))}
        {watching.map((s) => (
          <StatTile key={s.symbol} view={s} colorIndex={colorIndex(s.symbol)}
                    onRemove={() => remove(s.symbol)} />
        ))}
      </section>

      <section className="sm-chart-section" aria-label="Price history">
        <div className="sm-ranges" role="group" aria-label="Date range">
          {RANGES.map((r) => (
            <ChipButton key={r} variant={r === range ? "ok" : "neutral"}
                        aria-pressed={r === range} onClick={() => setRange(r)}>
              {r}
            </ChipButton>
          ))}
        </div>
        <IndexChart series={series} hidden={hidden} onToggle={toggle}
                    colorIndex={colorIndex} />
        <p className="muted sm-note">
          Each line is percent change from the start of the range, so symbols at
          very different prices stay comparable.
        </p>
      </section>

      <section className="sm-cols">
        <div>
          {brief
            ? <BriefCard brief={brief} />
            : loaded && <p className="muted">No brief yet — the first one lands
                on the next weekday run.</p>}
        </div>
        <aside className="sm-side">
          <h2>Watchlist</h2>
          <AddSymbol onAdd={add} busy={adding} full={watching.length >= 20} />
          {watching.length === 0 && (
            <p className="muted">
              Nothing watched yet. Added tickers are backfilled with five years
              of daily closes.
            </p>
          )}
        </aside>
      </section>
    </Shell>
  );
}
