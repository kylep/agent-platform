type Draft = Record<string, string>;
const object = (v: unknown): Record<string, unknown> | null => v && typeof v === "object" && !Array.isArray(v) ? v as Record<string, unknown> : null;
export function editedPaint(fill: unknown, draft: Draft): unknown {
  const entries = Object.entries(draft).filter(([key]) => key.startsWith("paint."));
  if (!entries.length) return fill;
  const paints = Array.isArray(fill) ? [...fill] : [fill];
  for (const [key, value] of entries) {
    const [, index, field] = key.split(".");
    const paint = { ...object(paints[Number(index)]) };
    if (field === "angle") {
      if (!value.trim()) delete paint.angle;
      else { const angle = Number(value); if (!Number.isFinite(angle) || Math.abs(angle) > 100000) throw new Error("Gradient angle must be a finite number between -100000 and 100000."); paint.angle = angle; }
    } else if (field === "stops") {
      let stops: unknown; try { stops = JSON.parse(value); } catch { throw new Error("Gradient stops must be valid positions and colours."); }
      if (Array.isArray(stops)) stops = stops.map((s) => Array.isArray(s) && typeof s[0] === "string" && s[0].trim() ? [Number(s[0]), s[1]] : s);
      if (!Array.isArray(stops) || stops.length < 2 || stops.length > 8 || stops.some((s) => !Array.isArray(s) || s.length !== 2 || typeof s[0] !== "number" || !Number.isFinite(s[0]) || s[0] < 0 || s[0] > 1 || typeof s[1] !== "string" || !/^(#[0-9a-fA-F]{3}|#[0-9a-fA-F]{6}|#[0-9a-fA-F]{8}|\$color\.[a-zA-Z0-9_.-]+)$/.test(s[1])) || stops.some((s, i) => i > 0 && s[0] < stops[i-1][0])) throw new Error("Use 2 to 8 stops in ascending order, with positions from 0 to 1 and a colour or token.");
      paint.stops = stops;
    } else if (field === "gradient") paint.gradient = value;
    paints[Number(index)] = paint;
  }
  return Array.isArray(fill) ? paints : paints[0];
}
