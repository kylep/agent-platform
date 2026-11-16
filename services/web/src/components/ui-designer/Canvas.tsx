import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent, type WheelEvent } from "react";
import { UD, flatten, pageBounds, type RNode, type RPage } from "../../lib/uiDesigner";

type Changes = Record<string, unknown>;
type Gesture = {
  kind: "move" | "resize"; id: string; px: number; py: number; pointer: number;
  node: RNode; x: number; y: number; w: number; h: number; corner?: string;
  changes: Changes; moved: boolean;
};
const number = (v: unknown, fallback: number) => typeof v === "number" && Number.isFinite(v) ? v : fallback;
const rounded = (v: number) => Math.round(v * 100) / 100;
const size = (v: number) => Math.min(100_000, Math.max(1, rounded(v)));

/** The server render remains the painter. Manipulation previews change only the
 * selection overlay; the parent submits one CAS edit after a completed gesture. */
export function Canvas({ slug, page, version, mode, selected, onSelect, highlight, rev,
  editable = false, onCommit, selectedAuthored, freePosition = false, busy = false, ancestorsRotated = false }: {
  slug: string; page: RPage; version: number | null; mode: "dark" | "light";
  selected: string | null; onSelect: (n: RNode | null) => void;
  highlight?: string | null; rev?: number; editable?: boolean;
  onCommit?: (id: string, changes: Changes) => void;
  selectedAuthored?: Record<string, unknown> | null; freePosition?: boolean;
  busy?: boolean; ancestorsRotated?: boolean;
}) {
  const b = pageBounds(page);
  const [view, setView] = useState({ s: 1, x: 20, y: 20 });
  const viewport = useRef<HTMLDivElement>(null);
  const pan = useRef<{ px: number; py: number; moved: boolean } | null>(null);
  const gesture = useRef<Gesture | null>(null);
  const [preview, setPreview] = useState<{ x: number; y: number; w: number; h: number } | null>(null);
  const suppressClick = useRef(false);
  const boxes = flatten(page.children ?? []);
  const node = boxes.find((n) => n.id === selected);
  const rotated = ancestorsRotated || number(selectedAuthored?.rotate, 0) !== 0;
  const canEdit = editable && !busy && !!onCommit && !!selectedAuthored && !rotated;
  const canMove = canEdit && freePosition;
  const canResize = canEdit && typeof selectedAuthored?.w === "number" && typeof selectedAuthored?.h === "number";
  const scale = typeof window !== "undefined" && window.devicePixelRatio > 1.25 ? 2 : 1;
  const q = new URLSearchParams({ mode, node: page.id, scale: String(scale) });
  if (version) q.set("version", String(version));
  if (rev) q.set("rev", String(rev));
  const cancel = () => { gesture.current = null; pan.current = null; setPreview(null); };
  useEffect(() => { setView({ s: 1, x: 20, y: 20 }); }, [page.id]);
  useEffect(() => { cancel(); }, [page.id, version, selected, editable, busy, mode, rev]);

  function onWheel(e: WheelEvent) {
    if (gesture.current) return;
    const f = e.deltaY < 0 ? 1.1 : 1 / 1.1;
    setView((v) => ({ ...v, s: Math.min(8, Math.max(0.1, v.s * f)) }));
  }
  function down(e: PointerEvent) {
    if (e.button !== 0 || (e.target as Element).closest("button")) return;
    viewport.current?.focus();
    suppressClick.current = false;
    pan.current = { px: e.clientX, py: e.clientY, moved: false };
    e.currentTarget.setPointerCapture(e.pointerId);
  }
  function begin(e: PointerEvent, n: RNode, corner?: string) {
    e.stopPropagation();
    if (e.button !== 0) return;
    viewport.current?.focus();
    suppressClick.current = false;
    onSelect(n);
    if (n.id !== selected || !(corner ? canResize : canMove)) return;
    gesture.current = {
      kind: corner ? "resize" : "move", id: n.id, px: e.clientX, py: e.clientY, pointer: e.pointerId,
      node: n, x: number(selectedAuthored?.x, 0), y: number(selectedAuthored?.y, 0),
      w: number(selectedAuthored?.w, n.w ?? 1), h: number(selectedAuthored?.h, n.h ?? 1),
      corner, changes: {}, moved: false,
    };
    e.currentTarget.setPointerCapture(e.pointerId);
    e.preventDefault();
  }
  function move(e: PointerEvent) {
    const g = gesture.current;
    if (g) {
      if (g.pointer !== e.pointerId) return;
      const dx = (e.clientX - g.px) / view.s, dy = (e.clientY - g.py) / view.s;
      if (Math.abs(e.clientX - g.px) + Math.abs(e.clientY - g.py) < 3 && !g.moved) return;
      g.moved = true;
      let x = g.x, y = g.y, w = g.w, h = g.h;
      if (g.kind === "move") {
        x = rounded(g.x + dx); y = rounded(g.y + dy);
        g.changes = { x, y };
      } else {
        const west = g.corner?.includes("w"), north = g.corner?.includes("n");
        if (g.corner?.includes("e") || west) w = size(g.w + (west ? -dx : dx));
        if (g.corner?.includes("s") || north) h = size(g.h + (north ? -dy : dy));
        if (west) x = rounded(g.x + g.w - w);
        if (north) y = rounded(g.y + g.h - h);
        g.changes = { w, h, ...(west ? { x } : {}), ...(north ? { y } : {}) };
      }
      setPreview({ x: (g.node.x ?? 0) + x - g.x, y: (g.node.y ?? 0) + y - g.y, w, h });
      return;
    }
    const d = pan.current;
    if (!d) return;
    const dx = e.clientX - d.px, dy = e.clientY - d.py;
    if (Math.abs(dx) + Math.abs(dy) > 3) d.moved = true;
    if (d.moved) {
      d.px = e.clientX; d.py = e.clientY;
      setView((v) => ({ ...v, x: v.x + dx, y: v.y + dy }));
    }
  }
  function up() {
    const g = gesture.current;
    suppressClick.current = !!(g?.moved || pan.current?.moved);
    if (g?.moved && canEdit && Object.keys(g.changes).length) onCommit?.(g.id, g.changes);
    cancel();
  }
  function keydown(e: KeyboardEvent) {
    if ((e.target as Element).closest("input,textarea,select,button,[contenteditable=true]")) return;
    if (e.key === "Escape") { cancel(); e.preventDefault(); return; }
    if (!canMove || !selected || e.altKey || e.ctrlKey || e.metaKey || gesture.current) return;
    const delta = e.shiftKey ? 10 : 1;
    const steps: Record<string, [number, number]> = { ArrowLeft: [-delta, 0], ArrowRight: [delta, 0], ArrowUp: [0, -delta], ArrowDown: [0, delta] };
    const step = steps[e.key];
    if (!step) return;
    e.preventDefault();
    onCommit?.(selected, { x: rounded(number(selectedAuthored?.x, 0) + step[0]), y: rounded(number(selectedAuthored?.y, 0) + step[1]) });
  }
  const selection = node ? preview ?? { x: node.x ?? 0, y: node.y ?? 0, w: node.w ?? 0, h: node.h ?? 0 } : null;
  const handles = canResize && selection ? (freePosition ? ["nw", "ne", "sw", "se"] : ["e", "s", "se"]) : [];
  return (
    <div ref={viewport} className="ud-viewport" tabIndex={0} aria-label="Design canvas" onKeyDown={keydown}
         onWheel={onWheel} onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={cancel} onLostPointerCapture={cancel}
         onClick={() => { if (!suppressClick.current) onSelect(null); suppressClick.current = false; }}>
      <div className="ud-zoom" onPointerDown={(e) => e.stopPropagation()} onClick={(e) => e.stopPropagation()}>
        <button type="button" onClick={() => { if (!gesture.current) setView((v) => ({ ...v, s: Math.min(8, v.s * 1.25) })); }} aria-label="Zoom in">+</button>
        <button type="button" onClick={() => { if (!gesture.current) setView((v) => ({ ...v, s: Math.max(.1, v.s / 1.25) })); }} aria-label="Zoom out">-</button>
        <button type="button" onClick={() => { cancel(); setView({ s: 1, x: 20, y: 20 }); }}>Reset</button>
      </div>
      {editable && selected && rotated && <div role="status" style={{ position: "absolute", bottom: 8, left: 8, zIndex: 1, background: "var(--ds-surface)", padding: 8 }}>
        Canvas gestures are unavailable for rotated nodes or ancestors. Use the inspector.
      </div>}
      <div className="ud-stage" style={{ width: b.w, height: b.h, transform: `translate(${view.x}px, ${view.y}px) scale(${view.s})` }}>
        <img className="ud-svg" draggable={false} alt={`Page ${page.id}`} src={`${UD}/${encodeURIComponent(slug)}/render?${q}`} width={b.w} height={b.h} />
        <svg className="ud-overlay" width={b.w} height={b.h} viewBox={`${b.x} ${b.y} ${b.w} ${b.h}`}>
          {boxes.map((n) => <rect key={n.id} x={n.x ?? 0} y={n.y ?? 0} width={n.w ?? 0} height={n.h ?? 0}
            className="ud-hit" data-selected={n.id === selected ? "" : undefined} data-highlight={n.id === highlight ? "" : undefined}
            style={{ cursor: n.id === selected && canMove ? "move" : "pointer" }}
            onPointerDown={(e) => begin(e, n)} onClick={(e) => { e.stopPropagation(); if (!suppressClick.current) onSelect(n); suppressClick.current = false; }} />)}
          {selection && preview && <rect x={selection.x} y={selection.y} width={selection.w} height={selection.h} fill="none" stroke="var(--ds-accent)" strokeWidth={2 / view.s} pointerEvents="none" />}
          {selection && node && handles.map((corner) => {
            const x = corner.includes("w") ? selection.x : corner.includes("e") ? selection.x + selection.w : selection.x + selection.w / 2;
            const y = corner.includes("n") ? selection.y : corner.includes("s") ? selection.y + selection.h : selection.y + selection.h / 2;
            const radius = 5 / view.s;
            return <rect key={corner} x={x - radius} y={y - radius} width={radius * 2} height={radius * 2}
              fill="var(--ds-surface)" stroke="var(--ds-accent)" strokeWidth={1 / view.s} data-resize={corner}
              style={{ cursor: `${corner}-resize` }} onPointerDown={(e) => begin(e, node, corner)} onClick={(e) => e.stopPropagation()} />;
          })}
        </svg>
      </div>
    </div>
  );
}
