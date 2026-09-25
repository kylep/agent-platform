import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";

type Block = { kind: "heading" | "paragraph" | "metric"; text: string; label: string; value: string;
  source: string | null; field: "total_km" | "runs" | "activities" | "latest_day" | null };
type PublishedView = {
  id: string;
  app_name: string;
  slug: string;
  published_version: number;
  definition: { renderer: "typed/v1"; title: string; blocks: Block[];
    reads: { alias: string; operation: "running.summary.read@1" }[] };
};

// Values are plain React text. No authored HTML, CSS, script or URL is ever
// interpreted by this first private-data renderer.
export default function LiveViewPage() {
  const { id } = useParams();
  const [view, setView] = useState<PublishedView | null>(null);
  const [readData, setReadData] = useState<Record<string, Record<string, unknown>>>({});
  const [readError, setReadError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!id) return;
    api<PublishedView>(`/api/live-views/${encodeURIComponent(id)}`)
      .then(setView).catch((e) => setError(e instanceof Error ? e.message : "Page unavailable."));
  }, [id]);
  useEffect(() => {
    if (!view?.definition.reads.length) return;
    let active = true;
    Promise.all(view.definition.reads.map(async ({ alias }) => [alias, await api<Record<string, unknown>>(
      `/api/live-views/${encodeURIComponent(view.id)}/data/${encodeURIComponent(alias)}`)] as const))
      .then((pairs) => { if (active) setReadData(Object.fromEntries(pairs)); })
      .catch((e) => { if (active) setReadError(e instanceof Error ? e.message : "Live data unavailable."); });
    return () => { active = false; };
  }, [view]);
  if (error) return <div className="page"><h1>Page unavailable</h1><p className="error">{error}</p><Link to="/apps">Apps</Link></div>;
  if (!view) return <div className="page"><p className="muted">Loading page…</p></div>;
  return (
    <div className="page">
      <div className="page-header"><h1>{view.definition.title}</h1></div>
      <p className="muted"><Link to="/apps">Apps</Link> / {view.app_name} / {view.slug}</p>
      {readError && <p className="error">Live data unavailable: {readError}</p>}
      <div className="live-view-blocks">
        {view.definition.blocks.map((block, index) => {
          if (block.kind === "heading") return <h2 key={index}>{block.text}</h2>;
          if (block.kind === "paragraph") return <p key={index}>{block.text}</p>;
          const dynamic = block.source && block.field
            ? readData[block.source]?.[block.field] : null;
          return <div className="live-view-metric" key={index}>
            <span className="muted">{block.label}</span>
            <strong>{block.source ? (dynamic === null || dynamic === undefined ? "—" : String(dynamic)) : block.value}</strong>
          </div>;
        })}
      </div>
    </div>
  );
}
