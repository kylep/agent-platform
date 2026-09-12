import { useMemo, useState } from "react";
import { Button } from "@ap/ui/button";
import { mentionableIn, participantLabel } from "../../lib/relay";
import Compose from "./Compose";
import Transcript from "./Transcript";
import { merge, useThreadPage, type Room } from "./useChannel";

// A thread, beside its room rather than instead of it — the point of a thread
// is following a side conversation without losing the channel it happened in.
// Below the breakpoint there is no "beside", so it becomes a sheet over the
// room with a way back.

export default function ThreadPane({ room, threadId, highlight, onHighlighted, onClose }: {
  room: Room;
  /** The root message's id: what `?thread=` carries and what a reply posts to. */
  threadId: string;
  highlight?: string | null;
  onHighlighted?: () => void;
  onClose: () => void;
}) {
  const { rows, pending } = useThreadPage(room.channelId, threadId);
  const [pin, setPin] = useState(0);

  // The fetched page is the thread's history; anything that arrived on the
  // room's stream since belongs at the end of it.
  const visible = useMemo(() => merge(rows, room.messages.filter(
    (m) => m.id === threadId || m.thread_root === threadId)), [rows, room.messages, threadId]);

  const mentionable = useMemo(
    () => mentionableIn(room.channel, room.agents), [room.channel, room.agents]);
  const root = visible.find((m) => m.id === threadId) ?? visible[0];
  const who = root ? participantLabel(root.author, room.me) : null;

  return (
    <section className="relay-pane relay-thread" aria-label="Thread">
      <header className="relay-pane-head">
        {/* Two controls, one job, because the pane is two different things: a
            column you dismiss on a desktop, a sheet you back out of on a
            phone. Each is hidden where the other is the right gesture. */}
        <Button variant="secondary" className="relay-thread-back" onClick={onClose}>
          ← Back to the channel
        </Button>
        <strong>Thread</strong>
        {who && <span className="relay-topic muted">started by {who}</span>}
        <button type="button" className="relay-thread-close" aria-label="Close thread"
                onClick={onClose}>✕</button>
      </header>

      <Transcript rows={visible} me={room.me} loading={pending} highlight={highlight}
                  onHighlighted={onHighlighted} pin={pin} inThread onReact={room.react} />

      <Compose channelId={room.channelId} archived={room.channel?.archived_at != null}
               agents={room.agents} mentionable={mentionable} threadId={threadId}
               onPosted={(m) => { room.absorb([m]); setPin((n) => n + 1); }} />
    </section>
  );
}
