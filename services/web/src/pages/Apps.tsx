import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type AppView } from "../api";
import { Chip } from "@ap/ui/chip";
import { listStateApps, type StateAppSummary } from "../lib/appData";
import { HealthChip } from "./StateApp";

// An App is a DB-owned collection (design/33). Published pages are its entry
// point; existing domain services still supply data and specialized controls.

function ReadyChip({ app }: { app: AppView }) {
  if (app.error) return <Chip variant="danger">broken</Chip>;
  if (!app.source_app) return <Chip variant="neutral">collection</Chip>;
  if (app.ready === null) return <Chip variant="neutral">not deployed</Chip>;
  return app.ready
    ? <Chip variant="ok">running</Chip>
    : <Chip variant="danger">not ready</Chip>;
}

type LivePage = { id: string; slug: string; published_version: number | null };

function AppPages({ appName, canEdit, legacyAvailable }: {
  appName: string; canEdit: boolean; legacyAvailable: boolean;
}) {
  const [pages, setPages] = useState<LivePage[] | null>(null);
  useEffect(() => {
    api<LivePage[]>(`/api/live-views?app_name=${encodeURIComponent(appName)}`)
      .then((all) => setPages(all.filter((page) => page.published_version !== null)))
      .catch(() => setPages([]));
  }, [appName]);
  if (pages === null) return null;
  const [primary, ...others] = [...pages].sort((a, b) =>
    (a.slug === "overview" ? -1 : b.slug === "overview" ? 1 : a.slug.localeCompare(b.slug)));
  return <>
    {primary ? <Link className="app-open" to={`/live-views/${primary.id}`}>Open {primary.slug} →</Link>
      : legacyAvailable ? <a className="app-open" href={`/apps/${appName}/`}>Open →</a> : null}
    {(others.length > 0 || legacyAvailable || canEdit) &&
      <div className="app-resources">
        {others.map((page) => <span key={page.id}>
          <Link to={`/live-views/${page.id}`}>{page.slug} →</Link>
          {canEdit && <> · <Link to={`/live-views/${page.id}/edit`}>edit</Link></>}
        </span>)}
        {primary && canEdit && <Link to={`/live-views/${primary.id}/edit`}>Edit {primary.slug}</Link>}
        {legacyAvailable && primary && <a href={`/apps/${appName}/`}>Detailed app →</a>}
        {canEdit && <Link to={`/live-views/new?app=${encodeURIComponent(appName)}`}>+ New live page</Link>}
      </div>}
  </>;
}

// State Apps (docs/design/39) are rows in `app_data`, built by agents; the
// collections below are the coded catalogue they replace, listed until it
// has migrated.
function StateApps() {
  const [apps, setApps] = useState<StateAppSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    listStateApps().then(setApps)
      .catch((e) => setError(e instanceof Error ? e.message : "State Apps unavailable."));
  }, []);
  return <section className="state-apps" aria-labelledby="state-apps-h">
    <h2 id="state-apps-h">State Apps</h2>
    {error ? <p className="error">State Apps unavailable: {error}</p>
      : !apps ? <p className="muted">Loading…</p>
      : apps.length === 0 ? <p className="muted">No state Apps yet.</p>
      : <div className="report-type-grid">{apps.map((a) => (
        <div key={a.id} className="app-card state-app-card">
          <div className="report-type-head">
            <Link className="report-type-name" to={`/apps/state/${encodeURIComponent(a.id)}`}>{a.name}</Link>
            {a.status === "retired" ? <Chip>retired</Chip> : <HealthChip status={a.health.status} />}
          </div>
          <p className="muted report-type-desc">{a.description || "—"}</p>
          <div className="app-resources muted">
            <span>owner {a.owner}</span>
            <span>{a.approved_version === null ? "never published" : `v${a.approved_version}`}</span>
            {a.health.issues > 0 && <span>{a.health.issues} {a.health.issues === 1 ? "issue" : "issues"}</span>}
            {a.approved_version !== null &&
              <Link to={`/apps/state/${encodeURIComponent(a.id)}/pages/home`}>Open {a.name} →</Link>}
          </div>
        </div>))}</div>}
  </section>;
}

export default function Apps() {
  const [apps, setApps] = useState<AppView[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [canEdit, setCanEdit] = useState(false);
  useEffect(() => {
    api<AppView[]>("/api/apps").then(setApps)
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load apps."));
    api<{ role: string }>("/api/whoami").then((me) => setCanEdit(me.role === "admin"))
      .catch(() => setCanEdit(false));
  }, []);
  if (error) return <div className="error">{error}</div>;
  if (!apps) return <p className="muted">Loading…</p>;
  return (
    <>
      <div className="page-header"><h1>Apps</h1></div>
      <p className="muted">
        Apps are collections of pages and actions. Open a published page here,
        or use its detailed app for specialized controls.
      </p>
      <StateApps />
      <h2>Collections</h2>
      {apps.length === 0 && <p className="muted">No apps declared yet.</p>}
      <div className="report-type-grid">
        {apps.map((a) => (
          <div key={a.name} className="app-card">
            <div className="report-type-head">
              <span className="report-type-icon" aria-hidden>{a.icon || "🧩"}</span>
              <span className="report-type-name">{a.display_name || a.name}</span>
              <ReadyChip app={a} />
            </div>
            <p className="muted report-type-desc">{a.description || "—"}</p>
            {a.error ? (
              <pre className="error app-error">{a.error}</pre>
            ) : (
              <div className="app-resources muted">
                {a.postgres && <span>schema app_{a.name.replace(/-/g, "_")}</span>}
                {a.kafka_topics.length > 0 && <span>{a.kafka_topics.length} kafka {a.kafka_topics.length === 1 ? "topic" : "topics"}</span>}
                {a.agent_key_role && <span>key: {a.agent_key_role}</span>}
                {a.redis && <span>redis</span>}
              </div>
            )}
            <AppPages appName={a.name} canEdit={canEdit}
              legacyAvailable={Boolean(a.ui && a.ready)} />
          </div>
        ))}
      </div>
    </>
  );
}
