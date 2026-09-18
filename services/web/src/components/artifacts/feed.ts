import type { ArtifactEvent } from "../../api";

// The artifacts stream, shared (docs/design/23). One EventSource per tab,
// however many things are listening — the grid, and every `[[artifact:…]]`
// card in every open room — because a card that never hears about a delete
// keeps showing a picture that is gone, and a card per socket would be a
// socket per chip. Started by the first subscriber, closed by the last, with
// the same widening retry the board's own stream uses so a rolling pod
// restart ends with a live socket.
//
// While the socket is down the feed says so on a timer, and again when it
// comes back: a subscriber that keeps a list re-reads it on each of those,
// which is the poll the single stream replaces.

const POLL_MS = 5000;
const FIRST_RETRY_MS = 2000;
const MAX_RETRY_MS = 30000;

export type FeedFrame =
  | { type: "artifact"; event: ArtifactEvent }
  // Frames may have been missed — the stream said so, or it was down for a
  // while. A list re-reads itself; a card need do nothing.
  | { type: "gap" };

type Listener = (frame: FeedFrame) => void;

const listeners = new Set<Listener>();
let stream: EventSource | null = null;
let poll: ReturnType<typeof setInterval> | null = null;
let retry: ReturnType<typeof setTimeout> | null = null;
let backoff = FIRST_RETRY_MS;

function tell(frame: FeedFrame): void {
  for (const l of [...listeners]) l(frame);
}

function stopPolling(): void {
  if (poll) { clearInterval(poll); poll = null; }
}

function connect(): void {
  if (listeners.size === 0 || stream) return;
  const es = new EventSource("/api/artifacts/events", { withCredentials: true });
  stream = es;
  es.addEventListener("open", () => {
    // Back after a drop: whatever happened meanwhile was missed.
    const wasDown = poll !== null;
    backoff = FIRST_RETRY_MS;
    stopPolling();
    if (wasDown) tell({ type: "gap" });
  });
  es.addEventListener("artifact", (e) => {
    tell({ type: "artifact", event: JSON.parse((e as MessageEvent).data) as ArtifactEvent });
  });
  es.addEventListener("overflow", () => tell({ type: "gap" }));
  es.onerror = () => {
    if (stream !== es) return;
    es.close();
    stream = null;
    if (!poll) poll = setInterval(() => tell({ type: "gap" }), POLL_MS);
    tell({ type: "gap" });
    if (retry) clearTimeout(retry);
    retry = setTimeout(() => {
      retry = null;
      backoff = Math.min(backoff * 2, MAX_RETRY_MS);
      connect();
    }, backoff);
  };
}

function disconnect(): void {
  stream?.close();
  stream = null;
  stopPolling();
  if (retry) { clearTimeout(retry); retry = null; }
  backoff = FIRST_RETRY_MS;
}

/** Hear every frame until the returned function is called. The socket opens
 * with the first listener and closes with the last. */
export function subscribeArtifactFeed(listener: Listener): () => void {
  listeners.add(listener);
  connect();
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0) disconnect();
  };
}
