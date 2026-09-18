import {
  useCallback, useEffect, useLayoutEffect, useRef, useState,
  type KeyboardEvent, type PointerEvent,
} from "react";
import { Banner } from "@ap/ui/banner";
import { Button } from "@ap/ui/button";
import { Input } from "@ap/ui/field";
import { uploadArtifact, type Artifact } from "../../api";
import { errorDetail } from "./generate";

// Markup (docs/design/23): a mode over the stage. The canvas is the picture's
// own pixels, scaled to the column by CSS; every stroke is kept as an
// operation and the canvas is redrawn from the source plus the stack, which
// is what makes Undo a pop. Save flattens to a PNG and posts it as a derived
// artifact naming its parent — so "circle the thing to change, then Iterate"
// is two clicks. Hand-rolled: about three hundred lines is cheaper than a
// dependency for five tools.

type Pt = { x: number; y: number };
type Tool = "pen" | "arrow" | "rect" | "text" | "crop";
type Op =
  | { kind: "pen"; points: Pt[]; color: string; width: number }
  | { kind: "arrow" | "rect"; from: Pt; to: Pt; color: string; width: number }
  | { kind: "text"; at: Pt; text: string; color: string; size: number }
  // A crop is on the stack too, so Undo takes it back like any stroke; the
  // last one wins, and it is applied at Save rather than drawn.
  | { kind: "crop"; from: Pt; to: Pt };

const TOOLS: { id: Tool; label: string }[] = [
  { id: "pen", label: "Pen" }, { id: "arrow", label: "Arrow" }, { id: "rect", label: "Rectangle" },
  { id: "text", label: "Text" }, { id: "crop", label: "Crop" },
];
const STROKES = [{ label: "Thin", width: 3 }, { label: "Thick", width: 6 }];
const TEXT_PX = 24;
// A drag shorter than this is a click, not a shape — a crop of two pixels is
// never what was meant, and an arrow with no length has no direction.
const MIN_DRAG = 2;
// The largest canvas every browser will paint (the historic mobile limit, in
// pixels of area); a bigger picture is worked on scaled down to it.
const MAX_AREA = 16_777_216;

/** The picture as an element the canvas can draw, or a rejection when it
 * will not load — the caller then stays on the stage and says so. */
export function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error("The picture could not be loaded for markup."));
    img.src = src;
  });
}

/** The swatches, read off the theme at mount: the chart cycle is the one
 * set of colours guaranteed to stand apart from each other on any picture. */
function themeColors(): string[] {
  const style = getComputedStyle(document.documentElement);
  return [1, 2, 3, 4, 5, 6]
    .map((n) => style.getPropertyValue(`--ds-chart-${n}`).trim())
    .filter(Boolean);
}

function rectOf(from: Pt, to: Pt) {
  const x = Math.min(from.x, to.x), y = Math.min(from.y, to.y);
  return { x, y, w: Math.abs(to.x - from.x), h: Math.abs(to.y - from.y) };
}

function drawOp(ctx: CanvasRenderingContext2D, op: Op) {
  if (op.kind === "crop") return;
  ctx.strokeStyle = ctx.fillStyle = op.color;
  ctx.lineCap = ctx.lineJoin = "round";
  if (op.kind === "text") {
    ctx.font = `bold ${op.size}px system-ui, sans-serif`;
    ctx.textBaseline = "alphabetic";
    ctx.fillText(op.text, op.at.x, op.at.y);
    return;
  }
  ctx.lineWidth = op.width;
  if (op.kind === "pen") {
    ctx.beginPath();
    op.points.forEach((p, i) => (i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)));
    if (op.points.length === 1) ctx.lineTo(op.points[0].x, op.points[0].y);
    ctx.stroke();
  } else if (op.kind === "rect") {
    const r = rectOf(op.from, op.to);
    ctx.strokeRect(r.x, r.y, r.w, r.h);
  } else {
    // The head is a filled triangle sized to the stroke, and the shaft stops
    // short of the tip so the stroke's round cap does not poke out of it.
    const angle = Math.atan2(op.to.y - op.from.y, op.to.x - op.from.x);
    const head = op.width * 4;
    const tip = op.to;
    const base = { x: tip.x - head * Math.cos(angle), y: tip.y - head * Math.sin(angle) };
    ctx.beginPath();
    ctx.moveTo(op.from.x, op.from.y);
    ctx.lineTo(base.x, base.y);
    ctx.stroke();
    const half = op.width * 2.2;
    ctx.beginPath();
    ctx.moveTo(tip.x, tip.y);
    ctx.lineTo(base.x + half * Math.sin(angle), base.y - half * Math.cos(angle));
    ctx.lineTo(base.x - half * Math.sin(angle), base.y + half * Math.cos(angle));
    ctx.closePath();
    ctx.fill();
  }
}

/** The size the canvas works at: the picture's own, unless its area is past
 * what a canvas can be, then scaled to fit it. */
function workingSize(image: HTMLImageElement): { w: number; h: number; scaled: boolean } {
  const area = image.naturalWidth * image.naturalHeight;
  if (area <= MAX_AREA) return { w: image.naturalWidth, h: image.naturalHeight, scaled: false };
  const k = Math.sqrt(MAX_AREA / area);
  return { w: Math.floor(image.naturalWidth * k), h: Math.floor(image.naturalHeight * k), scaled: true };
}

/** The picture with the drawing ops on it, at the working size. */
function flatten(image: HTMLImageElement, w: number, h: number, ops: Op[]): HTMLCanvasElement {
  const out = document.createElement("canvas");
  out.width = w; out.height = h;
  const ctx = out.getContext("2d")!;
  ctx.drawImage(image, 0, 0, w, h);
  ops.forEach((op) => drawOp(ctx, op));
  return out;
}

/** Everything drawn so far, cut to the crop when there is one. */
function exportCanvas(image: HTMLImageElement, w: number, h: number, ops: Op[]): HTMLCanvasElement {
  const flat = flatten(image, w, h, ops);
  const crop = ops.filter((op) => op.kind === "crop").pop();
  if (!crop || crop.kind !== "crop") return flat;
  const r = rectOf(crop.from, crop.to);
  const out = document.createElement("canvas");
  out.width = r.w; out.height = r.h;
  out.getContext("2d")!.drawImage(flat, r.x, r.y, r.w, r.h, 0, 0, r.w, r.h);
  return out;
}

export function Markup({ artifact, image, onSaved, onCancel }: {
  artifact: Artifact;
  image: HTMLImageElement;
  onSaved: (a: Artifact) => void;
  onCancel: () => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const frameRef = useRef<HTMLDivElement>(null);
  const firstTool = useRef<HTMLButtonElement>(null);
  const [colors] = useState(themeColors);
  const [tool, setTool] = useState<Tool>("pen");
  const [color, setColor] = useState(0);
  const [stroke, setStroke] = useState(0);
  const [ops, setOps] = useState<Op[]>([]);
  // The stroke under the pointer, outside React's render loop: a pointermove
  // storm must not schedule a render per event.
  const live = useRef<Op | null>(null);
  const textAt = useRef<Pt | null>(null);
  const [text, setText] = useState<{ at: Pt; css: Pt; value: string } | null>(null);
  // Whether the open text box still owes a commit: Enter commits and then the
  // box's unmount blurs it, and blur must not commit a second time.
  const textOpen = useRef(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const { w: nw, h: nh, scaled } = workingSize(image);

  // The canvas box is the column, its content the picture letterboxed inside
  // it (object-fit: contain), so a pointer's place on the picture is its place
  // in the box less the bars, over the scale — the same figure that makes a
  // stroke or a glyph read at the same size on screen whatever the picture's
  // resolution, never thinner than its own pixels.
  const fit = useCallback(() => {
    const box = canvasRef.current!.getBoundingClientRect();
    const scale = Math.min(box.width / nw, box.height / nh);
    return { scale, ox: box.left + (box.width - nw * scale) / 2, oy: box.top + (box.height - nh * scale) / 2 };
  }, [nw, nh]);
  const toPicture = useCallback((e: { clientX: number; clientY: number }): Pt => {
    const f = fit();
    const clamp = (v: number, max: number) => Math.max(0, Math.min(max, v));
    return { x: clamp(Math.floor((e.clientX - f.ox) / f.scale), nw - 1),
             y: clamp(Math.floor((e.clientY - f.oy) / f.scale), nh - 1) };
  }, [fit, nw, nh]);
  const gain = useCallback(() => Math.max(1, 1 / fit().scale), [fit]);

  const redraw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d")!;
    ctx.clearRect(0, 0, nw, nh);
    ctx.drawImage(image, 0, 0, nw, nh);
    ops.forEach((op) => drawOp(ctx, op));
    if (live.current) drawOp(ctx, live.current);
    const committed = ops.filter((op) => op.kind === "crop").pop();
    const active = live.current?.kind === "crop" ? live.current : committed;
    if (active?.kind === "crop") {
      const r = rectOf(active.from, active.to);
      ctx.save();
      ctx.fillStyle = "rgba(0, 0, 0, 0.45)";
      ctx.beginPath();
      ctx.rect(0, 0, nw, nh);
      ctx.rect(r.x, r.y, r.w, r.h);
      ctx.fill("evenodd");
      // A marquee in black and white, one dashed over the other in opposite
      // phase, so it reads on any picture — a swatch could match the pixels
      // under it.
      const dash = 6 * gain();
      ctx.lineWidth = 2 * gain();
      ctx.strokeStyle = "black";
      ctx.strokeRect(r.x, r.y, r.w, r.h);
      ctx.setLineDash([dash, dash]);
      ctx.strokeStyle = "white";
      ctx.strokeRect(r.x, r.y, r.w, r.h);
      ctx.restore();
    }
  }, [image, ops, nw, nh, gain]);

  useLayoutEffect(redraw, [redraw]);

  // The mode opens with the keyboard on its first tool; the stage hands it
  // back to Mark up on the way out.
  useEffect(() => { firstTool.current?.focus(); }, []);

  useEffect(() => {
    function key(e: globalThis.KeyboardEvent) {
      const typing = e.target instanceof HTMLInputElement;
      if (e.key === "Escape" && !typing) { e.preventDefault(); onCancel(); }
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "z" && !typing) {
        e.preventDefault();
        setOps((prev) => prev.slice(0, -1));
      }
    }
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [onCancel]);

  function down(e: PointerEvent<HTMLCanvasElement>) {
    if (saving || text) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    const p = toPicture(e);
    const c = colors[color] ?? "currentColor";
    const width = STROKES[stroke].width * gain();
    if (tool === "text") { textAt.current = p; return; }
    live.current = tool === "pen" ? { kind: "pen", points: [p], color: c, width }
      : tool === "crop" ? { kind: "crop", from: p, to: p }
      : { kind: tool, from: p, to: p, color: c, width };
    redraw();
  }

  function move(e: PointerEvent<HTMLCanvasElement>) {
    const op = live.current;
    if (!op) return;
    const p = toPicture(e);
    if (op.kind === "pen") op.points.push(p);
    else if (op.kind !== "text") op.to = p;
    redraw();
  }

  function up() {
    // The text box opens on release, not press: the press's own default is
    // to move focus, which would blur the box the moment it appeared.
    if (textAt.current) {
      const p = textAt.current;
      textAt.current = null;
      const f = fit();
      const frame = frameRef.current!.getBoundingClientRect();
      textOpen.current = true;
      setText({ at: p, value: "",
                css: { x: f.ox + p.x * f.scale - frame.left, y: f.oy + p.y * f.scale - frame.top } });
      return;
    }
    const op = live.current;
    if (!op) return;
    live.current = null;
    if (op.kind !== "pen" && op.kind !== "text") {
      const r = rectOf(op.from, op.to);
      const short = op.kind === "crop" ? Math.min(r.w, r.h) < MIN_DRAG : Math.max(r.w, r.h) < MIN_DRAG;
      if (short) { redraw(); return; }
    }
    setOps((prev) => [...prev, op]);
  }

  // A pointer the browser took back (a palm, a scroll) drew nothing.
  function cancel() {
    live.current = null;
    textAt.current = null;
    redraw();
  }

  function commitText() {
    if (!text || !textOpen.current) return;
    textOpen.current = false;
    const value = text.value.trim();
    if (value) {
      setOps((prev) => [...prev, { kind: "text", at: text.at, text: value,
                                   color: colors[color] ?? "currentColor", size: TEXT_PX * gain() }]);
    }
    setText(null);
  }

  function textKey(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter") { e.preventDefault(); commitText(); }
    if (e.key === "Escape") {
      e.preventDefault(); e.stopPropagation();
      textOpen.current = false;
      setText(null);
    }
  }

  async function save() {
    if (saving || ops.length === 0) return;
    setSaving(true); setError(null);
    try {
      const out = exportCanvas(image, nw, nh, ops);
      const blob = await new Promise<Blob | null>((r) => out.toBlob(r, "image/png"));
      if (!blob) throw new Error("The canvas could not be exported.");
      const name = `${artifact.name.replace(/\.[a-z0-9]+$/i, "")}-markup.png`;
      // Anything drawn is a markup; only a crop alone is a crop.
      const operation = ops.some((op) => op.kind !== "crop") ? "markup" : "crop";
      const form = new FormData();
      form.append("file", blob, name);
      form.append("name", name);
      form.append("meta", JSON.stringify({ parent_id: artifact.id, operation }));
      onSaved(await uploadArtifact(form));
    } catch (err) {
      setError(errorDetail(err, "The markup was not saved."));
      setSaving(false);
    }
  }

  const toolLabel = TOOLS.find((t) => t.id === tool)!.label;
  return (
    <div className="markup" data-tool={tool}>
      <div className="markup-toolbar" role="toolbar" aria-label="Markup tools">
        <div className="markup-group" aria-label="Tool">
          {TOOLS.map((t, i) => (
            <Button key={t.id} ref={i === 0 ? firstTool : undefined} variant="secondary" size="sm"
                    aria-pressed={tool === t.id} disabled={saving} onClick={() => setTool(t.id)}>
              {t.label}
            </Button>
          ))}
        </div>
        <div className="markup-group" aria-label="Colour">
          {colors.map((c, i) => (
            <Button key={c} variant="secondary" size="sm" className="markup-swatch"
                    aria-label={`Colour ${i + 1}`} aria-pressed={color === i}
                    style={{ background: c }} disabled={saving} onClick={() => setColor(i)} />
          ))}
        </div>
        <div className="markup-group" aria-label="Stroke">
          {STROKES.map((s, i) => (
            <Button key={s.label} variant="secondary" size="sm" aria-pressed={stroke === i}
                    disabled={saving} onClick={() => setStroke(i)}>
              {s.label}
            </Button>
          ))}
        </div>
        <div className="markup-group markup-group-end">
          <Button variant="secondary" size="sm" disabled={saving || ops.length === 0}
                  onClick={() => setOps((prev) => prev.slice(0, -1))}>
            Undo
          </Button>
          <Button variant="secondary" size="sm" disabled={saving} onClick={onCancel}>Cancel</Button>
          <Button size="sm" disabled={saving || ops.length === 0} onClick={save}>
            {saving ? "Saving…" : "Save"}
          </Button>
        </div>
      </div>
      <div ref={frameRef} className="markup-frame">
        <canvas ref={canvasRef} className="markup-canvas" width={nw} height={nh}
                role="img" aria-label={`Markup canvas, ${toolLabel} tool: ${artifact.name}`}
                onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={cancel} />
        {text && (
          <Input className="markup-text" aria-label="Text to draw" autoFocus value={text.value}
                 style={{ left: text.css.x, top: text.css.y }}
                 onChange={(e) => setText({ ...text, value: e.target.value })}
                 onKeyDown={textKey} onBlur={commitText} />
        )}
      </div>
      <p className="muted markup-hint">
        Esc cancels · ⌘Z undoes · Save keeps a copy, the original stays.
        {scaled && <> The copy is scaled to {nw}×{nh}: the original is more than a canvas can hold.</>}
      </p>
      {error && <Banner variant="danger" role="alert">{error}</Banner>}
    </div>
  );
}
