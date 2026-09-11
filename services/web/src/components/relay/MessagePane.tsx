import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Button } from "@ap/ui/button";
import { Chip } from "@ap/ui/chip";
import {
  api, type AgentSummary, type RelayChannelDetail, type RelayMessage,
  type RelayPresence, type RelayReaction,
} from "../../api";
import {
  agentName, channelLabel, mentionableIn, otherParticipant,
} from "../../lib/relay";
import Compose from "./Compose";
import { Face } from "./Face";
import { MessageBlock, type MessageGroup } from "./Message";
import { Thinking } from "./Presence";

// One room, live. The pane is the whole of Relay that AgentDetail reuses for
// an agent's DM, so it owns its own loading, its own stream and its own
// compose box and asks its host for nothing but a channel id.

const PAGE = 100;
// The API's own ceiling on a page, and what a catch-up asks for: a client that
// was away needs the gap, not a taste of it.
const CATCH_UP = 200;
// Consecutive messages from one author inside this window are one block.
const GROUP_WINDOW_MS = 5 * 60 * 1000;
// How often the fallback re-asks while the stream is down.
const POLL_MS = 5000;
// Reconnect delays. A rolling pod restart should end with a live socket, so
// the stream is retried on a widening delay and the poll only covers the gap.
const FIRST_RETRY_MS = 2000;
const MAX_RETRY_MS = 30000;

function byTime(a: RelayMessage, b: RelayMessage): number {
  const t = (a.created_at ?? "").localeCompare(b.created_at ?? "");
  return t !== 0 ? t : a.id.localeCompare(b.id);
}

/** Fold new messages into what we hold, oldest-first and deduped by id. The
 * stream, the fallback poll, the thread page and the compose box's own
 * response all land here, and all of them can carry a message we already have. */
function merge(prev: RelayMessage[], incoming: RelayMessage[]): RelayMessage[] {
  const by = new Map(prev.map((m) => [m.id, m]));
  for (const m of incoming) {
    const had = by.get(m.id);
    // A streamed message carries no reactions (they have no Kafka topic and
    // ride their own event), so an echo must not erase the ones on screen.
    by.set(m.id, had && !m.reactions?.length ? { ...had, ...m, reactions: had.reactions } : m);
  }
  return [...by.values()].sort(byTime);
}

function groupMessages(list: RelayMessage[]): MessageGroup[] {
  const out: MessageGroup[] = [];
  for (const m of list) {
    const standalone = m.kind !== "text";
    const open = out[out.length - 1];
    const last = open?.items[open.items.length - 1];
    const near = last && new Date(m.created_at ?? 0).getTime()
      - new Date(last.created_at ?? 0).getTime() < GROUP_WINDOW_MS;
    if (open && !open.standalone && !standalone && open.author === m.author && near) {
      open.items.push(m);
      continue;
    }
    out.push({ author: m.author, face: m.face, standalone, items: [m] });
  }
  return out;
}

export default function MessagePane({ channelId, threadId, onThread }: {
  channelId: string;
  // Set = the pane is showing one thread instead of the room. T9 hangs a side
  // panel off the same fetch; the fetch and the filter live here.
  threadId?: string | null;
  // Absent = this host has nowhere to put a thread (AgentDetail's dm tab), so
  // the pane must not offer to open one.
  onThread?: (id: string | null) => void;
}) {
  const [channel, setChannel] = useState<RelayChannelDetail | null>(null);
  const [messages, setMessages] = useState<RelayMessage[]>([]);
  // The thread's OWN page. A thread can be older than the room page we hold,
  // so filtering what is already on screen would show an empty conversation.
  const [thread, setThread] = useState<{ id: string; rows: RelayMessage[] } | null>(null);
  const [presence, setPresence] = useState<RelayPresence[]>([]);
  const [me, setMe] = useState<string | null>(null);
  const [agents, setAgents] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  // The newest id at the moment the stream opens — its resume cursor. Kept in
  // state (not a ref) because opening the stream WAITS for it.
  const [seed, setSeed] = useState<{ channel: string; after: string | null } | null>(null);
  const scroller = useRef<HTMLDivElement | null>(null);
  // The catch-up cursor. A ref because the interval that reads it is created
  // once per channel and must not be torn down per message.
  const newest = useRef<string | null>(null);
  // Reactions in flight, by message+emoji: a double-click would otherwise
  // send two toggles and land back where it started.
  const reacting = useRef(new Set<string>());

  const base = `/api/relay/channels/${encodeURIComponent(channelId)}`;

  // Identity and the enabled roster are platform-wide, not per-room.
  useEffect(() => {
    api<{ principal: string }>("/api/whoami")
      .then((w) => setMe(`user:${w.principal}`)).catch(() => setMe(null));
    api<AgentSummary[]>("/api/agents")
      .then((all) => setAgents(all.filter((a) => a.enabled !== false && !a.quarantined)
        .map((a) => a.name).sort()))
      .catch(() => setAgents([]));
  }, []);

  const reload = useCallback(async () => {
    const page = await api<RelayMessage[]>(
      `/api/relay/channels/${encodeURIComponent(channelId)}/messages?limit=${PAGE}`);
    // Without a cursor the API answers newest-first (it is a page of history);
    // a room reads the other way round.
    const rows = [...page].sort(byTime);
    setMessages(rows);
    return rows;
  }, [channelId]);

  useEffect(() => {
    let live = true;
    setChannel(null); setMessages([]); setLoaded(false); setError(null); setSeed(null);
    (async () => {
      try {
        const [detail, rows] = await Promise.all([
          api<RelayChannelDetail>(`/api/relay/channels/${encodeURIComponent(channelId)}`),
          reload(),
        ]);
        if (!live) return;
        setChannel(detail);
        setSeed({ channel: channelId, after: rows.length ? rows[rows.length - 1].id : null });
      } catch (err) {
        if (live) setError(err instanceof Error ? err.message : "Could not open this channel.");
      } finally {
        if (live) setLoaded(true);
      }
    })();
    api<RelayPresence[]>("/api/relay/presence")
      .then((rows) => { if (live) setPresence(rows); }).catch(() => {});
    return () => { live = false; };
  }, [channelId, reload]);

  // The thread's own page: root and replies, oldest-first, straight from the
  // API rather than filtered out of what happens to be loaded.
  useEffect(() => {
    if (!threadId) { setThread(null); return; }
    let live = true;
    setThread(null);
    api<RelayMessage[]>(
      `${base}/messages?thread=${encodeURIComponent(threadId)}&limit=${CATCH_UP}`)
      .then((rows) => { if (live) setThread({ id: threadId, rows: [...rows].sort(byTime) }); })
      .catch(() => { if (live) setThread({ id: threadId, rows: [] }); });
    return () => { live = false; };
  }, [base, threadId]);

  // --- live ----------------------------------------------------------------
  useEffect(() => {
    if (!seed || seed.channel !== channelId) return;
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

    const absorb = (rows: RelayMessage[]) => setMessages((prev) => merge(prev, rows));

    /** Everything that landed after the newest message we hold. With no cursor
     * — a room we have never seen a message in — there is nothing to be after,
     * so ask for the newest page instead. */
    const catchUp = () => api<RelayMessage[]>(newest.current
      ? `${base}/messages?after=${encodeURIComponent(newest.current)}&limit=${CATCH_UP}`
      : `${base}/messages?limit=${PAGE}`)
      .then(absorb).catch(() => {});

    function connect() {
      if (stopped) return;
      // Resume from what we hold, not from where the room was when the pane
      // opened: a reconnect after ten minutes must not replay ten minutes.
      const after = newest.current ?? seed!.after;
      const es = new EventSource(
        `${base}/events${after ? `?after=${encodeURIComponent(after)}` : ""}`,
        { withCredentials: true });
      stream = es;

      es.addEventListener("open", () => {
        // Live again: drop the poll and forget how long we were out.
        backoff = FIRST_RETRY_MS;
        stopPolling();
      });
      es.addEventListener("message", (e) => {
        const data = JSON.parse((e as MessageEvent).data) as RelayMessage;
        if (data.channel_id === channelId) absorb([data]);
      });
      es.addEventListener("reaction", (e) => {
        const r = JSON.parse((e as MessageEvent).data) as
          { message_id: string; emoji: string; count: number; participant: string };
        setMessages((prev) => prev.map((m) => m.id === r.message_id
          ? { ...m, reactions: applyReaction(m.reactions, r.emoji, r.count) } : m));
      });
      es.addEventListener("presence", (e) => {
        const p = JSON.parse((e as MessageEvent).data) as { agent: string; state: string };
        setPresence((prev) => {
          const was = prev.find((row) => row.agent === p.agent);
          const others = was?.thinking_in.filter((c) => c !== channelId) ?? [];
          // Merge, never replace: an agent thinking in three rooms must not be
          // reduced to this one because this one's stream spoke.
          const thinking_in = p.state === "thinking" ? [...others, channelId] : others;
          return [...prev.filter((row) => row.agent !== p.agent),
                  { agent: p.agent, state: p.state, thinking_in,
                    face: was?.face ?? { emoji: "", hue: 0 } }]
            .sort((a, b) => a.agent.localeCompare(b.agent));
        });
      });
      // The stream fell behind and dropped frames; it says where our picture
      // stops being true, and re-reading from the top is cheaper than guessing.
      es.addEventListener("overflow", () => { reload().catch(() => {}); });
      // The server ended it deliberately (no longer a member) — not a blip, so
      // no retry: reconnecting would be asking to be thrown out again.
      es.addEventListener("closed", stop);
      es.onerror = () => {
        if (stopped || stream !== es) return;
        es.close();
        stream = null;
        // Cover the gap while we are out, then try the socket again.
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
  }, [channelId, seed, reload, base]);

  useEffect(() => {
    newest.current = messages.length ? messages[messages.length - 1].id : null;
  }, [messages]);

  // Stick to the bottom. A room that scrolls away from the newest message the
  // moment it arrives is a room you cannot read.
  useEffect(() => {
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages.length, thread?.rows.length, channelId]);

  async function react(message: RelayMessage, emoji: string) {
    const key = `${message.id}:${emoji}`;
    if (reacting.current.has(key)) return;
    reacting.current.add(key);
    // Optimism would be wrong here: the server decides whether this is an add
    // or a take-back, and it answers with the count either way.
    try {
      const r = await api<RelayReaction>(
        `/api/relay/messages/${encodeURIComponent(message.id)}/reactions`,
        { method: "POST", body: JSON.stringify({ emoji }) });
      setMessages((prev) => prev.map((m) => m.id === message.id
        ? { ...m, reactions: applyReaction(m.reactions, r.emoji, r.count, r.mine) } : m));
    } catch {
      /* the row stays as it was — a lost reaction is not worth a banner */
    } finally {
      reacting.current.delete(key);
    }
  }

  const visible = useMemo(() => {
    if (!threadId) return messages;
    // The fetched page is the thread's history; anything that arrived live
    // since is in `messages` and belongs at the end of it.
    const live = messages.filter((m) => m.id === threadId || m.thread_root === threadId);
    return merge(thread?.id === threadId ? thread.rows : [], live);
  }, [messages, threadId, thread]);

  const groups = useMemo(() => groupMessages(visible), [visible]);
  const thinking = useMemo(
    () => presence.filter((p) => p.thinking_in.includes(channelId)), [presence, channelId]);
  const mentionable = useMemo(() => mentionableIn(channel, agents), [channel, agents]);

  const archived = channel?.archived_at != null;
  const title = channel ? channelLabel(channel, me) : "…";
  const group = channel?.kind === "group";
  const other = channel && channel.kind === "dm" ? otherParticipant(channel, me) : null;
  const faceOf = (participant: string) =>
    channel?.faces[agentName(participant) ?? ""] ?? null;
  // Waiting on the thread page is not the same as an empty room.
  const pending = Boolean(threadId) && thread?.id !== threadId;
  const empty = loaded && !error && !pending && visible.length === 0 && channel !== null;

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
        {threadId && (
          <span className="relay-thread-note">
            <Chip variant="accent">thread</Chip>
            <Button variant="link" onClick={() => onThread?.(null)}>back to the channel</Button>
          </span>
        )}
      </header>

      {error && <div className="error">{error}</div>}

      <div className="relay-messages" ref={scroller}>
        {empty && (
          <div className="relay-welcome">
            {avatar(44)}
            <h2>{channel!.kind === "channel" ? channel!.name : title}</h2>
            <p className="muted">{channel!.topic || "No topic yet."}</p>
            <p className="muted">Say something, or @mention an agent to wake it up.</p>
          </div>
        )}
        {groups.map((g) => (
          <MessageBlock key={g.items[0].id} group={g} me={me} threadId={threadId}
                        onReact={react} onThread={onThread ? (id) => onThread(id) : undefined} />
        ))}
        {(!loaded || pending) && <p className="muted">Loading…</p>}
      </div>

      <Thinking who={thinking} />
      <Compose channelId={channelId} archived={archived} agents={agents}
               mentionable={mentionable} threadId={threadId}
               onPosted={(m) => setMessages((prev) => merge(prev, [m]))} />
    </section>
  );
}

/** Fold one reaction count into a message's row. A count of zero means the
 * last person took theirs back, so the chip goes away rather than reading "0". */
function applyReaction(rows: RelayReaction[], emoji: string, count: number,
                       mine?: boolean): RelayReaction[] {
  const had = rows.find((r) => r.emoji === emoji);
  const rest = rows.filter((r) => r.emoji !== emoji);
  if (count <= 0) return rest;
  const next = { emoji, count, mine: mine ?? had?.mine ?? false };
  return had ? rows.map((r) => (r.emoji === emoji ? next : r)) : [...rows, next];
}
