import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../../api";
import {
  isStale, type WikiDiff, type WikiEvent, type WikiHistoryRow, type WikiPage,
  type WikiPageDetail, type WikiStats, type WikiWantedRow,
} from "../../lib/wiki";

// The wiki's live state, in one place — the same shape as the ticket board's
// `useTickets`: one EventSource, a poll that only covers the gap while it is
// down, and a widening retry so a rolling pod restart ends with a live socket.
// There is no `after=` cursor for the same reason there is none there: the API
// says a reader that fell behind resyncs by re-listing, which is one request
// and always correct.
//
// What differs is what a frame IS. A ticket frame is the row; a wiki frame is
// the whole `wiki.events` payload — the page AND the edit that made it, because
// "recent changes" is a list of EDITS, and the page alone carries neither the
// reason nor how many lines moved.

const POLL_MS = 15000;
const FIRST_RETRY_MS = 2000;
const MAX_RETRY_MS = 30000;
// The list route's own cap. Every page is loaded once so the chip pass can
// answer "does this slug exist" without a request per link, and so the stale
// list can be read off it (the route can ask for pages changed SINCE a time,
// never for ones untouched since).
const LIST_LIMIT = 200;
// How many edits the rail keeps. It is a feed of what is happening now, not a
// log — the history of a page lives on the page.
const RECENT = 20;

/** One row of Recent changes. `added`/`removed` are null on a row that was
 * SEEDED from the page listing rather than delivered by the stream: the listing
 * says a page changed and when, and nothing about why or by how much. Reported
 * as unknown rather than as zero, which would be a claim. */
export type WikiChange = {
  key: string;                  // slug + version — one edit, one row
  slug: string;
  title: string;
  author: string;
  version: number;
  reason: string;
  at: string | null;
  added: number | null;
  removed: number | null;
  run_id: string | null;
};

export type WikiIndex = {
  pages: WikiPage[];
  /** Every live slug, or null until the listing has landed — what makes a chip
   * blue or red. Null is "not known yet", never "nothing exists". */
  slugs: Set<string> | null;
  wanted: WikiWantedRow[];
  stats: WikiStats | null;
  recent: WikiChange[];
  tags: string[];
  stale: WikiPage[];
  /** The signed-in principal as a participant string — what makes "you" mean
   * you and not every other human who has edited a page. */
  me: string | null;
  loaded: boolean;
  error: string | null;
  reload: () => void;
};

/** Fold rows into what we hold, by slug. The newer `updated_at` wins rather
 * than the later arrival: a re-list that started before an edit can land after
 * the frame that carried it, and without this the page would walk backwards. */
export function upsertPages(prev: WikiPage[], rows: WikiPage[]): WikiPage[] {
  const by = new Map(prev.map((p) => [p.slug, p]));
  for (const row of rows) {
    const had = by.get(row.slug);
    if (had && (had.updated_at ?? "") > (row.updated_at ?? "")) continue;
    // An archived page is out of the wiki by definition — it stops being
    // listed, so a frame saying so takes it out of the index too.
    if (row.archived_at) by.delete(row.slug);
    else by.set(row.slug, row);
  }
  return [...by.values()].sort((a, b) => (b.updated_at ?? "").localeCompare(a.updated_at ?? ""));
}

/** Newest first, one row per edit, capped. Keyed by slug+version so a frame
 * replayed after a reconnect does not double the row it already delivered. */
export function upsertChanges(prev: WikiChange[], rows: WikiChange[]): WikiChange[] {
  const by = new Map(prev.map((c) => [c.key, c]));
  for (const row of rows) {
    // A streamed row knows more than a seeded one about the same edit, so it
    // replaces it; a seeded row never overwrites what the stream delivered.
    const had = by.get(row.key);
    if (had && row.added === null && had.added !== null) continue;
    by.set(row.key, row);
  }
  return [...by.values()]
    .sort((a, b) => (b.at ?? "").localeCompare(a.at ?? "") || b.version - a.version)
    .slice(0, RECENT);
}

function seeded(page: WikiPage): WikiChange {
  return {
    key: `${page.slug}:${page.version}`, slug: page.slug, title: page.title,
    author: page.updated_by, version: page.version, reason: "",
    at: page.updated_at, added: null, removed: null, run_id: null,
  };
}

function streamed(event: WikiEvent): WikiChange {
  return {
    key: `${event.page.slug}:${event.version}`, slug: event.page.slug,
    title: event.page.title, author: event.author, version: event.version,
    reason: event.reason, at: event.page.updated_at, added: event.added,
    removed: event.removed, run_id: event.run_id,
  };
}

/** The wiki as a whole: what exists, what is wanted, what is going stale, and
 * what is happening to it right now. Held by both wiki pages — the page view
 * needs the slug set for its chips, and gets a live row for itself out of the
 * same subscription. */
export function useWiki(): WikiIndex {
  const [pages, setPages] = useState<WikiPage[]>([]);
  const [wanted, setWanted] = useState<WikiWantedRow[]>([]);
  const [stats, setStats] = useState<WikiStats | null>(null);
  const [recent, setRecent] = useState<WikiChange[]>([]);
  const [me, setMe] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The stream opens once the first listing has landed, so a frame can never
  // arrive before the index it belongs to.
  const [seededOnce, setSeededOnce] = useState(false);
  const live = useRef(true);

  const absorb = useCallback((rows: WikiPage[]) => {
    setPages((prev) => upsertPages(prev, rows));
    setRecent((prev) => upsertChanges(prev, rows.map(seeded)));
  }, []);

  const reload = useCallback(() => api<WikiPage[]>(`/api/wiki/pages?limit=${LIST_LIMIT}`)
    .then((rows) => { if (live.current) absorb(rows); }), [absorb]);

  // The wanted list and the counts are aggregates the API computes over the
  // whole wiki — a client cannot add them up from a capped listing.
  const reloadIndex = useCallback(() => {
    api<WikiWantedRow[]>("/api/wiki/wanted")
      .then((rows) => { if (live.current) setWanted(rows); }).catch(() => {});
    api<WikiStats>("/api/wiki/stats")
      .then((s) => { if (live.current) setStats(s); }).catch(() => {});
  }, []);

  useEffect(() => {
    live.current = true;
    let retry: ReturnType<typeof setTimeout> | null = null;
    let backoff = FIRST_RETRY_MS;

    // The stream only opens once this has succeeded, so giving up on the first
    // failure would leave a page that is not just empty but permanently empty.
    function attempt() {
      reload()
        .then(() => { if (live.current) { setError(null); setSeededOnce(true); } })
        .catch((err) => {
          if (!live.current) return;
          setError(err instanceof Error ? err.message : "Could not read the wiki.");
          retry = setTimeout(attempt, backoff);
          backoff = Math.min(backoff * 2, MAX_RETRY_MS);
        })
        .finally(() => { if (live.current) setLoaded(true); });
    }

    attempt();
    reloadIndex();
    api<{ principal: string }>("/api/whoami")
      .then((w) => { if (live.current) setMe(`user:${w.principal}`); }).catch(() => {});
    return () => { live.current = false; if (retry) clearTimeout(retry); };
  }, [reload, reloadIndex]);

  useEffect(() => {
    if (!seededOnce) return;
    let stream: EventSource | null = null;
    let poll: ReturnType<typeof setInterval> | null = null;
    let retry: ReturnType<typeof setTimeout> | null = null;
    let backoff = FIRST_RETRY_MS;
    let stopped = false;

    const stopPolling = () => { if (poll) { clearInterval(poll); poll = null; } };
    const catchUp = () => reload().catch(() => {});

    function connect() {
      if (stopped) return;
      const es = new EventSource("/api/wiki/events", { withCredentials: true });
      stream = es;
      es.addEventListener("open", () => { backoff = FIRST_RETRY_MS; stopPolling(); });
      es.addEventListener("page", (e) => {
        const frame = JSON.parse((e as MessageEvent).data) as WikiEvent;
        setPages((prev) => upsertPages(prev, [frame.page]));
        setRecent((prev) => upsertChanges(prev, [streamed(frame)]));
        // An edit is also a change to the wanted list (a red link just turned
        // blue) and to the counts above it.
        reloadIndex();
      });
      // The stream dropped frames and says so; re-listing is the only honest
      // answer, since there is no cursor to resume from.
      es.addEventListener("overflow", () => { catchUp(); reloadIndex(); });
      es.onerror = () => {
        if (stopped || stream !== es) return;
        es.close();
        stream = null;
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
    return () => {
      stopped = true;
      stream?.close();
      stream = null;
      stopPolling();
      if (retry) clearTimeout(retry);
    };
  }, [seededOnce, reload, reloadIndex]);

  const slugs = useMemo(
    () => (loaded && !error ? new Set(pages.map((p) => p.slug)) : null),
    [pages, loaded, error]);
  const tags = useMemo(
    () => [...new Set(pages.flatMap((p) => p.tags))].sort(), [pages]);
  // Read off the listing, not asked for: see `staleBefore` in lib/wiki.
  const stale = useMemo(() => pages.filter((p) => isStale(p)), [pages]);

  return { pages, slugs, wanted, stats, recent, tags, stale, me, loaded, error, reload };
}

export type WikiPageState = {
  detail: WikiPageDetail | null;
  /** The page is not there — which is not an error here: a wanted page is an
   * invitation, and the editor opens on it. */
  missing: boolean;
  loaded: boolean;
  error: string | null;
  history: WikiHistoryRow[];
  /** The diff of one version, fetched when the reader asks for it. */
  diff: WikiDiff | null;
  diffFor: number | null;
  /** Why the diff above is not there. A read that fails silently leaves a
   * panel that says "Loading…" for as long as the page is open, which is the
   * one answer that is never true. */
  diffError: string | null;
  loadHistory: () => void;
  showDiff: (version: number | null) => void;
  reload: () => Promise<WikiPageDetail | null>;
  /** What a write answered with: the page the server stored. */
  absorb: (page: WikiPage) => void;
};

/** One page, its backlinks and its citations — plus its history once somebody
 * opens the drawer. The history is a second read on purpose: it is a list of
 * every version this page has ever had, and most readers never ask for it. */
export function useWikiPage(slug: string): WikiPageState {
  const [detail, setDetail] = useState<WikiPageDetail | null>(null);
  const [missing, setMissing] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<WikiHistoryRow[]>([]);
  const [diff, setDiff] = useState<WikiDiff | null>(null);
  const [diffFor, setDiffFor] = useState<number | null>(null);
  const [diffError, setDiffError] = useState<string | null>(null);
  const live = useRef(true);
  // Reads overlap — the first is still in flight when a frame asks for another
  // — and can answer in either order. Only the newest may write, or a slow
  // first read lands on top of the reload that was meant to correct it.
  const issued = useRef(0);

  const reload = useCallback(() => {
    const mine = ++issued.current;
    return api<WikiPageDetail>(`/api/wiki/pages/${encodeURIComponent(slug)}`)
      .then((d) => {
        if (!live.current || mine !== issued.current) return null;
        // A read that STARTED before a write can answer after it, and would
        // walk the page back a version on screen. What it still knows better
        // than we do is what points here — the write answered with the page
        // alone — so the newer page keeps its place and the links come from
        // the read.
        setDetail((prev) => (prev && prev.page.slug === d.page.slug
          && prev.page.version > d.page.version ? { ...d, page: prev.page } : d));
        setMissing(false);
        setError(null);
        return d;
      })
      .catch((err: unknown) => {
        if (!live.current || mine !== issued.current) return null;
        const message = err instanceof Error ? err.message : "Could not read this page.";
        // A 404 is an answer, not a failure: this slug is a wanted page.
        if (message.startsWith("404")) { setMissing(true); setDetail(null); }
        else setError(message);
        return null;
      });
  }, [slug]);

  useEffect(() => {
    live.current = true;
    setDetail(null); setMissing(false); setError(null); setLoaded(false);
    setHistory([]); setDiff(null); setDiffFor(null); setDiffError(null);
    reload().finally(() => { if (live.current) setLoaded(true); });
    return () => { live.current = false; };
  }, [reload]);

  const loadHistory = useCallback(() => {
    api<WikiHistoryRow[]>(`/api/wiki/pages/${encodeURIComponent(slug)}/history`)
      .then((rows) => { if (live.current) setHistory(rows); })
      .catch(() => { /* the drawer says so by staying empty */ });
  }, [slug]);

  const showDiff = useCallback((version: number | null) => {
    setDiffFor(version);
    setDiff(null);
    setDiffError(null);
    if (version === null) return;
    api<WikiDiff>(`/api/wiki/pages/${encodeURIComponent(slug)}/versions/${version}`)
      .then((d) => { if (live.current) setDiff(d); })
      .catch((err: unknown) => {
        if (!live.current) return;
        setDiffError(err instanceof Error ? err.message
          : "That version's diff could not be read.");
      });
  }, [slug]);

  const absorb = useCallback((page: WikiPage) => {
    setDetail((prev) => (prev ? { ...prev, page } : { page, backlinks: [], cited_in: { count: 0, count_capped: false, last: [] } }));
    setMissing(false);
  }, []);

  return { detail, missing, loaded, error, history, diff, diffFor, diffError,
           loadHistory, showDiff, reload, absorb };
}
