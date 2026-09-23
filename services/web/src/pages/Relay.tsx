import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, type Project, type Team, type RelayChannel, type RelayChannelDetail, type RelayMessage } from "../api";
import ChannelView from "../components/relay/ChannelView";
import Rail from "../components/relay/Rail";
import Search from "../components/relay/Search";
import ThreadPane from "../components/relay/ThreadPane";
import { useChannel } from "../components/relay/useChannel";
import { channelLabel } from "../lib/relay";
import { useTitle } from "../lib/title";

// Relay (docs/design/19): the place the agents and the humans are in the same
// rooms. Which room you are in lives in the URL — `?kind=dm` picks the DM
// side, `?channel=` a specific room, `?thread=` one conversation inside it —
// so a room is a link somebody can send you.

export default function Relay() {
  const [params, setParams] = useSearchParams();
  const kind = params.get("kind");
  const chosen = params.get("channel");
  const thread = params.get("thread");
  const popout = params.get("popout") === "1";
  const [channels, setChannels] = useState<RelayChannel[]>([]);
  const [teams, setTeams] = useState<Team[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [me, setMe] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [drawer, setDrawer] = useState(false);
  // The message a search result sent us to, marked until it has been seen.
  const [highlight, setHighlight] = useState<string | null>(null);

  // The mark is spent by the pane that shows the row, not by a clock started
  // here: a channel that took two seconds to load would otherwise arrive with
  // the highlight already over.
  const clearHighlight = useCallback(() => setHighlight(null), []);

  const load = useCallback(() => api<RelayChannel[]>("/api/relay/channels")
    .then(setChannels)
    .catch((err) => setError(err instanceof Error ? err.message : "Could not load the rooms."))
    .finally(() => setLoaded(true)), []);

  useEffect(() => {
    load();
    api<Team[]>("/api/teams").then(setTeams).catch(() => {});
    api<Project[]>("/api/projects").then(setProjects).catch(() => {});
    api<{ principal: string }>("/api/whoami")
      .then((w) => setMe(`user:${w.principal}`)).catch(() => setMe(null));
  }, [load]);

  // What the rail lands on before anyone clicks: the room the URL names, else
  // the first of whichever side `?kind` asked for. Deliberately NOT written
  // back into the URL — a default nobody chose should not become history.
  const selected = useMemo(() => {
    if (chosen && channels.some((c) => c.id === chosen)) return chosen;
    const wanted = kind === "dm"
      ? channels.filter((c) => c.home !== "external" && c.kind !== "channel")
      : kind === "connected"
        ? channels.filter((c) => c.home === "external")
        : channels.filter((c) => c.home !== "external" && c.kind === "channel");
    return (wanted[0] ?? channels[0])?.id ?? null;
  }, [chosen, kind, channels]);

  // The tab is named after the room you are in — a browser holding four rooms
  // open is the normal way to read Relay, and four identical tabs is not.
  const room = channels.find((c) => c.id === selected) ?? null;
  useTitle(room && channelLabel(room, me), "Relay");

  function select(id: string) {
    const next = new URLSearchParams(params);
    next.set("channel", id);
    // The thread belonged to the room we just left.
    next.delete("thread");
    setParams(next);
    setDrawer(false);
  }

  function selectThread(id: string | null) {
    const next = new URLSearchParams(params);
    if (id) next.set("thread", id);
    else next.delete("thread");
    setParams(next);
  }

  /** A search hit: open the room, open the conversation the message is part of
   * (its own thread if it is a root, the root's if it is a reply), and mark it
   * so the eye lands on the line rather than on the room. */
  function openHit(m: RelayMessage) {
    const next = new URLSearchParams(params);
    next.set("channel", m.channel_id);
    next.set("thread", m.thread_root ?? m.id);
    setParams(next);
    setHighlight(m.id);
    setDrawer(false);
  }

  function onCreated(channel: RelayChannelDetail) {
    setChannels((prev) => [channel, ...prev.filter((c) => c.id !== channel.id)]);
    select(channel.id);
  }

  function roomUrl(poppedOut: boolean): string {
    const url = new URL(window.location.href);
    if (selected) url.searchParams.set("channel", selected);
    if (poppedOut) url.searchParams.set("popout", "1");
    else url.searchParams.delete("popout");
    return url.href;
  }

  function openPopout() {
    if (selected) window.open(roomUrl(true), "_blank", "popup=yes,noopener,width=1100,height=800");
  }

  async function setScope(teamSlug: string, projectSlug: string) {
    if (!selected) return;
    try {
      const updated = await api<RelayChannel>(`/api/relay/channels/${selected}/scope`, {
        method: "PUT", body: JSON.stringify({ team_slug: teamSlug || null, project_slug: projectSlug || null }),
      });
      setChannels(prev => prev.map(c => c.id === selected ? { ...c, team_id: updated.team_id, project_id: updated.project_id } : c));
      setError(null);
    } catch (e) { setError(String(e)); }
  }

  const currentTeam = teams.find(t => t.id === room?.team_id)?.slug ?? "";
  const currentProject = projects.find(p => p.id === room?.project_id)?.slug ?? "";

  return (
    <div className={`page page-relay${popout ? " relay-popout" : ""}`}>
      {popout ? (
        <div className="relay-popout-bar">
          <span>Relay · {room ? channelLabel(room, me) : "room"}</span>
          {selected && <a href={roomUrl(false)}>Open in platform ↗</a>}
        </div>
      ) : (
        <div className="relay-head">
          <div>
            <h1>Relay</h1>
            <p className="muted">
              The rooms the platform talks in. Agents are members like anyone else — @mention one and
              it wakes up, answers in the channel, and links the run it did it in. Use <code>@team:slug</code> to summon a team's members in this room.
            </p>
          </div>
          <Search channels={channels} me={me} current={selected} onPick={openHit} />
        </div>
      )}
      {error && <div className="error">{error}</div>}
      {!popout && room && (teams.length > 0 || projects.length > 0) &&
        <div className="row-actions" aria-label="Conversation context">
          <label>Team <select value={currentTeam} onChange={e => setScope(e.target.value, currentProject)}>
            <option value="">None</option>{teams.filter(t => !t.archived).map(t => <option key={t.id} value={t.slug}>{t.name}</option>)}
          </select></label>
          <label>Project <select value={currentProject} onChange={e => setScope(currentTeam, e.target.value)}>
            <option value="">None</option>{projects.filter(p => !p.archived).map(p => <option key={p.id} value={p.slug}>{p.name}</option>)}
          </select></label>
        </div>}

      {/* The thread is a third column, and three columns do not fit every
          window — the rail steps aside for it below 1200px (CSS). */}
      <div className="relay-layout" data-thread={thread ? "open" : "closed"}>
        {!popout && (
          <Rail channels={channels} selected={selected} me={me} loaded={loaded}
                onSelect={select} onCreated={onCreated}
                open={drawer} onToggle={() => setDrawer((d) => !d)} />
        )}
        {selected
          // Keyed on the room: switching channels is a new subscription, not a
          // mutation of the one on screen.
          ? <Room key={selected} channelId={selected} thread={thread} highlight={highlight}
                  onHighlighted={clearHighlight} onThread={selectThread}
                  onPopout={popout ? undefined : openPopout} />
          : (
            <section className="relay-pane">
              <div className="relay-messages">
                <div className="relay-welcome">
                  <span className="relay-hash big" aria-hidden="true">#</span>
                  <h2>No rooms yet</h2>
                  <p className="muted">
                    {loaded
                      ? "Make a channel and say something — the agents are already members."
                      : "Loading…"}
                  </p>
                </div>
              </div>
            </section>
          )}
      </div>
    </div>
  );
}

/** The room and, when one is open, the thread beside it — both reading the one
 * subscription, so a reply posted in the thread shows up in the channel behind
 * it without a second stream to deliver it. */
function Room({ channelId, thread, highlight, onHighlighted, onThread, onPopout }: {
  channelId: string;
  thread: string | null;
  highlight: string | null;
  onHighlighted: () => void;
  onThread: (id: string | null) => void;
  onPopout?: () => void;
}) {
  const room = useChannel(channelId);
  return (
    <>
      <ChannelView room={room} onThread={onThread} highlight={highlight}
                   onHighlighted={onHighlighted} onPopout={onPopout} />
      {thread && (
        <ThreadPane room={room} threadId={thread} highlight={highlight}
                    onHighlighted={onHighlighted} onClose={() => onThread(null)} />
      )}
    </>
  );
}
