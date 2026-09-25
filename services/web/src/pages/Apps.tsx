import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type AppView } from "../api";
import { Chip } from "@ap/ui/chip";

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
