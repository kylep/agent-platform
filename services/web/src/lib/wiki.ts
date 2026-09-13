import type { RelayFace } from "../api";

// The wiki (docs/design/21): the platform's shared, citable knowledge. A page
// is a row, a `[[slug]]` is how anything points at it, and a link to a page
// nobody has written yet is a feature — the wanted list is the wiki's to-do.
//
// The rules here are the browser's half of `agentplatform/wiki.py`. They have
// to agree digit for digit with it: the backend decides what counts as a link
// when it stores one, and this decides what the reader is looking at. A chip
// on a slug the backend never recorded is a page claiming a citation nobody
// made.

// The slug grammar, from the backend's SLUG_RE. Anchored whole: the slug goes
// into a URL and into `[[...]]`, so a partial match is not a slug.
export const SLUG_RE = /^[a-z0-9][a-z0-9-]{0,63}$/;
export const SLUG_MAX = 64;
// Untouched this long and the page is stale (the backend's `wiki_stale_days`
// default). Read client-side because the list route can only express "changed
// since", never "older than" — see `staleBefore` below.
export const STALE_DAYS = 30;

export type WikiPage = {
  id: string;
  slug: string;                 // the URL and the `[[link]]` target
  title: string;
  body: string;
  summary: string;              // the first paragraph, recomputed on every write
  tags: string[];
  version: number;
  created_by: string;
  updated_by: string;
  source_memory_id: string | null;   // set when the page was promoted
  created_at: string | null;
  updated_at: string | null;
  archived_at: string | null;
  // Attached by the API, never stored — the SSE payload carries the page
  // WITHOUT it, so anything drawing a face falls back to lib/face.
  updated_by_face?: RelayFace | null;
};

export type WikiPageRef = { slug: string; title: string };

export type WikiCitation = {
  message_id: string; channel_id: string; author: string; created_at: string | null;
};

export type WikiCitations = {
  count: number;
  // The scan behind `count` hit its limit, so the count is a floor: "200+".
  count_capped: boolean;
  last: WikiCitation[];
};

export type WikiPageDetail = {
  page: WikiPage;
  backlinks: WikiPageRef[];
  cited_in: WikiCitations;
};

export type WikiVersion = {
  id: string; page_id: string; version: number; title: string; body: string;
  author: string; run_id: string | null; reason: string; created_at: string | null;
  author_face?: RelayFace | null;
};

export type WikiHistoryRow = Omit<WikiVersion, "body" | "page_id"> & {
  added: number; removed: number;
};

export type WikiDiff = {
  version: WikiVersion; diff: string; added: number; removed: number;
};

export type WikiWantedRow = { slug: string; linked_from: string[] };

export type WikiAuthorCount = { author: string; count: number; face: RelayFace | null };

export type WikiStats = {
  pages: number;
  edits_24h: WikiAuthorCount[];
  wanted: number;
  stale: number;
  budget: { limit: number; agents: { agent: string; used: number; left: number }[] };
};

/** One line of `wiki.events`, which is what the SSE stream carries whole: the
 * page AND what the edit was, because "recent changes" is a list of edits and
 * the page alone says nothing about who changed it or why. */
export type WikiEvent = {
  event: string;                // created | edited | appended | archived | restored | promoted
  page: WikiPage;
  version: number;
  author: string;
  run_id: string | null;
  reason: string;
  added: number;
  removed: number;
};

export type WikiRef = { slug: string; start: number; end: number };

/** A fenced block blanked out character for character, so offsets still point
 * into the ORIGINAL text. Inline spans are handled by the chip pass, which
 * protects far more than fences (`lib/chips.ts`); this keeps `wikiRefs` honest
 * on its own for callers that hand it raw prose. */
function withoutFences(text: string): string {
  return text.replace(/```[\s\S]*?(?:```|$)/g, (block) => " ".repeat(block.length));
}

/** Every `[[slug]]` in a piece of text, as offsets into it.
 *
 * A target that is not a slug is not a link — it is words that happen to sit
 * in brackets, and chipping them would fill the wanted list with prose nobody
 * meant to write. This is `wiki.find_links`, minus the de-duplication: a body
 * with the same page named twice gets two chips. */
export function wikiRefs(text: string): WikiRef[] {
  if (!text) return [];
  const scanned = withoutFences(text);
  const out: WikiRef[] = [];
  for (const m of scanned.matchAll(/\[\[([^[\]\n]*)\]\]/g)) {
    const slug = m[1].toLowerCase();
    if (!SLUG_RE.test(slug)) continue;
    out.push({ slug, start: m.index, end: m.index + m[0].length });
  }
  return out;
}

/** The slug for a title — `Kyle's Location!` → `kyles-location`.
 *
 * The backend's `slugify`, which promotion and the "new page" form both have
 * to land on for the same words, or a page created here and a page promoted
 * there would be two pages about one thing. Accents fold (a slug is typed and
 * grepped, not read aloud); anything else becomes a single `-`. Empty when
 * nothing usable is left — the caller decides whether that is an error. */
export function slugify(title: string): string {
  const ascii = (title || "").normalize("NFKD")
    // Combining marks, then the apostrophes, so `Kyle's` is `kyles` and not
    // `kyle-s`. Both the typewriter one and the typographic one.
    .replace(/[\u0300-\u036f]/g, "").replace(/['’]/g, "").toLowerCase();
  return ascii.replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "")
    .slice(0, SLUG_MAX).replace(/-+$/, "");
}

/** A body's headings pushed down one level, outside fenced code.
 *
 * The page's own title is already the H1 on screen, and agents habitually open
 * a page with `# Its Title` — rendered as written, every page would have two
 * H1s and an outline that starts over halfway down. The markdown is untouched
 * in storage: this is how the document is SHOWN inside a page that already has
 * a heading, the same call the help pages make by having no title of their own.
 * `#` in a fenced block is a shell comment and stays one. */
export function demoteHeadings(body: string): string {
  return (body || "").replace(/```[\s\S]*?(?:```|$)|^(#{1,5})(?=\s)/gm,
                              (whole, hashes: string | undefined) => (hashes ? `#${hashes}` : whole));
}

/** The instant before which a page counts as stale.
 *
 * Client-side because the list route can only ask for pages changed SINCE a
 * time, never for ones that have not been touched since — so the stale list is
 * read off the (capped) listing rather than fetched. The count in the header
 * is the API's own, computed over every live page, which is why the two can
 * legitimately differ on a wiki bigger than the cap. */
export function staleBefore(now: number = Date.now()): number {
  return now - STALE_DAYS * 86400000;
}

export function isStale(page: WikiPage, now: number = Date.now()): boolean {
  const t = new Date(page.updated_at ?? "").getTime();
  return !Number.isNaN(t) && t < staleBefore(now);
}

/** A write that failed, with the parts a UI can act on. `detail` is the
 * sentence the API wrote; a 409 additionally carries where the page has got to,
 * which is what makes "reload and keep my text" possible. */
export class WikiWriteError extends Error {
  status: number;
  currentVersion: number | null;
  currentSummary: string;

  constructor(status: number, detail: string, current?: {
    version?: number | null; summary?: string;
  }) {
    super(detail);
    this.name = "WikiWriteError";
    this.status = status;
    this.currentVersion = current?.version ?? null;
    this.currentSummary = current?.summary ?? "";
  }
}

/** One write against the wiki.
 *
 * Deliberately not `api()`: that helper flattens a failure into
 * `"409: {json…}"`, and the 409 body is the whole point here — the version the
 * page is at now and what it says. A 401 still sends the reader to the login
 * page, the way every other call does. */
async function write<T>(path: string, method: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method, credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (res.status === 401) {
    window.location.href = "/login";
    throw new WikiWriteError(401, "not signed in");
  }
  const text = await res.text();
  let parsed: unknown = null;
  try { parsed = text ? JSON.parse(text) : null; } catch { /* not json */ }
  if (res.ok) return parsed as T;
  const payload = (parsed ?? {}) as Record<string, unknown>;
  // FastAPI answers a validation failure with a LIST under `detail`; a string
  // is what every refusal this UI can cause looks like. Anything else is
  // reported as the status, never as a JSON blob on screen.
  const detail = typeof payload.detail === "string"
    ? payload.detail : `the wiki refused this write (${res.status})`;
  throw new WikiWriteError(res.status, detail, {
    version: typeof payload.current_version === "number" ? payload.current_version : null,
    summary: typeof payload.current_summary === "string" ? payload.current_summary : "",
  });
}

/** What a human types into the editor. `tags` is sent as given — on a write
 * the API reads `null` as "leave them alone", which the editor never means:
 * what is in the field IS the page's tags. */
export type WikiDraft = { body: string; title: string; reason: string; tags: string[] };

export function createPage(slug: string, draft: WikiDraft): Promise<WikiPage> {
  return write<WikiPage>("/api/wiki/pages", "POST", { slug, ...draft });
}

export function savePage(slug: string, draft: WikiDraft, baseVersion: number):
  Promise<WikiPage> {
  return write<WikiPage>(`/api/wiki/pages/${encodeURIComponent(slug)}`, "PUT",
                         { ...draft, base_version: baseVersion });
}

export function restoreVersion(slug: string, version: number): Promise<WikiPage> {
  return write<WikiPage>(`/api/wiki/pages/${encodeURIComponent(slug)}/restore`, "POST",
                         { version, reason: `restored v${version}` });
}

export type WikiDiffLine = { text: string; kind: "add" | "del" | "hunk" | "meta" | "" };

/** A unified diff split into lines that can be coloured — and, more
 * importantly, PREFIXED: the `+` and the `-` are the diff's own characters, so
 * what an added line is never depends on being able to see the colour. */
export function diffLines(diff: string): WikiDiffLine[] {
  return (diff || "").split("\n").map((text) => ({ text, kind: lineKind(text) }));
}

function lineKind(line: string): WikiDiffLine["kind"] {
  if (line.startsWith("+++") || line.startsWith("---")) return "meta";
  if (line.startsWith("@@")) return "hunk";
  if (line.startsWith("+")) return "add";
  if (line.startsWith("-")) return "del";
  return "";
}
