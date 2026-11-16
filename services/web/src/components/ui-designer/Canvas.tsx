import { useEffect, useRef, useState, type PointerEvent, type WheelEvent } from "react";
import { UD, flatten, pageBounds, type RNode, type RPage } from "../../lib/uiDesigner";

/** The server's PNG render in a pan/zoom viewport with a transparent hit-box overlay.
 * The browser paints nothing itself (the server is the only painter, so the picture
 * matches what an agent sees); the overlay only selects. An <img> cannot load the SVG
 * route's external asset URLs, so the canvas uses the raster route. */
export function Canvas({ slug, page, version, mode, selected, onSelect, highlight, rev }: {
  slug: string; page: RPage; version: number | null; mode: "dark" | "light";
  selected: string | null; onSelect: (n: RNode | null) => void;
  highlight?: string | null; rev?: number;
}) {
  const b = pageBounds(page);
  const [view, setView] = useState({ s: 1, x: 20, y: 20 });
  const drag = useRef<{ px: number; py: number; moved: boolean } | null>(null);
  // Whole-number scale the render route accepts: sharper on hi-dpi screens.
  const scale = typeof window !== "undefined" && window.devicePixelRatio > 1.25 ? 2 : 1;
  const q = new URLSearchParams({ mode, node: page.id, scale: String(scale) });
  if (version) q.set("version", String(version));
  if (rev) q.set("rev", String(rev));
  useEffect(() => setView({ s: 1, x: 20, y: 20 }), [page.id]);

  function onWheel(e: WheelEvent) {
    const f = e.deltaY < 0 ? 1.1 : 1 / 1.1;
    setView((v) => ({ ...v, s: Math.min(8, Math.max(0.1, v.s * f)) }));
  }
  function down(e: PointerEvent) {
    drag.current = { px: e.clientX, py: e.clientY, moved: false };
    e.currentTarget.setPointerCapture(e.pointerId);
  }
  function move(e: PointerEvent) {
    const d = drag.current;
    if (!d) return;
    const dx = e.clientX - d.px, dy = e.clientY - d.py;
    if (Math.abs(dx) + Math.abs(dy) > 3) d.moved = true;
    if (d.moved) {
      d.px = e.clientX; d.py = e.clientY;
      setView((v) => ({ ...v, x: v.x + dx, y: v.y + dy }));
    }
  }
  const up = () => { window.setTimeout(() => { drag.current = null; }, 0); };

  // Deepest-last order paints children above parents, so reverse for hit priority.
  const boxes = flatten(page.children ?? []);
  return (
    <div className="ud-viewport" onWheel={onWheel} onPointerDown={down} onPointerMove={move}
         onPointerUp={up} onClick={() => { if (!drag.current?.moved) onSelect(null); }}>
      <div className="ud-zoom">
        <button type="button" onClick={(e) => { e.stopPropagation(); setView((v) => ({ ...v, s: v.s * 1.25 })); }}
                aria-label="Zoom in">+</button>
        <button type="button" onClick={(e) => { e.stopPropagation(); setView((v) => ({ ...v, s: v.s / 1.25 })); }}
                aria-label="Zoom out">-</button>
        <button type="button" onClick={(e) => { e.stopPropagation(); setView({ s: 1, x: 20, y: 20 }); }}>Reset</button>
      </div>
      <div className="ud-stage" style={{ width: b.w, height: b.h,
           transform: `translate(${view.x}px, ${view.y}px) scale(${view.s})` }}>
        <img className="ud-svg" draggable={false} alt={`Page ${page.id}`}
             src={`${UD}/${encodeURIComponent(slug)}/render?${q}`} width={b.w} height={b.h} />
        <svg className="ud-overlay" width={b.w} height={b.h} viewBox={`${b.x} ${b.y} ${b.w} ${b.h}`}>
          {boxes.map((n) => (
            <rect key={n.id} x={n.x ?? 0} y={n.y ?? 0} width={n.w ?? 0} height={n.h ?? 0}
                  className="ud-hit" data-selected={n.id === selected ? "" : undefined}
                  data-highlight={n.id === highlight ? "" : undefined}
                  onClick={(e) => { e.stopPropagation(); if (!drag.current?.moved) onSelect(n); }} />
          ))}
        </svg>
      </div>
    </div>
  );
}
