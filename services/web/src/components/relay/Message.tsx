import { useEffect, useMemo, useRef, useState, type MouseEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Chip, ChipButton } from "@ap/ui/chip";
import { Markdown } from "@ap/ui/markdown";
import { api, type RelayCard, type RelayMessage } from "../../api";
import { linkChips, splitArtifacts, type BodyPiece } from "../../lib/chips";
import { ago } from "../../lib/time";
import { agentName, namespaceOf, participantLabel } from "../../lib/relay";
import { priorityLabel, stateLabel, type TicketProject } from "../../lib/tickets";
import { ArtifactCard } from "../artifacts/ArtifactCard";
import { useWikiSlugs } from "../wiki/Prose";
import { Face } from "./Face";
import type { MessageGroup, ThreadSummary } from "./useChannel";

// The room's transcript. Consecutive messages from the same author inside a
// short window are ONE block with one face and one timestamp — a chat where
// every line repeats the speaker reads like a log, not a conversation.

// The ticket prefixes that actually exist, fetched once per page rather than
// per message: every transcript row asks, and the answer is the same list for
// all of them.
let known: Promise<string[]> | null = null;

function ticketPrefixes(): Promise<string[]> {
  // A failure answers "no prefixes" — a room that renders keys as plain text is
  // a room, a room that fails to render is not — and FORGETS itself, so one
  // blip does not cost the session its chips.
  known ??= api<TicketProject[]>("/api/tickets/projects")
    .then((rows) => rows.map((p) => p.prefix))
    .catch(() => { known = null; return []; });
  return known;
}

function useTicketPrefixes(): string[] {
  const [prefixes, setPrefixes] = useState<string[]>([]);
  useEffect(() => {
    let on = true;
    ticketPrefixes().then((rows) => { if (on) setPrefixes(rows); });
    return () => { on = false; };
  }, []);
  return prefixes;
}

// A small, opinionated set: a picker that scrolls is a picker nobody uses.
const PICKER = ["👍", "🎉", "🙏", "👀", "🔥", "😂", "❤️", "🤖"];
// Roughly the picker's own height. Above this much room it opens upward (out
// of the way of the message you are reacting to); below it, downward — rather
// than sliding up over the transcript and hiding what came before.
const PICKER_HEIGHT = 48;

function Reactions({ message, onReact }: {
  message: RelayMessage;
  onReact: (message: RelayMessage, emoji: string) => void;
}) {
  const [picking, setPicking] = useState(false);
  const [below, setBelow] = useState(false);
  const anchor = useRef<HTMLSpanElement | null>(null);

  function toggle() {
    if (!picking && anchor.current) {
      const pane = anchor.current.closest(".relay-messages");
      const room = anchor.current.getBoundingClientRect().top
        - (pane?.getBoundingClientRect().top ?? 0);
      setBelow(room < PICKER_HEIGHT);
    }
    setPicking((p) => !p);
  }

  return (
    <span className="relay-reactions">
      {message.reactions.map((r) => (
        <ChipButton key={r.emoji} variant={r.mine ? "accent" : "neutral"}
                    aria-pressed={r.mine}
                    aria-label={`${r.emoji} ${r.count}${r.mine ? " — remove yours" : ""}`}
                    onClick={() => onReact(message, r.emoji)}>
          {r.emoji} {r.count}
        </ChipButton>
      ))}
      <span className="relay-picker-wrap" ref={anchor}>
        <ChipButton className="relay-react-add" aria-expanded={picking}
                    aria-label="Add a reaction" onClick={toggle}>+</ChipButton>
        {picking && (
          <span className={`relay-picker${below ? " below" : ""}`}>
            {PICKER.map((e) => (
              <button key={e} type="button" className="relay-picker-emoji" aria-label={e}
                      onClick={() => { setPicking(false); onReact(message, e); }}>
                {e}
              </button>
            ))}
          </span>
        )}
      </span>
    </span>
  );
}

/** A ticket's card in the room it was opened in (docs/design/20). The card is
 * the ticket's own row as it was at the time, so it is drawn as the board
 * draws one — key, state, who is on it — rather than as a title and a blank
 * body, which is all the generic card had to show.
 *
 * The face is derived from the assignee's name rather than read from the
 * room's face map: a transcript row is not handed that map, and the derivation
 * is the same hash the API uses, so an agent without a custom icon looks
 * identical either way. */
function TicketCardBody({ card, me }: { card: RelayCard; me: string | null }) {
  const key = card.key ?? "";
  const urgent = card.priority === "p0" || card.priority === "p1";
  return (
    <div className="relay-card relay-ticket-card">
      <div className="relay-card-title">
        {key && <Link to={`/tickets/${key}`} className="ticket-key">{key}</Link>}
        {" "}{card.title}
      </div>
      <div className="relay-ticket-meta">
        {card.state && <Chip>{stateLabel(card.state)}</Chip>}
        {/* The priority rides as a word, never as a colour alone. */}
        {urgent && card.priority && (
          <span className="ticket-priority">{priorityLabel(card.priority)}</span>
        )}
        {card.assignee && (
          <span className="ticket-who">
            <Face participant={card.assignee} size={18} />
            {participantLabel(card.assignee, me)}
          </span>
        )}
      </div>
    </div>
  );
}

/** The `#art` card (docs/design/23): the picture itself, as the same card a
 * chip in prose becomes, and the prompt under it in the poster's words. The
 * body the API wrote beside the card is the same thing flattened for a bridge
 * that cannot draw a card, so it is not drawn twice here. */
function ArtifactCardBody({ card, me }: { card: RelayCard; me: string | null }) {
  return (
    <div className="relay-card relay-artifact-card">
      {card.artifact_id
        ? <ArtifactCard id={card.artifact_id} me={me} />
        : <Chip className="artifact-missing">artifact not found</Chip>}
      {card.prompt && <div className="relay-card-body"><q>{card.prompt}</q></div>}
    </div>
  );
}

function Body({ message, me }: { message: RelayMessage; me: string | null }) {
  const prefixes = useTicketPrefixes();
  const slugs = useWikiSlugs();
  const navigate = useNavigate();
  const card = message.card ?? {};
  // One rewrite per message, not one per render: a room re-renders on every
  // frame that arrives, and this walks the whole body.
  const source = message.kind === "event" ? (card.body || message.body) : message.body;
  // The body cut around its artifact cards first — a card is a component, not
  // an anchor the markdown could carry — then the chip rewrite over each piece
  // of prose. Both walk the same protected spans, so `[[artifact:…]]` in a
  // code span is quoted here exactly as `[[slug]]` is.
  const pieces = useMemo(() => splitArtifacts(source).map((piece) =>
    (piece.kind === "text" ? { ...piece, text: linkChips(piece.text, prefixes, slugs) } : piece)),
  [source, prefixes, slugs]);

  // A chip — a ticket key or a `[[page]]` — is an anchor inside rendered
  // markdown: `Markdown` hands back HTML, not elements, so one delegated click
  // turns what would be a full page load back into a router navigation.
  // Modified clicks are left alone: "open that in a new tab" is a thing people
  // do.
  function onClick(e: MouseEvent<HTMLDivElement>) {
    const href = (e.target as HTMLElement).closest?.("a")?.getAttribute("href") ?? "";
    const ours = href.startsWith("/tickets/") || href.startsWith("/wiki/");
    if (!ours || e.metaKey || e.ctrlKey || e.shiftKey) return;
    e.preventDefault();
    navigate(href);
  }

  if (message.kind === "system") {
    // Joins, archivals, a rejected invocation: the room's own voice, and a
    // failure notice among them. Muted and italic so it never reads as speech.
    return <div className="relay-system">{message.body}</div>;
  }
  if (message.kind === "event") {
    if (card.type === "ticket") return <TicketCardBody card={card} me={me} />;
    if (card.type === "artifact") return <ArtifactCardBody card={card} me={me} />;
    return (
      <div className="relay-card" onClick={onClick}>
        {card.title && <div className="relay-card-title">{card.title}</div>}
        <div className="relay-card-body"><Pieces pieces={pieces} me={me} /></div>
      </div>
    );
  }
  return (
    <div onClick={onClick}>
      <Pieces pieces={pieces} me={me} className="relay-body" />
    </div>
  );
}

/** The body's pieces in order: prose as markdown, an artifact as its card. */
function Pieces({ pieces, me, className }: {
  pieces: BodyPiece[]; me: string | null; className?: string;
}) {
  // A body with nothing in it is still a body — the row keeps its element.
  if (pieces.length === 0) return <Markdown text="" className={className} />;
  return (
    <>
      {pieces.map((piece, i) => (piece.kind === "artifact"
        ? <ArtifactCard key={`${piece.id}-${i}`} id={piece.id} me={me} />
        : <Markdown key={i} text={piece.text} className={className} />))}
    </>
  );
}

export function MessageBlock({ group, me, inThread, highlight, threads,
                              onReact, onThread }: {
  group: MessageGroup;
  me: string | null;
  // This block is being drawn inside the thread pane rather than in the room.
  inThread?: boolean;
  // The message a search result sent the reader to: marked for a moment so the
  // eye lands on it, since a jumped-to message otherwise looks like any other.
  highlight?: string | null;
  // The threads hanging off these messages, by root id. A root with replies
  // advertises the conversation; anything else offers to start one.
  threads?: Map<string, ThreadSummary>;
  onReact: (message: RelayMessage, emoji: string) => void;
  // Absent where the host has nowhere to show a thread — then the action is
  // not offered at all rather than offered and dead.
  onThread?: (id: string) => void;
}) {
  const first = group.items[0];
  const agent = agentName(group.author);
  const ns = namespaceOf(group.author);

  if (group.standalone) {
    return (
      <div className="relay-block standalone">
        <div className="relay-block-body">
          {group.items.map((m) => (
            <div key={m.id} className={messageClass(m, highlight)} data-message-id={m.id}>
              <Body message={m} me={me} />
              <div className="relay-actions">
                <span className="relay-time">{ago(m.created_at)}</span>
                <Reactions message={m} onReact={onReact} />
              </div>
            </div>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="relay-block">
      <Face participant={group.author} face={group.face} />
      <div className="relay-block-body">
        <div className="relay-block-head">
          <span className="relay-author">{participantLabel(group.author, me)}</span>
          {/* A bridged human is not a platform identity — say so once, where
              the name is, rather than letting a snowflake read as a username. */}
          {ns !== "agent" && ns !== "user" && ns !== "" && <Chip>{ns}</Chip>}
          <span className="relay-time">{ago(first.created_at)}</span>
        </div>
        {group.items.map((m) => {
          // A reply drawn inside the thread pane already IS the thread, so it
          // offers neither the verb nor the count.
          const thread = inThread || !onThread ? undefined : summary(m, threads);
          return (
            <div key={m.id} className={messageClass(m, highlight)} data-message-id={m.id}>
              <Body message={m} me={me} />
              <div className="relay-actions">
                <Reactions message={m} onReact={onReact} />
                {agent && m.run_id && (
                  <Link to={`/runs/${m.run_id}`} className="relay-action">view run ↗</Link>
                )}
                {!inThread && onThread && !thread && (
                  <button type="button" className="relay-action"
                          onClick={() => onThread(m.thread_root ?? m.id)}>
                    reply in thread
                  </button>
                )}
              </div>
              {thread && onThread && (
                <ThreadAction message={m} thread={thread} onThread={onThread} />
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

/** The way into a thread. The replies themselves are NOT in the room — a room
 * where every side conversation is inlined is a room you cannot follow — so
 * this row is the only sign they exist, and it says how many and how recent so
 * the reader can decide without opening it. */
function ThreadAction({ message, thread, onThread }: {
  message: RelayMessage;
  thread: ThreadSummary;
  onThread: (id: string) => void;
}) {
  const last = ago(thread.last);
  return (
    <button type="button" className="relay-replies"
            onClick={() => onThread(message.thread_root ?? message.id)}>
      💬 {thread.count} {thread.count === 1 ? "reply" : "replies"}
      {last && <span className="relay-replies-when"> · last {last}</span>}
    </button>
  );
}

/** The thread hanging off this message, if it has one. Only a root can: a
 * reply's own id is never a thread root. The count is what the pane has
 * loaded plus whatever the stream has delivered since, which is all a client
 * can know without the server carrying one. */
function summary(message: RelayMessage, threads?: Map<string, ThreadSummary>):
  ThreadSummary | undefined {
  return message.thread_root && message.thread_root !== message.id
    ? undefined : threads?.get(message.id);
}

function messageClass(message: RelayMessage, highlight?: string | null): string {
  return message.id === highlight ? "relay-message highlight" : "relay-message";
}
