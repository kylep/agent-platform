import { Fragment, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, type PullRequest, type PullRequestFile, type RunDetailData } from "../api";
import { blockPath, DeployTracker, parseBranch } from "../components/ChangeFlow";
import { Banner } from "../ui/banner";
import { Button } from "../ui/button";
import { Chip } from "../ui/chip";
import { ConfirmDialog } from "../ui/dialog";
import { Table, TD, TH } from "../ui/table";

type Busy = { [n: number]: "merge" | "close" | undefined };
type Accepted = { number: number; title: string; sha: string | null; branch: string };
type Impact = {
  items: { file: string; block: string | null; area: string; status: string;
           additions: number; deletions: number; notable: string[] }[];
  warnings: string[];
};

function lineClass(line: string): string {
  if (line.startsWith("+")) return "diff-line diff-line-add";
  if (line.startsWith("-")) return "diff-line diff-line-del";
  if (line.startsWith("@@")) return "diff-line diff-line-hunk";
  return "diff-line";
}

function ColoredPatch({ patch }: { patch: string }) {
  return (
    <pre className="diff-patch">
      {patch.split("\n").map((l, i) => (
        <div key={i} className={lineClass(l)}>{l || " "}</div>
      ))}
    </pre>
  );
}

// The on-demand AI reviewer summary: one button → one change-summarizer run →
// the run's result rendered inline.
function Summary({ number }: { number: number }) {
  const [runId, setRunId] = useState<string | null>(null);
  const [run, setRun] = useState<RunDetailData | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function start() {
    setError(null);
    try {
      const r = await api<{ id: string }>(`/api/pull-requests/${number}/summarize`, { method: "POST" });
      setRunId(r.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start the summary.");
    }
  }

  useEffect(() => {
    if (!runId) return;
    let stop = false;
    let grace = 0;
    const id = setInterval(async () => {
      try {
        const r = await api<RunDetailData>(`/api/runs/${runId}`);
        if (stop) return;
        setRun(r);
        const active = ["queued", "dispatched", "running"].includes(r.state);
        // The k8s Job flips terminal slightly before the recorder persists the
        // result frame — grace-poll a few beats for the text to land.
        if (!active && (r.state !== "succeeded" || r.result || ++grace > 5)) clearInterval(id);
      } catch { /* transient */ }
    }, 3000);
    return () => { stop = true; clearInterval(id); };
  }, [runId]);

  if (!runId) {
    return (
      <div className="row-actions" style={{ marginTop: 6 }}>
        <Button variant="secondary" size="sm" onClick={start}>Summarize with AI</Button>
        {error && <span className="error">{error}</span>}
      </div>
    );
  }
  const running = !run || ["queued", "dispatched", "running"].includes(run.state)
    || (run.state === "succeeded" && !run.result);
  if (running) {
    return <p className="muted">Summarizing… (<Link to={`/runs/${runId}`}>watch the run</Link>)</p>;
  }
  if (run.state === "succeeded" && run.result) {
    return (
      <Banner>
        <b>AI summary:</b> {run.result}
        <span className="muted"> — <Link to={`/runs/${runId}`}>run</Link></span>
      </Banner>
    );
  }
  return (
    <div className="error">
      Summary run {run.state}{run.error ? `: ${run.error}` : ""} — <Link to={`/runs/${runId}`}>details</Link>
    </div>
  );
}

function Review({ number }: { number: number }) {
  const [files, setFiles] = useState<PullRequestFile[] | null>(null);
  const [impact, setImpact] = useState<Impact | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api<PullRequestFile[]>(`/api/pull-requests/${number}/files`)
      .then(setFiles)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load diff."));
    api<Impact>(`/api/pull-requests/${number}/impact`).then(setImpact).catch(() => {});
  }, [number]);
  if (error) return <div className="error">{error}</div>;
  if (!files) return <p className="muted">Loading diff…</p>;
  if (files.length === 0) return <p className="muted">No file changes.</p>;
  return (
    <div>
      {impact && impact.warnings.map((w, i) => (
        <Banner key={i} variant="danger">⚠ {w}</Banner>
      ))}
      {impact && (
        <div className="impact-panel">
          {impact.items.map((it) => (
            <div key={it.file} className="impact-item">
              <Chip variant={it.block ? "neutral" : "danger"}>{it.block ?? "platform code"}</Chip>
              <span className="muted"> {it.area} · {it.status} · +{it.additions} −{it.deletions}</span>
              {it.notable.length > 0 && (
                <pre className="impact-notable">{it.notable.join("\n")}</pre>
              )}
            </div>
          ))}
        </div>
      )}
      <Summary number={number} />
      {files.map((f) => (
        <div key={f.filename} className="diff-file">
          <div className="diff-file-head">
            {f.filename} <span className="muted">+{f.additions} −{f.deletions} ({f.status})</span>
          </div>
          {f.patch ? <ColoredPatch patch={f.patch} /> : <pre className="diff-patch">(no textual diff)</pre>}
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
    <Link to={blockPath(ref)} className="no-underline">
      <Chip variant="neutral">{ref.kind}: {ref.name}</Chip>
    </Link>
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
      .then((rows) => {
        setPrs(rows);
        // one pending change → open it for review straight away
        setOpen((o) => (o === null && rows.length === 1 ? rows[0].number : o));
      })
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
            <Banner key={a.number} variant="ok" className="flex items-center gap-2">
              <span>Accepted #{a.number} — {a.title}</span>
              <DeployTracker sha={a.sha} />
              <BlockChip branch={a.branch} />
              <Button variant="link" className="ml-auto text-muted"
                      onClick={() => setAccepted((l) => l.filter((x) => x.number !== a.number))}>
                dismiss
              </Button>
            </Banner>
          ))}
        </div>
      )}

      {loading && <p className="muted">Loading…</p>}
      {error && <div className="error">{error}</div>}
      {!loading && !error && prs.length === 0 && accepted.length === 0 && (
        <p className="muted">No pending changes.</p>
      )}
      {!loading && prs.length > 0 && (
        <Table>
          <thead>
            <tr><TH>#</TH><TH>Title</TH><TH>Building block</TH><TH>Author</TH><TH></TH></tr>
          </thead>
          <tbody>
            {prs.map((pr) => (
              <Fragment key={pr.number}>
                <tr>
                  <TD>
                    <a href={pr.url} target="_blank" rel="noreferrer">#{pr.number}</a>
                  </TD>
                  <TD>
                    <Button variant="link" className="text-default no-underline hover:text-accent hover:no-underline"
                            onClick={() => setOpen(open === pr.number ? null : pr.number)}>
                      {open === pr.number ? "▾ " : "▸ "}{pr.title}
                    </Button>
                  </TD>
                  <TD><BlockChip branch={pr.branch} /></TD>
                  <TD className="text-muted">{pr.author}</TD>
                  <TD>
                    <div className="row-actions">
                      <Button size="sm" onClick={() => accept(pr)} disabled={!!busy[pr.number]}>
                        {busy[pr.number] === "merge" ? "Accepting…" : "Accept"}
                      </Button>
                      <Button variant="secondary" size="sm" onClick={() => setConfirmDiscard(pr)} disabled={!!busy[pr.number]}>
                        {busy[pr.number] === "close" ? "Discarding…" : "Discard"}
                      </Button>
                    </div>
                  </TD>
                </tr>
                {open === pr.number && (
                  <tr>
                    <TD colSpan={5}><Review number={pr.number} /></TD>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </Table>
      )}

      <ConfirmDialog
        open={confirmDiscard !== null}
        title={`Discard change #${confirmDiscard?.number}?`}
        confirmLabel="Discard it"
        onConfirm={() => confirmDiscard && discard(confirmDiscard)}
        onCancel={() => setConfirmDiscard(null)}>
        "{confirmDiscard?.title}" will be closed and its branch deleted. The block it touches
        unlocks for new edits. This can't be undone from here.
      </ConfirmDialog>
    </div>
  );
}
