import { useEffect, useRef } from "react";

// The village map: a 2D top-down tile canvas (plan §9). Colours are read from
// the --ds-* tokens at mount, so there is no raw colour here and both themes
// work. Positions arrive at ~2 Hz in `characters`; each character is tweened
// toward its latest position every animation frame.

export type VCharacter = {
  id: string; name?: string; face?: string | { emoji?: string }; x: number; y: number;
  now?: string; blocked_reason?: string | null; thinking?: boolean;
  steps?: { op: string; target?: string | null; say?: string | null }[];
};
export type VObject = { id: string; kind?: string; name?: string; x: number; y: number };
export type VMap = { w?: number; h?: number };

export type MapCanvasProps = {
  map?: VMap; characters: VCharacter[]; objects?: VObject[];
  follow?: string | null; whole?: boolean; onPick?: (id: string | null) => void;
  width?: number; height?: number;
};

const DEFAULT_W = 64;
const DEFAULT_H = 40;
const SERIES = ["--ds-chart-1", "--ds-chart-2", "--ds-chart-3", "--ds-chart-5"];

function faceOf(c: VCharacter): string {
  const f = c.face;
  const glyph = typeof f === "string" ? f : f?.emoji;
  return glyph || (c.name || c.id).slice(0, 1).toUpperCase();
}

export default function MapCanvas({ map, characters, objects = [], follow = null, whole = true,
  onPick, width = 960, height = 600 }: MapCanvasProps) {
  const ref = useRef<HTMLCanvasElement>(null);
  const live = useRef({ characters, objects, follow, whole, map, onPick });
  const pos = useRef(new Map<string, { x: number; y: number }>());
  const camera = useRef({ x: DEFAULT_W / 2, y: DEFAULT_H / 2, scale: 0 });
  live.current = { characters, objects, follow, whole, map, onPick };

  useEffect(() => {
    const canvas = ref.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;
    const css = getComputedStyle(canvas);
    const tok = (name: string) => css.getPropertyValue(name).trim() || "currentColor";
    const colors = { bg: tok("--ds-canvas"), grid: tok("--ds-border"), text: tok("--ds-text"),
      muted: tok("--ds-text-muted"), obj: tok("--ds-raised"), accent: tok("--ds-accent"),
      series: SERIES.map(tok) };
    let raf = 0;
    const draw = () => {
      const { characters: chars, objects: objs, follow: fol, whole: all, map: m } = live.current;
      const mw = m?.w ?? DEFAULT_W, mh = m?.h ?? DEFAULT_H;
      const cam = camera.current;
      const followed = fol ? pos.current.get(fol) : undefined;
      const fit = Math.min(width / mw, height / mh);
      const target = all || !followed
        ? { x: mw / 2, y: mh / 2, scale: fit }
        : { x: followed.x, y: followed.y, scale: fit * 2.5 };
      if (!cam.scale) Object.assign(cam, target);
      cam.x += (target.x - cam.x) * 0.1; cam.y += (target.y - cam.y) * 0.1;
      cam.scale += (target.scale - cam.scale) * 0.1;
      const toX = (x: number) => width / 2 + (x - cam.x) * cam.scale;
      const toY = (y: number) => height / 2 + (y - cam.y) * cam.scale;

      ctx.fillStyle = colors.bg; ctx.fillRect(0, 0, width, height);
      ctx.strokeStyle = colors.grid; ctx.lineWidth = 1;
      ctx.strokeRect(toX(0), toY(0), mw * cam.scale, mh * cam.scale);
      for (const o of objs) {
        ctx.fillStyle = colors.obj; ctx.strokeStyle = colors.muted;
        const s = cam.scale;
        ctx.fillRect(toX(o.x) - s / 2, toY(o.y) - s / 2, s, s);
        ctx.strokeRect(toX(o.x) - s / 2, toY(o.y) - s / 2, s, s);
      }
      ctx.textAlign = "center"; ctx.textBaseline = "middle";
      chars.forEach((c, i) => {
        const p = pos.current.get(c.id) ?? { x: c.x, y: c.y };
        p.x += (c.x - p.x) * 0.15; p.y += (c.y - p.y) * 0.15;
        pos.current.set(c.id, p);
        const px = toX(p.x), py = toY(p.y), r = Math.max(8, cam.scale * 0.8);
        ctx.fillStyle = colors.series[i % colors.series.length];
        ctx.beginPath(); ctx.arc(px, py, r, 0, Math.PI * 2); ctx.fill();
        if (c.id === fol) { ctx.strokeStyle = colors.accent; ctx.lineWidth = 2; ctx.stroke(); }
        ctx.fillStyle = colors.bg; ctx.font = `${Math.round(r * 1.1)}px sans-serif`;
        ctx.fillText(faceOf(c), px, py);
        ctx.fillStyle = colors.text; ctx.font = "12px sans-serif";
        const line = c.blocked_reason ? `blocked: ${c.blocked_reason}` : c.thinking ? "thinking..." : c.now;
        if (line) ctx.fillText(line.slice(0, 48), px, py - r - 8);
        ctx.fillStyle = colors.muted; ctx.font = "11px sans-serif";
        (c.steps ?? []).slice(0, 3).forEach((s, j) =>
          ctx.fillText(`${s.op}${s.target ? ` ${s.target}` : ""}`.slice(0, 40), px, py + r + 12 + j * 12));
      });
      raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, [width, height]);

  const pick = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const sx = ((e.clientX - rect.left) / rect.width) * width;
    const sy = ((e.clientY - rect.top) / rect.height) * height;
    const cam = camera.current;
    let hit: string | null = null;
    for (const [id, p] of pos.current) {
      const dx = width / 2 + (p.x - cam.x) * cam.scale - sx;
      const dy = height / 2 + (p.y - cam.y) * cam.scale - sy;
      if (Math.hypot(dx, dy) <= Math.max(12, cam.scale)) hit = id;
    }
    live.current.onPick?.(hit);
  };

  return <canvas ref={ref} width={width} height={height} onClick={pick}
                 role="img" aria-label="Village map"
                 className="block h-auto w-full rounded-lg border border-border bg-canvas" />;
}
