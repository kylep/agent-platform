import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, apiErrorMessage } from "../api";
import { Banner } from "@ap/ui/banner";
import { Button } from "@ap/ui/button";
import { Chip, chipStatusVariant } from "@ap/ui/chip";
import { useTitle } from "../lib/title";
import MapCanvas, { type VCharacter, type VObject } from "../components/village/MapCanvas";

// The Live page, P1 subset (plan §9): map, follow / whole-village toggle, side
// feed and the admin strip. Data: GET /api/village/state, then the SSE stream.
// All text is rendered as React text nodes.

type Session = { id: string; day: string; kind: string; state: string; length_min: number;
  started_at: string | null; end_reason: string | null };
type VEvent = { seq: number; kind: string; actor: string | null; public_text: string | null;
  tick: number };
type State = { session: Session | null; last_seq: number;
  snapshot: { tick: number; state: { map?: { w?: number; h?: number };
    characters?: VCharacter[] | Record<string, VCharacter>; objects?: VObject[] } } | null };
type Admin = { runs: { id: string; persona: string; role: string; state: string; attempt: number }[];
  personas: Record<string, { think_ms_p50: number | null; cycles: number; last_wait: string | null }> };
type Frame = { characters: { id: string; x: number; y: number }[] };

const FEED_MAX = 60;
const asList = (c: VCharacter[] | Record<string, VCharacter> | undefined): VCharacter[] =>
  Array.isArray(c) ? c : Object.entries(c ?? {}).map(([id, v]) => ({ ...v, id }));

export default function Village() {
  useTitle("Village");
  const [session, setSession] = useState<Session | null>(null);
  const [chars, setChars] = useState<VCharacter[]>([]);
  const [objects, setObjects] = useState<VObject[]>([]);
  const [map, setMap] = useState<{ w?: number; h?: number } | undefined>();
  const [feed, setFeed] = useState<VEvent[]>([]);
  const [admin, setAdmin] = useState<Admin | null>(null);
  const [follow, setFollow] = useState<string | null>(null);
  const [whole, setWhole] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [live, setLive] = useState(false);
  const cursor = useRef(0);

  const load = useCallback(async () => {
    try {
      const st = await api<State>("/api/village/state");
      setSession(st.session);
      cursor.current = st.last_seq;
      const s = st.snapshot?.state;
      setChars(asList(s?.characters));
      setObjects(s?.objects ?? []);
      setMap(s?.map);
      setError(null);
    } catch (e) { setError(apiErrorMessage(e, "Could not load the village.")); }
    api<Admin>("/api/village/admin").then(setAdmin).catch(() => {});
  }, []);

  useEffect(() => { void load(); }, [load]);

  useEffect(() => {
    let es: EventSource | null = null;
    let retry: number | undefined;
    const open = () => {
      es = new EventSource(`/api/village/stream?after=${cursor.current}`, { withCredentials: true });
      es.onopen = () => setLive(true);
      es.onerror = () => { setLive(false); es?.close(); retry = window.setTimeout(open, 3000); };
      es.addEventListener("session", (m) => setSession(JSON.parse((m as MessageEvent).data)));
      es.addEventListener("event", (m) => {
        const ev = JSON.parse((m as MessageEvent).data) as VEvent;
        cursor.current = Math.max(cursor.current, ev.seq);
        setFeed((f) => [ev, ...f].slice(0, FEED_MAX));
      });
      es.addEventListener("frame", (m) => {
        const fr = JSON.parse((m as MessageEvent).data) as Frame;
        setChars((cur) => {
          const byId = new Map(fr.characters.map((c) => [c.id, c]));
          const known = cur.map((c) => ({ ...c, ...(byId.get(c.id) ?? {}) }));
          const extra = fr.characters.filter((c) => !cur.some((k) => k.id === c.id));
          return [...known, ...extra];
        });
      });
    };
    open();
    return () => { es?.close(); if (retry) window.clearTimeout(retry); };
  }, []);

  const control = async (path: string) => {
    try { await api(path, { method: "POST" }); await load(); }
    catch (e) { setError(apiErrorMessage(e, "That control is not available yet.")); }
  };

  const followed = useMemo(() => chars.find((c) => c.id === follow) ?? null, [chars, follow]);
  const running = session?.state === "live" || session?.state === "closing";

  return (
    <div className="page page-wide">
      <h1>Village</h1>
      {error && <Banner variant="danger">{error}</Banner>}
      <div className="mb-3 flex flex-wrap items-center gap-2">
        {session
          ? <><Chip variant={chipStatusVariant(session.state)}>{session.state}</Chip>
              <span className="text-sm text-muted">{session.day} · {session.kind} · {session.length_min} min</span></>
          : <span className="text-sm text-muted">No session yet.</span>}
        <Chip variant={live ? "ok" : "neutral"}>{live ? "stream up" : "stream down"}</Chip>
        <Button size="sm" variant="secondary" onClick={() => { setWhole(true); setFollow(null); }}
                aria-pressed={whole}>Whole village</Button>
        {chars.map((c) => (
          <Button key={c.id} size="sm" variant="secondary"
                  onClick={() => { setFollow(c.id); setWhole(false); }}>
            Follow {c.name || c.id}
          </Button>
        ))}
        <span className="grow" />
        <Button size="sm" variant="secondary" disabled={running}
                onClick={() => void control("/api/village/sessions/test")}>Start test</Button>
        <Button size="sm" variant="danger" disabled={!running || !session}
                onClick={() => session && void control(`/api/village/sessions/${session.id}/stop`)}>Stop</Button>
      </div>
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <div>
          <MapCanvas map={map} characters={chars} objects={objects} follow={follow} whole={whole}
                     onPick={(id) => { setFollow(id); setWhole(id === null); }} />
          {followed && <p className="mt-2 text-sm text-muted">
            Following {followed.name || followed.id}{followed.now ? `: ${followed.now}` : ""}
          </p>}
        </div>
        <aside className="flex flex-col gap-4">
          <section aria-label="Feed" className="rounded-lg border border-border bg-surface p-3">
            <h2 className="mb-2 text-sm font-semibold">Feed</h2>
            {feed.length === 0
              ? <p className="text-sm text-muted">Nothing yet.</p>
              : <ul className="m-0 flex max-h-96 list-none flex-col gap-1 overflow-y-auto p-0 text-sm">
                  {feed.map((e) => <li key={e.seq}>
                    <span className="text-subtle">{e.kind}</span>{" "}
                    {e.actor && <strong>{e.actor} </strong>}{e.public_text}
                  </li>)}
                </ul>}
          </section>
          <section aria-label="Admin" className="rounded-lg border border-border bg-surface p-3">
            <h2 className="mb-2 text-sm font-semibold">Runs</h2>
            {!admin || admin.runs.length === 0
              ? <p className="text-sm text-muted">No runs this session.</p>
              : <ul className="m-0 flex list-none flex-col gap-1 p-0 text-sm">
                  {admin.runs.map((r) => <li key={r.id}>
                    {r.persona} {r.role} <Chip variant={chipStatusVariant(r.state)}>{r.state}</Chip>
                    {" "}attempt {r.attempt}
                    {admin.personas[r.persona]?.think_ms_p50 != null &&
                      <span className="text-muted"> · think p50 {admin.personas[r.persona].think_ms_p50} ms</span>}
                  </li>)}
                </ul>}
          </section>
        </aside>
      </div>
    </div>
  );
}
