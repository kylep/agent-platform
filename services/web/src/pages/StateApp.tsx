import { useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { Chip } from "@ap/ui/chip";
import {
  AppDataError, getStateApp, listAppProposals, pageHref, proposalHref,
  type AppProposal,
  type ApprovedDefinition, type DefinitionDraft, type HealthStatus, type StateAppDetail,
} from "../lib/appData";
import { TypedV2Page } from "./LiveView";

// The builder area for one state App (docs/design/39): what its builder
// published, what it's drafting, the notes it left for the next maintainer,
// and what health found. Read-only — every change goes through `apps`.

const TABS = [
  { id: "pages", label: "Pages" },
  { id: "definitions", label: "Definitions" },
  { id: "proposals", label: "Proposals" },
  { id: "notes", label: "Build notes" },
  { id: "health", label: "Health" },
] as const;
type Tab = typeof TABS[number]["id"];

export function HealthChip({ status }: { status: HealthStatus }) {
  return <Chip variant={status === "ok" ? "ok" : status === "warn" ? "warn" : "danger"}>{status}</Chip>;
}

const when = (at: string | null) => (at ? new Date(at).toLocaleString() : "never");

function bytes(n: number): string {
  if (n >= 1024 ** 2) return `${(n / 1024 ** 2).toLocaleString(undefined, { maximumFractionDigits: 1 })} MiB`;
  if (n >= 1024) return `${(n / 1024).toLocaleString(undefined, { maximumFractionDigits: 1 })} KiB`;
  return `${n} B`;
}

function Json({ value }: { value: unknown }) {
  return <pre className="json-view">{JSON.stringify(value, null, 2)}</pre>;
}

function Approved({ items }: { items: ApprovedDefinition[] }) {
  return <section className="state-app-section" aria-labelledby="approved-h">
    <h2 id="approved-h">Approved</h2>
    {items.length === 0 ? <p className="muted">Nothing published yet.</p> : items.map((d) =>
      <details key={`${d.kind}/${d.name}`} className="definition">
        <summary><Chip>{d.kind}</Chip> <strong>{d.name}</strong> <span className="muted">
          v{d.version} · published by {d.published_by} {when(d.published_at)}</span></summary>
        <Json value={d.definition} />
      </details>)}
  </section>;
}

function Drafts({ items }: { items: DefinitionDraft[] }) {
  return <section className="state-app-section" aria-labelledby="drafts-h">
    <h2 id="drafts-h">Drafts</h2>
    {items.length === 0 ? <p className="muted">No open drafts.</p> : items.map((d) =>
      <details key={`${d.kind}/${d.name}`} className="definition">
        <summary><Chip variant="warn">{d.kind}</Chip> <strong>{d.name}</strong> <span className="muted">
          revision {d.revision} · {d.base_version === null ? "never published" : `based on v${d.base_version}`}
          {" "}· {d.updated_by} {when(d.updated_at)}</span></summary>
        <Json value={d.definition} />
      </details>)}
  </section>;
}

function Pages({ app }: { app: StateAppDetail }) {
  const pages = app.approved.filter((d) => d.kind === "page").map((d) => d.name);
  if (pages.length === 0) return <p className="muted">No published pages yet.</p>;
  const shown = pages.includes("overview") ? "overview" : pages[0];
  return <>
    <nav className="state-app-pages" aria-label="Pages">
      {pages.map((name) => <Link key={name} to={pageHref(app.id, name)}>{name}</Link>)}
    </nav>
    <TypedV2Page appId={app.id} page={shown} embedded />
  </>;
}

function Notes({ app }: { app: StateAppDetail }) {
  const notes = app.build_notes;
  if (!notes) return <p className="muted">No build notes yet.</p>;
  return <>
    <p className="muted">revision {notes.revision} · {notes.updated_by} {when(notes.updated_at)}</p>
    <pre className="build-notes">{notes.text}</pre>
  </>;
}

function Proposals({ app }: { app: StateAppDetail }) {
  const [items, setItems] = useState<AppProposal[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    listAppProposals(app.id).then((rows) => { if (active) setItems(rows); })
      .catch((e: unknown) => { if (active) setError(e instanceof Error ? e.message : "Could not load proposals."); });
    return () => { active = false; };
  }, [app.id]);
  if (error) return <p className="error" role="alert">{error}</p>;
  if (!items) return <p className="muted">Loading proposals…</p>;
  if (!items.length) return <p className="muted">No proposals for this App.</p>;
  return <section className="state-app-section" aria-labelledby="proposals-h">
    <h2 id="proposals-h">Proposals</h2>
    <ul>{items.map((p) => <li key={p.id}>
      <Chip variant={p.state === "open" ? "warn" : "ok"}>{p.state}</Chip>{" "}
      <Link to={proposalHref(app.id, p.id)}>{p.kind} · {p.proposer}</Link>{" "}
      <span className="muted">{when(p.created_at)}</span>
      {p.delta.widening[0] && <div>{p.delta.widening[0]}</div>}
    </li>)}</ul>
  </section>;
}

function Health({ app }: { app: StateAppDetail }) {
  const h = app.health;
  return <>
    <p><HealthChip status={h.status} /> <span className="muted">checked {when(h.checked_at)}</span></p>
    <section className="state-app-section" aria-labelledby="bindings-h">
      <h2 id="bindings-h">Invalid bindings</h2>
      {h.invalid_bindings.length === 0 ? <p className="muted">Every definition validates.</p>
        : <ul>{h.invalid_bindings.map((b) => <li key={`${b.kind}/${b.name}/${b.code}`}>
            <Chip>{b.kind}</Chip> <strong>{b.name}</strong> <code>{b.code}</code> <span>{b.message}</span>
          </li>)}</ul>}
    </section>
    <section className="state-app-section" aria-labelledby="rules-h">
      <h2 id="rules-h">Rule violations</h2>
      {h.rule_violations.length === 0 ? <p className="muted">No record breaks a rule.</p>
        : <ul>{h.rule_violations.map((v) => <li key={`${v.collection}/${v.rule}`}>
            <strong>{v.collection}</strong> <code>{v.rule}</code> <span>
            {v.count.toLocaleString()} {v.count === 1 ? "record" : "records"}
            {v.record_ids.length > 0 && <> ({v.record_ids.join(", ")})</>}</span>
          </li>)}</ul>}
    </section>
    <section className="state-app-section" aria-labelledby="quota-h">
      <h2 id="quota-h">Quota</h2>
      <p>{h.quota.records.toLocaleString()} of {h.quota.records_limit.toLocaleString()} records ·
        {" "}{bytes(h.quota.bytes)} of {bytes(h.quota.bytes_limit)}</p>
    </section>
    <section className="state-app-section" aria-labelledby="ops-h">
      <h2 id="ops-h">Recent build operations</h2>
      {app.build_ops.length === 0 ? <p className="muted">None yet.</p>
        : <ul>{app.build_ops.map((op) => <li key={op.request_id}>
            <Chip variant={op.status === "succeeded" ? "ok" : op.status === "refused" ? "warn" : "danger"}>
              {op.status}</Chip> <strong>{op.action}</strong> <span className="muted">
              {op.actor} {when(op.created_at)}</span>
            <div>{op.summary}</div>
            <code className="muted">{op.request_id}</code>
          </li>)}</ul>}
    </section>
  </>;
}

export default function StateApp() {
  const { id = "" } = useParams();
  const [search, setSearch] = useSearchParams();
  const asked = search.get("tab");
  const tab: Tab = TABS.some((t) => t.id === asked) ? asked as Tab : "pages";
  const [app, setApp] = useState<StateAppDetail | null>(null);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    let active = true;
    setApp(null); setError(null);
    getStateApp(id).then((a) => { if (active) setApp(a); })
      .catch((e) => { if (active) setError(e); });
    return () => { active = false; };
  }, [id]);
  if (error) {
    const status = error instanceof AppDataError ? error.status : 0;
    return <div className="page">
      <h1>{status === 403 ? "No access" : status === 404 ? "App not found" : "App unavailable"}</h1>
      <p className="error">{error instanceof Error ? error.message : "The App couldn't be loaded."}</p>
      <Link to="/apps">Apps</Link>
    </div>;
  }
  if (!app) return <div className="page"><p className="muted">Loading App…</p></div>;
  return <div className="page">
    <div className="page-header"><h1>{app.name}</h1></div>
    <p className="state-app-meta">
      {app.status === "retired" && <Chip>retired</Chip>} <HealthChip status={app.health.status} />
      {" "}<span className="muted"><Link to="/apps">Apps</Link> · owner {app.owner} ·
        {" "}{app.approved_version === null ? "never published" : `approved v${app.approved_version}`}</span>
    </p>
    {app.description && <p className="muted">{app.description}</p>}
    <div className="tabs" role="tablist" aria-label="Builder" tabIndex={0}>
      {TABS.map((t) => <button key={t.id} role="tab" id={`tab-${t.id}`} aria-selected={tab === t.id}
        aria-controls="state-app-panel" className={tab === t.id ? "tab active" : "tab"}
        onClick={() => setSearch(t.id === "pages" ? {} : { tab: t.id }, { replace: true })}>{t.label}</button>)}
    </div>
    <div role="tabpanel" id="state-app-panel" aria-labelledby={`tab-${tab}`} className="state-app-panel">
      {tab === "pages" && <Pages app={app} />}
      {tab === "definitions" && <><Approved items={app.approved} /><Drafts items={app.drafts} /></>}
      {tab === "proposals" && <Proposals app={app} />}
      {tab === "notes" && <Notes app={app} />}
      {tab === "health" && <Health app={app} />}
    </div>
  </div>;
}
