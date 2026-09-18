import { useMemo, useState } from "react";
import { Chip } from "@ap/ui/chip";
import { agentName, channelLabel, mentionableIn, otherParticipant } from "../../lib/relay";
import Compose from "./Compose";
import { Face } from "./Face";
import { Thinking } from "./Presence";
import Transcript from "./Transcript";
import { splitThreads, type Room } from "./useChannel";

// The room itself: header, transcript, who is thinking, compose. It draws the
// state a `useChannel` subscription holds and owns none of it, so the thread
// pane beside it is looking at the same live messages rather than at a second
// copy of them.

export default function ChannelView({ room, onThread, highlight, onHighlighted }: {
  room: Room;
  // Absent = this host has nowhere to put a thread (AgentDetail's dm tab), so
  // the pane must not offer to open one.
  onThread?: (id: string) => void;
  highlight?: string | null;
  onHighlighted?: () => void;
}) {
  // Bumped on every message the reader sends, so the transcript follows them
  // back to the live edge.
  const [pin, setPin] = useState(0);
  const { channel, messages, presence, me, agents, error, loaded } = room;

  // The room draws roots; the replies live in the thread pane, counted on the
  // root that opens it. A thread older than the loaded page can still be
  // opened from its root — it just does not advertise a count until the page
  // reaches it. A DM is one conversation, so it inlines everything (QA-16):
  // its replies pre-date the API answering top-level, and a pane with no
  // thread beside it — AgentDetail's tab — would otherwise never show them.
  const dm = channel?.kind === "dm";
  const { roots, threads } = useMemo(
    () => (dm ? { roots: messages, threads: new Map() } : splitThreads(messages)),
    [messages, dm]);
  // …and offers no way into one either, even where the host has a thread
  // pane: a reply posted there would be drawn twice, inline and in the pane.
  const openThread = dm ? undefined : onThread;

  const thinking = useMemo(
    () => presence.filter((p) => p.thinking_in.includes(room.channelId)),
    [presence, room.channelId]);
  const mentionable = useMemo(() => mentionableIn(channel, agents), [channel, agents]);

  const archived = channel?.archived_at != null;
  const title = channel ? channelLabel(channel, me) : "…";
  const group = channel?.kind === "group";
  const other = channel && channel.kind === "dm" ? otherParticipant(channel, me) : null;
  const faceOf = (participant: string) =>
    channel?.faces[agentName(participant) ?? ""] ?? null;
  const empty = loaded && !error && messages.length === 0 && channel !== null;

  const avatar = (size: number) => {
    if (group) {
      return (
        <span className="relay-faces">
          {channel!.participants.map((p) => (
            <Face key={p} participant={p} face={faceOf(p)} size={size} />
          ))}
        </span>
      );
    }
    if (other) return <Face participant={other} face={faceOf(other)} size={size} />;
    return <span className={size > 30 ? "relay-hash big" : "relay-hash"} aria-hidden="true">#</span>;
  };

  return (
    <section className={`relay-pane${empty ? " empty" : ""}`} aria-label={`Channel ${title}`}>
      <header className="relay-pane-head">
        {avatar(24)}
        <strong>{channel?.kind === "channel" ? channel.name : title}</strong>
        {channel?.topic && <span className="relay-topic muted">{channel.topic}</span>}
        {archived && <Chip variant="warn">archived</Chip>}
      </header>

      {error && <div className="error">{error}</div>}

      <Transcript rows={roots} me={me} loading={!loaded} highlight={highlight}
                  onHighlighted={onHighlighted} pin={pin}
                  onReact={room.react} onThread={openThread} threads={threads}>
        {empty && (
          <div className="relay-welcome">
            {avatar(44)}
            <h2>{channel!.kind === "channel" ? channel!.name : title}</h2>
            <p className="muted">{channel!.topic || "No topic yet."}</p>
            <p className="muted">Say something, or @mention an agent to wake it up.</p>
          </div>
        )}
      </Transcript>

      <Thinking who={thinking} />
      <Compose channelId={room.channelId} archived={archived} agents={agents}
               mentionable={mentionable}
               onPosted={(m) => { room.absorb([m]); setPin((n) => n + 1); }} />
    </section>
  );
}
