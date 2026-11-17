// Read-only types and helpers for the UI Designer web view (docs/design/46 §7).
export type UiDocSummary = {
  slug: string; title: string; owner: string; version: number;
  node_count: number; updated_at: string | null;
};
export type UiVersionRow = {
  range: string; first_version: number; version: number; author: string;
  label: string | null; created_at: string | null; reasons: string[];
};
export type RNode = {
  id: string; type?: string; name?: string; title?: string;
  x?: number; y?: number; w?: number; h?: number; children?: RNode[];
  of?: string; visible?: boolean; clip?: boolean; rotate?: number; bounds?: [number, number, number, number]; link_to?: UiPrototypeLink;
};
export type RPage = { id: string; name?: string; title?: string; children?: RNode[] };
export type Resolved = { pages?: RPage[] };

export const UD = "/api/ui-designer/docs";

export const nodeLabel = (n: { id: string; name?: string; title?: string }) =>
  n.name || n.title || n.id;

/** The page's painted bounds: the union of its visible children, as the server's SVG uses. */
export function pageBounds(p: RPage) {
  const kids = p.children ?? [];
  if (!kids.length) return { x: 0, y: 0, w: 1, h: 1 };
  const x0 = Math.min(...kids.map((c) => c.x ?? 0));
  const y0 = Math.min(...kids.map((c) => c.y ?? 0));
  const x1 = Math.max(...kids.map((c) => (c.x ?? 0) + (c.w ?? 0)));
  const y1 = Math.max(...kids.map((c) => (c.y ?? 0) + (c.h ?? 0)));
  return { x: x0, y: y0, w: Math.max(1, x1 - x0), h: Math.max(1, y1 - y0) };
}

export function flatten(nodes: RNode[], out: RNode[] = []): RNode[] {
  for (const n of nodes) { out.push(n); flatten(n.children ?? [], out); }
  return out;
}

// Phase 1 shapes (api/schemas.py UiComment*, UiDocDiffOut, export).
export type UiComment = {
  id: string; node_id: string | null; parent_id: string | null; body: string; author: string;
  resolved_at: string | null; resolved_by: string | null; created_at: string | null;
};
export type UiDiffChunk = { version: number; ops: Record<string, unknown>[]; reason?: string; at?: string };
export type UiDiff = {
  slug: string; version: number; from_version: number; to_version: number;
  chunks: UiDiffChunk[]; text: string; truncated: boolean;
};
export type UiExport = { artifact_id: string; name: string; url: string; width: number; height: number; mime?: string; size?: number; warnings?: string[] };
export type UiEvent = { type: string; slug: string; version?: number; author?: string };

export const opSummary = (o: Record<string, unknown>) =>
  [o.op, o.id ?? o.node ?? o.parent].filter(Boolean).join(" ");

export type UiEditOp = { op: "update" | "resize"; id: string; set: Record<string, unknown> };
export type UiWriteResult = { version: number; replayed?: boolean; undo_ops?: UiEditOp[]; warnings?: Record<string, unknown>[] };
export type UiInspection = { version: number; id: string; type?: string; authored: Record<string, unknown> | null; text?: string; warnings?: Record<string, unknown>[] };

export type UiPrototypeLink = { target: string; trigger?: "tap" | "hover" | "after-delay"; transition?: "instant" | "dissolve" | "slide"; duration_ms?: number; delay_ms?: number };
