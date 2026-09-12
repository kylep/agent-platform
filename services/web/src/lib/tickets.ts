// Tickets (docs/design/20): the platform's work, as rows a board can draw.
//
// The types below are the API's own shapes — `TicketView` carries the faces and
// the `stale` flag the API attaches on the way out, so a card never has to ask
// a second endpoint who its assignee is or whether it has gone quiet.

export type TicketFace = { emoji: string; hue: number };

export type Ticket = {
  id: string;
  key: string;                 // 'OPS-12', stable for the life of the ticket
  channel_id: string;          // the project it belongs to
  title: string;
  body: string;
  state: string;               // open | in_progress | blocked | review | done | cancelled
  priority: string;            // p0 | p1 | p2 | p3
  assignee: string | null;     // a participant string, never a bare name
  reporter: string;
  labels: string[];
  parent_id: string | null;
  due_at: string | null;
  run_id: string | null;       // the run that opened it, on agent-reported work
  root_message_id: string | null;   // the card in the room = the ticket's thread
  created_at: string | null;
  updated_at: string | null;
  // Activity, not edits: a comment moves this and `stale` is read off it.
  last_activity_at: string | null;
  closed_at: string | null;
  assignee_face: TicketFace | null;
  reporter_face: TicketFace | null;
  stale: boolean;
};

export type TicketEvent = {
  id: string;
  ticket_id: string;
  actor: string;
  kind: string;                // created | moved | assigned | edited | commented | reopened
  from_value: string | null;
  to_value: string | null;
  reason: string | null;
  message_id: string | null;
  run_id: string | null;
  created_at: string | null;
  actor_face: TicketFace | null;
};

export type TicketRunRef = {
  id: string; agent: string; state: string; trigger: string; created_at: string | null;
};

export type TicketThinking = { run_id: string; agent: string; started_at?: string | null };

export type TicketDetail = {
  ticket: Ticket;
  events: TicketEvent[];
  root_message_id: string | null;
  runs: TicketRunRef[];
  thinking: TicketThinking | null;
};

export type TicketProject = {
  id: string;                  // the Relay channel the project IS
  name: string | null;
  title: string | null;
  prefix: string;              // 'OPS' — the half of a key before the number
  open: number;
  in_progress: number;
};

export type TicketActorCount = {
  actor: string; label: string; count: number; face: TicketFace | null;
};

export type TicketAgentBudget = {
  agent: string; used: number; left: number; face: TicketFace | null;
};

export type TicketStats = {
  open: number;
  in_progress: number;
  blocked: number;
  review: number;
  done_24h: number;
  moved_24h: TicketActorCount[];
  stale: number;
  orphaned: number;
  budget: { creates_per_hour: number; stale_days: number; agents: TicketAgentBudget[] };
};

/** What a caller may send when opening one. No `reporter`: authorship comes
 * from the token, and the API rejects the field outright. */
export type TicketIn = {
  channel: string;
  title: string;
  body?: string;
  assignee?: string | null;
  priority?: string;
  labels?: string[];
  notify?: boolean;
};

export const TICKET_STATES = ["open", "in_progress", "blocked", "review",
                              "done", "cancelled"] as const;

export const CLOSED_STATES = ["done", "cancelled"];

export function isClosed(state: string): boolean {
  return CLOSED_STATES.includes(state);
}

/** The state as a room says it. The wire form is a snake_case enum; nothing a
 * person reads should carry an underscore. */
export function stateLabel(state: string): string {
  return state.replace(/_/g, " ");
}

// Deliberately words rather than the raw `p0`: a stripe that is only a colour
// says nothing to a screen reader, and "urgent" is what the colour means.
const PRIORITIES: Record<string, string> = {
  p0: "urgent", p1: "high", p2: "normal", p3: "low",
};

export function priorityLabel(priority: string): string {
  return PRIORITIES[priority] ?? priority;
}

/** Whether the API will accept this move — `tickets.can_move`, mirrored.
 *
 * Closed work only reopens: done → in_progress is not a move, it is a reopen
 * followed by a move, and the backend 409s it. A same-state move is a no-op the
 * API also refuses, so neither is ever offered: a menu that lists transitions
 * the server always rejects is a menu of mistakes. */
export function canMove(from: string, to: string): boolean {
  if (from === to || !(TICKET_STATES as readonly string[]).includes(to)) return false;
  return isClosed(from) ? to === "open" : true;
}

/** Where a ticket sits on a five-column board: done and cancelled are both
 * "closed", because a board reads as what is left to do. */
export function columnOf(state: string): string {
  return isClosed(state) ? "closed" : state;
}

export type TicketRef = { key: string; start: number; end: number };

/** Every ticket key in a piece of prose, as offsets into it. Restricted to the
 * prefixes that actually exist (`/api/tickets/projects`) and case-sensitive,
 * for the same reason the backend's `KEY_RE` is: `ops-12` in a sentence is the
 * word, and `AB-1` is a ticket only if somebody's project is called AB. */
export function ticketRefs(text: string, prefixes: string[]): TicketRef[] {
  const known = new Set(prefixes);
  if (!text || known.size === 0) return [];
  const scanned = withoutFences(text);
  const re = /\b([A-Z][A-Z0-9]{1,5})-(\d+)\b/g;
  const out: TicketRef[] = [];
  for (const m of scanned.matchAll(re)) {
    if (!known.has(m[1])) continue;
    out.push({ key: m[0], start: m.index, end: m.index + m[0].length });
  }
  return out;
}

/** A fenced block blanked out, character for character, so the offsets above
 * still point into the ORIGINAL text. Inline backticks are deliberately left
 * alone, exactly as the backend leaves them: people habitually write a key as
 * `OPS-12`, and that is still a reference to it. */
function withoutFences(text: string): string {
  return text.replace(/```[\s\S]*?(?:```|$)/g, (block) => " ".repeat(block.length));
}
