import { useEffect, useState } from "react";
import { api, type AppView } from "../api";
import { Chip } from "@ap/ui/chip";

// An App is a DB-owned collection (design/33). Existing domain services still
// supply their data and specialized UI while Live Views are introduced.

function ReadyChip({ app }: { app: AppView }) {
  if (app.error) return <Chip variant="danger">broken</Chip>;
  if (!app.source_app) return <Chip variant="neutral">collection</Chip>;
  if (app.ready === null) return <Chip variant="neutral">not deployed</Chip>;
  return app.ready
    ? <Chip variant="ok">running</Chip>
    : <Chip variant="danger">not ready</Chip>;
}

export default function Apps() {
  const [apps, setApps] = useState<AppView[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api<AppView[]>("/api/apps").then(setApps)
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load apps."));
  }, []);
  if (error) return <div className="error">{error}</div>;
  if (!apps) return <p className="muted">Loading…</p>;
  return (
    <>
      <div className="page-header"><h1>Apps</h1></div>
      <p className="muted">
        Apps are named collections of pages and actions. Existing domain services
        still provide their data and specialized screens while live pages are added.
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
            {a.ui && a.ready && (
              <a className="app-open" href={`/apps/${a.name}/`}>Open →</a>
            )}
          </div>
        ))}
      </div>
    </>
  );
}
