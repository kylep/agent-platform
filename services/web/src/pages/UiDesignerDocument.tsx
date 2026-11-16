import { useEffect, useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api, apiErrorMessage } from "../api";
import { Canvas } from "../components/ui-designer/Canvas";
import { useTitle } from "../lib/title";
import { UD, flatten, nodeLabel, opSummary, type RNode, type Resolved, type UiComment, type UiDiff, type UiEvent, type UiExport, type UiVersionRow } from "../lib/uiDesigner";

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
  const [cmp, setCmp] = useState<{ from: string; to: string; diff: UiDiff | null } | null>(null);
  const [exported, setExported] = useState<UiExport | null>(null);
  const base = `${UD}/${encodeURIComponent(slug)}`;

  // Live refresh: the events stream carries every document; only the open one reloads.
  useEffect(() => {
    if (typeof EventSource === "undefined") return;
    const es = new EventSource("/api/ui-designer/events", { withCredentials: true });
    es.addEventListener("design", (m) => {
      try {
        const ev = JSON.parse(m.data) as UiEvent;
        if (ev.slug === slug) setRev((r) => r + 1);
      } catch { /* non-JSON frame */ }
    });
    return () => es.close();
  }, [slug]);

  useEffect(() => {
    api<{ comments: UiComment[] }>(`${base}/comments`)
      .then((r) => setComments(r.comments ?? []))
      .catch((e) => setErr(String(e.message ?? e)));
  }, [base, rev]);

  useEffect(() => {
    api<{ version: number; versions: UiVersionRow[] }>(`${UD}/${encodeURIComponent(slug)}/versions`)
      .then((r) => { setVersions(r.versions); setCurrent(r.version); })
      .catch((e) => setErr(String(e.message ?? e)));
  }, [slug, rev]);

  useEffect(() => {
    setErr("");
    const q = new URLSearchParams({ mode });
    if (version) q.set("version", String(version));
    api<Resolved>(`${UD}/${encodeURIComponent(slug)}/resolved?${q}`)
      .then((r) => { setResolved(r); setSel(null); })
      .catch((e) => setErr(String(e.message ?? e)));
  }, [slug, version, mode, rev]);

  const pages = resolved?.pages ?? [];
  const page = pages.find((p) => p.id === pageId) ?? pages[0] ?? null;
  const known = useMemo(() => flatten(page?.children ?? []), [page]);
  const picked = sel ? known.find((n) => n.id === sel.id) ?? sel : null;

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
  const doExport = () => act(async () => {
    setExported(await api<UiExport>(`${base}/export`, { method: "POST",
      body: JSON.stringify({ node: page?.id ?? null, version: version ?? undefined, mode }) }));
  });
  async function loadDiff(from: string, to: string) {
    setCmp({ from, to, diff: null });
    const d = await act(() => api<UiDiff>(`${base}/diff?from=${from}&to=${to}`));
    setCmp({ from, to, diff: d ?? null });
  }
  const openCompare = () => {
    const to = String(current ?? ""), from = String(versions.length > 1 ? versions[1].version : to);
    return loadDiff(from, to);
  };

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
          <button type="button" onClick={labelVersion}>Label this version</button>
          <button type="button" onClick={() => (cmp ? setCmp(null) : openCompare())}>
            {cmp ? "Close compare" : "Compare"}</button>
          <button type="button" onClick={doExport}>Export PNG</button>
        </div>
      </div>
      {exported && (
        <p role="status">Exported:{" "}
          <Link to={exported.url}>
            {exported.name || exported.url}</Link></p>
      )}
      {cmp && page && (
        <section aria-label="Compare versions">
          <div className="ud-actions">
            {(["from", "to"] as const).map((k) => (
              <label key={k} className="muted">{k}{" "}
                <select value={cmp[k]} aria-label={`Compare ${k}`}
                        onChange={(e) => loadDiff(k === "from" ? e.target.value : cmp.from,
                                                  k === "to" ? e.target.value : cmp.to)}>
                  {versions.map((v) => <option key={v.version} value={v.version}>
                    {`v${v.version}${v.label ? ` ${v.label}` : ""}`}</option>)}
                </select>
              </label>
            ))}
          </div>
          <div className="ud-compare">
            {[cmp.from, cmp.to].map((v, i) => (
              <figure key={i}><img alt={`Version ${v}`}
                src={`${base}/render?${new URLSearchParams({ mode, node: page.id, version: v })}`} />
                <figcaption className="muted">v{v}</figcaption></figure>
            ))}
          </div>
          {cmp.diff && (
            <ul className="muted">
              {cmp.diff.chunks.length === 0 && <li>No changes</li>}
              {cmp.diff.chunks.map((c) => (
                <li key={c.version}>v{c.version}{c.reason ? ` ${c.reason}` : ""}: {c.ops.map(opSummary).join(", ")}</li>
              ))}
              {cmp.diff.truncated && <li>Truncated</li>}
            </ul>
          )}
        </section>
      )}
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
          {page ? <Canvas slug={slug} page={page} version={version} mode={mode}
                          selected={picked?.id ?? null} onSelect={setSel}
                          highlight={hl} rev={rev} />
            : !err && <p className="muted">Loading...</p>}
        </div>
        <aside className="ud-inspector" aria-label="Inspector">
          <h2>Inspector</h2>
          {picked ? (
            <dl>
              <dt>id</dt><dd>{picked.id}</dd>
              <dt>type</dt><dd>{picked.type ?? "node"}</dd>
              <dt>size</dt><dd>{Math.round(picked.w ?? 0)} x {Math.round(picked.h ?? 0)}</dd>
            </dl>
          ) : <p className="muted">Click a node.</p>}
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
