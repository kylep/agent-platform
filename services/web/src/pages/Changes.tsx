import { useEffect, useState } from "react";
import { api, type PullRequest } from "../api";

type Busy = { [n: number]: "merge" | "close" | undefined };

export default function Changes() {
  const [prs, setPrs] = useState<PullRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<Busy>({});

  function load() {
    setLoading(true);
    api<PullRequest[]>("/api/pull-requests")
      .then(setPrs)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load changes."))
      .finally(() => setLoading(false));
  }

  useEffect(load, []);

  async function act(number: number, action: "merge" | "close") {
    setBusy((b) => ({ ...b, [number]: action }));
    setError(null);
    try {
      await api(`/api/pull-requests/${number}/${action}`, { method: "POST" });
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : `Failed to ${action} #${number}.`);
    } finally {
      setBusy((b) => ({ ...b, [number]: undefined }));
    }
  }

  return (
    <div className="page">
      <h1>Pending Changes</h1>
      <p className="muted">
        Pull requests the platform opened editing its own agents. Merge to apply, or close to discard.
      </p>
      {loading && <p className="muted">Loading…</p>}
      {error && <div className="error">{error}</div>}
      {!loading && !error && prs.length === 0 && <p className="muted">No pending changes.</p>}
      {!loading && prs.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th>#</th>
              <th>Title</th>
              <th>Branch</th>
              <th>Author</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {prs.map((pr) => (
              <tr key={pr.number}>
                <td>
                  <a href={pr.url} target="_blank" rel="noreferrer">#{pr.number}</a>
                </td>
                <td>{pr.title}</td>
                <td className="muted">{pr.branch}</td>
                <td className="muted">{pr.author}</td>
                <td className="row-actions">
                  <button onClick={() => act(pr.number, "merge")} disabled={!!busy[pr.number]}>
                    {busy[pr.number] === "merge" ? "Merging…" : "Merge"}
                  </button>
                  <button className="secondary" onClick={() => act(pr.number, "close")} disabled={!!busy[pr.number]}>
                    {busy[pr.number] === "close" ? "Closing…" : "Close"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
