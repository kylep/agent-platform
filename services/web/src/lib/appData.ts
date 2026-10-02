// State Apps (docs/design/39): an App is rows in `app_data` — definitions,
// drafts, build notes — with no code. This module is the browser's half of
// the Kyle-session read routes under /api/app-data, written before the
// backend so the two can be built in parallel. The types below ARE the
// contract: the backend's responses must match them field for field.
//
// Routes (all GET, Kyle's browser session; errors are FastAPI's
// `{"detail": "..."}` with the status codes listed):
//
//   /api/app-data/apps
//     → StateAppSummary[]: every App the caller can read, retired included.
//
//   /api/app-data/apps/{app_id}
//     → StateAppDetail: the approved definitions, open drafts with their
//       revisions, build notes, health (computed on read) and recent build ops.
//       404 unknown App; 403 the caller can't read it.
//
//   /api/app-data/apps/{app_id}/pages/{page}
//     → PublishedPage: the page's approved `typed/v2` definition.
//       404 unknown page; 403 not readable by the caller; 503 the published
//       definition no longer validates against the App's definitions (the
//       same 503 a broken `typed/v1` page answers).
//
//   /api/app-data/apps/{app_id}/views/{view}?<param>=<value>&limit=&cursor=
//     → ViewResult: either rows or, for an ungrouped `count` view, a count.
//       View parameters are plain query parameters; `limit` (≤ 200) and
//       `cursor` (the previous page's `next_cursor`) are reserved names.
//       403 the caller can't read the view; 422 a bad parameter; 503 the
//       view no longer validates.
//
// Field access is per field for the actual caller. A field the caller may
// not read comes back as `null` in `values` AND is named in the row's
// `restricted` list, so a restricted value is never confused with an empty
// one — the page shows a muted "restricted" marker, never the value.

export type HealthStatus = "ok" | "warn" | "failing";

/** The list-row digest of an App's health; `issues` counts everything in
 *  HealthDetail's lists. `checked_at` is null before the first check. */
export type HealthSummary = { status: HealthStatus; issues: number; checked_at: string | null };

export type StateAppSummary = {
  id: string;                     // immutable App id
  name: string;                   // never reused
  owner: string;                  // an agent name, or "kyle"
  status: "active" | "retired";
  description: string;
  approved_version: number | null; // null until the first publish
  updated_at: string | null;
  health: HealthSummary;
};

/** `tool` is an App tool: which collections a tool's manifest roles reach in
 *  this App. Kyle approves those; the builder area only shows them. */
export type DefinitionKind = "collection" | "view" | "page" | "tool";

/** One approved definition, as published. `definition` is the raw JSON the
 *  builder wrote (validated server-side), shown read-only. */
export type ApprovedDefinition = {
  kind: DefinitionKind;
  name: string;
  version: number;
  published_at: string;
  published_by: string;
  definition: Record<string, unknown>;
};

/** An open draft. `revision` is what `apps draft` takes as
 *  `expected_revision`; `base_version` is the approved version it was drafted
 *  against (null for a definition that has never been published). */
export type DefinitionDraft = {
  kind: DefinitionKind;
  name: string;
  revision: number;
  base_version: number | null;
  updated_at: string;
  updated_by: string;
  definition: Record<string, unknown>;
};

export type BuildNotes = { text: string; revision: number; updated_at: string; updated_by: string };

export type BuildOp = {
  request_id: string;
  action: string;                 // apps action: create, draft, notes, publish, …
  status: "succeeded" | "refused" | "failed";
  actor: string;
  created_at: string;
  summary: string;
};

export type HealthDetail = HealthSummary & {
  /** Definitions bound to something that no longer validates. */
  invalid_bindings: { kind: DefinitionKind; name: string; code: string; message: string }[];
  /** Records that break a rule; `record_ids` is a sample, `count` the total. */
  rule_violations: { collection: string; rule: string; count: number; record_ids: string[] }[];
  quota: { records: number; records_limit: number; bytes: number; bytes_limit: number };
};

export type StateAppDetail = Omit<StateAppSummary, "health"> & {
  approved: ApprovedDefinition[];
  drafts: DefinitionDraft[];
  build_notes: BuildNotes | null;
  health: HealthDetail;
  build_ops: BuildOp[];           // newest first, at most 20
};

// --- typed/v2 pages (Release 1: table, detail, metric, text) ---------------

/** A view parameter: a literal, or a value taken from the page URL's query
 *  string (`{query: "id"}` reads `?id=`). */
export type ParamBinding = string | { query: string };

export type ColumnFormat = "text" | "int" | "number" | "percent" | "date" | "datetime" | "bool";

export type Column = { field: string; label?: string; format?: ColumnFormat };

/** A row link opens another page of the same App with query parameters
 *  filled from the row: `{page: "entry", params: {id: "id"}}` links to
 *  `…/pages/entry?id=<row.id>`. Only same-App pages are linkable. */
export type RowLink = { page: string; params: Record<string, string> };

/** Text links reach only the App's own pages or platform pages (a path
 *  starting with one `/`). Anything else renders as plain text. */
export type TextLink = { page: string } | { path: string };

export type V2Component =
  | { kind: "table"; label?: string; view: string; params?: Record<string, ParamBinding>;
      columns: Column[]; row_link?: RowLink; limit?: number }
  | { kind: "detail"; label?: string; view: string; params?: Record<string, ParamBinding>;
      fields: Column[] }
  | { kind: "metric"; label: string; view: string; params?: Record<string, ParamBinding> }
  | { kind: "text"; style: "heading" | "paragraph"; text: string; link?: TextLink };

export type PageV2 = { renderer: "typed/v2"; title: string; components: V2Component[] };

export type PublishedPage = {
  app_id: string;
  app_name: string;
  page: string;
  version: number;
  definition: PageV2;
};

export type Scalar = string | number | boolean | null;

/** A record as the caller may see it. `id` is the system field; `values`
 *  holds every field the view selects, `null` where `restricted` names it. */
export type ViewRow = { id: string; values: Record<string, Scalar>; restricted: string[] };

/** `as_of` is when the result was computed (a materialized view's refresh
 *  time); `stale` is the server's judgement that it's older than the view's
 *  schedule allows, and the page says so. */
export type ViewRows = { rows: ViewRow[]; next_cursor: string | null; as_of: string; stale: boolean };
export type ViewCount = { count: number; as_of: string; stale: boolean };
export type ViewResult = ViewRows | ViewCount;

export function isCount(result: ViewResult): result is ViewCount {
  return "count" in result;
}

// --- client ----------------------------------------------------------------

/** A failed read, with the status a page needs to pick its state from. */
export class AppDataError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(path, { credentials: "include" });
  if (res.status === 401) {
    window.location.href = "/login";
    throw new AppDataError(401, "Sign in again.");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json() as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch { /* a non-JSON error body keeps the status text */ }
    throw new AppDataError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

const base = (appId: string) => `/api/app-data/apps/${encodeURIComponent(appId)}`;

export function listStateApps(): Promise<StateAppSummary[]> {
  return get("/api/app-data/apps");
}

export function getStateApp(appId: string): Promise<StateAppDetail> {
  return get(base(appId));
}

export function getPage(appId: string, page: string): Promise<PublishedPage> {
  return get(`${base(appId)}/pages/${encodeURIComponent(page)}`);
}

export function readView(appId: string, view: string, params: Record<string, string>,
                         opts: { limit?: number; cursor?: string | null } = {}): Promise<ViewResult> {
  const query = new URLSearchParams(params);
  if (opts.limit) query.set("limit", String(opts.limit));
  if (opts.cursor) query.set("cursor", opts.cursor);
  const qs = query.toString();
  return get(`${base(appId)}/views/${encodeURIComponent(view)}${qs ? `?${qs}` : ""}`);
}

/** Where a page of a state App lives in the console. */
export function pageHref(appId: string, page: string, params: Record<string, string> = {}): string {
  const qs = new URLSearchParams(params).toString();
  return `/apps/state/${encodeURIComponent(appId)}/pages/${encodeURIComponent(page)}${qs ? `?${qs}` : ""}`;
}

/** The text-link guard: only one leading `/` is a platform path. `//host`
 *  and `/\host` are protocol-relative escapes, and any scheme is outbound. */
export function internalPath(path: string): boolean {
  return /^\/(?![/\\])/.test(path) && !/\s/.test(path);
}
