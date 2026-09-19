import { useEffect, useState } from "react";
import { BrowserRouter, Link, NavLink, Route, Routes, useParams,
         useSearchParams } from "react-router-dom";
import { Chip } from "@ap/ui/chip";
import { Input, Select } from "@ap/ui/field";
import { buildPlatformNav, SideNav, type AppNavInfo } from "@ap/ui/sidenav";
import { Stat, StatRow } from "@ap/ui/stat";
import { Table, TD, TH } from "@ap/ui/table";
import { agentFaces, api, LAYERS, type AgentFaces, type CaseDetail, type CaseList,
         type FlakyRef, type Overview, type PruneCandidate, type Run, type RunDetail,
         type SlowRef } from "./api";
import { AgentFace, Card, DurationBar, fmtMs, fmtSeconds, fmtWhen, pct, Pyramid,
         ResultChip, shortSha, Sparkline, StatusDot, TotalsChips, VerifyMark } from "./components";

// The TCMS browser (docs/design/25): four pages over the app's read API.
//   /            Overview — the pyramid, pass rate, coverage, what needs attention
//   /runs        every recorded run; /runs/:id one run, grouped by file, failures first
//   /cases       the case catalogue with its filters in the URL; /cases/:key one case
//   /health      the slowest, the flaky, and what could be pruned

const CASES_REPO = "https://github.com/kylep/agent-platform/blob/main/";
const HISTORY_DOTS = 10;

// --- shell -----------------------------------------------------------------------------

function Shell({ children }: { children: React.ReactNode }) {
  // The shared platform sidebar (from @ap/ui) wraps the app — same chrome as
  // the console, with this app active under the Apps accordion. Platform
  // links are plain anchors (leaving the app is a full page load by design).
  const [apps, setApps] = useState<AppNavInfo[]>([{ name: "tcms", icon: "🧪" }]);
  useEffect(() => {
    fetch("/api/apps", { credentials: "include" })
      .then((r) => (r.ok ? r.json() : []))
      .then((all: { name: string; icon: string; ui: boolean; ready: boolean | null }[]) =>
        setApps(all.filter((a) => a.ui && a.ready)))
      .catch(() => {});
  }, []);
  const tab = ({ isActive }: { isActive: boolean }) => `tc-tab${isActive ? " active" : ""}`;
  return (
    <div className="layout">
      <SideNav entries={buildPlatformNav(apps)} activePath="/apps/tcms/" />
      <main className="main">
        <div className="tc-shell">
          <header className="tc-top">
            <Link to="/" className="tc-brand">🧪 TCMS</Link>
            <nav className="tc-tabs" aria-label="TCMS pages">
              <NavLink to="/" end className={tab}>Overview</NavLink>
              <NavLink to="/runs" className={tab}>Runs</NavLink>
              <NavLink to="/cases" className={tab}>Cases</NavLink>
              <NavLink to="/health" className={tab}>Health</NavLink>
            </nav>
          </header>
          {children}
        </div>
      </main>
    </div>
  );
}

/** One fetch per page: `null` while loading, the error string on failure. */
function useLoad<T>(load: () => Promise<T>, deps: unknown[]): { data: T | null; error: string | null } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    setData(null); setError(null);
    load().then((d) => { if (live) setData(d); })
      .catch((e) => { if (live) setError(e instanceof Error ? e.message : "Failed to load."); });
    return () => { live = false; };
  }, deps);
  return { data, error };
}

function useFaces(): AgentFaces {
  const [faces, setFaces] = useState<AgentFaces>({});
  useEffect(() => { agentFaces().then(setFaces); }, []);
  return faces;
}

function Status({ error, loading }: { error: string | null; loading: boolean }) {
  if (error) return <div className="error">{error}</div>;
  if (loading) return <p className="muted">Loading…</p>;
  return null;
}

// --- overview -----------------------------------------------------------------------

function OverviewPage() {
  const { data, error } = useLoad(() => api<Overview>("/overview"), []);
  const faces = useFaces();
  if (!data) return <Status error={error} loading />;
  const { attention, latest_run: latest, pass_rate, coverage } = data;
  const rates = pass_rate.map((p) => p.rate);
  const last = pass_rate[pass_rate.length - 1];
  const covTrend = coverage.trend;
  const prevCov = covTrend.length > 1 ? covTrend[covTrend.length - 2].pct : null;
  const delta = prevCov == null ? null : coverage.pct - prevCov;
  return (
    <>
      <h1>Overview</h1>
      <StatRow>
        <Stat label="Failing now" value={attention.failing} warn={attention.failing > 0}
              to={latest ? `/runs/${latest.id}` : "/runs"} />
        <Stat label="Flaky" value={attention.flaky} warn={attention.flaky > 0} to="/health#flaky" />
        <Stat label="Unlinked cases" value={attention.unlinked} to="/cases?unlinked=1" />
        <Stat label="Prune candidates" value={attention.prune_candidates} to="/health#prune" />
      </StatRow>

      <div className="tc-cols">
        <Card title="Test pyramid"
              aside={latest ? <>latest run <Link to={`/runs/${latest.id}`}>#{latest.id}</Link></> : undefined}>
          <Pyramid layers={data.pyramid} />
        </Card>

        <div className="tc-stack">
          <Card title="Pass rate" aside={`last ${pass_rate.length || 0} runs`}>
            <div className="tc-metric">
              <span className="tc-metric-value">{pct(last?.rate)}</span>
              <span className="tc-metric-sub muted">
                {last ? `${last.pass} of ${last.total} on ${shortSha(last.commit_sha)}` : "no runs yet"}
              </span>
            </div>
            <Sparkline values={rates} max={1} className="tc-spark-wide"
                       titles={pass_rate.map((p) =>
                         `${shortSha(p.commit_sha)} · ${pct(p.rate)} · ${p.pass}/${p.total} · ${fmtWhen(p.started_at)}`)} />
          </Card>

          <Card title="Coverage" aside={coverage.lines_total ? `${coverage.lines_covered.toLocaleString()} / ${coverage.lines_total.toLocaleString()} lines` : undefined}>
            <div className="tc-metric">
              <span className="tc-metric-value">{coverage.lines_total ? `${coverage.pct.toFixed(1)}%` : "—"}</span>
              {delta != null && (
                <span className={`tc-metric-sub ${delta < 0 ? "tc-down" : "tc-up"}`}>
                  {delta >= 0 ? "▲" : "▼"} {Math.abs(delta).toFixed(1)} pts vs previous
                </span>
              )}
              {!coverage.lines_total && <span className="tc-metric-sub muted">no coverage recorded</span>}
            </div>
            {covTrend.length > 0 && (
              <Sparkline values={covTrend.map((c) => c.pct)} className="tc-spark-wide"
                         titles={covTrend.map((c) => `${shortSha(c.commit_sha)} · ${c.pct.toFixed(1)}% · ${fmtWhen(c.started_at)}`)} />
            )}
          </Card>
        </div>
      </div>

      {latest && (
        <Card title="Latest run" aside={<Link to={`/runs/${latest.id}`}>open</Link>}>
          <RunLine run={latest} faces={faces} />
        </Card>
      )}
    </>
  );
}

function RunLine({ run, faces }: { run: Run; faces: AgentFaces }) {
  return (
    <div className="tc-runline">
      <code className="tc-sha" title={run.commit_sha}>{shortSha(run.commit_sha)}</code>
      {run.branch && <span className="muted">{run.branch}</span>}
      <AgentFace name={run.agent} faces={faces} />
      <span className="muted" title={run.started_at ?? undefined}>{fmtWhen(run.started_at)}</span>
      <span>{fmtSeconds(run.seconds)}</span>
      <TotalsChips totals={run.totals} />
      <span>verify <VerifyMark ok={run.verify_ok} /></span>
      {run.unlinked > 0 && <span className="muted">{run.unlinked} unlinked</span>}
    </div>
  );
}

// --- runs ------------------------------------------------------------------------------

function RunsPage() {
  const { data, error } = useLoad(() => api<Run[]>("/runs?limit=100"), []);
  const faces = useFaces();
  return (
    <>
      <h1>Runs</h1>
      <Status error={error} loading={!data} />
      {data && data.length === 0 && (
        <Card><p className="muted">No runs recorded yet — the <code>tcms</code> tool's <code>record_results</code> writes the first one.</p></Card>
      )}
      {data && data.length > 0 && (
        <Card>
          <Table>
            <thead>
              <tr>
                <TH>Run</TH><TH>Commit</TH><TH>Agent</TH><TH>When</TH><TH>Duration</TH>
                <TH>Results</TH><TH>Verify</TH>
              </tr>
            </thead>
            <tbody>
              {data.map((r) => (
                <tr key={r.id}>
                  <TD><Link to={`/runs/${r.id}`}>#{r.id}</Link></TD>
                  <TD>
                    <code className="tc-sha" title={r.commit_sha}>{shortSha(r.commit_sha)}</code>
                    {r.branch && <span className="muted tc-branch"> {r.branch}</span>}
                  </TD>
                  <TD><AgentFace name={r.agent} faces={faces} /></TD>
                  <TD className="tc-nowrap" title={r.started_at ?? undefined}>{fmtWhen(r.started_at)}</TD>
                  <TD className="tc-nowrap tc-num">{fmtSeconds(r.seconds)}</TD>
                  <TD><TotalsChips totals={r.totals} /></TD>
                  <TD><VerifyMark ok={r.verify_ok} /></TD>
                </tr>
              ))}
            </tbody>
          </Table>
        </Card>
      )}
    </>
  );
}

function RunPage() {
  const { id } = useParams();
  const { data, error } = useLoad(() => api<RunDetail>(`/runs/${id}`), [id]);
  const faces = useFaces();
  if (!data) return <Status error={error} loading />;
  const failing = data.groups.filter((g) => g.failures > 0).length;
  return (
    <>
      <p className="tc-crumb"><Link to="/runs">Runs</Link> / #{data.id}</p>
      <h1>Run #{data.id}</h1>
      <Card>
        <RunLine run={data} faces={faces} />
        {data.suites.length > 0 && (
          <div className="tc-suites">
            {data.suites.map((s, i) => (
              <Chip key={i} variant={s.exit === 0 ? "ok" : s.exit == null ? "neutral" : "danger"}>
                {s.name ?? `suite ${i + 1}`} · {fmtSeconds(s.seconds ?? 0)}
              </Chip>
            ))}
          </div>
        )}
      </Card>

      {data.groups.length === 0 && <p className="muted">No results in this run.</p>}
      {failing > 0 && (
        <p className="muted">{failing} of {data.groups.length} files have failures — they come first.</p>
      )}
      {data.groups.map((g) => (
        <Card key={g.file} className={g.failures > 0 ? "tc-group-failing" : ""}
              title={g.file || "(no file)"}
              aside={`${g.failures > 0 ? `${g.failures} failing · ` : ""}${plural(g.results.length, "result")}`}>
          <ul className="tc-results">
            {g.results.map((r) => (
              <li key={r.id} className={`tc-result tc-result-${r.status}`}>
                <div className="tc-result-head">
                  <ResultChip status={r.status} />
                  <span className="tc-result-name">{r.name}</span>
                  {r.case_key
                    ? <Link to={`/cases/${r.case_key}`} className="tc-case-key">{r.case_key}</Link>
                    : <span className="muted tc-case-key">unlinked</span>}
                  <span className="muted tc-num">{fmtMs(r.duration_ms)}</span>
                </div>
                {r.message && <pre className="tc-msg">{r.message}</pre>}
              </li>
            ))}
          </ul>
        </Card>
      ))}
    </>
  );
}

// --- cases ------------------------------------------------------------------------------

function CasesPage() {
  // The filters live in the URL so a filtered list is a link: the Overview's
  // "unlinked" tile, a ticket, a Relay line can all point at one.
  const [params, setParams] = useSearchParams();
  const layer = params.get("layer") ?? "";
  const area = params.get("area") ?? "";
  const status = params.get("status") ?? "";
  const automation = params.get("automation") ?? "";
  const unlinked = params.get("unlinked") === "1";
  const q = params.get("q") ?? "";
  const [draft, setDraft] = useState(q);
  useEffect(() => setDraft(q), [q]);

  const query = new URLSearchParams();
  if (layer) query.set("layer", layer);
  if (area) query.set("area", area);
  if (status) query.set("status", status);
  if (automation) query.set("automation", automation);
  if (unlinked) query.set("unlinked", "true");
  if (q) query.set("q", q);
  query.set("limit", "200");
  const qs = query.toString();
  const { data, error } = useLoad(() => api<CaseList>(`/cases?${qs}`), [qs]);

  function set(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value); else next.delete(key);
    setParams(next);
  }
  const anyFilter = layer || area || status || automation || unlinked || q;

  return (
    <>
      <h1>Cases</h1>
      <form className="tc-filters" onSubmit={(e) => { e.preventDefault(); set("q", draft.trim()); }}>
        <Input placeholder="Search key or title…" value={draft} aria-label="Search cases"
               onChange={(e) => setDraft(e.target.value)} />
        <Select value={layer} aria-label="Layer" onChange={(e) => set("layer", e.target.value)}>
          <option value="">any layer</option>
          {LAYERS.map((l) => <option key={l} value={l}>{l}</option>)}
        </Select>
        <Input placeholder="area" value={area} aria-label="Area" className="tc-filter-area"
               onChange={(e) => set("area", e.target.value)} />
        <Select value={status} aria-label="Status" onChange={(e) => set("status", e.target.value)}>
          <option value="">any status</option>
          <option value="active">active</option>
          <option value="retired">retired</option>
        </Select>
        <Select value={automation} aria-label="Automation" onChange={(e) => set("automation", e.target.value)}>
          <option value="">automated or manual</option>
          <option value="automated">automated</option>
          <option value="manual">manual</option>
        </Select>
        <label className="tc-check">
          <input type="checkbox" checked={unlinked} onChange={(e) => set("unlinked", e.target.checked ? "1" : "")} />
          unlinked only
        </label>
        {anyFilter && <Link to="/cases" className="tc-clear">clear</Link>}
      </form>

      <Status error={error} loading={!data} />
      {data && (
        <Card aside={`showing ${data.cases.length} of ${data.total}`} title="Catalogue">
          {data.cases.length === 0 && <p className="muted">No cases match.</p>}
          {data.cases.length > 0 && (
            <Table>
              <thead>
                <tr>
                  <TH>Key</TH><TH>Title</TH><TH>Layer</TH><TH>Area</TH><TH>Pri</TH>
                  <TH>Status</TH><TH>Refs</TH><TH>Last</TH>
                </tr>
              </thead>
              <tbody>
                {data.cases.map((c) => (
                  <tr key={c.key}>
                    <TD className="tc-nowrap"><Link to={`/cases/${c.key}`}><code className="tc-key">{c.key}</code></Link></TD>
                    <TD>{c.title}</TD>
                    <TD className="tc-nowrap">{c.layer}</TD>
                    <TD className="tc-nowrap">{c.area}</TD>
                    <TD className="tc-nowrap">{c.priority}</TD>
                    <TD><Chip variant={c.status === "active" ? "ok" : "neutral"}>{c.status}</Chip></TD>
                    <TD className="tc-num">
                      {c.layer === "manual" ? <span className="muted">manual</span>
                        : c.automation.length || <span className="tc-verify-bad" title="no automation ref">0</span>}
                    </TD>
                    <TD><StatusDot status={c.last_status} title={c.last_status ?? "no result yet"} /></TD>
                  </tr>
                ))}
              </tbody>
            </Table>
          )}
        </Card>
      )}
    </>
  );
}

function CasePage() {
  const { key } = useParams();
  const { data, error } = useLoad(() => api<CaseDetail>(`/cases/${key}`), [key]);
  if (!data) return <Status error={error} loading />;
  return (
    <>
      <p className="tc-crumb"><Link to="/cases">Cases</Link> / <code className="tc-key">{data.key}</code></p>
      <h1>{data.title}</h1>
      <div className="tc-chips tc-case-chips">
        <Chip variant={data.status === "active" ? "ok" : "neutral"}>{data.status}</Chip>
        <Chip variant="accent">{data.layer}</Chip>
        <Chip>{data.priority}</Chip>
        <Chip>{data.area}</Chip>
        {data.tags.map((t) => <Chip key={t}>#{t}</Chip>)}
      </div>

      <div className="tc-cols">
        <Card title="Steps">
          {data.preconditions.length > 0 && (
            <>
              <h3 className="tc-h3">Preconditions</h3>
              <ul className="tc-list">{data.preconditions.map((p, i) => <li key={i}>{p}</li>)}</ul>
            </>
          )}
          {data.steps.length > 0
            ? <ol className="tc-list tc-steps">{data.steps.map((s, i) => <li key={i}>{s}</li>)}</ol>
            : <p className="muted">No steps written.</p>}
          <h3 className="tc-h3">Expected</h3>
          <p className="tc-expected">{data.expected || <span className="muted">—</span>}</p>
        </Card>

        <Card title="Automation"
              aside={<a href={CASES_REPO + data.file} target="_blank" rel="noreferrer">{data.file} ↗</a>}>
          {data.refs.length === 0 && (
            <p className="muted">
              {data.layer === "manual" ? "A manual case: no automation, by design."
                : "No automation ref — this case is unlinked."}
            </p>
          )}
          <ul className="tc-refs">
            {data.refs.map((r) => {
              const recent = r.results.slice(0, HISTORY_DOTS).reverse();
              return (
                <li key={r.ref}>
                  <div className="tc-ref-name">
                    <span className="muted">{r.path}</span>
                    <b>{r.name}</b>
                  </div>
                  <div className="tc-dots" aria-label={`last ${recent.length} results`}>
                    {recent.length === 0 && <span className="muted">never run</span>}
                    {recent.map((x, i) => (
                      <StatusDot key={i} status={x.status}
                                 title={`${x.status} · ${fmtMs(x.duration_ms)} · ${shortSha(x.commit_sha)} · ${fmtWhen(x.started_at)}`} />
                    ))}
                  </div>
                </li>
              );
            })}
          </ul>
          {data.tickets.length > 0 && (
            <p className="muted tc-tickets">tickets: {data.tickets.join(", ")}</p>
          )}
          <p className="muted tc-synced">
            synced {fmtWhen(data.synced_at)}{data.source_sha && <> from <code className="tc-sha">{shortSha(data.source_sha)}</code></>}
          </p>
        </Card>
      </div>
    </>
  );
}

// --- health -----------------------------------------------------------------------------

function HealthPage() {
  const { data, error } = useLoad(() => Promise.all([
    api<SlowRef[]>("/slowest?limit=15"),
    api<FlakyRef[]>("/flaky"),
    api<PruneCandidate[]>("/prune-candidates"),
  ]), []);
  // Deep links from the Overview tiles land on the section, once it exists.
  useEffect(() => {
    if (!data || !location.hash) return;
    document.getElementById(location.hash.slice(1))?.scrollIntoView();
  }, [data]);
  if (!data) return <Status error={error} loading />;
  const [slowest, flaky, prune] = data;
  const maxAvg = Math.max(1, ...slowest.map((s) => s.avg_ms));
  return (
    <>
      <h1>Health</h1>

      <Card id="slowest" title="Slowest" aside="average over the last 30 runs">
        {slowest.length === 0 && <p className="muted">No results yet.</p>}
        {slowest.length > 0 && (
          <Table>
            <thead>
              <tr><TH>Test</TH><TH>Layer</TH><TH>Average</TH><TH>Max</TH><TH>Runs</TH><TH>Over time</TH></tr>
            </thead>
            <tbody>
              {slowest.map((s) => (
                <tr key={s.ref}>
                  <TD><RefCell path={s.path} name={s.name} /></TD>
                  <TD className="tc-nowrap">{s.layer ?? "—"}</TD>
                  <TD className="tc-bar-cell"><DurationBar value={s.avg_ms} max={maxAvg} label={fmtMs(s.avg_ms)} /></TD>
                  <TD className="tc-nowrap tc-num">{fmtMs(s.max_ms)}</TD>
                  <TD className="tc-num">{s.n}</TD>
                  <TD>
                    <Sparkline values={s.history.map((h) => h.duration_ms)} width={120} height={28} min={0}
                               titles={s.history.map((h) => `${fmtMs(h.duration_ms)} · ${h.status} · ${shortSha(h.commit_sha)}`)} />
                  </TD>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>

      <Card id="flaky" title="Flaky" aside="failed then passed on one commit, or alternating across commits">
        {flaky.length === 0 && <p className="muted">Nothing flaky in the window.</p>}
        {flaky.length > 0 && (
          <Table>
            <thead>
              <tr><TH>Test</TH><TH>Fails</TH><TH>Passes</TH><TH>Flaky</TH><TH>Failing commits</TH><TH>Same-commit flips</TH></tr>
            </thead>
            <tbody>
              {flaky.map((f) => (
                <tr key={f.ref}>
                  <TD><RefCell path={f.path} name={f.name} /></TD>
                  <TD className="tc-num tc-verify-bad">{f.fails}</TD>
                  <TD className="tc-num">{f.passes}</TD>
                  <TD className="tc-num">{f.flaky}</TD>
                  <TD className="tc-num">{f.failing_commits}</TD>
                  <TD className="tc-num">{f.same_commit_flips}</TD>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>

      <Card id="prune" title="Prune candidates" aside="what the QA may retire, and why">
        {prune.length === 0 && <p className="muted">Nothing to prune.</p>}
        {prune.length > 0 && (
          <Table>
            <thead>
              <tr><TH>Test</TH><TH>Case</TH><TH>Average</TH><TH>Runs</TH><TH>Fails</TH><TH>Reason</TH></tr>
            </thead>
            <tbody>
              {prune.map((p) => (
                <tr key={p.ref}>
                  <TD><RefCell path={p.path} name={p.name} /></TD>
                  <TD className="tc-nowrap">
                    {p.case_key ? <Link to={`/cases/${p.case_key}`}><code className="tc-key">{p.case_key}</code></Link>
                      : <span className="muted">unlinked</span>}
                  </TD>
                  <TD className="tc-nowrap tc-num">{fmtMs(p.avg_ms)}</TD>
                  <TD className="tc-num">{p.n}</TD>
                  <TD className="tc-num">{p.fails}</TD>
                  <TD><Chip variant={p.reason === "retired-case" ? "neutral" : "warn"}>{p.reason}</Chip></TD>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </>
  );
}

function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

function RefCell({ path, name }: { path: string; name: string }) {
  return (
    <span className="tc-ref-name">
      <span className="muted">{path}</span>
      <b>{name}</b>
    </span>
  );
}

// --- app ------------------------------------------------------------------------------------

export default function App() {
  return (
    <BrowserRouter basename="/apps/tcms">
      <Shell>
        <Routes>
          <Route path="/" element={<OverviewPage />} />
          <Route path="/runs" element={<RunsPage />} />
          <Route path="/runs/:id" element={<RunPage />} />
          <Route path="/cases" element={<CasesPage />} />
          <Route path="/cases/:key" element={<CasePage />} />
          <Route path="/health" element={<HealthPage />} />
        </Routes>
      </Shell>
    </BrowserRouter>
  );
}
