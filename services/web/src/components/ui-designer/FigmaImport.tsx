import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Button } from "@ap/ui/button";
import { Input } from "@ap/ui/field";
import { api, apiErrorMessage } from "../../api";
const IMPORTS = "/api/ui-designer/imports";

type Job = {
  id: string; slug: string; state: "queued" | "running" | "done" | "failed";
  pages: Record<string, { state: string }>;
  report: Record<string, unknown> | null; error: string | null;
};
const savedJob = "ui-designer-figma-import";

function ReportValue({ value }: { value: unknown }) {
  if (value == null) return <>None</>;
  if (typeof value !== "object") return <>{String(value)}</>;
  if (Array.isArray(value)) return value.length ? <ul>{value.map((item, i) => <li key={i}><ReportValue value={item} /></li>)}</ul> : <>None</>;
  return <ul>{Object.entries(value).map(([key, item]) => <li key={key}>{key.replaceAll("_", " ").replace(/^./, (c) => c.toUpperCase())}: <ReportValue value={item} /></li>)}</ul>;
}

const fidelityFields = ["converted", "approximated", "dropped", "unmapped", "items"];
function fidelityReport(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return Object.fromEntries(Object.entries(value).filter(([key]) => fidelityFields.includes(key)));
}
function ImportReport({ report }: { report: Record<string, unknown> }) {
  const summary = Object.fromEntries(Object.entries(report).filter(([key]) =>
    ["pages_imported", "failed_pages", ...fidelityFields].includes(key)));
  const pageReports = report.pages && typeof report.pages === "object" && !Array.isArray(report.pages)
    ? Object.entries(report.pages) : [];
  const references = Array.isArray(report.references) ? report.references.filter((r): r is { node_id: string; artifact_id: string } =>
    !!r && typeof r === "object" && typeof r.node_id === "string" && typeof r.artifact_id === "string") : [];
  return <>
    <ReportValue value={summary} />
    {pageReports.map(([id, value]) => <div key={id}><h4>Page {id}</h4><ReportValue value={fidelityReport(value)} /></div>)}
    {references.length > 0 && <div><h4>Figma references</h4><ul>{references.map((r) =>
      <li key={r.artifact_id}><Link to={`/artifacts/${encodeURIComponent(r.artifact_id)}`}>View Figma reference for {r.node_id}</Link></li>)}</ul></div>}
  </>;
}

function fileKey(value: string): string {
  const trimmed = value.trim();
  if (/^[a-zA-Z0-9_-]+$/.test(trimmed)) return trimmed;
  try {
    const url = new URL(trimmed);
    const match = /^\/(?:design|file|proto)\/([a-zA-Z0-9_-]+)(?:\/|$)/.exec(url.pathname);
    if (url.protocol === "https:" && ["figma.com", "www.figma.com"].includes(url.hostname) && match) return match[1];
  } catch { /* explain invalid input below */ }
  throw new Error("Enter a Figma file URL or file key.");
}

export default function FigmaImport() {
  const [source, setSource] = useState("");
  const [slug, setSlug] = useState("");
  const [title, setTitle] = useState("");
  const [pages, setPages] = useState("");
  const [job, setJob] = useState<Job | null>(null);
  const [jobId, setJobId] = useState(() => localStorage.getItem(savedJob) || "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [pollError, setPollError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    if (!jobId) return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const result = await api<Job>(`${IMPORTS}/${encodeURIComponent(jobId)}`);
        if (disposed) return;
        setJob(result); setPollError("");
        if (result.state === "queued" || result.state === "running") timer = setTimeout(poll, 2000);
      } catch (e) {
        if (!disposed) setPollError(apiErrorMessage(e, "Could not check import status. Try again."));
      }
    }
    void poll();
    return () => { disposed = true; clearTimeout(timer); };
  }, [jobId, retry]);
  const active = busy || (!!jobId && (!job || job.state === "queued" || job.state === "running"));
  async function start(event: React.FormEvent) {
    event.preventDefault(); setError("");
    try {
      const key = fileKey(source);
      const target = slug.trim() || `figma-${key.toLowerCase().replaceAll("_", "-")}`.slice(0, 64);
      if (!/^[a-z0-9][a-z0-9-]{0,63}$/.test(target)) throw new Error("Use up to 64 lowercase letters, numbers and hyphens for the document slug.");
      setBusy(true);
      const result = await api<Job>(IMPORTS, { method: "POST", body: JSON.stringify({
        file_key: key, slug: target, ...(title.trim() ? { title: title.trim() } : {}),
        ...(pages.trim() ? { pages: pages.split(",").map((p) => p.trim()).filter(Boolean) } : {}),
      }) });
      setJob(result); setJobId(result.id); localStorage.setItem(savedJob, result.id); setPollError("");
    } catch (e) { setError(e instanceof Error && !/^\d+:/.test(e.message) ? e.message : apiErrorMessage(e, "Could not start import.")); }
    finally { setBusy(false); }
  }
  return <section aria-label="Import from Figma">
    <h2>Import from Figma</h2>
    <p className="muted">Import a file using the configured Figma connection. You can leave this page and return to check progress.</p>
    <form onSubmit={start}>
      <p><label htmlFor="figma-source">Figma file URL or key</label><Input id="figma-source" value={source} onChange={(e) => setSource(e.target.value)} required disabled={active} /></p>
      <p><label htmlFor="figma-slug">Document slug (optional)</label><Input id="figma-slug" maxLength={64} value={slug} onChange={(e) => setSlug(e.target.value)} disabled={active} placeholder="Defaults to figma-file-key" /></p>
      <p><label htmlFor="figma-title">Title (optional)</label><Input id="figma-title" maxLength={120} value={title} onChange={(e) => setTitle(e.target.value)} disabled={active} /></p>
      <p><label htmlFor="figma-pages">Page IDs (optional, comma separated)</label><Input id="figma-pages" value={pages} onChange={(e) => setPages(e.target.value)} disabled={active} /></p>
      <Button type="submit" disabled={active}>{busy ? "Starting import…" : "Import from Figma"}</Button>
      {error && <p role="alert">{error}</p>}
    </form>
    {jobId && <div aria-label="Figma import status" role="status" aria-live="polite">
      <h3>{!job ? "Checking import…" : job.state === "done" ? "Import complete" : job.state === "failed" ? "Import failed" : job.state === "queued" ? "Import queued" : "Import in progress"}</h3>
      {job && Object.keys(job.pages || {}).length > 0 && <p>{Object.values(job.pages).filter((p) => p.state === "done").length} of {Object.keys(job.pages).length} pages imported.</p>}
      {job?.error && <p>{job.error}</p>}
      {job?.state === "done" && <p><Link to={`/ui-designer/${encodeURIComponent(job.slug)}`}>Open imported document</Link></p>}
      {job?.report && <details><summary>Import report</summary><ImportReport report={job.report} /></details>}
      {pollError && <p role="alert">{pollError} <Button onClick={() => setRetry((n) => n + 1)}>Check again</Button></p>}
      {(pollError || job?.state === "done" || job?.state === "failed") && <Button onClick={() => { setJobId(""); setJob(null); setPollError(""); localStorage.removeItem(savedJob); }}>Dismiss status</Button>}
    </div>}
  </section>;
}
