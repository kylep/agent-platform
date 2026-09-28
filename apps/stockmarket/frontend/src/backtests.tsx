import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, baseCurrency, fmtMetric, fmtMetricByKey, metricLabel,
         type BacktestDetail, type BacktestEvent, type BacktestEventsPage,
         type BacktestListItem, type BacktestMetrics, type BacktestMetricsView,
         type BacktestSeriesView, type BacktestSpecView, type RerunOut,
         type WhoAmI } from "./api";
import { DrawdownChart, ValueContributedChart } from "./chart";
import { Banner } from "@ap/ui/banner";
import { Button } from "@ap/ui/button";
import { ChipButton } from "@ap/ui/chip";
import { ConfirmDialog } from "@ap/ui/dialog";
import { Input } from "@ap/ui/field";
import { Stat, StatRow } from "@ap/ui/stat";
import { Table, TD, TH } from "@ap/ui/table";

// The Backtests view (docs/design/35 T8): list → experiment page → compare
// 2-4. Every number a person reads is either the exact string/number the
// read API returned (fmtMetric) or is explicitly labeled as derived — see
// the caveats in chart.tsx and the "returns by year" note below.

const MAX_EVENT_PAGES = 10;   // 1,000 events; the design has no "return all".
const MAX_COMPARE = 4;

// The headline keys shown per strategy — same list, same order, same labels
// on the experiment page's stat row AND the compare table, so the two views
// can never present the same metric under different names.
const HEADLINE_KEYS = ["xirr", "final_value", "contributed", "twr_annualized",
                       "max_drawdown_depth"] as const;

/** The app can't ask its own backend for the caller's role (that lives on
 * the platform API, not this app's DB), but the platform session cookie is
 * shared across every `/apps/*` page — the same way Shell already calls
 * `/api/apps` directly. `known: false` means the lookup itself failed, in
 * which case the button is shown anyway and a real 403 does the denying. */
function useWhoAmI(): { role: string | null; known: boolean } {
  const [state, setState] = useState<{ role: string | null; known: boolean }>(
    { role: null, known: false });
  useEffect(() => {
    fetch("/api/whoami", { credentials: "include" })
      .then((r) => (r.ok ? r.json() as Promise<WhoAmI> : Promise.reject()))
      .then((w) => setState({ role: w.role, known: true }))
      .catch(() => setState({ role: null, known: false }));
  }, []);
  return state;
}

function canRerun(role: string | null, known: boolean): boolean {
  if (!known) return true;      // role unknowable here — let the 403 speak.
  const r = (role ?? "").toLowerCase();
  return r !== "" && r !== "reader";
}

// --- list --------------------------------------------------------------

export function BacktestsList() {
  const [items, setItems] = useState<BacktestListItem[]>([]);
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const navigate = useNavigate();

  const load = useCallback(() => {
    const qs = new URLSearchParams({ limit: "50" });
    if (q) qs.set("q", q);
    api<BacktestListItem[]>(`/backtests?${qs}`)
      .then((rows) => { setItems(rows); setError(null); })
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load."))
      .finally(() => setLoaded(true));
  }, [q]);
  useEffect(load, [load]);

  function toggle(id: string) {
    setSelected((prev) => prev.includes(id) ? prev.filter((x) => x !== id)
      : prev.length >= MAX_COMPARE ? prev : [...prev, id]);
  }

  return (
    <section aria-label="Backtest experiments">
      <h1>Backtests</h1>
      <div className="sm-bt-toolbar">
        <Input placeholder="Search by name" value={q} aria-label="Search backtests"
               onChange={(e) => setQ(e.target.value)} />
        <Button variant="secondary" disabled={selected.length < 2}
                onClick={() => navigate(`/backtests/compare?ids=${selected.join(",")}`)}>
          Compare selected ({selected.length})
        </Button>
      </div>
      {error && <Banner variant="danger">{error}</Banner>}
      {!loaded && <p className="muted">Loading…</p>}
      {loaded && items.length === 0 && !error && (
        <p className="muted">No backtests yet.</p>
      )}
      {items.length > 0 && (
        <div className="sm-bt-scroll">
          <Table>
            <thead>
              <tr>
                <TH aria-label="Select for comparison" />
                <TH>Name</TH><TH>Created</TH><TH>Strategy</TH><TH>XIRR</TH><TH>Final value</TH>
              </tr>
            </thead>
            <tbody>
              {items.map((it) => {
                const rows = it.strategies.length ? it.strategies : [null];
                return rows.map((s, i) => (
                  <tr key={`${it.id}-${s?.id ?? "none"}`}>
                    {i === 0 && (
                      <TD rowSpan={rows.length}>
                        <input type="checkbox" aria-label={`Select ${it.name} for comparison`}
                               checked={selected.includes(it.id)}
                               disabled={!selected.includes(it.id) && selected.length >= MAX_COMPARE}
                               onChange={() => toggle(it.id)} />
                      </TD>
                    )}
                    {i === 0 && (
                      <TD rowSpan={rows.length}>
                        <Link to={`/backtests/${it.id}`}>{it.name}</Link>
                      </TD>
                    )}
                    {i === 0 && <TD rowSpan={rows.length}>{it.created_at.slice(0, 10)}</TD>}
                    {s ? (
                      <>
                        <TD>{s.label}</TD>
                        <TD>{fmtMetricByKey("xirr", s.xirr)}</TD>
                        <TD>{fmtMetricByKey("final_value", s.final_value)}</TD>
                      </>
                    ) : (
                      <TD colSpan={3} className="muted">No strategies recorded</TD>
                    )}
                  </tr>
                ));
              })}
            </tbody>
          </Table>
        </div>
      )}
    </section>
  );
}

// --- experiment page -----------------------------------------------------

export function BacktestExperimentPage() {
  const { id = "" } = useParams();
  const [detail, setDetail] = useState<BacktestDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [strategyId, setStrategyId] = useState<string | null>(null);
  const [series, setSeries] = useState<BacktestSeriesView | null>(null);
  const [spec, setSpec] = useState<Record<string, unknown> | null>(null);
  const [fullMetrics, setFullMetrics] = useState<Record<string, BacktestMetrics>>({});
  const [events, setEvents] = useState<BacktestEvent[]>([]);
  const [eventsTruncated, setEventsTruncated] = useState(false);
  const [rerunOpen, setRerunOpen] = useState(false);
  const [rerunBusy, setRerunBusy] = useState(false);
  const [rerunMsg, setRerunMsg] = useState<string | null>(null);
  const { role, known } = useWhoAmI();

  useEffect(() => {
    setDetail(null); setError(null); setNotFound(false); setSpec(null); setFullMetrics({});
    api<BacktestDetail>(`/backtests/${encodeURIComponent(id)}`)
      .then((d) => { setDetail(d); setStrategyId(d.strategies[0]?.id ?? null); })
      .catch((e) => {
        if (e instanceof Error && e.message.startsWith("404")) setNotFound(true);
        else setError(e instanceof Error ? e.message : "Failed to load.");
      });
  }, [id]);

  // The bounded detail response carries only headline numbers (docs/design/35
  // T7's repair: a full-spec, full-metrics detail measured 20+ KB). The spec
  // panel and each strategy's "Full metrics" panel fetch the whole thing from
  // their own routes instead.
  useEffect(() => {
    api<BacktestSpecView>(`/backtests/${encodeURIComponent(id)}/spec`)
      .then((s) => setSpec(s.spec))
      .catch(() => setSpec(null));
  }, [id]);

  useEffect(() => {
    if (!detail) return;
    let cancelled = false;
    Promise.all(detail.strategies.map((s) =>
      api<BacktestMetricsView>(
        `/backtests/${encodeURIComponent(id)}/metrics?strategy=${encodeURIComponent(s.id)}`)
        .then((m): [string, BacktestMetrics | null] => [s.id, m.metrics])
        .catch((): [string, BacktestMetrics | null] => [s.id, null])))
      .then((pairs) => {
        if (cancelled) return;
        const next: Record<string, BacktestMetrics> = {};
        for (const [sid, m] of pairs) if (m) next[sid] = m;
        setFullMetrics(next);
      });
    return () => { cancelled = true; };
  }, [id, detail]);

  useEffect(() => {
    if (!strategyId) { setSeries(null); return; }
    api<BacktestSeriesView>(
      `/backtests/${encodeURIComponent(id)}/series?strategy=${encodeURIComponent(strategyId)}&sample=monthly`)
      .then(setSeries)
      .catch(() => setSeries(null));
  }, [id, strategyId]);

  useEffect(() => {
    if (!strategyId) { setEvents([]); setEventsTruncated(false); return; }
    let cancelled = false;
    async function load() {
      const collected: BacktestEvent[] = [];
      let page = 1, truncated = false;
      for (;;) {
        const p: BacktestEventsPage = await api(
          `/backtests/${encodeURIComponent(id)}/events?strategy=${encodeURIComponent(strategyId!)}&page=${page}`);
        collected.push(...p.events);
        if (!p.has_more) break;
        if (page >= MAX_EVENT_PAGES) { truncated = true; break; }
        page += 1;
      }
      if (!cancelled) { setEvents(collected); setEventsTruncated(truncated); }
    }
    load().catch(() => { if (!cancelled) { setEvents([]); setEventsTruncated(false); } });
    return () => { cancelled = true; };
  }, [id, strategyId]);

  // Only when the detail response actually says so (assumed.base_currency) —
  // never guessed from a symbol or a description.
  const currency = useMemo(() => detail ? baseCurrency(detail.assumed) : null, [detail]);

  // The pick timeline: month x symbol, counted from buy events only (a sell
  // is a different decision and the design calls out "picks" specifically).
  const timeline = useMemo(() => {
    const buys = events.filter((e) => e.kind === "buy" && e.symbol);
    const months = [...new Set(buys.map((e) => e.day.slice(0, 7)))].sort();
    const symbols = [...new Set(buys.map((e) => e.symbol as string))].sort();
    const cell = new Map<string, number>();
    for (const e of buys) {
      const key = `${e.day.slice(0, 7)}|${e.symbol}`;
      cell.set(key, (cell.get(key) ?? 0) + 1);
    }
    return { months, symbols, cell };
  }, [events]);

  // "Returns by year": see the note rendered with it — this is year-end
  // value/contributed, not a computed return, because the monthly series
  // carries no intra-year cash-flow timing to derive one honestly.
  const byYear = useMemo(() => {
    const out: { year: string; value: unknown; contributed: unknown }[] = [];
    for (const p of series?.points ?? []) {
      const year = p.day.slice(0, 4);
      if (out.length && out[out.length - 1].year === year) {
        out[out.length - 1] = { year, value: p.value, contributed: p.contributed };
      } else {
        out.push({ year, value: p.value, contributed: p.contributed });
      }
    }
    return out;
  }, [series]);

  async function submitRerun() {
    setRerunBusy(true);
    try {
      const out = await api<RerunOut>(`/backtests/${encodeURIComponent(id)}/rerun`, { method: "POST" });
      setRerunMsg(out.requested
        ? `Rerun requested (run ${out.run_id}). The experiment above is unchanged until it lands.`
        : "The rerun could not be started — the operator key or run API may be unavailable.");
    } catch (e) {
      setRerunMsg(e instanceof Error ? e.message : "Rerun request failed.");
    } finally {
      setRerunBusy(false); setRerunOpen(false);
    }
  }

  return (
    <section aria-label={detail ? `Backtest: ${detail.name}` : "Backtest experiment"}>
      <Link to="/backtests" className="sm-bt-back">← All backtests</Link>
      {notFound && <Banner variant="danger">No backtest experiment found for “{id}”.</Banner>}
      {error && <Banner variant="danger">{error}</Banner>}
      {!detail && !notFound && !error && <p className="muted">Loading…</p>}
      {detail && (
        <>
          <header className="sm-bt-head">
            <h1>{detail.name}</h1>
            <span className="muted">{detail.created_at.slice(0, 10)}</span>
          </header>
          {detail.description && <p>{detail.description}</p>}

          <details className="sm-bt-spec">
            <summary>Spec</summary>
            <div className="sm-bt-scroll">
              <pre className="sm-bt-spec-body">
                {spec ? JSON.stringify(spec, null, 2) : "Loading…"}
              </pre>
            </div>
          </details>

          {detail.strategies.length > 1 && (
            <div className="sm-ranges" role="group" aria-label="Strategy">
              {detail.strategies.map((s) => (
                <ChipButton key={s.id} variant={s.id === strategyId ? "accent" : "neutral"}
                            aria-pressed={s.id === strategyId}
                            onClick={() => setStrategyId(s.id)}>
                  {s.label}
                </ChipButton>
              ))}
            </div>
          )}

          {detail.strategies.map((s) => (
            <div key={s.id} className="sm-bt-strategy-row">
              <h2>{s.label}</h2>
              <div className="sm-bt-scroll">
                <StatRow>
                  {HEADLINE_KEYS.map((key) => (
                    <Stat key={key} label={metricLabel(key)}
                          value={fmtMetricByKey(key, s.metrics[key], currency)} />
                  ))}
                </StatRow>
              </div>
              <details className="sm-bt-spec">
                <summary>Full metrics</summary>
                <div className="sm-bt-scroll">
                  <pre className="sm-bt-spec-body">
                    {fullMetrics[s.id] ? JSON.stringify(fullMetrics[s.id], null, 2) : "Loading…"}
                  </pre>
                </div>
              </details>
            </div>
          ))}

          <section className="sm-chart-section" aria-label="Value vs contributed">
            <h2>Value vs contributed</h2>
            {series ? <ValueContributedChart points={series.points} currency={currency} />
                    : <p className="muted">Loading…</p>}
          </section>

          <section className="sm-chart-section" aria-label="Drawdown">
            <h2>Drawdown</h2>
            {series ? <DrawdownChart points={series.points} />
                    : <p className="muted">Loading…</p>}
          </section>

          <section aria-label="Pick timeline">
            <h2>Pick timeline</h2>
            {timeline.months.length === 0 ? (
              <p className="muted">No buy events recorded{strategyId ? " for this strategy" : ""}.</p>
            ) : (
              <div className="sm-bt-scroll">
                <Table>
                  <thead>
                    <tr>
                      <TH>Month</TH>
                      {timeline.symbols.map((sym) => <TH key={sym}>{sym}</TH>)}
                    </tr>
                  </thead>
                  <tbody>
                    {timeline.months.map((m) => (
                      <tr key={m}>
                        <TD>{m}</TD>
                        {timeline.symbols.map((sym) => {
                          const n = timeline.cell.get(`${m}|${sym}`) ?? 0;
                          return (
                            <TD key={sym} className={n ? "sm-bt-pick" : "muted"}>
                              {n || "—"}
                            </TD>
                          );
                        })}
                      </tr>
                    ))}
                  </tbody>
                </Table>
              </div>
            )}
            {eventsTruncated && (
              <p className="muted sm-note">
                Showing the first {events.length} events for this strategy; more remain
                (page through <code>/backtests/{id}/events</code> for the full log).
              </p>
            )}
          </section>

          <section aria-label="Returns by year">
            <h2>Returns by year</h2>
            <p className="muted sm-note">
              A calendar-year return rate can't be derived honestly from month-end
              value and contributed snapshots alone — that needs the exact date of
              each contribution within the year, which this series doesn't carry.
              Showing year-end value and cumulative contributions instead; XIRR and
              TWR (annualized) above are the authoritative whole-period figures.
            </p>
            {byYear.length > 0 && (
              <div className="sm-bt-scroll">
                <Table>
                  <thead><tr><TH>Year</TH><TH>Value</TH><TH>Contributed</TH></tr></thead>
                  <tbody>
                    {byYear.map((r) => (
                      <tr key={r.year}>
                        <TD>{r.year}</TD>
                        <TD>{fmtMetricByKey("value", r.value, currency)}</TD>
                        <TD>{fmtMetricByKey("contributed", r.contributed, currency)}</TD>
                      </tr>
                    ))}
                  </tbody>
                </Table>
              </div>
            )}
          </section>

          {detail.caveats.length > 0 && (
            <section aria-label="Caveats">
              <h2>Caveats</h2>
              <ul className="sm-bt-list">
                {detail.caveats.map((c) => <li key={c.code}>{c.text}</li>)}
              </ul>
            </section>
          )}

          <section aria-label="Exclusions">
            <h2>Exclusions</h2>
            <p className="muted">
              {detail.exclusions_summary.count === 0
                ? "Nothing excluded."
                : `${detail.exclusions_summary.count} excluded` +
                  (detail.exclusions_summary.symbols.length
                    ? `, including ${detail.exclusions_summary.symbols.join(", ")}.`
                    : ".")}
            </p>
          </section>

          {detail.assumed.length > 0 && (
            <section aria-label="Assumptions">
              <h2>Assumed</h2>
              <ul className="sm-bt-list">
                {detail.assumed.map((a) => (
                  <li key={a.path}><code>{a.path}</code>: {fmtMetric(a.value)}</li>
                ))}
              </ul>
            </section>
          )}

          {detail.report_id && (
            <p><a href={`/reports/${detail.report_id}`}>Full report →</a></p>
          )}

          {canRerun(role, known) && (
            <div className="sm-bt-rerun">
              <Button variant="secondary" onClick={() => setRerunOpen(true)}>
                Re-run on current data
              </Button>
              {rerunMsg && <p className="muted sm-note">{rerunMsg}</p>}
            </div>
          )}
          <ConfirmDialog open={rerunOpen} title="Re-run this backtest?"
                         confirmLabel={rerunBusy ? "Starting…" : "Re-run"}
                         onCancel={() => setRerunOpen(false)}
                         onConfirm={submitRerun}>
            This starts a new stockmarket-data run against fresh prices. The
            experiment stored above does not change until that run completes.
          </ConfirmDialog>
        </>
      )}
    </section>
  );
}

// --- compare (2-4) -------------------------------------------------------

export function BacktestsCompare() {
  const [params] = useSearchParams();
  const ids = useMemo(() => [...new Set(
    (params.get("ids") ?? "").split(",").map((s) => s.trim()).filter(Boolean),
  )].slice(0, MAX_COMPARE), [params]);
  const [byId, setById] = useState<Record<string, BacktestDetail>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});

  useEffect(() => {
    setById({}); setErrors({});
    ids.forEach((id) => {
      api<BacktestDetail>(`/backtests/${encodeURIComponent(id)}`)
        .then((d) => setById((prev) => ({ ...prev, [id]: d })))
        .catch((e) => setErrors((prev) => (
          { ...prev, [id]: e instanceof Error ? e.message : "Failed to load." })));
    });
  }, [ids]);

  // Each column keeps its OWN experiment's currency — two compared backtests
  // can run in different base currencies, and a money figure must never
  // borrow another experiment's code.
  type CompareCol = {
    id: string; label: string;
    metrics: BacktestDetail["strategies"][number]["metrics"];
    currency: string | null;
  };
  const cols: CompareCol[] = ids.flatMap((id) => {
    const d = byId[id];
    if (!d) return [];
    const currency = baseCurrency(d.assumed);
    return d.strategies.map((s) => ({ id, label: `${d.name} — ${s.label}`, metrics: s.metrics, currency }));
  });

  if (ids.length < 2) {
    return (
      <section aria-label="Compare backtests">
        <h1>Compare</h1>
        <Banner variant="danger">
          Pick 2 to {MAX_COMPARE} backtests from the list to compare.
        </Banner>
        <p><Link to="/backtests">← Back to backtests</Link></p>
      </section>
    );
  }

  return (
    <section aria-label="Compare backtests">
      <h1>Compare</h1>
      <Link to="/backtests" className="sm-bt-back">← All backtests</Link>
      {Object.entries(errors).map(([id, msg]) => (
        <Banner key={id} variant="danger">{id}: {msg}</Banner>
      ))}
      {cols.length === 0 ? (
        <p className="muted">Loading…</p>
      ) : (
        <div className="sm-bt-scroll">
          <Table>
            <thead>
              <tr><TH>Metric</TH>{cols.map((c) => <TH key={c.id + c.label}>{c.label}</TH>)}</tr>
            </thead>
            <tbody>
              {/* Same keys, same labels as the experiment page's stat row
                  (HEADLINE_KEYS + metricLabel) — never a raw snake_case key. */}
              {HEADLINE_KEYS.map((key) => (
                <tr key={key}>
                  <TD>{metricLabel(key)}</TD>
                  {cols.map((c) => (
                    <TD key={c.id + c.label}>{fmtMetricByKey(key, c.metrics[key], c.currency)}</TD>
                  ))}
                </tr>
              ))}
            </tbody>
          </Table>
        </div>
      )}
      <p className="sm-bt-list">
        {ids.map((id) => (
          <Link key={id} to={`/backtests/${id}`} className="sm-bt-compare-link">
            {byId[id]?.name ?? id} →
          </Link>
        ))}
      </p>
    </section>
  );
}
