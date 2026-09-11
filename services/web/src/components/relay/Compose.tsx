import { useEffect, useId, useMemo, useRef, useState } from "react";
import { Button } from "@ap/ui/button";
import { Textarea } from "@ap/ui/field";
import { api, type RelayMessage } from "../../api";

// Writing in a room. Enter sends, because this is a chat and not a form; the
// `@` menu exists because a mention is the only thing that wakes an agent up,
// and an agent you cannot spell is an agent you cannot reach.

// Same opening rule the backend parses with (agentplatform.relay): an `@` at
// the start of the line or after whitespace or an opening bracket/quote — so
// `a@news.com` never opens a menu.
const MENTION = /(?:^|[\s([{"'])@([A-Za-z0-9-]*)$/;
const MAX_SUGGESTIONS = 8;

/** What to say when a send comes back 4xx. The API's 409s on a dm are both
 * "not now" — a turn already running, or a disabled agent — and neither is
 * worth showing an operator a raw status line over. */
function sendFailure(err: unknown): string {
  const text = err instanceof Error ? err.message : "";
  if (text.startsWith("409")) {
    return text.includes("disabled")
      ? "That agent is disabled — turn it back on before writing to it."
      : "a reply is already in progress — give the agent a moment.";
  }
  return err instanceof Error ? err.message : "Could not send that.";
}

export default function Compose({ channelId, archived, agents, mentionable,
                                 threadId, onPosted }: {
  channelId: string;
  archived: boolean;
  // Every enabled, non-quarantined agent on the platform.
  agents: string[];
  // The subset this ROOM can summon. A closed room, a group or a dm honours
  // only its members, so offering the rest would offer a summons that the
  // backend drops on the floor.
  mentionable: string[];
  threadId?: string | null;
  onPosted: (message: RelayMessage) => void;
}) {
  const listId = useId();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [menu, setMenu] = useState<{ start: number; end: number; query: string } | null>(null);
  const [cursor, setCursor] = useState(0);
  const [caret, setCaret] = useState<number | null>(null);
  const box = useRef<HTMLTextAreaElement | null>(null);

  // `@all` wakes the room rather than one agent, so it sits with the names it
  // stands in for.
  const suggestions = useMemo(() => {
    if (!menu) return [];
    const q = menu.query.toLowerCase();
    return ["all", ...mentionable]
      .filter((n) => n.toLowerCase().startsWith(q)).slice(0, MAX_SUGGESTIONS);
  }, [menu, mentionable]);

  // A real agent, typed correctly, that this room simply cannot reach. Saying
  // so is the difference between a typo and a mention that silently does
  // nothing.
  const outsider = useMemo(() => {
    if (!menu || !menu.query || suggestions.length) return null;
    const q = menu.query.toLowerCase();
    return agents.find((n) => n.toLowerCase().startsWith(q)) ?? null;
  }, [menu, suggestions, agents]);

  // Putting the caret back after an accepted mention has to wait for the value
  // to land, or the browser parks it at the end of the new text.
  useEffect(() => {
    if (caret === null) return;
    box.current?.setSelectionRange(caret, caret);
    setCaret(null);
  }, [caret]);

  function retarget(value: string, at: number) {
    const m = MENTION.exec(value.slice(0, at));
    setMenu(m ? { start: at - m[1].length - 1, end: at, query: m[1] } : null);
    setCursor(0);
  }

  function accept(name: string) {
    if (!menu) return;
    const next = `${text.slice(0, menu.start)}@${name} ${text.slice(menu.end)}`;
    setText(next);
    setMenu(null);
    setCaret(menu.start + name.length + 2);
  }

  async function send() {
    const body = text.trim();
    if (!body || busy) return;
    setBusy(true); setError(null);
    try {
      const posted = await api<RelayMessage>(
        `/api/relay/channels/${encodeURIComponent(channelId)}/messages`,
        { method: "POST", body: JSON.stringify({ body, reply_to: threadId ?? undefined }) });
      setText("");
      onPosted(posted);
    } catch (err) {
      // The text stays in the box on failure — retyping a paragraph because
      // the network blinked is the worst thing a compose box can do.
      setError(sendFailure(err));
    } finally {
      setBusy(false);
    }
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (menu && suggestions.length) {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        const step = e.key === "ArrowDown" ? 1 : suggestions.length - 1;
        setCursor((c) => (c + step) % suggestions.length);
        return;
      }
      if (e.key === "Enter" || e.key === "Tab") {
        e.preventDefault();
        accept(suggestions[cursor]);
        return;
      }
      if (e.key === "Escape") { e.preventDefault(); setMenu(null); return; }
    }
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
  }

  if (archived) {
    return (
      <div className="relay-compose archived muted">
        This channel is archived — its history stays readable, but nothing new can be posted.
      </div>
    );
  }

  return (
    <div className="relay-compose">
      {error && <div className="error">{error}</div>}
      <div className="relay-compose-row">
        <div className="relay-compose-field">
          <Textarea
            ref={box}
            value={text}
            rows={2}
            aria-label={threadId ? "Reply in thread" : "Message"}
            // A textbox, not a combobox: `aria-expanded` is not allowed on the
            // role and axe gates on that. haspopup + controls + activedescendant
            // say the same thing in attributes the role does accept.
            aria-autocomplete="list"
            aria-haspopup="listbox"
            aria-controls={listId}
            aria-activedescendant={menu && suggestions.length ? `${listId}-${cursor}` : undefined}
            placeholder="Message… (Enter to send, @ to summon an agent)"
            onChange={(e) => { setText(e.target.value); retarget(e.target.value, e.target.selectionStart); }}
            onKeyUp={(e) => retarget(e.currentTarget.value, e.currentTarget.selectionStart)}
            onBlur={() => setMenu(null)}
            onKeyDown={onKeyDown}
          />
          {outsider && (
            <p className="relay-mentions relay-mention-hint muted" role="status">
              @{outsider} is not in this room — a mention here won't reach it.
            </p>
          )}
          {menu && suggestions.length > 0 && (
            <ul className="relay-mentions" id={listId} role="listbox" aria-label="Agents to mention">
              {suggestions.map((name, i) => (
                <li key={name} id={`${listId}-${i}`} role="option" aria-selected={i === cursor}
                    className={i === cursor ? "active" : undefined}
                    // mousedown, not click: the textarea's blur would close the
                    // menu out from under a click.
                    onMouseDown={(e) => { e.preventDefault(); accept(name); }}>
                  @{name}
                  {name === "all" && <span className="muted"> — everyone in the room</span>}
                </li>
              ))}
            </ul>
          )}
        </div>
        <Button onClick={send} disabled={busy || !text.trim()}>{busy ? "Sending…" : "Send"}</Button>
      </div>
    </div>
  );
}
