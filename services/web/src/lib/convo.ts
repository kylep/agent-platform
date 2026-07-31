import type { Conversation } from "../api";

// Connector-created conversations default to machine names ("discord:15291…").
// Show a human fallback; renames still win.
export function convoTitle(c: Pick<Conversation, "title" | "connector" | "created_at">): string {
  if (c.title && !/^[a-z]+:[\w-]+$/.test(c.title)) return c.title;
  const kind = c.connector.charAt(0).toUpperCase() + c.connector.slice(1);
  const when = c.created_at
    ? new Date(c.created_at).toLocaleDateString(undefined, { month: "short", day: "numeric" })
    : "";
  return `${kind} thread${when ? ` · ${when}` : ""}`;
}
