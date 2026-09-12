import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Chip, ChipButton } from "@ap/ui/chip";
import { Markdown } from "@ap/ui/markdown";
import type { RelayMessage } from "../../api";
import { ago } from "../../lib/time";
import { agentName, namespaceOf, participantLabel } from "../../lib/relay";
import { Face } from "./Face";
import type { MessageGroup, ThreadSummary } from "./useChannel";

// The room's transcript. Consecutive messages from the same author inside a
// short window are ONE block with one face and one timestamp — a chat where
// every line repeats the speaker reads like a log, not a conversation.

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

function Body({ message }: { message: RelayMessage }) {
  if (message.kind === "system") {
    // Joins, archivals, a rejected invocation: the room's own voice, and a
    // failure notice among them. Muted and italic so it never reads as speech.
    return <div className="relay-system">{message.body}</div>;
  }
  if (message.kind === "event") {
    const card = message.card ?? {};
    return (
      <div className="relay-card">
        {card.title && <div className="relay-card-title">{card.title}</div>}
        <div className="relay-card-body"><Markdown text={card.body || message.body} /></div>
      </div>
    );
  }
  return <Markdown text={message.body} className="relay-body" />;
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
              <Body message={m} />
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
              <Body message={m} />
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
