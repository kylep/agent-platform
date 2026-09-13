import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Button } from "@ap/ui/button";
import { Input, Select } from "@ap/ui/field";
import { Board } from "../components/tickets/Board";
import { NewTicketDialog } from "../components/tickets/NewTicketDialog";
import { TodayStrip } from "../components/tickets/TodayStrip";
import { useTickets } from "../components/tickets/useTickets";
import { participantLabel } from "../lib/relay";
import type { Ticket } from "../lib/tickets";
import { useTitle } from "../lib/title";

// The board (docs/design/20): the platform's work, live. Which slice of it you
// are looking at lives in the URL — project, assignee, label, mine, a search —
// so a filtered board is a link somebody can send you.
//
// The whole board is loaded once and narrowed here rather than per filter on
// the server: five columns are one picture, and a picture that takes five
// requests to redraw flickers on every keystroke.

// How long the search box waits before it rewrites the URL. The filtering
// itself is immediate — this is only about not putting a history entry (and a
// re-render of the router) behind every letter.
const SEARCH_SETTLE_MS = 200;

function matches(t: Ticket, q: string): boolean {
  return `${t.title} ${t.body}`.toLowerCase().includes(q);
}

export default function Tickets() {
  useTitle("Tickets");
  const [params, setParams] = useSearchParams();
  const board = useTickets();
  const [creating, setCreating] = useState(false);
  const [term, setTerm] = useState(params.get("q") ?? "");
  const search = useRef<HTMLInputElement>(null);

  const project = params.get("project") ?? "";
  const assignee = params.get("assignee") ?? "";
  const label = params.get("label") ?? "";
  const mine = params.get("mine") === "1";

  function setFilter(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value); else next.delete(key);
    setParams(next);
  }

  // The typed term is the truth on screen; the URL catches up once the typing
  // stops, so the back button steps between searches rather than letters.
  useEffect(() => {
    // Nothing to write is nothing to do: navigating to the URL we are already
    // on would re-run this effect, and a page that navigates every 200ms is a
    // page nothing can be clicked on.
    if ((params.get("q") ?? "") === term) return;
    const id = setTimeout(() => {
      const next = new URLSearchParams(params);
      if (term) next.set("q", term); else next.delete("q");
      setParams(next, { replace: true });
    }, SEARCH_SETTLE_MS);
    return () => clearTimeout(id);
  }, [term, params, setParams]);

  // `/` is the search key everywhere else on the web, and a board is not a text
  // box — but a card's Move menu is a real control, so check first.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key !== "/" || e.metaKey || e.ctrlKey || e.altKey) return;
      const el = document.activeElement as HTMLElement | null;
      // A focused control gets its own keystrokes — a `/` typed into a card's
      // Move menu is that menu's business, not the search box's.
      if (el && (["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName) || el.isContentEditable)) return;
      e.preventDefault();
      search.current?.focus();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  // The pickers offer what is actually on the board. A label nobody has used
  // and an agent nobody has assigned would be filters that can only empty it.
  const assignees = useMemo(() => [...new Set(board.tickets
    .map((t) => t.assignee).filter((a): a is string => !!a))].sort(), [board.tickets]);
  const labels = useMemo(() => [...new Set(board.tickets.flatMap((t) => t.labels))].sort(),
                         [board.tickets]);

  const channel = board.projects.find((p) => p.prefix === project)?.id ?? "";
  const q = term.trim().toLowerCase();
  const visible = board.tickets.filter((t) =>
    (!channel || t.channel_id === channel)
    && (!assignee || t.assignee === assignee)
    && (!label || t.labels.includes(label))
    && (!mine || (!!board.me && t.assignee === board.me))
    && (!q || matches(t, q)));

  return (
    <div className="page page-tickets">
      <div className="ticket-head">
        <div>
          <h1>Tickets</h1>
          <p className="muted">
            What the platform is working on. Agents open tickets, pick them up and hand them
            over here the same way people do — every move is a line in the ticket's thread.
          </p>
        </div>
        <div className="ticket-head-actions">
          <Input ref={search} type="search" aria-label="Search tickets"
                 placeholder="Search tickets  /" value={term}
                 onChange={(e) => setTerm(e.target.value)} />
          <Button onClick={() => setCreating(true)}>New ticket</Button>
        </div>
      </div>

      {board.error && <div className="error">{board.error}</div>}

      <div className="ticket-filters">
        <label className="muted">Project{" "}
          <Select aria-label="Filter by project" value={project}
                  onChange={(e) => setFilter("project", e.target.value)}>
            <option value="">all</option>
            {board.projects.map((p) => (
              <option key={p.id} value={p.prefix}>{p.prefix} · {p.title ?? p.name}</option>
            ))}
          </Select>
        </label>
        <label className="muted">Assignee{" "}
          <Select aria-label="Filter by assignee" value={assignee}
                  onChange={(e) => setFilter("assignee", e.target.value)}>
            <option value="">anyone</option>
            {assignees.map((a) => (
              <option key={a} value={a}>{participantLabel(a, board.me)}</option>
            ))}
          </Select>
        </label>
        <label className="muted">Label{" "}
          <Select aria-label="Filter by label" value={label}
                  onChange={(e) => setFilter("label", e.target.value)}>
            <option value="">any</option>
            {labels.map((l) => <option key={l} value={l}>{l}</option>)}
          </Select>
        </label>
        <label className="muted ticket-mine">
          <input type="checkbox" checked={mine}
                 onChange={(e) => setFilter("mine", e.target.checked ? "1" : "")} />
          {" "}Only mine
        </label>
      </div>

      {board.stats && <TodayStrip moved={board.stats.moved_24h} />}

      {/* A board that could not be read is not an empty board: "nothing on the
          board" under an error banner is the page lying about the platform.
          The hook keeps retrying underneath this. */}
      {!board.loaded
        ? <p className="muted">Loading…</p>
        : board.error && board.tickets.length === 0
          ? (
            <p className="ticket-failed">
              The board could not be loaded. Still trying…
            </p>
          )
          : (
            <Board tickets={visible} presence={board.presence} me={board.me}
                   moving={board.moving}
                   filtered={!!(channel || assignee || label || mine || q)}
                   onMove={board.move} />
          )}

      <NewTicketDialog open={creating} projects={board.projects} presence={board.presence}
                       me={board.me} onClose={() => setCreating(false)}
                       onCreate={board.create} />
    </div>
  );
}
