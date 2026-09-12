import { useEffect, useMemo, useRef, useState } from "react";
import type { RelayMessage } from "../../api";
import { MessageBlock } from "./Message";
import { groupMessages, type ThreadSummary } from "./useChannel";

// The scrolling half of a room — the same list whether it is drawing a whole
// channel or one thread beside it, so the grouping, the follow-the-live-edge
// rule and the "jump to the message you searched for" behaviour are written
// once.

// How close to the bottom still counts as reading the live edge. A reader who
// has gone up into history must not be dragged back by an arrival, and a
// reader at the bottom must not have to chase one.
const STICK_SLACK = 80;
// How long a jumped-to message stays marked. The countdown starts when the row
// is actually on screen, not when the link was clicked: a slow channel would
// otherwise spend the whole mark before there was anything to see.
const HIGHLIGHT_MS = 2500;

export default function Transcript({ rows, me, loading, highlight, onHighlighted,
                                     onReact, onThread, threads, inThread, pin,
                                     children }: {
  rows: RelayMessage[];
  me: string | null;
  loading: boolean;
  /** A message id to scroll to and mark, set when a search result is opened. */
  highlight?: string | null;
  /** Called once the marked row has been shown for its moment. */
  onHighlighted?: () => void;
  onReact: (message: RelayMessage, emoji: string) => void;
  // Absent where the host has nowhere to show a thread (AgentDetail's dm tab),
  // so the action is not offered at all rather than offered and dead.
  onThread?: (id: string) => void;
  /** The threads hanging off these messages, for the "N replies" affordance. */
  threads?: Map<string, ThreadSummary>;
  /** This list IS a thread, so a reply inside it must not offer to open one. */
  inThread?: boolean;
  /** Bumped when the reader posts: their own message always brings them back
   * to the live edge, wherever in history they were reading. */
  pin?: number;
  /** The welcome card an empty room draws in place of a transcript. */
  children?: React.ReactNode;
}) {
  const scroller = useRef<HTMLDivElement | null>(null);
  // Whether the reader is parked at the live edge. A ref, not state: it is
  // read by the scroll effect and must never cause a render of its own.
  const stick = useRef(true);
  // The highlight we have already scrolled to, so the arrival of another
  // message does not re-scroll (or restart the countdown on) the same row.
  const [found, setFound] = useState<string | null>(null);
  const groups = useMemo(() => groupMessages(rows), [rows]);

  useEffect(() => {
    const el = scroller.current;
    if (el && stick.current && !highlight) el.scrollTop = el.scrollHeight;
  }, [rows.length, highlight]);

  // Their own message: back to the bottom, and reading from the bottom again.
  useEffect(() => {
    const el = scroller.current;
    if (!el || pin === undefined) return;
    stick.current = true;
    el.scrollTop = el.scrollHeight;
  }, [pin]);

  // Re-runs as rows land, because the message a search result points at is
  // usually not on screen yet when the click happens.
  useEffect(() => {
    if (!highlight || found === highlight) return;
    const row = scroller.current
      ?.querySelector(`[data-message-id="${CSS.escape(highlight)}"]`);
    if (!row) return;
    row.scrollIntoView({ block: "center" });
    setFound(highlight);
  }, [highlight, rows, found]);

  useEffect(() => {
    if (!highlight) { setFound(null); return; }
    if (found !== highlight) return;
    const id = setTimeout(() => onHighlighted?.(), HIGHLIGHT_MS);
    return () => clearTimeout(id);
  }, [highlight, found, onHighlighted]);

  return (
    <div className="relay-messages" ref={scroller}
         onScroll={(e) => {
           const el = e.currentTarget;
           stick.current = el.scrollHeight - el.scrollTop - el.clientHeight <= STICK_SLACK;
         }}>
      {children}
      {groups.map((g) => (
        <MessageBlock key={g.items[0].id} group={g} me={me} inThread={inThread}
                      highlight={highlight} threads={threads}
                      onReact={onReact} onThread={onThread} />
      ))}
      {loading && <p className="muted">Loading…</p>}
    </div>
  );
}
