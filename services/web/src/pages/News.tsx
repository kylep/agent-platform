import { Fragment, useEffect, useState } from "react";
import { api, type PendingNews } from "../api";

type Busy = { [id: string]: "approve" | "reject" | undefined };

export default function News() {
  const [items, setItems] = useState<PendingNews[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<Busy>({});
  const [open, setOpen] = useState<string | null>(null);

  function load() {
    setLoading(true);
    api<PendingNews[]>("/api/news/pending")
      .then(setItems)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load pending news."))
      .finally(() => setLoading(false));
  }
  useEffect(load, []);

  async function decide(id: string, action: "approve" | "reject") {
    setBusy((b) => ({ ...b, [id]: action }));
    setError(null);
    try {
      await api(`/api/news/pending/${id}/${action}`, { method: "POST" });
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : `Failed to ${action}.`);
    } finally {
      setBusy((b) => ({ ...b, [id]: undefined }));
    }
  }

  return (
    <div className="page">
      <h1>Pending News</h1>
      <p className="muted">
        News digests the gatherer produced, held for your review before they post to the channel.
        Expand to preview, then approve to post (and remember these stories) or reject to drop them
        (rejected stories may resurface in a later run).
      </p>
      {loading && <p className="muted">Loading…</p>}
      {error && <div className="error">{error}</div>}
      {!loading && !error && items.length === 0 && <p className="muted">Nothing waiting to post.</p>}
      {!loading && items.length > 0 && (
        <table className="table">
          <thead>
            <tr><th>Date</th><th>Channel</th><th>Stories</th><th>Gathered</th><th></th></tr>
          </thead>
          <tbody>
            {items.map((n) => (
              <Fragment key={n.id}>
                <tr>
                  <td>
                    <button className="linkish" onClick={() => setOpen(open === n.id ? null : n.id)}>
                      {open === n.id ? "▾ " : "▸ "}{n.date || "(undated)"}
                    </button>
                  </td>
                  <td className="muted">#{n.channel}</td>
                  <td>{n.item_count}</td>
                  <td className="muted">{n.created_at ? new Date(n.created_at).toLocaleString() : "—"}</td>
                  <td>
                    <div className="row-actions">
                      <button onClick={() => decide(n.id, "approve")} disabled={!!busy[n.id]}>
                        {busy[n.id] === "approve" ? "Posting…" : "Approve"}
                      </button>
                      <button className="secondary" onClick={() => decide(n.id, "reject")} disabled={!!busy[n.id]}>
                        {busy[n.id] === "reject" ? "Rejecting…" : "Reject"}
                      </button>
                    </div>
                  </td>
                </tr>
                {open === n.id && (
                  <tr>
                    <td colSpan={5}><pre className="news-preview">{n.post_text}</pre></td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
