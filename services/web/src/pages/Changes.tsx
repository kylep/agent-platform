import { Fragment, useEffect, useState } from "react";
import { api, type PullRequest, type PullRequestFile } from "../api";

type Busy = { [n: number]: "merge" | "close" | undefined };

function Diff({ number }: { number: number }) {
  const [files, setFiles] = useState<PullRequestFile[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api<PullRequestFile[]>(`/api/pull-requests/${number}/files`)
      .then(setFiles)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load diff."));
  }, [number]);
  if (error) return <div className="error">{error}</div>;
  if (!files) return <p className="muted">Loading diff…</p>;
  if (files.length === 0) return <p className="muted">No file changes.</p>;
  return (
    <div>
      {files.map((f) => (
        <div key={f.filename} className="diff-file">
          <div className="diff-file-head">
            {f.filename} <span className="muted">+{f.additions} −{f.deletions} ({f.status})</span>
          </div>
          <pre className="diff-patch">{f.patch || "(no textual diff)"}</pre>
        </div>
      ))}
    </div>
  );
}

export default function Changes() {
  const [prs, setPrs] = useState<PullRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<Busy>({});
  const [open, setOpen] = useState<number | null>(null);

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
        Pull requests the platform opened editing its own agents. Expand to review the diff, then merge to apply or close to discard.
      </p>
      {loading && <p className="muted">Loading…</p>}
      {error && <div className="error">{error}</div>}
      {!loading && !error && prs.length === 0 && <p className="muted">No pending changes.</p>}
      {!loading && prs.length > 0 && (
        <table className="table">
          <thead>
            <tr><th>#</th><th>Title</th><th>Branch</th><th>Author</th><th></th></tr>
          </thead>
          <tbody>
            {prs.map((pr) => (
              <Fragment key={pr.number}>
                <tr>
                  <td>
                    <a href={pr.url} target="_blank" rel="noreferrer">#{pr.number}</a>
                  </td>
                  <td>
                    <button className="linkish" onClick={() => setOpen(open === pr.number ? null : pr.number)}>
                      {open === pr.number ? "▾ " : "▸ "}{pr.title}
                    </button>
                  </td>
                  <td className="muted">{pr.branch}</td>
                  <td className="muted">{pr.author}</td>
                  <td>
                    <div className="row-actions">
                      <button onClick={() => act(pr.number, "merge")} disabled={!!busy[pr.number]}>
                        {busy[pr.number] === "merge" ? "Merging…" : "Merge"}
                      </button>
                      <button className="secondary" onClick={() => act(pr.number, "close")} disabled={!!busy[pr.number]}>
                        {busy[pr.number] === "close" ? "Closing…" : "Close"}
                      </button>
                    </div>
                  </td>
                </tr>
                {open === pr.number && (
                  <tr>
                    <td colSpan={5}><Diff number={pr.number} /></td>
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
