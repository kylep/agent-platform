import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api, apiErrorMessage } from "../api";
import { prototypeFrames } from "../lib/prototype";
import IconPicker from "../components/ui-designer/IconPicker";
import VectorTools from "../components/ui-designer/VectorTools";
import LibrariesPanel from "../components/ui-designer/LibrariesPanel";
import FindingsPanel from "../components/ui-designer/FindingsPanel";
import CompareVersions from "../components/ui-designer/CompareVersions";
import MotionControls from "../components/ui-designer/MotionControls";
import PresenceBar from "../components/ui-designer/PresenceBar";
import { useDesignerPresence } from "../lib/useDesignerPresence";
import SharingPanel from "../components/ui-designer/SharingPanel";
import { useDesignerAccess } from "../lib/useDesignerAccess";
import FontsPanel from "../components/ui-designer/FontsPanel";
import type { UiFont, UiFonts } from "../lib/uiFonts";
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
  const linkedNode = params.get("node");
  const handledLink = useRef("");
  const failedRenders=useRef(new Set<string>());
  const vParam = params.get("version");
  const version = vParam && /^\d+$/.test(vParam) ? Number(vParam) : null;
  useTitle(slug, "UI Designer");

  const [resolved, setResolved] = useState<Resolved | null>(null);
  const resolvedOwner = useRef("");
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
  const [showLibraries, setShowLibraries] = useState(false);
  const [libraryLocked, setLibraryLocked] = useState(false);
  const [showSharing,setShowSharing]=useState(false),[sharingPending,setSharingPending]=useState(false);
  const [showFonts,setShowFonts]=useState(false),[fontLocked,setFontLocked]=useState(false);
  const writeLocked=libraryLocked || fontLocked;
  const [fontSnapshot,setFontSnapshot]=useState<{base:string;version:number;fonts:UiFont[]}|null>(null),[fontsLoading,setFontsLoading]=useState(false),[fontsError,setFontsError]=useState("");
  const [vectorEpoch, setVectorEpoch] = useState(0);
  const [showIcons, setShowIcons] = useState(false);
  const base = `${UD}/${encodeURIComponent(slug)}`;

  const accessState=useDesignerAccess(base);
  const {access,canRead,reload:reloadAccess,denied:accessDenied}=accessState;
  const [globalWrite, setGlobalWrite] = useState(false);
  const canWrite=globalWrite && canRead && !!access?.can_write;
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
  const editing = useDesignerEditing(base, refresh, `${base}:${version ?? "latest"}:${access?.document_id ?? "checking"}`);
  const eventHandler = useRef(editing.external); eventHandler.current = editing.external;
  useEffect(() => { api<{ role: string }>("/api/whoami").then((me) => setGlobalWrite(["admin", "operator"].includes(me.role))).catch(() => setGlobalWrite(false)); }, []);
  useEffect(() => {
    resolvedOwner.current = "";failedRenders.current.clear();
    setPinned(null); setInspection(null); setParents([]); setStale(false); setCurrent(null);
    setVersions([]); setResolved(null); setPageId(null); setSel(null); setComments([]);
    setDraft(""); setComparing(false); setShowFindings(false); setShowLibraries(false); setLibraryLocked(false);setShowFonts(false);setFontLocked(false);setFontSnapshot(null);setShowSharing(false);setSharingPending(false); setVectorEpoch(0); setShowIcons(false); setHl(null); setErr("");
  }, [slug]);

  // Live refresh: the events stream carries every document; only the open one reloads.
  useEffect(() => {
    if (typeof EventSource === "undefined") return;
    const es = new EventSource("/api/ui-designer/events", { withCredentials: true });
    es.addEventListener("design", (m) => {
      try {
        const ev = JSON.parse(m.data) as UiEvent;
        if (ev.slug === slug) {
          if(ev.type === "access"){reloadAccess();return;}
          if (ev.type === "version" || ev.type === "imported" || ev.type === "restored") {
            if (ev.version) eventHandler.current(ev.version);
          } else setRev((r) => r + 1);
        }
      } catch { /* non-JSON frame */ }
    });
    return () => es.close();
  }, [slug,reloadAccess]);

  useEffect(() => {
    if(!canRead)return;
    let cancelled = false;
    api<{ comments: UiComment[] }>(`${base}/comments`)
      .then((r) => { if (!cancelled) setComments(r.comments ?? []); })
      .catch((e) => { if (!cancelled) if(e instanceof Error && /^(403|404):/.test(e.message))accessDenied();else setErr(String(e.message ?? e)); });
    return () => { cancelled = true; };
  }, [base, rev,canRead,accessDenied]);

  useEffect(() => {
    if(!canRead)return;
    const seq = ++headLoad.current;
    let cancelled = false;
    api<{ version: number; versions: UiVersionRow[] }>(`${UD}/${encodeURIComponent(slug)}/versions`)
      .then((r) => { if (cancelled || seq !== headLoad.current) return; setVersions(r.versions); setCurrent((old) => Math.max(old ?? 0, r.version)); if (eventHandler.current(r.version)) setPinned((old) => old == null || r.version > old ? r.version : old); })
      .catch((e) => { if (!cancelled && seq === headLoad.current) if(e instanceof Error && /^(403|404):/.test(e.message))accessDenied();else setErr(String(e.message ?? e)); });
    return () => { cancelled = true; };
  }, [slug, rev, version,canRead,accessDenied]);

  useEffect(() => {
    setErr("");
    if (!canRead || !(version ?? pinned)) return;
    let cancelled = false;
    const q = new URLSearchParams({ mode, version: String(version ?? pinned) });
    api<Resolved>(`${UD}/${encodeURIComponent(slug)}/resolved?${q}`)
      .then((r) => { if (!cancelled) { resolvedOwner.current = base; setResolved(r); } })
      .catch((e) => { if (!cancelled) if(e instanceof Error && /^(403|404):/.test(e.message))accessDenied();else setErr(String(e.message ?? e)); });
    return () => { cancelled = true; };
  }, [base, slug, version, pinned, mode, rev,canRead,accessDenied]);

  useEffect(()=>{
    if(!canRead || !(version ?? pinned))return;
    const controller=new AbortController(),selectedVersion=(version ?? pinned)!;setFontsLoading(true);setFontsError("");
    api<UiFonts>(`${base}/fonts?${new URLSearchParams({version:String(selectedVersion)})}`,{signal:controller.signal})
      .then((r)=>{if(!controller.signal.aborted)setFontSnapshot({base,version:selectedVersion,fonts:r.fonts});})
      .catch((e)=>{if(!controller.signal.aborted)setFontsError(apiErrorMessage(e,"Could not load installed fonts."));})
      .finally(()=>{if(!controller.signal.aborted)setFontsLoading(false);});
    return()=>controller.abort();
  },[base,version,pinned,rev,canRead]);
  const installedFonts=fontSnapshot?.base===base && fontSnapshot.version===(version ?? pinned) ? fontSnapshot.fonts:[];
  useEffect(()=>{if(!canRead){setResolved(null);setSel(null);setInspection(null);setComments([]);setFontSnapshot(null);setParents([]);setPinned(null);setCurrent(null);setVersions([]);setDraft("");setPageId(null);setHl(null);}},[canRead]);
  const pages = resolved?.pages ?? [];
  const page = pages.find((p) => p.id === pageId) ?? pages[0] ?? null;
  const known = useMemo(() => flatten(page?.children ?? []), [page]);
  const picked = sel ? known.find((n) => n.id === sel.id) ?? null : null;

  useEffect(() => {
    const key = `${base}:${linkedNode ?? ""}`;
    if (!linkedNode) { handledLink.current = ""; return; }
    if (!resolved || resolvedOwner.current !== base || handledLink.current === key) return;
    handledLink.current = key;
    for (const candidate of resolved.pages ?? []) {
      const node = flatten(candidate.children ?? []).find((item) => item.id === linkedNode);
      if (node) { setPageId(candidate.id); setSel(node); return; }
    }
    setErr("The linked node is unavailable in this version.");
  }, [base, linkedNode, resolved]);

  const selectedId = picked?.id;
  const pageNodes = useRef<RNode[]>([]); pageNodes.current = page?.children ?? [];
  useEffect(() => {
    if (!canRead || !selectedId || !pinned || stale) return;
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
  }, [base, selectedId, pinned, version, mode, stale,canRead]);
  const parent = parents.at(-1);
  const freePosition = !parent?.layout || !!inspection?.authored?.absolute;
  const editable = canWrite && !version && !stale && !!inspection?.authored && inspection.version === pinned && inspection.id === picked?.id && !editing.busy && !editing.uncertain && !writeLocked;
  const presence=useDesignerPresence({base,documentId:access?.document_id ?? null,version:version ?? pinned,pageId:page?.id ?? null,nodeId:picked?.id.includes("/") ? known.find((node)=>node.id===picked.id.split("/")[0] && node.of)?.id ?? null : picked?.id ?? null,editing:editable,accessVersion:access?.access_version ?? 0,enabled:canRead,onAccessCheck:reloadAccess});
  function saveNode(id: string, changes: Record<string, unknown>) {
    if (!editable || inspection?.id !== id || picked?.id !== id || !inspection.authored) return;
    const before = Object.fromEntries(Object.keys(changes).map((key) => [key, inspection.authored![key] ?? null]));
    const resize = Object.entries(changes).some(([key, value]) => ["min_w", "max_w", "min_h", "max_h"].includes(key) || (["w", "h"].includes(key) && typeof value === "number"));
    editing.commit([{ op: resize ? "resize" : "update", id, set: changes }], [{ op: "update", id, set: before }]);
  }

  async function act<T>(fn: () => Promise<T>): Promise<T | undefined> {
    try { setErr(""); return await fn(); } catch (e) { setErr(apiErrorMessage(e, "request failed")); }
  }
  const addComment = () => act(async () => {
    if(!canWrite)return;
    if (!draft.trim()) return;
    await api(`${base}/comments`, { method: "POST",
      body: JSON.stringify({ node_id: picked?.id ?? null, body: draft.trim() }) });
    setDraft(""); setRev((r) => r + 1);
  });
  const resolve = (id: string) => act(async () => {
    if(!canWrite)return;
    await api(`${base}/comments/${encodeURIComponent(id)}/resolve`, { method: "POST" });
    setRev((r) => r + 1);
  });
  const labelVersion = () => act(async () => {
    if(!canWrite)return;
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

  const sharingPanel=showSharing && access ? <SharingPanel key={`${base}:${access.document_id}`} base={base} access={access} checking={accessState.checking} onRefresh={reloadAccess} onPending={setSharingPending} />:null;
  if(!canRead)return <div className="ud-doc"><div className="page-header"><h1><Link to="/ui-designer">UI Designer</Link> / {slug}</h1>{access && <Button disabled={sharingPending} onClick={()=>setShowSharing((open)=>!open)}>{showSharing ? "Close sharing":"Sharing"}</Button>}</div>{sharingPanel}{accessState.checking ? <p role="status">Checking document access…</p>:<><p role="alert">{accessState.unavailable ? "This design is unavailable or private.":accessState.error || "Could not load this design."}</p><Button onClick={reloadAccess}>Retry document access</Button></>}</div>;
  return (
    <div className="ud-doc">
      <div className="page-header">
        <h1><Link to="/ui-designer">UI Designer</Link> / {slug}</h1>
        <label className="muted">Version{" "}
          <select value={version ?? ""} onChange={(e) => pickVersion(e.target.value)}
                  aria-label="Version" disabled={writeLocked}>
            <option value="">{current ? `Latest (v${current})` : "Latest"}</option>
            {versions.map((v) => (
              <option key={v.version} value={v.version}>
                {`v${v.version}${v.label ? ` ${v.label}` : ""} · ${v.author}`}
              </option>
            ))}
          </select>
        </label>
        <div className="ud-actions">
          <Button disabled={sharingPending} onClick={()=>setShowSharing((open)=>!open)}>{showSharing ? "Close sharing":"Sharing"}</Button>
          {(version ?? pinned) && <Link to={`/ui-designer/${encodeURIComponent(slug)}/play?${new URLSearchParams({ version: String(version ?? pinned), mode, ...(page && page.children?.find((n)=>n.type === "frame" && !n.of) ? { frame: page.children.find((n)=>n.type === "frame" && !n.of)!.id } : {}) })}`}>Play prototype</Link>}
          {canWrite && !version && <><Button onClick={editing.undo} disabled={writeLocked || stale || editing.busy || editing.uncertain || !editing.canUndo}>Undo</Button><Button onClick={editing.redo} disabled={writeLocked || stale || editing.busy || editing.uncertain || !editing.canRedo}>Redo</Button></>}
          <Button onClick={() => setShowIcons((open) => !open)}>{showIcons ? "Close icons" : "Icons"}</Button>
          <Button disabled={fontLocked} onClick={()=>setShowFonts((open)=>!open)}>{showFonts ? "Close fonts":"Fonts"}</Button>
          <Button disabled={writeLocked} onClick={() => setShowLibraries((open) => !open)}>{showLibraries ? "Close libraries" : "Libraries"}</Button>
          <Button onClick={() => setShowFindings((open) => !open)}>{showFindings ? "Close findings" : "Findings"}</Button>
          <button type="button" disabled={!canWrite} onClick={labelVersion}>Label this version</button>
          <button type="button" onClick={() => setComparing((open) => !open)}>
            {comparing ? "Close compare" : "Compare"}</button>

        </div>
      </div>
      {sharingPanel}
      {presence.active && <PresenceBar sessions={presence.sessions} notice={presence.notice} version={version ?? pinned} />}
      {(version ?? pinned) && <ExportControls key={`${base}:${version ?? pinned}:${mode}`} base={base} version={(version ?? pinned)!} mode={mode} pageId={page?.id ?? null} selectedId={picked?.id ?? null} canExport={canWrite} />}
      {(version ?? pinned) && resolved && <MotionControls key={`motion:${base}:${version ?? pinned}:${mode}`} base={base} version={(version ?? pinned)!} mode={mode} frames={prototypeFrames(resolved)} canExport={canWrite} />}
      {comparing && page && (version ?? pinned) && <CompareVersions key={base} base={base} versions={versions} initialVersion={(version ?? pinned)!} pageId={page.id} selectedId={picked?.id ?? null} mode={mode} onSelectNode={(id) => { const node = known.find((n) => n.id === id); if (node) setSel(node); }} />}
      {showIcons && page && <IconPicker key={base} pageId={page.id} pageTitle={page.title || page.id} selected={picked} disabled={!canWrite || !!version || stale || editing.busy || editing.uncertain || writeLocked} onInsert={(op) => {
        if (canWrite && !version && !stale && !editing.busy && !editing.uncertain && !writeLocked) editing.commit([op], []);
      }} />}
      {showFonts && (version ?? pinned) && <FontsPanel key={`${base}:${version ?? "latest"}`} base={base} version={(version ?? pinned)!} fonts={installedFonts} loading={fontsLoading} listError={fontsError} canWrite={canWrite && !version} editorBusy={editing.busy || editing.uncertain || libraryLocked || stale} onWriteLock={setFontLocked} onCommitted={(savedVersion)=>{editing.reset("Font installed. Undo history was cleared; restore an earlier version to undo the upload.");editing.pin(savedVersion);refresh(Math.max(pinned ?? 0,savedVersion),true);}} onConflict={()=>{editing.reset("The document changed before the font upload. Reloaded the latest version; undo history was cleared.");refresh(undefined,true);}} />}
      {showLibraries && (version ?? pinned) && <LibrariesPanel key={base} base={base} version={(version ?? pinned)!} canWrite={canWrite && !version} editorBusy={editing.busy || editing.uncertain || fontLocked} onWriteLock={setLibraryLocked} onCommitted={(savedVersion) => {
        editing.reset("Library changes saved. Undo history was cleared."); editing.pin(savedVersion); refresh(Math.max(pinned ?? 0, savedVersion), true);
      }} onConflict={() => { editing.reset("The document changed before the library save. Reloaded the latest version; undo history was cleared."); refresh(undefined, true); }} />}
      {showFindings && (version ?? pinned) && <FindingsPanel key={base} base={base} version={(version ?? pinned)!} mode={mode} pageId={page?.id ?? null} selectedId={picked?.id ?? null} canvasNodeIds={pages.flatMap((p) => flatten(p.children ?? []).map((n) => n.id))} onSelectNode={(id) => {
        for (const candidatePage of pages) { const node = flatten(candidatePage.children ?? []).find((n) => n.id === id); if (node) { setPageId(candidatePage.id); setSel(node); return; } }
      }} />}
      {editing.message && <p role="status">{editing.message}</p>}
      {editing.uncertain && <Button onClick={editing.retry} disabled={editing.busy || !canWrite}>Retry save</Button>}
      {stale && <p role="status">The document changed. Your inspector draft is preserved. <Button onClick={() => { setStale(false); setInspection(null); setVectorEpoch((n) => n + 1); }}>Discard draft and reload inspector</Button></p>}
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
                          selected={picked?.id ?? null} onSelect={setSel} onCursor={presence.onCursor} remotePresence={presence.sessions.filter((peer)=>peer.version===(version ?? pinned) && peer.page_id===page.id)}
                          highlight={hl} rev={rev} editable={editable} onRenderError={()=>{setErr("Could not render this page.");const key=`${base}:${version ?? pinned}:${page.id}:${mode}`;if(!failedRenders.current.has(key)){if(failedRenders.current.size>=16)failedRenders.current.clear();failedRenders.current.add(key);reloadAccess();}}} busy={editing.busy || editing.uncertain}
                          selectedAuthored={inspection?.id === picked?.id ? inspection?.authored : null} freePosition={freePosition}
                          ancestorsRotated={parents.some((p) => !!p.rotate)} onCommit={saveNode} />
            : !err && <p className="muted">{resolved ? "This document has no pages yet." : "Loading..."}</p>}
        </div>
        <aside className="ud-inspector" aria-label="Inspector">
          <h2>Inspector</h2>
          {picked && inspection?.id === picked.id ? <Inspector fonts={installedFonts} inspection={inspection} disabled={!editable} freePosition={freePosition} frames={resolved ? prototypeFrames(resolved) : []} onSave={(changes) => saveNode(inspection.id, changes)} /> : <p className="muted">{picked ? "Loading node…" : "Click a node."}</p>}
          {page && (version ?? pinned) && <VectorTools key={`${base}:${vectorEpoch}`} base={base} version={(version ?? pinned)!} page={page} selected={picked} inspection={inspection} disabled={!canWrite || !!version || stale || editing.busy || editing.uncertain || writeLocked} onCommit={(ops, inverse) => {
            if (canWrite && !version && !stale && !editing.busy && !editing.uncertain && !writeLocked) editing.commit(ops, inverse);
          }} onSelect={setSel} onUpdate={saveNode} />}
          <section className="ud-comments" aria-label="Comments">
            <h2>Comments</h2>
            <ul>
              {comments.map((c) => (
                <li key={c.id} className="ud-comment" data-resolved={c.resolved_at ? "" : undefined}
                    onClick={() => setHl(c.node_id)}>
                  <p>{c.body}</p>
                  <span className="muted">{c.author}{c.node_id ? ` on ${c.node_id}` : ""}</span>{" "}
                  {canWrite && !c.resolved_at && !c.parent_id && (
                    <button type="button" onClick={(e) => { e.stopPropagation(); resolve(c.id); }}>
                      Resolve</button>
                  )}
                </li>
              ))}
              {!comments.length && <li className="muted">No comments.</li>}
            </ul>
            <textarea disabled={!canWrite} value={draft} onChange={(e) => setDraft(e.target.value)} rows={3}
                      placeholder={picked ? `Comment on ${picked.id}` : "Comment on the document"} />
            <button type="button" disabled={!canWrite} onClick={addComment}>Add comment</button>
          </section>
        </aside>
      </div>
    </div>
  );
}
