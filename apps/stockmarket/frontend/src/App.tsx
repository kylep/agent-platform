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
  // A custom From→To window overrides the preset when both dates are set.
  const [custom, setCustom] = useState<{ from: string; to: string } | null>(null);
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
    const q = new URLSearchParams({ symbols: symbolKey });
    if (custom) { q.set("day_from", custom.from); q.set("day_to", custom.to); }
    else { q.set("range", range); }
    api<SeriesView[]>(`/series?${q}`)
      .then(setSeries)
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load prices."));
  }, [symbolKey, range, custom]);

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
            <ChipButton key={r} variant={r === range && !custom ? "ok" : "neutral"}
                        aria-pressed={r === range && !custom}
                        onClick={() => { setRange(r); setCustom(null); }}>
              {r}
            </ChipButton>
          ))}
          <label className="sm-range-custom">
            <input type="date" aria-label="From date" max={summary?.latest_day ?? undefined}
                   value={custom?.from ?? ""}
                   onChange={(e) => {
                     const from = e.target.value;
                     setCustom(from && custom?.to ? { from, to: custom.to } : from ? { from, to: summary?.latest_day ?? from } : null);
                   }} />
            <span>→</span>
            <input type="date" aria-label="To date" max={summary?.latest_day ?? undefined}
                   value={custom?.to ?? ""}
                   onChange={(e) => {
                     const to = e.target.value;
                     setCustom(to && custom?.from ? { from: custom.from, to } : to ? { from: to, to } : null);
                   }} />
            {custom && (
              <button type="button" aria-label="Clear custom range"
                      onClick={() => setCustom(null)}>✕</button>
            )}
          </label>
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
            : loaded && (
                <div className="sm-brief sm-brief-empty">
                  <h2>Market brief</h2>
                  <p className="muted">No brief yet — the first one lands on the
                    next weekday run (weekdays, 9:35 ET).</p>
                </div>
              )}
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
