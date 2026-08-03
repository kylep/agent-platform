import { Fragment, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, type PullRequest, type PullRequestFile } from "../api";
import { blockPath, DeployTracker, parseBranch } from "../components/ChangeFlow";
import { Banner } from "@ap/ui/banner";
import { Button } from "@ap/ui/button";
import { Chip } from "@ap/ui/chip";
import { ConfirmDialog } from "@ap/ui/dialog";
import { Markdown } from "@ap/ui/markdown";
import { Table, TD, TH } from "@ap/ui/table";

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

// The AI reviewer summary — generated automatically by the platform (the
// dispatcher summarizes every open change and posts it as a PR comment; this
// just renders that comment). Polls while the summary is still being written.
function Summary({ number }: { number: number }) {
  const [state, setState] = useState<{ state: string; summary: string | null } | null>(null);

  useEffect(() => {
    let stop = false;
    let timer: ReturnType<typeof setInterval> | null = null;
    async function load() {
      try {
        const r = await api<{ state: string; summary: string | null }>(
          `/api/pull-requests/${number}/summary`);
        if (stop) return;
        setState(r);
        if (r.state === "ready" && timer) clearInterval(timer);
      } catch {
        if (!stop) setState({ state: "unavailable", summary: null });
        if (timer) clearInterval(timer);
      }
    }
    load();
    timer = setInterval(load, 10_000);
    return () => { stop = true; if (timer) clearInterval(timer); };
  }, [number]);

  if (!state || state.state === "unavailable") return null;
  if (state.state !== "ready") {
    return <p className="muted">AI summary is being written — it lands here and as a PR comment.</p>;
  }
  return (
    <Banner>
      <b>AI reviewer summary</b>
      <Markdown text={state.summary ?? ""} />
    </Banner>
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
      <Summary number={number} />
      {impact && impact.warnings.map((w, i) => (
        <Banner key={i} variant="danger">⚠ {w}</Banner>
      ))}
      {impact && (
        <div className="impact-panel">
          {impact.items.map((it) => (
            <div key={it.file} className="impact-item">
              <Chip variant={it.block ? "neutral" : "danger"}>{it.block ?? "platform code"}</Chip>
              <span className="muted"> {it.area} · {it.status} · +{it.additions} −{it.deletions}</span>
            </div>
          ))}
        </div>
      )}
      {files.map((f) => <FileView key={f.filename} f={f} />)}
    </div>
  );
}

// A brand-new file is CONTENT, not a wall of +prefixed diff lines: render
// markdown files properly and everything else as a plain code block. Edits
// and deletions keep the colored diff, scrolling with the page (no nested
// scroll trap).
function addedContent(patch: string): string {
  return patch.split("\n")
    .filter((l) => l.startsWith("+") && !l.startsWith("+++"))
    .map((l) => l.slice(1))
    .join("\n");
}

function FileView({ f }: { f: PullRequestFile }) {
  const isNew = f.status === "added" && !!f.patch;
  return (
    <div className="diff-file">
      <div className="diff-file-head">
        {f.filename} <span className="muted">+{f.additions} −{f.deletions} ({f.status})</span>
      </div>
      {isNew && f.filename.endsWith(".md") && (
        <div className="file-panel"><Markdown text={addedContent(f.patch!)} /></div>
      )}
      {isNew && !f.filename.endsWith(".md") && (
        <pre className="diff-patch">{addedContent(f.patch!)}</pre>
      )}
      {!isNew && (f.patch
        ? <ColoredPatch patch={f.patch} />
        : <pre className="diff-patch">(no textual diff)</pre>)}
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
        <Table className="table-fixed">
          <thead>
            <tr><TH className="w-14">#</TH><TH>Title</TH><TH className="w-44">Building block</TH><TH className="w-32">Author</TH><TH className="w-44"></TH></tr>
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
                  <TD className="pr-4"><BlockChip branch={pr.branch} /></TD>
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
