import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Button } from "@ap/ui/button";
import { Select } from "@ap/ui/field";
import { api, apiErrorMessage } from "../../api";
import { ARTIFACT_ID_RE } from "../../lib/artifacts";
import type { UiExport } from "../../lib/uiDesigner";

type Format = "png" | "svg" | "pdf" | "html";
type Props = { base: string; version: number; mode: string; pageId: string | null; selectedId: string | null; canExport: boolean };

export default function ExportControls({ base, version, mode, pageId, selectedId, canExport }: Props) {
  const [format, setFormat] = useState<Format>("png");
  const [scope, setScope] = useState("page");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<UiExport | null>(null);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);
  const node = scope === "selection" ? selectedId : pageId;
  async function exportDesign() {
    if (busy || !canExport || !node) return;
    const request = new AbortController(); controller.current = request;
    setBusy(true); setError(""); setResult(null);
    try {
      const exported = await api<UiExport>(`${base}/export`, { method: "POST", signal: request.signal,
        body: JSON.stringify({ format, node, version, mode, scale: 1 }) });
      if (request.signal.aborted) return;
      if (!ARTIFACT_ID_RE.test(exported.artifact_id)) throw new Error("The export returned an invalid artifact link.");
      setResult(exported);
    } catch (e) {
      if (!request.signal.aborted) setError(apiErrorMessage(e, e instanceof Error && !/^\d+:/.test(e.message) ? e.message : "Could not export this design. Try again."));
    } finally { if (!request.signal.aborted) setBusy(false); }
  }
  return <section aria-label="Export design" className="ud-export-controls">
    <div className="ud-actions">
      <label>Export format <Select aria-label="Export format" value={format} disabled={busy} onChange={(e) => setFormat(e.target.value as Format)}>
        <option value="png">PNG</option><option value="svg">SVG</option><option value="pdf">PDF</option><option value="html">HTML + CSS snippet</option>
      </Select></label>
      <label>Export scope <Select aria-label="Export scope" value={scope} disabled={busy} onChange={(e) => setScope(e.target.value)}>
        <option value="page">Current page</option><option value="selection" disabled={!selectedId}>Selected node</option>
      </Select></label>
      <Button onClick={exportDesign} disabled={busy || !canExport || !node}>{busy ? "Exporting…" : "Export design"}</Button>
    </div>
    {format === "html" && <p className="muted">Static HTML and CSS; interactive prototype behavior is not included.</p>}
    {error && <p role="alert">{error}</p>}
    {result && <div role="status">
      <p>Exported {result.name} · v{version}{" "}<Link to={`/artifacts/${result.artifact_id}`}>Open artifact</Link>{" · "}
        <a href={`/api/artifacts/${result.artifact_id}/content`} download={result.name}>Download export</a></p>
      {result.warnings?.length ? <ul aria-label="Export notes">{result.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul> : null}
    </div>}
  </section>;
}
