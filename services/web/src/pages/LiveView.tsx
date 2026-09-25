import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";

type Block = { kind: "heading" | "paragraph" | "metric" | "action"; text: string; label: string; value: string;
  source: string | null; field: "total_km" | "runs" | "activities" | "latest_day" | null;
  action_alias: string | null };
type PublishedView = {
  id: string;
  app_name: string;
  slug: string;
  published_version: number;
  definition: { renderer: "typed/v1"; title: string; blocks: Block[];
    reads: { alias: string; operation: "running.summary.read@1" }[];
    actions: { alias: string; operation: "tickets.create@1"; channel: string }[] };
};

type ActionIntent = { intent_id: string; target: string; arguments: { title: string; body: string } };
type ActionReceipt = { id: string; status: string; result: { ticket_key?: string; reason?: string } | null };

function TicketAction({ viewId, label, alias, channel }: { viewId: string; label: string; alias: string; channel: string }) {
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [intent, setIntent] = useState<ActionIntent | null>(null);
  const [key, setKey] = useState<string | null>(null);
  const [receipt, setReceipt] = useState<ActionReceipt | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function preview() {
    setBusy(true); setError(null);
    try {
      const created = await api<ActionIntent>(`/api/live-views/${encodeURIComponent(viewId)}/intents`, {
        method: "POST", body: JSON.stringify({ alias, arguments: { title, body } }),
      });
      setIntent(created);
      setKey(crypto.randomUUID().replaceAll("-", ""));
    } catch (e) { setError(e instanceof Error ? e.message : "Could not prepare action."); }
    finally { setBusy(false); }
  }
  async function confirm() {
    if (!intent || !key) return;
    setBusy(true); setError(null);
    try {
      const result = await api<ActionReceipt>(`/api/live-views/${encodeURIComponent(viewId)}/calls`, {
        method: "POST", body: JSON.stringify({ intent_id: intent.intent_id, idempotency_key: key }),
      });
      setReceipt(result);
    } catch (e) {
      // Keep the same intent and key. Retrying cannot create a second ticket.
      setError(e instanceof Error ? e.message : "Outcome unavailable. Retry to get the receipt.");
    } finally { setBusy(false); }
  }
  return <section className="live-view-action">
    <h2>{label || "Create ticket"}</h2>
    {receipt ? <p role="status">{receipt.status === "succeeded"
      ? `Created ${receipt.result?.ticket_key ?? "ticket"}.`
      : `Action ${receipt.status.replaceAll("_", " ")}: ${receipt.result?.reason ?? "Check the ticket board."}`}</p>
      : intent ? <div>
        <p>Send a ticket to {intent.target}?</p>
        <p><strong>{intent.arguments.title}</strong></p>
        {intent.arguments.body && <p>{intent.arguments.body}</p>}
        <button type="button" onClick={confirm} disabled={busy}>{busy ? "Sending…" : "Confirm and send"}</button>{" "}
        <button type="button" onClick={() => { setIntent(null); setKey(null); }} disabled={busy}>Edit</button>
      </div> : <div>
        <p className="muted">Creates a ticket in #{channel}.</p>
        <label>Title<input value={title} maxLength={160} onChange={(e) => setTitle(e.target.value)} /></label>
        <label>Details<textarea value={body} maxLength={4000} onChange={(e) => setBody(e.target.value)} /></label>
        <button type="button" onClick={preview} disabled={busy || !title.trim()}>{busy ? "Preparing…" : "Review ticket"}</button>
      </div>}
    {error && <p role="alert" className="error">{error}</p>}
  </section>;
}

// Values are plain React text. No authored HTML, CSS, script or URL is ever
// interpreted by this first private-data renderer.
export default function LiveViewPage() {
  const { id } = useParams();
  const [view, setView] = useState<PublishedView | null>(null);
  const [readData, setReadData] = useState<Record<string, Record<string, unknown>>>({});
  const [readError, setReadError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [snapshot, setSnapshot] = useState<{ id: string; resource_uri: string } | null>(null);
  const [snapshotBusy, setSnapshotBusy] = useState(false);
  const [snapshotError, setSnapshotError] = useState<string | null>(null);
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
  }, [view, refresh]);
  async function capture() {
    const alias = view?.definition.reads[0]?.alias;
    if (!view || !alias) return;
    setSnapshotBusy(true); setSnapshotError(null);
    try {
      setSnapshot(await api<{ id: string; resource_uri: string }>(
        `/api/live-views/${encodeURIComponent(view.id)}/snapshots`, {
          method: "POST", body: JSON.stringify({ alias }),
        }));
    } catch (e) { setSnapshotError(e instanceof Error ? e.message : "Snapshot unavailable."); }
    finally { setSnapshotBusy(false); }
  }
  if (error) return <div className="page"><h1>Page unavailable</h1><p className="error">{error}</p><Link to="/apps">Apps</Link></div>;
  if (!view) return <div className="page"><p className="muted">Loading page…</p></div>;
  return (
    <div className="page">
      <div className="page-header"><h1>{view.definition.title}</h1></div>
      <p className="muted"><Link to="/apps">Apps</Link> / {view.app_name} / {view.slug}</p>
      {view.definition.reads.length > 0 && <div className="live-view-controls">
        <button type="button" onClick={() => setRefresh((n) => n + 1)}>Refresh data</button>{" "}
        <button type="button" onClick={capture} disabled={snapshotBusy}>
          {snapshotBusy ? "Capturing…" : "Save snapshot"}
        </button>
        {snapshot && <span role="status">Saved snapshot: <code>{snapshot.resource_uri}</code></span>}
        {snapshotError && <span role="alert" className="error">{snapshotError}</span>}
      </div>}
      {readError && <p className="error">Live data unavailable: {readError}</p>}
      <div className="live-view-blocks">
        {view.definition.blocks.map((block, index) => {
          if (block.kind === "heading") return <h2 key={index}>{block.text}</h2>;
          if (block.kind === "paragraph") return <p key={index}>{block.text}</p>;
          if (block.kind === "action") {
            const action = view.definition.actions.find((a) => a.alias === block.action_alias);
            return action ? <TicketAction key={index} viewId={view.id} label={block.label}
              alias={action.alias} channel={action.channel} /> : null;
          }
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
