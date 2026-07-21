import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type DlqEntry } from "../api";

type Busy = { [id: string]: "retry" | "discard" | undefined };

export default function Dlq() {
  const [rows, setRows] = useState<DlqEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<Busy>({});

  function load() {
    setLoading(true);
    api<DlqEntry[]>("/api/dlq")
      .then(setRows)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load DLQ."))
      .finally(() => setLoading(false));
  }
  useEffect(load, []);

  async function act(id: string, action: "retry" | "discard") {
    setBusy((b) => ({ ...b, [id]: action }));
    setError(null);
    try {
      await api(`/api/dlq/${id}/${action}`, { method: "POST" });
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : `Failed to ${action} ${id.slice(0, 8)}.`);
    } finally {
      setBusy((b) => ({ ...b, [id]: undefined }));
    }
  }

  return (
    <div className="page">
      <h1>Dead-letter queue</h1>
      <p className="muted">
        Runs the dispatcher couldn't launch (after retries). Retry to re-queue a run, or discard to
        drop it. The error column shows why it failed.
      </p>
      {loading && <p className="muted">Loading…</p>}
      {error && <div className="error">{error}</div>}
      {!loading && !error && rows.length === 0 && <p className="muted">Dead-letter queue is empty.</p>}
      {!loading && rows.length > 0 && (
        <table className="table">
          <thead>
            <tr><th>ID</th><th>Agent</th><th>Error</th><th>Failed</th><th></th></tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td><Link to={`/runs/${r.id}`}>{r.id.slice(0, 8)}</Link></td>
                <td>{r.agent}</td>
                <td className="error">{r.error || "—"}</td>
                <td className="muted">{r.finished_at ? new Date(r.finished_at).toLocaleString() : "—"}</td>
                <td>
                  <div className="row-actions">
                    <button onClick={() => act(r.id, "retry")} disabled={!!busy[r.id]}>
                      {busy[r.id] === "retry" ? "Retrying…" : "Retry"}
                    </button>
                    <button className="secondary" onClick={() => act(r.id, "discard")} disabled={!!busy[r.id]}>
                      {busy[r.id] === "discard" ? "Discarding…" : "Discard"}
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
