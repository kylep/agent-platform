import type { DragEvent } from "react";
import { Link } from "react-router-dom";
import { Chip } from "@ap/ui/chip";
import { Select } from "@ap/ui/field";
import { Face } from "../relay/Face";
import { participantLabel } from "../../lib/relay";
import { canMove, priorityLabel, stateLabel, TICKET_STATES, type Ticket } from "../../lib/tickets";

// One piece of work, as a card. Everything on it is a fact the row already
// carries — key, title, who is on it, what it is about — except the two badges
// that are facts about the platform rather than about the ticket: `thinking` is
// a live run, `orphaned` is an assignee nobody employs any more.
//
// The face gets its own column rather than being a dot in a line of small
// print: a board of agents has to read as a board of people, and a 20px disc is
// a colour, not a face.

// Relay's size, so the same agent is the same size wherever it turns up.
const FACE = 28;

export function TicketCard({ ticket, me, thinking, orphaned, busy, onMove, onDragStart }: {
  ticket: Ticket;
  me: string | null;
  thinking: boolean;
  orphaned: boolean;
  /** A move on this card has not answered yet: it holds still until it has. */
  busy: boolean;
  onMove: (state: string) => void;
  onDragStart: (e: DragEvent<HTMLElement>) => void;
}) {
  const urgent = ticket.priority === "p0" || ticket.priority === "p1";
  return (
    <article className="ticket-card" data-priority={ticket.priority} data-key={ticket.key}
             draggable={!busy} data-busy={busy ? "yes" : undefined}
             onDragStart={onDragStart}>
      <span className="ticket-face" data-thinking={thinking ? "yes" : undefined}>
        {ticket.assignee
          ? <Face participant={ticket.assignee} face={ticket.assignee_face} size={FACE} />
          : <span className="ticket-face-none" style={{ width: FACE, height: FACE }}
                  aria-hidden="true" />}
      </span>
      <div className="ticket-card-head">
        <Link className="ticket-key" to={`/tickets/${ticket.key}`}>{ticket.key}</Link>
        {/* The stripe down the left edge is the priority; colour alone is not a
            statement, so the word rides along for anyone not reading in
            colour. */}
        <span className={urgent ? "ticket-priority" : "sr-only"}>
          {priorityLabel(ticket.priority)}
        </span>
        {ticket.stale && <Chip variant="warn">stale</Chip>}
        {orphaned && <Chip variant="danger">orphaned</Chip>}
      </div>
      <p className="ticket-title">{ticket.title}</p>
      <div className="ticket-card-foot">
        <span className={ticket.assignee ? "ticket-who" : "ticket-who muted"}>
          {ticket.assignee ? participantLabel(ticket.assignee, me) : "unassigned"}
        </span>
        {thinking && <span className="ticket-pulse">thinking…</span>}
        {ticket.labels.map((l) => <Chip key={l}>{l}</Chip>)}
      </div>
      {/* The keyboard half of drag-to-move, and the only half a screen reader
          or a phone has. Both ends call the same handler. */}
      <Select className="ticket-move" value="" disabled={busy}
              aria-label={`Move ${ticket.key} to…`}
              onChange={(e) => { if (e.target.value) onMove(e.target.value); }}>
        <option value="">{busy ? "Moving…" : "Move to…"}</option>
        {/* Only the transitions the API will accept: a closed ticket reopens
            and does nothing else, so offering it "review" would be offering a
            409. */}
        {TICKET_STATES.filter((s) => canMove(ticket.state, s))
          .map((s) => <option key={s} value={s}>{stateLabel(s)}</option>)}
      </Select>
    </article>
  );
}
