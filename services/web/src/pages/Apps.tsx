import { useEffect, useState } from "react";
import { api, type AppView } from "../api";
import { Chip } from "@ap/ui/chip";

// Apps (docs/design/11): full web-server workloads under apps/<name>/ in the
// repo, deployed like platform services, each owning its declared resources
// (pg schema, kafka topics, scoped platform key). This registry page shows
// what's declared and whether it's live; the app owns its interior UX at
// /apps/<name>/.

function ReadyChip({ app }: { app: AppView }) {
  if (app.error) return <Chip variant="danger">broken</Chip>;
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
        Full applications built on the platform: their own APIs and UIs, their
        own data, driven by agents. Declared by <code>apps/&lt;name&gt;/app.yaml</code>,
        provisioned automatically, served at <code>/apps/&lt;name&gt;/</code>.
      </p>
      {apps.length === 0 && <p className="muted">No apps declared yet.</p>}
      <div className="report-type-grid">
        {apps.map((a) => (
          <div key={a.name} className="app-card">
            <div className="report-type-head">
              <span className="report-type-icon" aria-hidden>{a.icon || "🧩"}</span>
              <span className="report-type-name">{a.name}</span>
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
