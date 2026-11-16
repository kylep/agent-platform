import { useEffect, useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { Canvas } from "../components/ui-designer/Canvas";
import { useTitle } from "../lib/title";
import { UD, flatten, nodeLabel, type RNode, type Resolved, type UiVersionRow } from "../lib/uiDesigner";

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

  useEffect(() => {
    api<{ version: number; versions: UiVersionRow[] }>(`${UD}/${encodeURIComponent(slug)}/versions`)
      .then((r) => { setVersions(r.versions); setCurrent(r.version); })
      .catch((e) => setErr(String(e.message ?? e)));
  }, [slug]);

  useEffect(() => {
    setErr("");
    const q = new URLSearchParams({ mode });
    if (version) q.set("version", String(version));
    api<Resolved>(`${UD}/${encodeURIComponent(slug)}/resolved?${q}`)
      .then((r) => { setResolved(r); setSel(null); })
      .catch((e) => setErr(String(e.message ?? e)));
  }, [slug, version, mode]);

  const pages = resolved?.pages ?? [];
  const page = pages.find((p) => p.id === pageId) ?? pages[0] ?? null;
  const known = useMemo(() => flatten(page?.children ?? []), [page]);
  const picked = sel ? known.find((n) => n.id === sel.id) ?? sel : null;

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
      </div>
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
                          selected={picked?.id ?? null} onSelect={setSel} />
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
        </aside>
      </div>
    </div>
  );
}
