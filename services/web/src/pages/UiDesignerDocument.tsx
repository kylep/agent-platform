import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api, apiErrorMessage } from "../api";
import { prototypeFrames } from "../lib/prototype";
import FindingsPanel from "../components/ui-designer/FindingsPanel";
import CompareVersions from "../components/ui-designer/CompareVersions";
import ExportControls from "../components/ui-designer/ExportControls";
import Inspector from "../components/ui-designer/Inspector";
import { Button } from "@ap/ui/button";
import { useDesignerEditing } from "../lib/useDesignerEditing";
import type { UiInspection } from "../lib/uiDesigner";
import { Canvas } from "../components/ui-designer/Canvas";
import { useTitle } from "../lib/title";
import { UD, flatten, nodeLabel, type RNode, type Resolved, type UiComment, type UiEvent, type UiVersionRow } from "../lib/uiDesigner";

// Read-only document view (docs/design/46 §7): outline, server-SVG canvas
// with a hit overlay, a small inspector, and a version picker.
function useMode(): "dark" | "light" {
  const read = () => (document.documentElement.dataset.theme === "light" ? "light" : "dark");
  const [m, setM] = useState<"dark" | "light">(read);
  useEffect(() => {
    const o = new MutationObserver(() => setM(read()));
    o.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => o.disconnect();
  }, []);
  return m;
}

function Outline({ nodes, depth, selected, onSelect }: {
  nodes: RNode[]; depth: number; selected: string | null; onSelect: (n: RNode) => void;
}) {
  return (
    <ul className="ud-tree">
      {nodes.map((n) => (
        <li key={n.id}>
          <button type="button" className="ud-node" data-selected={n.id === selected ? "" : undefined}
                  style={{ paddingLeft: 6 + depth * 12 }} onClick={() => onSelect(n)}>
            <span className="muted">{n.type ?? "node"}</span> {nodeLabel(n)}
          </button>
          {n.children?.length ? <Outline nodes={n.children} depth={depth + 1} selected={selected}
                                         onSelect={onSelect} /> : null}
        </li>
      ))}
    </ul>
  );
}

export default function UiDesignerDocument() {
  const { slug = "" } = useParams<{ slug: string }>();
  const [params, setParams] = useSearchParams();
  const mode = useMode();
  const vParam = params.get("version");
  const version = vParam && /^\d+$/.test(vParam) ? Number(vParam) : null;
  useTitle(slug, "UI Designer");

  const [resolved, setResolved] = useState<Resolved | null>(null);
  const [versions, setVersions] = useState<UiVersionRow[]>([]);
  const [current, setCurrent] = useState<number | null>(null);
  const [pageId, setPageId] = useState<string | null>(null);
  const [sel, setSel] = useState<RNode | null>(null);
  const [err, setErr] = useState("");
  const [rev, setRev] = useState(0);
  const [comments, setComments] = useState<UiComment[]>([]);
  const [hl, setHl] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [comparing, setComparing] = useState(false);
  const [showFindings, setShowFindings] = useState(false);
  const base = `${UD}/${encodeURIComponent(slug)}`;

  const [canWrite, setCanWrite] = useState(false);
  const [pinned, setPinned] = useState<number | null>(null);
  const [inspection, setInspection] = useState<UiInspection | null>(null);
  const [parents, setParents] = useState<Record<string, unknown>[]>([]);
  const [stale, setStale] = useState(false);
  const headLoad = useRef(0);
  const refresh = useCallback((next?: number, external?: boolean) => {
    if (external) setStale(true);
    if (next) { setCurrent(next); setPinned(next); }
    setRev((r) => r + 1);
  }, []);
  const editing = useDesignerEditing(base, refresh, `${base}:${version ?? "latest"}`);
  const eventHandler = useRef(editing.external); eventHandler.current = editing.external;
  useEffect(() => { api<{ role: string }>("/api/whoami").then((me) => setCanWrite(["admin", "operator"].includes(me.role))).catch(() => setCanWrite(false)); }, []);
  useEffect(() => {
    setPinned(null); setInspection(null); setParents([]); setStale(false); setCurrent(null);
    setVersions([]); setResolved(null); setPageId(null); setSel(null); setComments([]);
    setDraft(""); setComparing(false); setShowFindings(false); setHl(null); setErr("");
  }, [slug]);

  // Live refresh: the events stream carries every document; only the open one reloads.
  useEffect(() => {
    if (typeof EventSource === "undefined") return;
    const es = new EventSource("/api/ui-designer/events", { withCredentials: true });
    es.addEventListener("design", (m) => {
      try {
        const ev = JSON.parse(m.data) as UiEvent;
        if (ev.slug === slug) {
          if (ev.type === "version" || ev.type === "imported" || ev.type === "restored") {
            if (ev.version) eventHandler.current(ev.version);
          } else setRev((r) => r + 1);
        }
      } catch { /* non-JSON frame */ }
    });
    return () => es.close();
  }, [slug]);

  useEffect(() => {
    let cancelled = false;
    api<{ comments: UiComment[] }>(`${base}/comments`)
      .then((r) => { if (!cancelled) setComments(r.comments ?? []); })
      .catch((e) => { if (!cancelled) setErr(String(e.message ?? e)); });
    return () => { cancelled = true; };
  }, [base, rev]);

  useEffect(() => {
    const seq = ++headLoad.current;
    let cancelled = false;
    api<{ version: number; versions: UiVersionRow[] }>(`${UD}/${encodeURIComponent(slug)}/versions`)
      .then((r) => { if (cancelled || seq !== headLoad.current) return; setVersions(r.versions); setCurrent((old) => Math.max(old ?? 0, r.version)); if (eventHandler.current(r.version)) setPinned((old) => old == null || r.version > old ? r.version : old); })
      .catch((e) => { if (!cancelled && seq === headLoad.current) setErr(String(e.message ?? e)); });
    return () => { cancelled = true; };
  }, [slug, rev, version]);

  useEffect(() => {
    setErr("");
    if (!(version ?? pinned)) return;
    let cancelled = false;
    const q = new URLSearchParams({ mode, version: String(version ?? pinned) });
    api<Resolved>(`${UD}/${encodeURIComponent(slug)}/resolved?${q}`)
      .then((r) => { if (!cancelled) setResolved(r); })
      .catch((e) => { if (!cancelled) setErr(String(e.message ?? e)); });
    return () => { cancelled = true; };
  }, [slug, version, pinned, mode, rev]);

  const pages = resolved?.pages ?? [];
  const page = pages.find((p) => p.id === pageId) ?? pages[0] ?? null;
  const known = useMemo(() => flatten(page?.children ?? []), [page]);
  const picked = sel ? known.find((n) => n.id === sel.id) ?? null : null;

  const selectedId = picked?.id;
  const pageNodes = useRef<RNode[]>([]); pageNodes.current = page?.children ?? [];
  useEffect(() => {
    if (!selectedId || !pinned || stale) return;
    let cancelled = false;
    const ancestors: string[] = [];
    function find(nodes: RNode[], path: string[]): boolean {
      for (const node of nodes) {
        if (node.id === selectedId) { ancestors.push(...path); return true; }
        if (find(node.children ?? [], [...path, node.id])) return true;
      }
      return false;
    }
    find(pageNodes.current, []);
    setInspection(null);
    Promise.all([selectedId, ...ancestors].map((id) => api<UiInspection>(`${base}/inspect?${new URLSearchParams({ node: id, version: String(version ?? pinned), mode })}`)))
      .then(([selected, ...parentNodes]) => { if (!cancelled) { setInspection(selected); setParents(parentNodes.map((p) => p.authored ?? {})); } })
      .catch((e) => { if (!cancelled) setErr(apiErrorMessage(e, "Could not inspect node.")); });
    return () => { cancelled = true; };
  }, [base, selectedId, pinned, version, mode, stale]);
  const parent = parents.at(-1);
  const freePosition = !parent?.layout || !!inspection?.authored?.absolute;
  const editable = canWrite && !version && !stale && !!inspection?.authored && inspection.version === pinned && inspection.id === picked?.id && !editing.busy && !editing.uncertain;
  function saveNode(id: string, changes: Record<string, unknown>) {
    if (!editable || inspection?.id !== id || picked?.id !== id || !inspection.authored) return;
    const before = Object.fromEntries(Object.keys(changes).map((key) => [key, inspection.authored![key] ?? null]));
    editing.commit([{ op: "update", id, set: changes }], [{ op: "update", id, set: before }]);
  }

  async function act<T>(fn: () => Promise<T>): Promise<T | undefined> {
    try { setErr(""); return await fn(); } catch (e) { setErr(apiErrorMessage(e, "request failed")); }
  }
  const addComment = () => act(async () => {
    if (!draft.trim()) return;
    await api(`${base}/comments`, { method: "POST",
      body: JSON.stringify({ node_id: picked?.id ?? null, body: draft.trim() }) });
    setDraft(""); setRev((r) => r + 1);
  });
  const resolve = (id: string) => act(async () => {
    await api(`${base}/comments/${encodeURIComponent(id)}/resolve`, { method: "POST" });
    setRev((r) => r + 1);
  });
  const labelVersion = () => act(async () => {
    const label = window.prompt("Label for this version");
    if (!label) return;
    await api(`${base}/label`, { method: "POST",
      body: JSON.stringify({ version: version ?? current, label }) });
    setRev((r) => r + 1);
  });
  function pickVersion(v: string) {
    const next = new URLSearchParams(params);
    if (v) next.set("version", v); else next.delete("version");
    setParams(next);
  }

  return (
    <div className="ud-doc">
      <div className="page-header">
        <h1><Link to="/ui-designer">UI Designer</Link> / {slug}</h1>
        <label className="muted">Version{" "}
          <select value={version ?? ""} onChange={(e) => pickVersion(e.target.value)}
                  aria-label="Version">
            <option value="">{current ? `Latest (v${current})` : "Latest"}</option>
            {versions.map((v) => (
              <option key={v.version} value={v.version}>
                {`v${v.version}${v.label ? ` ${v.label}` : ""} · ${v.author}`}
              </option>
            ))}
          </select>
        </label>
        <div className="ud-actions">
          {(version ?? pinned) && <Link to={`/ui-designer/${encodeURIComponent(slug)}/play?${new URLSearchParams({ version: String(version ?? pinned), mode, ...(page && page.children?.find((n)=>n.type === "frame" && !n.of) ? { frame: page.children.find((n)=>n.type === "frame" && !n.of)!.id } : {}) })}`}>Play prototype</Link>}
          {canWrite && !version && <><Button onClick={editing.undo} disabled={stale || editing.busy || editing.uncertain || !editing.canUndo}>Undo</Button><Button onClick={editing.redo} disabled={stale || editing.busy || editing.uncertain || !editing.canRedo}>Redo</Button></>}
          <Button onClick={() => setShowFindings((open) => !open)}>{showFindings ? "Close findings" : "Findings"}</Button>
          <button type="button" onClick={labelVersion}>Label this version</button>
          <button type="button" onClick={() => setComparing((open) => !open)}>
            {comparing ? "Close compare" : "Compare"}</button>

        </div>
      </div>
      {(version ?? pinned) && <ExportControls key={`${base}:${version ?? pinned}:${mode}`} base={base} version={(version ?? pinned)!} mode={mode} pageId={page?.id ?? null} selectedId={picked?.id ?? null} canExport={canWrite} />}
      {comparing && page && (version ?? pinned) && <CompareVersions key={base} base={base} versions={versions} initialVersion={(version ?? pinned)!} pageId={page.id} selectedId={picked?.id ?? null} mode={mode} onSelectNode={(id) => { const node = known.find((n) => n.id === id); if (node) setSel(node); }} />}
      {showFindings && (version ?? pinned) && <FindingsPanel key={base} base={base} version={(version ?? pinned)!} mode={mode} pageId={page?.id ?? null} selectedId={picked?.id ?? null} canvasNodeIds={pages.flatMap((p) => flatten(p.children ?? []).map((n) => n.id))} onSelectNode={(id) => {
        for (const candidatePage of pages) { const node = flatten(candidatePage.children ?? []).find((n) => n.id === id); if (node) { setPageId(candidatePage.id); setSel(node); return; } }
      }} />}
      {editing.message && <p role="status">{editing.message}</p>}
      {editing.uncertain && <Button onClick={editing.retry} disabled={editing.busy}>Retry save</Button>}
      {stale && <p role="status">The document changed. Your inspector draft is preserved. <Button onClick={() => { setStale(false); setInspection(null); }}>Discard draft and reload inspector</Button></p>}
      {version && <p className="muted">Viewing a historical version. Return to Latest to edit.</p>}
      {err && <p role="alert" className="muted">Could not load: {err}</p>}
      <div className="ud-cols">
        <aside className="ud-outline" aria-label="Outline">
          {pages.map((p) => (
            <div key={p.id}>
              <button type="button" className="ud-node ud-page"
                      data-selected={page?.id === p.id ? "" : undefined}
                      onClick={() => { setPageId(p.id); setSel(null); }}>
                {nodeLabel(p)}
              </button>
              {page?.id === p.id && (
                <Outline nodes={p.children ?? []} depth={1} selected={picked?.id ?? null}
                         onSelect={setSel} />
              )}
            </div>
          ))}
        </aside>
        <div className="ud-canvas">
          {page ? <Canvas slug={slug} page={page} version={version ?? pinned} mode={mode}
                          selected={picked?.id ?? null} onSelect={setSel}
                          highlight={hl} rev={rev} editable={editable} busy={editing.busy || editing.uncertain}
                          selectedAuthored={inspection?.id === picked?.id ? inspection?.authored : null} freePosition={freePosition}
                          ancestorsRotated={parents.some((p) => !!p.rotate)} onCommit={saveNode} />
            : !err && <p className="muted">Loading...</p>}
        </div>
        <aside className="ud-inspector" aria-label="Inspector">
          <h2>Inspector</h2>
          {picked && inspection?.id === picked.id ? <Inspector inspection={inspection} disabled={!editable} freePosition={freePosition} frames={resolved ? prototypeFrames(resolved) : []} onSave={(changes) => saveNode(inspection.id, changes)} /> : <p className="muted">{picked ? "Loading node…" : "Click a node."}</p>}
          <section className="ud-comments" aria-label="Comments">
            <h2>Comments</h2>
            <ul>
              {comments.map((c) => (
                <li key={c.id} className="ud-comment" data-resolved={c.resolved_at ? "" : undefined}
                    onClick={() => setHl(c.node_id)}>
                  <p>{c.body}</p>
                  <span className="muted">{c.author}{c.node_id ? ` on ${c.node_id}` : ""}</span>{" "}
                  {!c.resolved_at && !c.parent_id && (
                    <button type="button" onClick={(e) => { e.stopPropagation(); resolve(c.id); }}>
                      Resolve</button>
                  )}
                </li>
              ))}
              {!comments.length && <li className="muted">No comments.</li>}
            </ul>
            <textarea value={draft} onChange={(e) => setDraft(e.target.value)} rows={3}
                      placeholder={picked ? `Comment on ${picked.id}` : "Comment on the document"} />
            <button type="button" onClick={addComment}>Add comment</button>
          </section>
        </aside>
      </div>
    </div>
  );
}
