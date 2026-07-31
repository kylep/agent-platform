import { Fragment, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, type PullRequest, type PullRequestFile } from "../api";
import { blockPath, DeployTracker, parseBranch } from "../components/ChangeFlow";

type Busy = { [n: number]: "merge" | "close" | undefined };
type Accepted = { number: number; title: string; sha: string | null; branch: string };

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

// "agent: news" chip linking to the building block the change touches.
function BlockChip({ branch }: { branch: string }) {
  const ref = parseBranch(branch);
  if (!ref) return <span className="muted">{branch}</span>;
  return (
    <Link to={blockPath(ref)} className="chip">{ref.kind}: {ref.name}</Link>
  );
}

export default function Changes() {
  const [params] = useSearchParams();
  const [prs, setPrs] = useState<PullRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<Busy>({});
  const [open, setOpen] = useState<number | null>(
    params.get("open") ? Number(params.get("open")) : null);
  const [accepted, setAccepted] = useState<Accepted[]>([]);
  const [confirmDiscard, setConfirmDiscard] = useState<PullRequest | null>(null);

  function load() {
    api<PullRequest[]>("/api/pull-requests")
      .then(setPrs)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load changes."))
      .finally(() => setLoading(false));
  }
  useEffect(() => {
    load();
    const id = setInterval(load, 15000);   // keep the review queue fresh
    return () => clearInterval(id);
  }, []);

  async function accept(pr: PullRequest) {
    setBusy((b) => ({ ...b, [pr.number]: "merge" }));
    setError(null);
    try {
      const r = await api<{ merged: boolean; sha: string | null }>(
        `/api/pull-requests/${pr.number}/merge`, { method: "POST" });
      setAccepted((a) => [{ number: pr.number, title: pr.title, sha: r.sha, branch: pr.branch }, ...a]);
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : `Failed to accept #${pr.number}.`);
    } finally {
      setBusy((b) => ({ ...b, [pr.number]: undefined }));
    }
  }

  async function discard(pr: PullRequest) {
    setConfirmDiscard(null);
    setBusy((b) => ({ ...b, [pr.number]: "close" }));
    setError(null);
    try {
      await api(`/api/pull-requests/${pr.number}/close`, { method: "POST" });
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : `Failed to discard #${pr.number}.`);
    } finally {
      setBusy((b) => ({ ...b, [pr.number]: undefined }));
    }
  }

  return (
    <div className="page">
      <h1>Pending Changes</h1>
      <p className="muted">
        Every edit to a building block — agent, skill, or secret declaration — lands here as a
        pull request. Review the diff, then <b>Accept</b> to make it live (the cluster syncs within
        a minute — tracked below) or <b>Discard</b> to drop it.
      </p>

      {accepted.length > 0 && (
        <div style={{ marginBottom: 12 }}>
          {accepted.map((a) => (
            <div key={a.number} className="banner banner-ok" style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <span>Accepted #{a.number} — {a.title}</span>
              <DeployTracker sha={a.sha} />
              <BlockChip branch={a.branch} />
              <button className="linkish muted" style={{ marginLeft: "auto" }}
                      onClick={() => setAccepted((l) => l.filter((x) => x.number !== a.number))}>
                dismiss
              </button>
            </div>
          ))}
        </div>
      )}

      {loading && <p className="muted">Loading…</p>}
      {error && <div className="error">{error}</div>}
      {!loading && !error && prs.length === 0 && accepted.length === 0 && (
        <p className="muted">No pending changes.</p>
      )}
      {!loading && prs.length > 0 && (
        <table className="table">
          <thead>
            <tr><th>#</th><th>Title</th><th>Building block</th><th>Author</th><th></th></tr>
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
                  <td><BlockChip branch={pr.branch} /></td>
                  <td className="muted">{pr.author}</td>
                  <td>
                    <div className="row-actions">
                      <button onClick={() => accept(pr)} disabled={!!busy[pr.number]}>
                        {busy[pr.number] === "merge" ? "Accepting…" : "Accept"}
                      </button>
                      <button className="secondary" onClick={() => setConfirmDiscard(pr)} disabled={!!busy[pr.number]}>
                        {busy[pr.number] === "close" ? "Discarding…" : "Discard"}
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

      {confirmDiscard && (
        <div className="modal-backdrop" onClick={() => setConfirmDiscard(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h2>Discard change #{confirmDiscard.number}?</h2>
            <p className="muted">
              "{confirmDiscard.title}" will be closed and its edits dropped. The block it touches
              unlocks for new edits. This can't be undone from here.
            </p>
            <div className="row-actions">
              <button onClick={() => discard(confirmDiscard)}>Discard it</button>
              <button className="secondary" onClick={() => setConfirmDiscard(null)}>Keep it</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
