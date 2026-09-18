import type { Artifact } from "../api";

// Artifacts (docs/design/23): the small readings of a row the grid, the card
// and the lightbox all do, and the chip grammar the room rewrites on.

// An id as the API mints them. Anchored whole where it is tested alone: an
// id goes into a URL, so a partial match is not an id.
export const ARTIFACT_ID_RE = /^[0-9a-f]{32}$/;

export type ArtifactRef = { id: string; start: number; end: number };

// Where the bytes are allowed to come from. The API writes `thumb_url` and
// `content_url` itself, so a row whose URLs point anywhere else is a row the
// browser should not believe — an `<img src>` is a request the reader never
// chose to make, and an `<a href>` a place they never chose to go.
const BYTES_PREFIX = "/api/artifacts/";

/** A byte URL fit for `src` or `href`, or null for anything off the
 * artifacts routes — the caller then draws a glyph and no link. */
export function safeArtifactUrl(url: string | null | undefined): string | null {
  return typeof url === "string" && url.startsWith(BYTES_PREFIX) && !url.startsWith(BYTES_PREFIX + "/")
    ? url : null;
}

/** Every `[[artifact:<id>]]` in a piece of text, as offsets into it.
 *
 * The wiki's `wikiRefs` sibling, not a loosening of it: a slug has no colon,
 * and letting one through there would make `[[artifact:…]]` a wanted page. The
 * caller has already kept this away from code and links (`lib/chips`), so a
 * fence is not blanked here — anything that is not an id is words in
 * brackets, and stays words. */
export function artifactRefs(text: string): ArtifactRef[] {
  if (!text) return [];
  const out: ArtifactRef[] = [];
  for (const m of text.matchAll(/\[\[artifact:([0-9a-f]{32})\]\]/g)) {
    out.push({ id: m[1], start: m.index, end: m.index + m[0].length });
  }
  return out;
}

/** `412 kB`, `1.2 MB`, `500 MB` — a decimal above kilobytes only when it
 * says something; a cap of `500.0 MB` reads like a measurement. */
export function formatBytes(n: number): string {
  if (!Number.isFinite(n) || n < 0) return "—";
  if (n < 1024) return `${n} B`;
  const kb = n / 1024;
  if (kb < 1024) return `${Math.round(kb)} kB`;
  const mb = kb / 1024;
  if (mb < 1024) return `${Number(mb.toFixed(1))} MB`;
  return `${Number((mb / 1024).toFixed(2))} GB`;
}

/** The one-line provenance a card shows: `model · 3 s · $0.04` for a
 * generated image, read off its meta. Whatever wrote the row chose the keys,
 * so each part is included only when it is the number it claims to be, and an
 * upload — which has none of them — gets its source word instead. */
export function provenanceLine(a: Artifact): string {
  const meta = a.meta ?? {};
  const parts: string[] = [];
  if (typeof meta.model === "string" && meta.model) parts.push(meta.model);
  if (typeof meta.duration_ms === "number") parts.push(`${Math.round(meta.duration_ms / 1000)} s`);
  if (typeof meta.cost_usd === "number") parts.push(`$${meta.cost_usd.toFixed(2)}`);
  return parts.length ? parts.join(" · ") : a.source;
}

/** The generated image's prompt, when its meta carries one. */
export function promptOf(a: Artifact): string | null {
  const p = a.meta?.prompt;
  return typeof p === "string" && p ? p : null;
}

/** The parent a derived artifact names, when it does. */
export function parentOf(a: Artifact): string | null {
  const p = a.meta?.parent_id;
  return typeof p === "string" && ARTIFACT_ID_RE.test(p) ? p : null;
}

/** The glyph a file tile wears, by what the bytes are. */
export function fileGlyph(mime: string): string {
  if (mime.startsWith("text/")) return "📄";
  if (mime === "application/pdf") return "📕";
  if (mime === "application/json") return "🧾";
  if (mime.startsWith("audio/")) return "🎵";
  if (mime.startsWith("video/")) return "🎬";
  if (mime.includes("zip") || mime.includes("tar")) return "🗜️";
  return "📎";
}
