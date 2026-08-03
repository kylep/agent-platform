import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type DlqEntry } from "../api";
import { Button } from "@ap/ui/button";
import { Table, TD, TH } from "@ap/ui/table";

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
        <Table>
          <thead>
            <tr><TH>ID</TH><TH>Agent</TH><TH>Error</TH><TH>Failed</TH><TH></TH></tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <TD><Link to={`/runs/${r.id}`}>{r.id.slice(0, 8)}</Link></TD>
                <TD>{r.agent}</TD>
                <TD className="error">{r.error || "—"}</TD>
                <TD className="text-muted">{r.finished_at ? new Date(r.finished_at).toLocaleString() : "—"}</TD>
                <TD>
                  <div className="row-actions">
                    <Button size="sm" onClick={() => act(r.id, "retry")} disabled={!!busy[r.id]}>
                      {busy[r.id] === "retry" ? "Retrying…" : "Retry"}
                    </Button>
                    <Button variant="secondary" size="sm" onClick={() => act(r.id, "discard")} disabled={!!busy[r.id]}>
                      {busy[r.id] === "discard" ? "Discarding…" : "Discard"}
                    </Button>
                  </div>
                </TD>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </div>
  );
}
