import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Chip } from "@ap/ui/chip";
import { api, type AgentSummary } from "../api";
import { DetailActivity } from "../components/tickets/DetailActivity";
import { DetailAssign } from "../components/tickets/DetailAssign";
import { DetailFields } from "../components/tickets/DetailFields";
import { Face } from "../components/relay/Face";
import ThreadPane from "../components/relay/ThreadPane";
import { useChannel } from "../components/relay/useChannel";
import { Prose } from "../components/wiki/Prose";
import {
  type Ticket, type TicketDetail as Detail, type TicketProject,
} from "../lib/tickets";
import { useTitle } from "../lib/title";

// One piece of work as a page (docs/design/20): the fields somebody has to be
// able to change, the history of what has already been changed, and the thread
// it is being discussed in — which is not a copy of the room's conversation,
// it IS the room's conversation, read through the card the ticket left there.

// How long a dropped stream waits before trying again. Same widening retry as
// the board and as Relay, for the same reason: a rolling pod restart should end
// with a live socket rather than a page nobody thought to reload.
const FIRST_RETRY_MS = 2000;
const MAX_RETRY_MS = 30000;

type Page = {
  detail: Detail | null;
  /** The project's other tickets — where the parent and the children come
   * from. The API has no "children" endpoint: a parent lives in the same
   * channel by construction, so one list of the project answers both. */
  siblings: Ticket[];
  project: TicketProject | null;
  /** Every project's prefix — what the description's chip pass rewrites. The
   * ticket's own project is not enough: a body routinely names work on another
   * board, and a key nobody can click is the one thing chips exist to fix. */
  prefixes: string[];
  agents: string[];
  me: string | null;
  error: string | null;
  loaded: boolean;
  /** What a write answers with: the row the server stored. */
  absorb: (row: Ticket) => void;
};

function useTicketPage(key: string): Page {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [siblings, setSiblings] = useState<Ticket[]>([]);
  const [projects, setProjects] = useState<TicketProject[]>([]);
  const [agents, setAgents] = useState<string[]>([]);
  const [me, setMe] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const live = useRef(true);
  // The ticket's id, so a stream frame can be matched without the state the
  // effect below would otherwise have to re-subscribe on.
  const id = useRef<string | null>(null);

  // Reads can overlap — the first one is still in flight when a stream frame
  // asks for another — and the answers can come back in either order. Only the
  // newest request may write, or a slow first read lands on top of the reload
  // that was supposed to correct it.
  const issued = useRef(0);

  const read = useCallback(() => {
    const mine = ++issued.current;
    return api<Detail>(`/api/tickets/${encodeURIComponent(key)}`)
      .then((d) => {
        if (!live.current || mine !== issued.current) return;
        setDetail(d);
        id.current = d.ticket.id;
      });
  }, [key]);

  useEffect(() => {
    live.current = true;
    setDetail(null); setSiblings([]); setError(null); setLoaded(false);
    read()
      .catch((err) => {
        if (live.current) {
          setError(err instanceof Error ? err.message : "Could not load this ticket.");
        }
      })
      .finally(() => { if (live.current) setLoaded(true); });
    api<TicketProject[]>("/api/tickets/projects")
      .then((rows) => { if (live.current) setProjects(rows); }).catch(() => {});
    api<AgentSummary[]>("/api/agents")
      .then((all) => { if (live.current) {
        setAgents(all.filter((a) => a.enabled !== false && !a.quarantined)
          .map((a) => a.name).sort());
      } }).catch(() => {});
    api<{ principal: string }>("/api/whoami")
      .then((w) => { if (live.current) setMe(`user:${w.principal}`); }).catch(() => {});
    return () => { live.current = false; };
  }, [read]);

  const channel = detail?.ticket.channel_id;
  useEffect(() => {
    if (!channel) return;
    let on = true;
    api<Ticket[]>(`/api/tickets?channel=${encodeURIComponent(channel)}`)
      .then((rows) => { if (on) setSiblings(rows); }).catch(() => {});
    return () => { on = false; };
  }, [channel]);

  // Live: the platform's one ticket stream, filtered here to this ticket. A
  // frame is the row, but a change to a ticket is also a line in its history
  // and possibly a run — so the page re-reads itself rather than patching the
  // row it holds. Nothing is refetched on a bare reconnect: a page holding one
  // ticket is cheap to reload, and a re-read on every retry would fight with
  // whatever the reader has just written.
  useEffect(() => {
    let stream: EventSource | null = null;
    let retry: ReturnType<typeof setTimeout> | null = null;
    let backoff = FIRST_RETRY_MS;
    let stopped = false;

    function connect() {
      if (stopped) return;
      const es = new EventSource("/api/tickets/events", { withCredentials: true });
      stream = es;
      es.addEventListener("open", () => { backoff = FIRST_RETRY_MS; });
      es.addEventListener("ticket", (e) => {
        const row = JSON.parse((e as MessageEvent).data) as Ticket;
        if (row.id === id.current || row.key === key) read().catch(() => {});
      });
      // The stream fell behind and dropped frames. It cannot say which, so the
      // only honest answer is to re-read this ticket — one request, and the
      // page stops claiming something that may already be false.
      es.addEventListener("overflow", () => { read().catch(() => {}); });
      es.onerror = () => {
        if (stopped || stream !== es) return;
        es.close();
        stream = null;
        if (retry) clearTimeout(retry);
        retry = setTimeout(() => {
          backoff = Math.min(backoff * 2, MAX_RETRY_MS);
          connect();
        }, backoff);
      };
    }

    connect();
    return () => {
      stopped = true;
      stream?.close();
      if (retry) clearTimeout(retry);
    };
  }, [key, read]);

  const absorb = useCallback((row: Ticket) => {
    setDetail((prev) => (prev ? { ...prev, ticket: row } : prev));
    setSiblings((prev) => prev.map((t) => (t.id === row.id ? row : t)));
  }, []);

  const project = projects.find((p) => p.id === detail?.ticket.channel_id) ?? null;
  // Stable across renders, so the description is rewritten when it changes and
  // not on every stream frame that touches the page.
  const prefixes = useMemo(() => projects.map((p) => p.prefix), [projects]);
  return { detail, siblings, project, prefixes, agents, me, error, loaded, absorb };
}

/** The ticket's thread, live, through the same subscription Relay uses. The
 * pane's "close" is not a close here — there is no room behind this page — so
 * it goes to the room, which is where the rest of the conversation is. */
function TicketThread({ channelId, threadId }: { channelId: string; threadId: string }) {
  const navigate = useNavigate();
  const room = useChannel(channelId);
  return (
    <ThreadPane room={room} threadId={threadId}
                onClose={() => navigate(
                  `/relay?channel=${encodeURIComponent(channelId)}`
                  + `&thread=${encodeURIComponent(threadId)}`)} />
  );
}

export default function TicketDetail() {
  const { key = "" } = useParams<{ key: string }>();
  const page = useTicketPage(key);
  const [assigning, setAssigning] = useState(false);
  const [busy, setBusy] = useState(false);
  const [moveError, setMoveError] = useState<string | null>(null);

  const detail = page.detail;
  const ticket = detail?.ticket ?? null;
  // The key is in the URL, so the tab can say which ticket this is before
  // the row lands — and what it is about once it has.
  useTitle(key, ticket?.title);

  /** One write, one answer: the server returns the row it stored, which is the
   * only honest thing to put on screen — a move can be refused (a closed
   * ticket only reopens) and an optimistic card would have lied.
   *
   * The failure is RAISED rather than displayed here: each caller shows it
   * where its own control is, which for the hand-off is the dialog it was
   * typed in and for the move is the select beside the state. */
  async function write(path: string, body: unknown) {
    if (!ticket) return;
    setBusy(true);
    try {
      page.absorb(await api<Ticket>(
        `/api/tickets/${encodeURIComponent(ticket.key)}/${path}`,
        { method: "POST", body: JSON.stringify(body) }));
    } finally {
      setBusy(false);
    }
  }

  async function move(state: string) {
    setMoveError(null);
    try {
      await write("move", { state });
    } catch (err) {
      setMoveError(err instanceof Error
        ? `Could not move it: ${err.message}` : "Could not move it.");
    }
  }

  if (!page.loaded) return <div className="page"><p className="muted">Loading…</p></div>;
  if (!ticket || !detail) {
    const gone = page.error?.startsWith("404");
    return (
      <div className="page">
        <h1>{key}</h1>
        {gone
          ? (
            <p className="muted">
              No ticket with this key — it may never have existed, or it lives in a room
              you cannot see. The board is under <Link to="/tickets">Tickets</Link>.
            </p>
          )
          : <div className="error">{page.error ?? "Could not load this ticket."}</div>}
      </div>
    );
  }

  const parent = page.siblings.find((t) => t.id === ticket.parent_id) ?? null;
  const subtasks = page.siblings.filter((t) => t.parent_id === ticket.id);
  const thinking = detail.thinking;

  return (
    <div className="page page-ticket">
      <div className="ticket-detail-head">
        <h1><span className="ticket-key">{ticket.key}</span> {ticket.title}</h1>
        {ticket.stale && <Chip variant="warn">stale</Chip>}
      </div>

      {/* Who is on it right now, derived from a live run exactly as presence
          is in a room — never stored, so nothing has to be cleaned up when a
          pod dies. */}
      {thinking && (
        <p className="ticket-working">
          <Face participant={`agent:${thinking.agent}`} size={22} thinking />
          <span>{thinking.agent} is working on this</span>
          {" · "}
          <Link to={`/runs/${thinking.run_id}`}>view run ↗</Link>
        </p>
      )}

      <div className="ticket-detail">
        {/* A wrapper, not a landmark: on a phone this box is dissolved
            (`display: contents`) so its two blocks can be ordered against the
            description and the thread, and a dissolved element is one no
            screen reader is promised to keep. The landmarks live on the blocks
            themselves — the fields are the <aside>, the history its own
            labelled region. */}
        <div className="ticket-side">
          <DetailFields ticket={ticket} me={page.me} project={page.project} parent={parent}
                        subtasks={subtasks} runs={detail.runs} busy={busy}
                        moveError={moveError} onMove={move}
                        onAssign={() => setAssigning(true)} />
          <DetailActivity events={detail.events} me={page.me} />
        </div>

        <div className="ticket-main">
          {/* The description is prose like a room message is, so it gets the
              same chips: `OPS-12` links to the ticket, `[[deploying]]` to the
              page — red when nobody has written it (docs/design/21). */}
          {ticket.body && (
            <Prose text={ticket.body} prefixes={page.prefixes} className="ticket-body" />
          )}
          {detail.root_message_id
            ? <TicketThread channelId={ticket.channel_id} threadId={detail.root_message_id} />
            : (
              <p className="muted">
                This ticket has no card in a room, so there is nothing to discuss against it
                yet. Its project is{" "}
                <Link to={`/relay?channel=${encodeURIComponent(ticket.channel_id)}`}>
                  #{page.project?.name ?? "the channel"}
                </Link>.
              </p>
            )}
        </div>
      </div>

      <DetailAssign open={assigning} ticket={ticket} me={page.me} agents={page.agents}
                    onClose={() => setAssigning(false)}
                    onAssign={(body) => write("assign", body)} />
    </div>
  );
}
