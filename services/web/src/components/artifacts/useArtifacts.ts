import { useCallback, useEffect, useRef, useState } from "react";
import {
  api, artifactStats, deleteArtifact, getArtifact, listArtifacts,
  type Artifact, type ArtifactQuery, type ArtifactStats,
} from "../../api";
import { subscribeArtifactFeed, type FeedFrame } from "./feed";

// The Studio's live state, the board's shape (components/tickets/useTickets):
// a first list, then the stream — here the tab's ONE shared stream (`feed`)
// — and a re-list whenever the feed says frames may have been missed.
//
// One thing differs: the list is FILTERED on the server. A ticket board is
// five columns of one picture; a grid of pictures is a page of a store that
// can hold thousands, so the URL's filters travel to the list route and a
// change of filter is a new list. The stream does not care which filter is
// on — a frame that arrives is folded in only if it belongs to the view — so
// changing a filter never touches the socket.

const FIRST_RETRY_MS = 2000;
const MAX_RETRY_MS = 30000;

/** Whether a row belongs in the view the query describes — the list route's
 * own filters, so a frame is folded in exactly where a re-list would put it. */
export function matchesQuery(a: Artifact, q: ArtifactQuery): boolean {
  const term = (q.q ?? "").trim().toLowerCase();
  return (!q.kind || a.kind === q.kind)
    && (!q.owner || a.owner === q.owner)
    && (!q.source || a.source === q.source)
    && (!q.tag || a.tags.includes(q.tag))
    && (!term || a.name.toLowerCase().includes(term));
}

/** Newest first, by id: the list route's order, and a frame's row lands where
 * the list would have put it rather than wherever it arrived. */
export function upsert(prev: Artifact[], rows: Artifact[]): Artifact[] {
  const by = new Map(prev.map((a) => [a.id, a]));
  for (const a of rows) by.set(a.id, a);
  return [...by.values()].sort((x, y) => (y.created_at ?? "").localeCompare(x.created_at ?? ""));
}

export type ArtifactsView = {
  artifacts: Artifact[];
  stats: ArtifactStats | null;
  me: string | null;
  loaded: boolean;
  error: string | null;
  /** The soft delete, and the row gone from the view without waiting for the
   * stream to say so. */
  remove: (a: Artifact) => Promise<void>;
  /** Fold rows in — what a page that made something (an upload, a
   * generation) calls with the answer, rather than re-listing. */
  absorb: (rows: Artifact[]) => void;
};

export function useArtifacts(query: ArtifactQuery): ArtifactsView {
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [stats, setStats] = useState<ArtifactStats | null>(null);
  const [me, setMe] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The stream opens once the first page has landed, so a frame can never
  // arrive before the list it belongs to.
  const [seeded, setSeeded] = useState(false);
  const live = useRef(true);
  // The query as a string, so an equal query from a re-render is the same
  // dependency and not a new list; and as a ref, so the stream — which never
  // re-subscribes — always reads the current one.
  const key = JSON.stringify(query);
  const current = useRef(query);
  current.current = query;

  const absorb = useCallback((rows: Artifact[]) => {
    setArtifacts((prev) => upsert(prev, rows.filter((a) => matchesQuery(a, current.current))));
  }, []);

  const reload = useCallback(() => listArtifacts(JSON.parse(key) as ArtifactQuery)
    .then((rows) => { if (live.current) setArtifacts(rows); }), [key]);
  const latest = useRef(reload);
  latest.current = reload;

  // The count and the bytes are the store's own sums, not something a
  // filtered page can add up.
  const reloadStats = useCallback(() => artifactStats()
    .then((s) => { if (live.current) setStats(s); }).catch(() => {}), []);

  useEffect(() => {
    live.current = true;
    let retry: ReturnType<typeof setTimeout> | null = null;
    let backoff = FIRST_RETRY_MS;

    // The stream only opens once this has succeeded, so giving up on the first
    // failure would leave a page that is not just empty but permanently empty.
    function attempt() {
      reload()
        .then(() => { if (live.current) { setError(null); setSeeded(true); } })
        .catch((err) => {
          if (!live.current) return;
          setError(err instanceof Error ? err.message : "Could not load the artifacts.");
          retry = setTimeout(attempt, backoff);
          backoff = Math.min(backoff * 2, MAX_RETRY_MS);
        })
        .finally(() => { if (live.current) setLoaded(true); });
    }

    attempt();
    reloadStats();
    api<{ principal: string }>("/api/whoami")
      .then((w) => { if (live.current) setMe(`user:${w.principal}`); }).catch(() => {});
    return () => { live.current = false; if (retry) clearTimeout(retry); };
  }, [reload, reloadStats]);

  useEffect(() => {
    if (!seeded) return;
    return subscribeArtifactFeed((frame: FeedFrame) => {
      if (frame.type === "gap") { latest.current().catch(() => {}); return; }
      const { event, artifact } = frame.event;
      if (event === "created" && artifact) absorb([artifact]);
      if (event === "deleted" && artifact) {
        const gone = artifact.id;
        setArtifacts((prev) => prev.filter((a) => a.id !== gone));
      }
      // An `agent_image` frame changes a face, not the store; the bytes bar
      // moves on the other two.
      if (event !== "agent_image") reloadStats();
    });
  }, [seeded, absorb, reloadStats]);

  const remove = useCallback(async (a: Artifact) => {
    await deleteArtifact(a.id);
    setArtifacts((prev) => prev.filter((row) => row.id !== a.id));
    // The row is gone before the stream can say so; a card drawn from the
    // cache in the meantime must not still show it.
    settle(a.id, null);
    reloadStats();
  }, [reloadStats]);

  return { artifacts, stats, me, loaded, error, remove, absorb };
}

// --- one artifact, by id --------------------------------------------------------
// What a card in a room asks for. Cached per id across the page: a room
// re-renders on every frame that lands, a transcript can name the same
// picture ten times, and a row's fields never change under its id. What DOES
// change is whether there is a row at all — a picture can land after the chip
// naming it was drawn, and be deleted while the chip is on screen — so the
// cache listens to the feed and every mounted card is told when its answer
// moves.

// How long "nobody has this" is believed before a card asks again. Long
// enough that a transcript full of dead chips is not a request per render;
// short enough that a chip drawn ahead of its row, on a page the feed never
// reached, corrects itself in the reader's lifetime.
const MISSING_TTL_MS = 30000;
// The cache is bounded: a session that scrolls a busy #art for an afternoon
// must not hold every picture it ever passed. Insertion order is age.
const CACHE_MAX = 500;

type Entry = { answer: Promise<Artifact | null>; missingAt: number | null };

const rows = new Map<string, Entry>();
const watchers = new Set<(id: string) => void>();
let unsubscribe: (() => void) | null = null;

function fetchOne(id: string): Promise<Artifact | null> {
  const had = rows.get(id);
  // A stale "not found" is asked again; anything else is the answer.
  if (had && (had.missingAt === null || Date.now() - had.missingAt < MISSING_TTL_MS)) {
    return had.answer;
  }
  const entry: Entry = { missingAt: null, answer: Promise.resolve(null) };
  entry.answer = getArtifact(id)
    .catch((err: unknown) => {
      // A 404 is an answer — "nobody has this" — and is kept for a while.
      // Anything else (a blip, a 500) is forgotten so the next card to ask
      // tries afresh.
      if (err instanceof Error && err.message.startsWith("404")) {
        entry.missingAt = Date.now();
        return null;
      }
      if (rows.get(id) === entry) rows.delete(id);
      throw err;
    });
  remember(id, entry);
  return entry.answer;
}

function remember(id: string, entry: Entry): void {
  rows.delete(id);
  rows.set(id, entry);
  while (rows.size > CACHE_MAX) {
    const oldest = rows.keys().next().value;
    if (oldest === undefined) break;
    rows.delete(oldest);
  }
}

/** The answer for an id is now `row` (a null is a fresh "gone"), and every
 * card showing it is told. */
function settle(id: string, row: Artifact | null): void {
  remember(id, { answer: Promise.resolve(row), missingAt: row ? null : Date.now() });
  for (const w of [...watchers]) w(id);
}

/** Drop what is held for an id, and tell its cards to ask again. */
function forget(id: string): void {
  rows.delete(id);
  for (const w of [...watchers]) w(id);
}

function onFeed(frame: FeedFrame): void {
  if (frame.type !== "artifact") return;
  const { event, artifact } = frame.event;
  if (!artifact) return;
  // Created: the frame carries the row, but the card re-fetches rather than
  // trusting it — the cache's answers all come from the one route, and a
  // chip drawn ahead of its row is the very case this exists for.
  if (event === "created") forget(artifact.id);
  if (event === "deleted") settle(artifact.id, null);
}

/** Be told when the answer for any id moves. The feed is joined by the first
 * watcher and left by the last, so a page with no cards costs no socket. */
function watch(w: (id: string) => void): () => void {
  watchers.add(w);
  unsubscribe ??= subscribeArtifactFeed(onFeed);
  return () => {
    watchers.delete(w);
    if (watchers.size === 0 && unsubscribe) { unsubscribe(); unsubscribe = null; }
  };
}

export type ArtifactLookup =
  { state: "loading"; artifact: null }
  | { state: "ok"; artifact: Artifact }
  | { state: "missing"; artifact: null };

export function useArtifact(id: string | null): ArtifactLookup {
  const [look, setLook] = useState<ArtifactLookup>({ state: "loading", artifact: null });
  useEffect(() => {
    if (!id) { setLook({ state: "missing", artifact: null }); return; }
    let on = true;
    // No "loading" flash on a re-ask: a card that is being re-fetched after a
    // frame keeps what it shows until the new answer lands.
    const ask = () => fetchOne(id)
      .then((a) => { if (on) setLook(a ? { state: "ok", artifact: a } : { state: "missing", artifact: null }); })
      // A card that could not be fetched is not a card that does not exist;
      // it reads as missing all the same, since a broken card is worse, and
      // the cache has already forgotten the failure.
      .catch(() => { if (on) setLook({ state: "missing", artifact: null }); });
    ask();
    const stop = watch((moved) => { if (moved === id) ask(); });
    return () => { on = false; stop(); };
  }, [id]);
  return look;
}
