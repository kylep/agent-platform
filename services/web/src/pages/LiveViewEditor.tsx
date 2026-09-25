import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import { Button } from "@ap/ui/button";
import { Input, Textarea } from "@ap/ui/field";

type Definition = { renderer: "typed/v1"; title: string; blocks: {
  kind: "heading" | "paragraph" | "metric" | "table" | "action" | "link"; text?: string;
  label?: string; value?: string; source?: string; field?: string; columns?: string[];
  action_alias?: string; href?: string }[];
  reads: { alias: string; operation: string; channel_id?: string }[];
  actions: { alias: string; operation: string; channel: string }[] };
type Draft = { id: string; app_name: string; slug: string; draft_revision: number;
  published_version: number | null; definition: Definition };
type Version = { version: number; published_at: string; published_by: string; current: boolean };
type OutputShape = { type?: string | string[]; format?: string;
  properties?: Record<string, OutputShape>; items?: OutputShape };
type Operation = { id: string; tool: string; source: string; effects: string[];
  reason: string; view_eligible: boolean; output_schema?: OutputShape };

function sampleValue(shape: OutputShape | undefined, field: string): string {
  if (!shape) return "Sample value";
  const type = Array.isArray(shape.type) ? shape.type.find((kind) => kind !== "null") : shape.type;
  if (shape.format === "date") return "2026-09-25";
  if (type === "boolean") return "Yes";
  if (type === "integer") return "3";
  if (type === "number") return "12.5";
  return field.replaceAll("_", " ").replace(/^./, (letter) => letter.toUpperCase());
}

function previewField(operations: Operation[], definition: Definition, alias: string | undefined,
                      field: string | undefined): OutputShape | undefined {
  const binding = definition.reads.find((read) => read.alias === alias);
  return operations.find((operation) => operation.id === binding?.operation)
    ?.output_schema?.properties?.[field || ""];
}

function example(appName: string): Definition {
  return { renderer: "typed/v1", title: `${appName} overview`,
    blocks: [{ kind: "heading", text: "Overview" },
      { kind: "paragraph", text: "Describe what this App helps you do." }],
    reads: [], actions: [] };
}

export default function LiveViewEditor() {
  const { id } = useParams();
  const creating = !id;
  const appName = new URLSearchParams(window.location.search).get("app") || "";
  const [slug, setSlug] = useState("");
  const [draft, setDraft] = useState<Draft | null>(null);
  const [source, setSource] = useState(JSON.stringify(example(appName), null, 2));
  const [versions, setVersions] = useState<Version[]>([]);
  const [operations, setOperations] = useState<Operation[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function reload(viewId: string) {
    const [next, history] = await Promise.all([
      api<Draft>(`/api/live-views/${encodeURIComponent(viewId)}/draft`),
      api<Version[]>(`/api/live-views/${encodeURIComponent(viewId)}/versions`),
    ]);
    setDraft(next); setVersions(history);
    setSource(JSON.stringify(next.definition, null, 2));
  }
  useEffect(() => {
    if (!id) return;
    reload(id).catch((e) => setError(e instanceof Error ? e.message : "Draft unavailable."));
  }, [id]);
  useEffect(() => {
    api<Operation[]>("/api/live-operations?eligible_only=true")
      .then(setOperations).catch(() => setOperations([]));
  }, []);

  let preview: Definition | null = null;
  try {
    const parsed: unknown = JSON.parse(source);
    if (parsed && typeof parsed === "object" && "blocks" in parsed &&
        Array.isArray(parsed.blocks) && "title" in parsed && typeof parsed.title === "string") {
      preview = parsed as Definition;
    }
  } catch { /* The save path reports invalid JSON. */ }

  async function save() {
    setError(null); setNotice(null); setBusy(true);
    try {
      const definition = JSON.parse(source) as Definition;
      if (creating) {
        const made = await api<{ id: string }>("/api/live-views", {
          method: "POST", body: JSON.stringify({ app_name: appName, slug, definition }),
        });
        window.location.assign(`/live-views/${encodeURIComponent(made.id)}/edit`);
        return;
      }
      await api(`/api/live-views/${encodeURIComponent(id!)}/draft`, {
        method: "PUT", body: JSON.stringify({ expected_revision: draft!.draft_revision, definition }),
      });
      await reload(id!);
      setNotice("Draft saved. The published page is unchanged.");
    } catch (e) { setError(e instanceof Error ? e.message : "Could not save draft."); }
    finally { setBusy(false); }
  }
  async function publish() {
    if (!id) return;
    setError(null); setNotice(null); setBusy(true);
    try {
      await api(`/api/live-views/${encodeURIComponent(id)}/publish`, { method: "POST" });
      await reload(id);
      setNotice("Page published. New visits use this version.");
    } catch (e) { setError(e instanceof Error ? e.message : "Could not publish page."); }
    finally { setBusy(false); }
  }
  async function rollback(version: number) {
    if (!id || !window.confirm(`Publish version ${version} again?`)) return;
    setError(null); setNotice(null); setBusy(true);
    try {
      await api(`/api/live-views/${encodeURIComponent(id)}/rollback/${version}`, { method: "POST" });
      await reload(id);
      setNotice(`Version ${version} is live again. The current draft remains available.`);
    } catch (e) { setError(e instanceof Error ? e.message : "Could not restore version."); }
    finally { setBusy(false); }
  }

  return <div className="page">
    <div className="page-header"><h1>{creating ? "New live page" : `Edit ${draft?.slug || "live page"}`}</h1></div>
    <p className="muted"><Link to="/apps">Apps</Link> / {draft?.app_name || appName} · Typed pages render text and trusted controls only.</p>
    {creating && <label>Page URL name<Input value={slug} onChange={(e) => setSlug(e.target.value)}
      placeholder="overview" pattern="[a-z][a-z0-9-]*" /></label>}
    <div className="live-editor-layout">
      <section>
        <label htmlFor="live-definition">Page definition</label>
        <Textarea id="live-definition" value={source} onChange={(e) => setSource(e.target.value)}
          rows={22} spellCheck={false} />
        <p className="muted">Saving validates the fields and bindings. Publishing is a separate step.</p>
        {operations.length > 0 && <details className="live-editor-operations">
          <summary>Available page operations</summary>
          <p className="muted">Add an operation to <code>reads</code> or <code>actions</code>,
            then reference its alias from a block. A binding does not grant permission.</p>
          <ul>{operations.filter((operation) => operation.source === "human-adapter" ||
            operation.source === "platform-adapter" ||
            operation.tool === (draft?.app_name || appName)).map((operation) =>
            <li key={operation.id}><code>{operation.id}</code> · {operation.reason}</li>)}</ul>
          <p className="muted">Relay reads need a fixed <code>channel_id</code> from the room URL.
            Closed rooms still require the viewer to be a member.</p>
        </details>}
        <Button onClick={save} disabled={busy || (creating && !slug)}>Save draft</Button>{" "}
        {!creating && <Button variant="secondary" onClick={publish} disabled={busy}>Publish saved draft</Button>}{" "}
        {!creating && draft?.published_version && <Link to={`/live-views/${id}`}>Open live page →</Link>}
        {error && <p role="alert" className="error">{error}</p>}
        {notice && <p role="status">{notice}</p>}
      </section>
      <section className="live-editor-preview" aria-label="Draft preview">
        <h2>Draft preview</h2>
        <p className="muted">Sample data only. Preview makes no live calls.</p>
        {preview ? <>
          <h3>{preview.title}</h3>
          {preview.blocks.map((block, index) => {
            if (block.kind === "heading") return <h4 key={index}>{block.text}</h4>;
            if (block.kind === "paragraph") return <p key={index}>{block.text}</p>;
            if (block.kind === "link") return <p key={index}>
              {block.href === `/apps/${draft?.app_name || appName}/`
                ? <a href={block.href}>{block.label || "Open app"} →</a>
                : <span className="error">Link must open this App's reviewed interface.</span>}
            </p>;
            if (block.kind === "metric") return <div className="live-view-metric" key={index}>
              <span className="muted">{block.label}</span>
              <strong>{block.source
                ? sampleValue(previewField(operations, preview!, block.source, block.field), block.field || "Value")
                : block.value}</strong></div>;
            if (block.kind === "table") {
              const rowFields = previewField(operations, preview!, block.source, "rows")?.items?.properties || {};
              const columns = block.columns?.length ? block.columns : Object.keys(rowFields);
              return <section className="live-view-table" key={index}>
                <h4>{block.label || "Table"}</h4>
                {columns.length ? <div className="table-scroll"><table><thead><tr>{columns.map((column) =>
                  <th key={column}>{column.replaceAll("_", " ")}</th>)}</tr></thead><tbody><tr>
                  {columns.map((column) => <td key={column} data-label={column.replaceAll("_", " ")}>
                    {sampleValue(rowFields[column], column)}</td>)}
                </tr></tbody></table></div> : <p className="muted">Add a reviewed table read to preview its columns.</p>}
              </section>;
            }
            return <p key={index}><strong>{block.label || "Action"}</strong> · {block.action_alias}</p>;
          })}
        </> : <p className="muted">Enter a typed/v1 JSON definition to preview it.</p>}
      </section>
    </div>
    {!creating && <section className="live-editor-history"><h2>Published versions</h2>
      {versions.length === 0 ? <p className="muted">Nothing published yet.</p> :
        <ul>{versions.map((v) => <li key={v.version}>
          Version {v.version} · {new Date(v.published_at).toLocaleString()} · {v.published_by}
          {v.current ? " · live" : <> · <Button variant="secondary" onClick={() => rollback(v.version)} disabled={busy}>Restore</Button></>}
        </li>)}</ul>}
    </section>}
  </div>;
}
