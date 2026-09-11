import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, type RelayChannel, type RelayChannelDetail } from "../api";
import MessagePane from "../components/relay/MessagePane";
import Rail from "../components/relay/Rail";

// Relay (docs/design/19): the place the agents and the humans are in the same
// rooms. Which room you are in lives in the URL — `?kind=dm` picks the DM
// side, `?channel=` a specific room, `?thread=` one conversation inside it —
// so a room is a link somebody can send you.

export default function Relay() {
  const [params, setParams] = useSearchParams();
  const kind = params.get("kind");
  const chosen = params.get("channel");
  const thread = params.get("thread");
  const [channels, setChannels] = useState<RelayChannel[]>([]);
  const [me, setMe] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [drawer, setDrawer] = useState(false);

  const load = useCallback(() => api<RelayChannel[]>("/api/relay/channels")
    .then(setChannels)
    .catch((err) => setError(err instanceof Error ? err.message : "Could not load the rooms."))
    .finally(() => setLoaded(true)), []);

  useEffect(() => {
    load();
    api<{ principal: string }>("/api/whoami")
      .then((w) => setMe(`user:${w.principal}`)).catch(() => setMe(null));
  }, [load]);

  // What the rail lands on before anyone clicks: the room the URL names, else
  // the first of whichever side `?kind` asked for. Deliberately NOT written
  // back into the URL — a default nobody chose should not become history.
  const selected = useMemo(() => {
    if (chosen && channels.some((c) => c.id === chosen)) return chosen;
    const wanted = kind === "dm"
      ? channels.filter((c) => c.kind !== "channel")
      : channels.filter((c) => c.kind === "channel");
    return (wanted[0] ?? channels[0])?.id ?? null;
  }, [chosen, kind, channels]);

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

  function onCreated(channel: RelayChannelDetail) {
    setChannels((prev) => [channel, ...prev.filter((c) => c.id !== channel.id)]);
    select(channel.id);
  }

  return (
    <div className="page page-wide">
      <h1>Relay</h1>
      <p className="muted">
        The rooms the platform talks in. Agents are members like anyone else — @mention one and it
        wakes up, answers in the channel, and links the run it did it in.
      </p>
      {error && <div className="error">{error}</div>}

      <div className="relay-layout">
        <Rail channels={channels} selected={selected} me={me} loaded={loaded}
              onSelect={select} onCreated={onCreated}
              open={drawer} onToggle={() => setDrawer((d) => !d)} />
        {selected
          ? <MessagePane key={selected} channelId={selected} threadId={thread}
                         onThread={selectThread} />
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
