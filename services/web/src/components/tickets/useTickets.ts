import { useCallback, useEffect, useRef, useState } from "react";
import { api, type RelayPresence } from "../../api";
import {
  canMove, type Ticket, type TicketIn, type TicketProject, type TicketStats,
} from "../../lib/tickets";

// The board's live state, in one place — the tickets equivalent of Relay's
// useChannel, and deliberately the same shape: one EventSource, a poll that
// only covers the gap while it is down, and a widening retry so a rolling pod
// restart ends with a live socket.
//
// Two things differ, both because a ticket is a page of work rather than a line
// of conversation:
//
// - There is no `after=` cursor. The API says so itself: a reader that fell
//   behind resyncs by re-listing, which is one request and always correct. So
//   "catch up" here is "read the board again".
// - The board loads UNFILTERED and narrows in the browser. Every column has to
//   be drawn at once, so a server-side filter would mean five requests per
//   keystroke to build one picture; the list route's cap (500) is the board.

const POLL_MS = 5000;
const FIRST_RETRY_MS = 2000;
const MAX_RETRY_MS = 30000;

/** Fold rows into what we hold, by id. A ticket is never deleted — it is
 * cancelled — so this only ever adds or replaces.
 *
 * The newer `updated_at` wins rather than the later arrival: a re-list that
 * started before a move can land after the stream frame that carried it, and
 * without this the card would walk back across the board on its own. */
export function upsert(prev: Ticket[], rows: Ticket[]): Ticket[] {
  const by = new Map(prev.map((t) => [t.id, t]));
  for (const t of rows) {
    const had = by.get(t.id);
    if (had && (had.updated_at ?? "") > (t.updated_at ?? "")) continue;
    by.set(t.id, t);
  }
  return [...by.values()];
}

/** Whether somebody is working on this ticket right now.
 *
 * The list row has no `thinking` field — only the detail does — so the board
 * derives it the way Relay derives its own: the assignee is an agent with a
 * live run IN THE TICKET'S PROJECT. Scoping it to the channel matters; an agent
 * busy in a dm is not busy on this card. */
export function isThinking(ticket: Ticket, presence: RelayPresence[]): boolean {
  const name = ticket.assignee?.startsWith("agent:") ? ticket.assignee.slice(6) : null;
  if (!name) return false;
  const row = presence.find((p) => p.agent === name);
  return !!row && row.state === "thinking" && row.thinking_in.includes(ticket.channel_id);
}

/** Assigned to an agent that is gone or switched off — work with an owner who
 * will never pick it up. Presence is the roster: an agent that has no row there
 * is one the platform no longer runs. */
export function isOrphaned(ticket: Ticket, presence: RelayPresence[]): boolean {
  const name = ticket.assignee?.startsWith("agent:") ? ticket.assignee.slice(6) : null;
  if (!name || presence.length === 0) return false;
  const row = presence.find((p) => p.agent === name);
  return !row || row.state === "disabled";
}

export type Board = {
  tickets: Ticket[];
  projects: TicketProject[];
  stats: TicketStats | null;
  /** Every agent the platform knows about, with what it is doing right now —
   * the board's source for both the thinking pulse and the orphan badge. */
  presence: RelayPresence[];
  me: string | null;
  loaded: boolean;
  error: string | null;
  /** One definition of a move, served by the drag and by the menu alike. */
  move: (ticket: Ticket, state: string, reason?: string) => Promise<void>;
  /** Ticket ids whose move has not answered yet. */
  moving: string[];
  create: (body: TicketIn) => Promise<Ticket>;
};

export function useTickets(): Board {
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [projects, setProjects] = useState<TicketProject[]>([]);
  const [stats, setStats] = useState<TicketStats | null>(null);
  const [presence, setPresence] = useState<RelayPresence[]>([]);
  const [me, setMe] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The stream opens once the first page has landed, so a frame can never
  // arrive before the board it belongs to.
  const [seeded, setSeeded] = useState(false);
  // Moves in flight, by ticket id. A second move on the same card would be
  // refused as a same-state move and reported as a failure the user caused by
  // being quick, so the card is held until the first one answers.
  const [moving, setMoving] = useState<string[]>([]);
  const inFlight = useRef(new Set<string>());
  const live = useRef(true);

  const absorb = useCallback((rows: Ticket[]) => {
    setTickets((prev) => upsert(prev, rows));
  }, []);

  const reload = useCallback(() => api<Ticket[]>("/api/tickets")
    .then((rows) => { if (live.current) absorb(rows); }), [absorb]);

  // The counts and the Today strip are a second read of the same board: they
  // are aggregates the API computes, not something a client can add up (`stale`
  // and `orphaned` depend on settings and on the roster).
  const reloadStats = useCallback(() => api<TicketStats>("/api/tickets/stats")
    .then((s) => { if (live.current) setStats(s); }).catch(() => {}), []);

  useEffect(() => {
    live.current = true;
    let retry: ReturnType<typeof setTimeout> | null = null;
    let backoff = FIRST_RETRY_MS;

    // The stream only opens once this has succeeded, so giving up on the first
    // failure would leave a page that is not just empty but permanently empty —
    // no board, no stream, no poll. It keeps asking on the same widening delay
    // the socket uses.
    function attempt() {
      Promise.all([
        reload(),
        api<TicketProject[]>("/api/tickets/projects")
          .then((rows) => { if (live.current) setProjects(rows); }),
      ])
        .then(() => { if (live.current) { setError(null); setSeeded(true); } })
        .catch((err) => {
          if (!live.current) return;
          setError(err instanceof Error ? err.message : "Could not load the board.");
          retry = setTimeout(attempt, backoff);
          backoff = Math.min(backoff * 2, MAX_RETRY_MS);
        })
        .finally(() => { if (live.current) setLoaded(true); });
    }

    attempt();
    reloadStats();
    api<RelayPresence[]>("/api/relay/presence")
      .then((rows) => { if (live.current) setPresence(rows); }).catch(() => {});
    api<{ principal: string }>("/api/whoami")
      .then((w) => { if (live.current) setMe(`user:${w.principal}`); }).catch(() => {});
    return () => { live.current = false; if (retry) clearTimeout(retry); };
  }, [reload, reloadStats]);

  useEffect(() => {
    if (!seeded) return;
    let stream: EventSource | null = null;
    let poll: ReturnType<typeof setInterval> | null = null;
    let retry: ReturnType<typeof setTimeout> | null = null;
    let backoff = FIRST_RETRY_MS;
    let stopped = false;

    const stopPolling = () => { if (poll) { clearInterval(poll); poll = null; } };
    const stop = () => {
      stopped = true;
      stream?.close();
      stream = null;
      stopPolling();
      if (retry) { clearTimeout(retry); retry = null; }
    };

    const catchUp = () => reload().catch(() => {});

    function connect() {
      if (stopped) return;
      const es = new EventSource("/api/tickets/events", { withCredentials: true });
      stream = es;
      es.addEventListener("open", () => {
        backoff = FIRST_RETRY_MS;
        stopPolling();
      });
      es.addEventListener("ticket", (e) => {
        absorb([JSON.parse((e as MessageEvent).data) as Ticket]);
        // A change is also a change to the counts above the columns.
        reloadStats();
      });
      // The stream dropped frames and says so; re-listing is the only honest
      // answer, since there is no cursor to resume from.
      es.addEventListener("overflow", () => { catchUp(); });
      es.onerror = () => {
        if (stopped || stream !== es) return;
        es.close();
        stream = null;
        if (!poll) poll = setInterval(catchUp, POLL_MS);
        catchUp();
        if (retry) clearTimeout(retry);
        retry = setTimeout(() => {
          backoff = Math.min(backoff * 2, MAX_RETRY_MS);
          connect();
        }, backoff);
      };
    }

    connect();
    return stop;
  }, [seeded, reload, absorb, reloadStats]);

  const move = useCallback(async (t: Ticket, state: string, reason?: string) => {
    // A transition the API would refuse is not sent at all: the UI offers the
    // legal ones, and anything else arriving here (a drop on the wrong column)
    // is a gesture that missed, not an error worth a banner.
    if (!canMove(t.state, state) || inFlight.current.has(t.id)) return;
    inFlight.current.add(t.id);
    setMoving((prev) => [...prev, t.id]);
    // No optimism otherwise: the server owns the row and answers with it.
    try {
      const row = await api<Ticket>(`/api/tickets/${encodeURIComponent(t.key)}/move`, {
        method: "POST",
        body: JSON.stringify(reason ? { state, reason } : { state }),
      });
      absorb([row]);
      reloadStats();
    } catch (err) {
      setError(err instanceof Error
        ? `${t.key} could not be moved: ${err.message}` : `${t.key} could not be moved.`);
    } finally {
      inFlight.current.delete(t.id);
      setMoving((prev) => prev.filter((id) => id !== t.id));
    }
  }, [absorb, reloadStats]);

  const create = useCallback(async (body: TicketIn) => {
    const row = await api<Ticket>("/api/tickets",
                                  { method: "POST", body: JSON.stringify(body) });
    absorb([row]);
    reloadStats();
    return row;
  }, [absorb, reloadStats]);

  return { tickets, projects, stats, presence, me, loaded, error, moving, move, create };
}
