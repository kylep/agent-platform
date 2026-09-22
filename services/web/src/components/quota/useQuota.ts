import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../../api";
import type { QuotaSnapshot } from "@ap/ui/quota";

// The sidebar's usage snapshot (docs/design/22), live — the same shape as
// useTickets' stream: one EventSource, a poll that only covers the gap while
// it is down, and a widening retry so a rolling pod restart ends connected.
//
// Two things are its own:
//
// - A STALE snapshot is refreshed once, on mount. The platform's cheapest
//   number is the one the proxy reports as a side effect of somebody else's
//   work; the probe is what it costs when nobody has done any. Asking once
//   per page load is the whole budget this hook is allowed — the API
//   coalesces and rate-limits the rest.
// - Nothing here ever throws or surfaces an error. A snapshot the console
//   could not read is not an incident the operator has to see; it is bars
//   that are not drawn.

const POLL_MS = 60000;
const CODEX_REFRESH_MS = 5 * 60000;
const FIRST_RETRY_MS = 2000;
const MAX_RETRY_MS = 30000;

/** What the bars are spread from. `{}` until the first read lands, which is
 * exactly what `QuotaBars` draws nothing for. */
export type QuotaState = QuotaSnapshot;

export function useQuota(): QuotaState {
  const [snapshot, setSnapshot] = useState<QuotaState>({});
  const live = useRef(true);
  // One probe per page load, tracked here rather than in state: it must not
  // re-arm when a stale frame arrives on the stream.
  const refreshed = useRef(false);
  const codexRefreshed = useRef(false);

  // What the server just decided: a stream frame, the refresh's own answer,
  // the first read. These REPLACE, with no comparison — the snapshot is a
  // singleton and the server owns it.
  const absorb = useCallback((next: QuotaSnapshot) => {
    if (!live.current) return;
    setSnapshot((prev) => next.provider === "codex"
      ? { ...prev, codex: next }
      : { ...next, codex: next.codex ?? prev.codex });
  }, []);

  // The catch-up read: after a dropped stream, on the poll that covers the
  // gap, and when the tab comes back. This one is ORDERED, because it is the
  // only path that races — a read can start before a refresh and land after
  // it, and without the guard the bars would walk back to the stale numbers
  // on their own. The guard is deliberately not on `absorb`: comparing
  // timestamps there would let one backwards step of the server's clock wedge
  // the bars for the life of the mount.
  const catchUp = useCallback(() => api<QuotaSnapshot>("/api/quota")
    .then((next) => {
      if (!live.current) return;
      setSnapshot((prev) => (
        (prev.observed_at ?? "") > (next.observed_at ?? "") ? prev : next));
    })
    .catch(() => {}), []);

  // Codex's usage endpoint is a read: unlike the Claude fallback probe it
  // spends no model tokens. Refresh it while the console is open so a weekly
  // window does not remain "fresh" (and frozen) until its reset several days
  // later. The API still coalesces callers across tabs.
  const refreshCodex = useCallback(() => api<QuotaSnapshot>(
    "/api/quota/refresh?provider=codex", { method: "POST" })
    .then(absorb).catch(() => {}), [absorb]);

  useEffect(() => {
    live.current = true;
    api<QuotaSnapshot>("/api/quota")
      .then((body) => {
        absorb(body);
        if (body.stale && !refreshed.current) {
          refreshed.current = true;
          codexRefreshed.current = true;
          // A stale Claude reading needs the model probe. Its default `all`
          // refresh also updates Codex, so do not immediately ask twice.
          return api<QuotaSnapshot>("/api/quota/refresh", { method: "POST" })
            .then(absorb).catch(() => {});
        }
        if (body.codex && !codexRefreshed.current) {
          codexRefreshed.current = true;
          return refreshCodex();
        }
      })
      .catch(() => {});
    return () => { live.current = false; };
  }, [absorb, refreshCodex]);

  useEffect(() => {
    let stream: EventSource | null = null;
    let poll: ReturnType<typeof setInterval> | null = null;
    let retry: ReturnType<typeof setTimeout> | null = null;
    let backoff = FIRST_RETRY_MS;
    let stopped = false;

    const stopPolling = () => { if (poll) { clearInterval(poll); poll = null; } };

    function connect() {
      if (stopped) return;
      const es = new EventSource("/api/quota/events", { withCredentials: true });
      stream = es;
      es.addEventListener("open", () => { backoff = FIRST_RETRY_MS; stopPolling(); });
      es.addEventListener("quota", (e) => {
        try {
          absorb(JSON.parse((e as MessageEvent).data) as QuotaSnapshot);
        } catch { /* a frame we cannot read is one we do not draw */ }
      });
      // There is one snapshot and no cursor, so a reader that fell behind
      // catches up with a single GET — always correct for a singleton.
      es.addEventListener("overflow", () => { catchUp(); });
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

    // A tab that was in the background missed every frame the stream sent
    // while it was throttled; one read on the way back is cheaper than
    // keeping it awake.
    const onVisible = () => {
      if (document.visibilityState !== "visible") return;
      catchUp();
      refreshCodex();
    };
    document.addEventListener("visibilitychange", onVisible);

    // GET polling covers dropped SSE frames; this refresh creates a new Codex
    // reading even when no other platform process has asked for one.
    const codexRefresh = setInterval(() => {
      if (document.visibilityState === "visible") refreshCodex();
    }, CODEX_REFRESH_MS);

    connect();
    return () => {
      stopped = true;
      document.removeEventListener("visibilitychange", onVisible);
      clearInterval(codexRefresh);
      stream?.close();
      stopPolling();
      if (retry) clearTimeout(retry);
    };
  }, [absorb, catchUp, refreshCodex]);

  return snapshot;
}
