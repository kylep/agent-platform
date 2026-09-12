import { useState, type DragEvent } from "react";
import { FormDialog } from "@ap/ui/dialog";
import { Input } from "@ap/ui/field";
import type { RelayPresence } from "../../api";
import { canMove, columnOf, stateLabel, type Ticket } from "../../lib/tickets";
import { TicketCard } from "./TicketCard";
import { isOrphaned, isThinking } from "./useTickets";

// Five columns for six states: done and cancelled are both "closed", because a
// board is read for what is left to do, and a finished ticket is only there to
// prove the week happened. The closed column holds the last seven days —
// anything older is history, and history is what the ticket page is for.

const CLOSED_DAYS = 7;
const COLUMNS = ["open", "in_progress", "blocked", "review", "closed"];
// A card dropped on the closed column is done work, not abandoned work:
// cancelling is a decision, and a decision needs a reason somebody typed.
const DROP_STATE: Record<string, string> = { closed: "done" };
// Blocked is the one state that is a claim about the world rather than about
// the work, so it asks what the block is. Optional — an answer nobody has yet
// is still worth recording as blocked.
const WANTS_REASON = "blocked";

function recentlyClosed(t: Ticket, now: number): boolean {
  const at = t.closed_at ?? t.updated_at;
  return !!at && now - new Date(at).getTime() < CLOSED_DAYS * 86400000;
}

export function Board({ tickets, presence, me, moving, filtered, onMove }: {
  tickets: Ticket[];
  presence: RelayPresence[];
  me: string | null;
  /** Ticket ids with a move in flight — held until the server answers. */
  moving: string[];
  /** Whether a filter is on — an empty board means two different things. */
  filtered: boolean;
  onMove: (ticket: Ticket, state: string, reason?: string) => void;
}) {
  // Which column the pointer is over, so a drag has somewhere to land visibly.
  const [over, setOver] = useState<string | null>(null);
  // The move that is waiting on a reason.
  const [asking, setAsking] = useState<{ ticket: Ticket; state: string } | null>(null);
  const [reason, setReason] = useState("");
  const now = Date.now();
  const shown = tickets.filter((t) => columnOf(t.state) !== "closed" || recentlyClosed(t, now));

  /** Every move on this page comes through here — the menu, the drop, and the
   * reason dialog's Open button all end up at the same call. */
  function request(ticket: Ticket, state: string) {
    if (!canMove(ticket.state, state) || moving.includes(ticket.id)) return;
    if (state === WANTS_REASON) { setReason(""); setAsking({ ticket, state }); return; }
    onMove(ticket, state);
  }

  function drop(e: DragEvent<HTMLElement>, column: string) {
    e.preventDefault();
    setOver(null);
    const id = e.dataTransfer.getData("text/ap-ticket");
    const ticket = tickets.find((t) => t.id === id);
    // A drop on a column the ticket cannot reach is a gesture that missed.
    // Nothing is sent and nothing is said: the card simply stays where it is.
    if (ticket) request(ticket, DROP_STATE[column] ?? column);
  }

  return (
    <div className="ticket-board-wrap" data-empty={shown.length === 0 ? "yes" : undefined}>
      {/* An empty board keeps its columns: five headings at zero say "nothing
          is in review either", which a bare sentence on a blank canvas does
          not. The message sits over them rather than instead of them. */}
      {shown.length === 0 && (
        <div className="ticket-empty">
          <div className="ticket-empty-card">
            <p className="ticket-empty-lead">
              {filtered ? "No tickets match these filters." : "Nothing on the board."}
            </p>
            <p className="muted">
              {filtered
                ? "Clear a filter to see the rest of the work."
                : "Open one — or let an agent open it for you."}
            </p>
          </div>
        </div>
      )}
      <div className="ticket-board">
        {COLUMNS.map((column) => {
          const rows = shown.filter((t) => columnOf(t.state) === column);
          return (
            <section key={column} className="ticket-col" aria-label={stateLabel(column)}
                     data-over={over === column ? "yes" : undefined}
                     onDragOver={(e) => { e.preventDefault(); setOver(column); }}
                     onDragLeave={() => setOver((c) => (c === column ? null : c))}
                     onDrop={(e) => drop(e, column)}>
              <h2 className="ticket-col-head">
                {stateLabel(column)}
                {column === "closed" && <span className="muted"> · {CLOSED_DAYS}d</span>}
                <span className="ticket-count">{rows.length}</span>
              </h2>
              <div className="ticket-col-body">
                {rows.map((t) => (
                  <TicketCard key={t.id} ticket={t} me={me}
                              thinking={isThinking(t, presence)}
                              orphaned={isOrphaned(t, presence)}
                              busy={moving.includes(t.id)}
                              onMove={(state) => request(t, state)}
                              onDragStart={(e) => {
                                e.dataTransfer.setData("text/ap-ticket", t.id);
                                e.dataTransfer.effectAllowed = "move";
                              }} />
                ))}
              </div>
            </section>
          );
        })}
      </div>

      <FormDialog open={asking !== null}
                  title={asking ? `Block ${asking.ticket.key}` : "Block"}
                  submitLabel="Block it" onCancel={() => setAsking(null)}
                  onSubmit={() => {
                    if (asking) onMove(asking.ticket, asking.state, reason.trim() || undefined);
                    setAsking(null);
                  }}>
        <label className="field-label" htmlFor="ticket-block-reason">
          What is it waiting on? (optional)
        </label>
        <Input id="ticket-block-reason" value={reason} autoFocus
               placeholder="waiting on the Discord token"
               onChange={(e) => setReason(e.target.value)}
               onKeyDown={(e) => {
                 if (e.key !== "Enter" || !asking) return;
                 e.preventDefault();
                 onMove(asking.ticket, asking.state, reason.trim() || undefined);
                 setAsking(null);
               }} />
      </FormDialog>
    </div>
  );
}
