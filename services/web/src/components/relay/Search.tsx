import { useEffect, useId, useRef, useState } from "react";
import { ChipButton } from "@ap/ui/chip";
import { Input } from "@ap/ui/field";
import { api, type RelayChannel, type RelayMessage } from "../../api";
import { ago } from "../../lib/time";
import { channelLabel, participantLabel } from "../../lib/relay";
import { Face } from "./Face";

// Finding the thing somebody said. The API does the matching (tsvector in
// postgres), so this is only about asking at a human typing speed and showing
// enough of each hit — who, where, when, the words around the match — to
// recognise it without opening it.

const LIMIT = 50;
// Long enough that a five-letter word is one request, short enough that the
// list feels attached to the keyboard.
const DEBOUNCE_MS = 200;
// Characters of body kept either side of the match.
const BEFORE = 50;
const AFTER = 90;

/** The words around the match, with the match itself marked. The API matches
 * stems, so a hit whose literal text is not in the body is normal — then the
 * head of the message is the best snippet there is. */
function snippet(body: string, q: string): [string, string, string] {
  const flat = body.replace(/\s+/g, " ").trim();
  const at = flat.toLowerCase().indexOf(q.trim().toLowerCase());
  if (at < 0) return [flat.slice(0, BEFORE + AFTER) + (flat.length > BEFORE + AFTER ? "…" : ""), "", ""];
  const from = Math.max(0, at - BEFORE);
  const to = Math.min(flat.length, at + q.trim().length + AFTER);
  return [
    (from ? "…" : "") + flat.slice(from, at),
    flat.slice(at, at + q.trim().length),
    flat.slice(at + q.trim().length, to) + (to < flat.length ? "…" : ""),
  ];
}

export default function Search({ channels, me, current, onPick }: {
  channels: RelayChannel[];
  me: string | null;
  /** The room being read, for the "this channel only" scope. */
  current: string | null;
  onPick: (message: RelayMessage) => void;
}) {
  const listId = useId();
  const [q, setQ] = useState("");
  const [here, setHere] = useState(false);
  const [hits, setHits] = useState<RelayMessage[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Which hit Enter would open. Every new answer starts at the top.
  const [cursor, setCursor] = useState(0);
  const box = useRef<HTMLInputElement | null>(null);

  const scope = here && current ? current : null;

  useEffect(() => {
    const term = q.trim();
    // An empty box is not a search with no results — it is no search, and the
    // dropdown goes away rather than saying "nothing found".
    if (!term) { setHits(null); setError(null); return; }
    let live = true;
    const id = setTimeout(() => {
      api<RelayMessage[]>(`/api/relay/search?q=${encodeURIComponent(term)}`
        + (scope ? `&channel=${encodeURIComponent(scope)}` : "")
        + `&limit=${LIMIT}`)
        .then((rows) => { if (live) { setHits(rows); setCursor(0); setError(null); } })
        .catch((e) => {
          if (live) setError(e instanceof Error ? e.message : "Search failed.");
        });
    }, DEBOUNCE_MS);
    return () => { live = false; clearTimeout(id); };
  }, [q, scope]);

  // `/` is the search key everywhere else on the web, and a page whose main
  // content is a text box has to check it is not stealing a real keystroke.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key !== "/" || e.metaKey || e.ctrlKey || e.altKey) return;
      const el = document.activeElement as HTMLElement | null;
      if (el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable)) return;
      e.preventDefault();
      box.current?.focus();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  function close() { setQ(""); setHits(null); setError(null); setCursor(0); }

  function open(message: RelayMessage) { onPick(message); close(); }

  /** The arrow keys belong to the list while it is open — Enter opens what is
   * selected rather than resubmitting the text, which is the one thing a
   * search box must not do to somebody halfway down a list of results. */
  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Escape") { e.preventDefault(); close(); return; }
    if (!hits?.length) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const step = e.key === "ArrowDown" ? 1 : hits.length - 1;
      setCursor((c) => (c + step) % hits.length);
      return;
    }
    if (e.key === "Enter") { e.preventDefault(); open(hits[cursor]); }
  }

  const label = (id: string) => {
    const channel = channels.find((c) => c.id === id);
    return channel ? channelLabel(channel, me) : "a channel";
  };

  return (
    <div className="relay-search">
      {/* A textbox, not a combobox: `aria-expanded` is not allowed on the role
          and axe gates on that. haspopup + controls + activedescendant say the
          same thing in attributes the role does accept. */}
      <Input ref={box} value={q} type="search" aria-label="Search messages"
             placeholder="Search messages…  ( / )"
             aria-autocomplete="list" aria-haspopup="listbox" aria-controls={listId}
             aria-activedescendant={hits?.length ? `${listId}-${cursor}` : undefined}
             onChange={(e) => setQ(e.target.value)} onKeyDown={onKeyDown} />
      {current && (
        <ChipButton variant={here ? "accent" : "neutral"} aria-pressed={here}
                    onClick={() => setHere((h) => !h)}>
          this channel only
        </ChipButton>
      )}
      {(hits || error) && (
        <div className="relay-search-results">
          {error && <div className="error">{error}</div>}
          {hits?.length === 0 && <p className="muted relay-search-empty">No messages match.</p>}
          <ul id={listId} role="listbox" aria-label="Search results">
            {hits?.map((m, i) => {
              const [pre, hit, post] = snippet(m.body, q);
              return (
                // mousedown, not click: a blur handler or a re-render under the
                // pointer would otherwise close the list out from under it.
                <li key={m.id} id={`${listId}-${i}`} role="option" aria-selected={i === cursor}
                    className={i === cursor ? "active" : undefined}
                    onMouseDown={(e) => { e.preventDefault(); open(m); }}>
                  <Face participant={m.author} face={m.face} size={18} />
                  <span className="relay-search-who">{participantLabel(m.author, me)}</span>
                  <span className="relay-search-where muted">{label(m.channel_id)}</span>
                  <span className="relay-time">{ago(m.created_at)}</span>
                  <span className="relay-search-snippet">
                    {pre}{hit && <mark>{hit}</mark>}{post}
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}
