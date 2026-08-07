import { useEffect, useState } from "react";
import { BrowserRouter, Link, Route, Routes, useNavigate, useParams,
         useSearchParams } from "react-router-dom";
import { api, type CalendarDay, type ItemView, type Summary, type TopicView } from "./api";
import { ItemList, Sparkline, TopicChip, VolumeCalendar, monthLabel, shiftMonth,
         ym } from "./components";
import { Button } from "@ap/ui/button";
import { Input } from "@ap/ui/field";
import { buildPlatformNav, SideNav, type AppNavInfo } from "@ap/ui/sidenav";

// The news browser (docs/design/11): topic × date are the two axes.
//   /            home — today so far, topic tiles, volume calendar
//   /day/:date   one day, grouped by topic
//   /topic/:slug one topic, reverse-chron with a date scrubber
// Search rides ?q= on the home route.

function Shell({ children }: { children: React.ReactNode }) {
  // The shared platform sidebar (from @ap/ui) wraps the app — same chrome as
  // the console, with this app active under the Apps accordion. Platform
  // links are plain anchors (leaving the app is a full page load by design).
  const [apps, setApps] = useState<AppNavInfo[]>([{ name: "news", icon: "🗞️" }]);
  useEffect(() => {
    fetch("/api/apps", { credentials: "include" })
      .then((r) => (r.ok ? r.json() : []))
      .then((all: { name: string; icon: string; ui: boolean; ready: boolean | null }[]) =>
        setApps(all.filter((a) => a.ui && a.ready)))
      .catch(() => {});
  }, []);
  return (
    <div className="layout">
      <SideNav entries={buildPlatformNav(apps)} activePath="/apps/news/" />
      <main className="main">
        <div className="news-shell">
          <header className="news-top">
            <Link to="/" className="news-brand">🗞️ News</Link>
            <SearchBox />
            <a className="news-ask" href="/agents/news-librarian?tab=conversations">
              💬 Ask the librarian
            </a>
          </header>
          <main className="news-main">{children}</main>
        </div>
      </main>
    </div>
  );
}

function SearchBox() {
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  return (
    <form className="news-search" onSubmit={(e) => {
      e.preventDefault();
      if (q.trim()) navigate(`/?q=${encodeURIComponent(q.trim())}`);
    }}>
      <Input placeholder="Search news…" value={q} aria-label="Search news"
             onChange={(e) => setQ(e.target.value)} />
    </form>
  );
}

function Home() {
  const [params] = useSearchParams();
  const q = params.get("q") ?? "";
  const [summary, setSummary] = useState<Summary | null>(null);
  const [topics, setTopics] = useState<TopicView[]>([]);
  const [today, setToday] = useState<ItemView[]>([]);
  const [results, setResults] = useState<ItemView[] | null>(null);
  const [month, setMonth] = useState(ym(new Date()));
  const [cal, setCal] = useState<Record<string, CalendarDay>>({});
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    api<Summary>("/summary").then(setSummary).catch((e) => setError(String(e)));
    api<TopicView[]>("/topics").then(setTopics).catch(() => {});
  }, []);
  useEffect(() => {
    if (!summary?.latest_day) return;
    api<ItemView[]>(`/items?day=${summary.latest_day}`).then(setToday).catch(() => {});
  }, [summary?.latest_day]);
  useEffect(() => {
    api<Record<string, CalendarDay>>(`/calendar?month=${month}`).then(setCal).catch(() => {});
  }, [month]);
  useEffect(() => {
    if (!q) { setResults(null); return; }
    api<ItemView[]>(`/items?q=${encodeURIComponent(q)}`).then(setResults).catch(() => {});
  }, [q]);

  if (error) return <div className="error">{error}</div>;
  if (results !== null) {
    return (
      <>
        <h1>Search: “{q}”</h1>
        <p className="muted">{results.length} {results.length === 1 ? "match" : "matches"} · <Link to="/">clear</Link></p>
        <ItemList items={results} showDay />
      </>
    );
  }
  return (
    <>
      <div className="news-stats">
        <div className="news-stat"><span>{summary?.today ?? "–"}</span><label>today</label></div>
        <div className="news-stat"><span>{summary?.week ?? "–"}</span><label>this week</label></div>
        <div className="news-stat"><span>{summary?.total ?? "–"}</span><label>archived</label></div>
        <div className="news-stat"><span>{summary?.topics ?? "–"}</span><label>topics</label></div>
      </div>
      <div className="news-cols">
        <section>
          <h2>{summary?.latest_day === new Date().toISOString().slice(0, 10)
            ? "Today so far" : `Latest — ${summary?.latest_day ?? ""}`}</h2>
          <ItemList items={today} />
        </section>
        <aside>
          <h2>Topics</h2>
          <div className="topic-tiles">
            {topics.map((t) => (
              <Link key={t.slug} to={`/topic/${t.slug}`} className="topic-tile">
                <div className="topic-tile-head">
                  <TopicChip {...t} />
                </div>
                <Sparkline values={t.spark} color={t.color} />
              </Link>
            ))}
            {topics.length === 0 && <p className="muted">No topics yet — the first gather creates them.</p>}
          </div>
          <h2>
            <span className="cal-title">
              <Button variant="secondary" size="sm" aria-label="Previous month"
                      onClick={() => setMonth(shiftMonth(month, -1))}>‹</Button>
              {monthLabel(month)}
              <Button variant="secondary" size="sm" aria-label="Next month"
                      onClick={() => setMonth(shiftMonth(month, 1))}>›</Button>
            </span>
          </h2>
          <VolumeCalendar month={month} days={cal} onOpen={(d) => navigate(`/day/${d}`)} />
        </aside>
      </div>
    </>
  );
}

function Day() {
  const { date } = useParams();
  const [items, setItems] = useState<ItemView[] | null>(null);
  const [topics, setTopics] = useState<TopicView[]>([]);
  useEffect(() => {
    api<ItemView[]>(`/items?day=${date}`).then(setItems).catch(() => setItems([]));
    api<TopicView[]>("/topics").then(setTopics).catch(() => {});
  }, [date]);
  const prev = date ? new Date(new Date(date).getTime() - 86400000).toISOString().slice(0, 10) : "";
  const next = date ? new Date(new Date(date).getTime() + 86400000).toISOString().slice(0, 10) : "";
  const bySlug = new Map<string, ItemView[]>();
  for (const i of items ?? []) bySlug.set(i.topic, [...(bySlug.get(i.topic) ?? []), i]);
  return (
    <>
      <div className="day-nav">
        <Link to={`/day/${prev}`}>‹ {prev}</Link>
        <h1>{date}</h1>
        <Link to={`/day/${next}`}>{next} ›</Link>
      </div>
      <p className="muted">
        {items?.length ?? "…"} items · <a href={`/reports/daily-news/${date}`}>daily report</a>
      </p>
      {items !== null && items.length === 0 && <p className="muted">No news gathered on {date}.</p>}
      {[...bySlug.entries()].map(([slug, list]) => {
        const t = topics.find((x) => x.slug === slug);
        return (
          <section key={slug}>
            <h2><TopicChip slug={slug} label={t?.label ?? slug} color={t?.color ?? 8}
                           count={list.length} /></h2>
            <ItemList items={list} />
          </section>
        );
      })}
    </>
  );
}

function TopicPage() {
  const { slug } = useParams();
  const [params, setParams] = useSearchParams();
  const month = params.get("m");
  const [items, setItems] = useState<ItemView[] | null>(null);
  const [topics, setTopics] = useState<TopicView[]>([]);
  useEffect(() => {
    const range = month ? `&day_from=${month}-01&day_to=${month}-31` : "";
    api<ItemView[]>(`/items?topic=${slug}${range}&limit=200`).then(setItems)
      .catch(() => setItems([]));
    api<TopicView[]>("/topics").then(setTopics).catch(() => {});
  }, [slug, month]);
  const t = topics.find((x) => x.slug === slug);
  const scrub = month ?? ym(new Date());
  return (
    <>
      <h1><span className="topic-dot lg" aria-hidden
                style={{ ["--topic-color" as string]: `var(--ds-chart-${t?.color ?? 8})` }} />
        {t?.label ?? slug}</h1>
      <div className="day-nav scrub">
        <Button variant="secondary" size="sm"
                onClick={() => setParams({ m: shiftMonth(scrub, -1) })}>‹</Button>
        <span className="muted">{month ? monthLabel(month) : "All time (newest first)"}</span>
        <Button variant="secondary" size="sm"
                onClick={() => setParams({ m: shiftMonth(scrub, 1) })}>›</Button>
        {month && <Button variant="secondary" size="sm" onClick={() => setParams({})}>all</Button>}
      </div>
      {items === null ? <p className="muted">Loading…</p> : <ItemList items={items} showDay />}
    </>
  );
}

export default function App() {
  return (
    <BrowserRouter basename="/apps/news">
      <Shell>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/day/:date" element={<Day />} />
          <Route path="/topic/:slug" element={<TopicPage />} />
        </Routes>
      </Shell>
    </BrowserRouter>
  );
}
