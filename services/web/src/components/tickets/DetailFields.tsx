import { Link } from "react-router-dom";
import { Button } from "@ap/ui/button";
import { Chip, StatusChip } from "@ap/ui/chip";
import { Select } from "@ap/ui/field";
import { Face } from "../relay/Face";
import { participantLabel } from "../../lib/relay";
import {
  canMove, priorityLabel, stateLabel, TICKET_STATES,
  type Ticket, type TicketProject, type TicketRunRef,
} from "../../lib/tickets";

// Everything about a ticket that is a fact rather than a conversation, down
// the side of the page. Two of the rows are also the controls — the state
// carries the move, the assignee carries the hand-off — because a field you
// can read but not change is a field you end up editing somewhere else.

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{children}</dd>
    </>
  );
}

/** A participant with its face, the way a room draws one. */
function Who({ participant, face, me }: {
  participant: string;
  face: Ticket["assignee_face"];
  me: string | null;
}) {
  return (
    <span className="ticket-who">
      <Face participant={participant} face={face} size={20} />
      {participantLabel(participant, me)}
    </span>
  );
}

function TicketLink({ ticket }: { ticket: Ticket }) {
  return (
    <Link to={`/tickets/${ticket.key}`} className="ticket-link">
      <span className="ticket-key">{ticket.key}</span> {ticket.title}
    </Link>
  );
}

export function DetailFields({ ticket, me, project, parent, subtasks, runs, busy,
                              moveError, onMove, onAssign }: {
  ticket: Ticket;
  me: string | null;
  project: TicketProject | null;
  parent: Ticket | null;
  /** Tickets whose parent is this one. Named for what they are rather than
   * `children`, which in a React component means the JSX inside it. */
  subtasks: Ticket[];
  runs: TicketRunRef[];
  busy: boolean;
  /** A refused move, shown against the control that asked for it rather than
   * at the top of the page — the answer belongs where the question was. */
  moveError: string | null;
  onMove: (state: string) => void;
  onAssign: () => void;
}) {
  return (
    // The landmark for the side of the page. It sits on the fields rather than
    // on the column that holds them because that column stops being a box on a
    // phone, and the history below it is already a labelled region of its own.
    <aside className="ticket-fields-box" aria-label="Details">
      <dl className="def-list ticket-fields">
        <Row label="State">
          <span className="ticket-state-row">
            <StatusChip status={stateLabel(ticket.state)} className="ticket-state" />
            {/* The same move the board's card offers, and the same one an agent
                makes with the tool: one endpoint, three ways in. Only the
                transitions the API will actually accept are listed — a closed
                ticket offers "open", because reopening is the only way out of
                done. */}
            <Select className="ticket-move" value="" disabled={busy}
                    aria-label={`Move ${ticket.key} to…`}
                    onChange={(e) => { if (e.target.value) onMove(e.target.value); }}>
              <option value="">Move to…</option>
              {TICKET_STATES.filter((s) => canMove(ticket.state, s))
                .map((s) => <option key={s} value={s}>{stateLabel(s)}</option>)}
            </Select>
          </span>
          {moveError && <div className="error ticket-move-error">{moveError}</div>}
        </Row>
        <Row label="Priority">{ticket.priority} · {priorityLabel(ticket.priority)}</Row>
        <Row label="Assignee">
          <span className="ticket-state-row">
            {ticket.assignee
              ? <Who participant={ticket.assignee} face={ticket.assignee_face} me={me} />
              : <span className="muted">unassigned</span>}
            <Button variant="secondary" size="sm" disabled={busy} onClick={onAssign}>
              Assign
            </Button>
          </span>
        </Row>
        <Row label="Reporter">
          <Who participant={ticket.reporter} face={ticket.reporter_face} me={me} />
        </Row>
        <Row label="Labels">
          {ticket.labels.length
            ? <span className="ticket-label-row">
                {ticket.labels.map((l) => <Chip key={l}>{l}</Chip>)}
              </span>
            : <span className="muted">none</span>}
        </Row>
        <Row label="Due">
          {ticket.due_at
            ? new Date(ticket.due_at).toLocaleDateString()
            : <span className="muted">no date</span>}
        </Row>
        <Row label="Project">
          {/* The project IS a Relay room (docs/design/20), so the field goes
              where the conversation is rather than to a board filter. */}
          <Link to={`/relay?channel=${encodeURIComponent(ticket.channel_id)}`}>
            #{project?.name ?? project?.title ?? "channel"}
          </Link>
        </Row>
        {parent && <Row label="Parent"><TicketLink ticket={parent} /></Row>}
        {subtasks.length > 0 && (
          <Row label="Children">
            <ul className="ticket-list">
              {subtasks.map((c) => <li key={c.id}><TicketLink ticket={c} /></li>)}
            </ul>
          </Row>
        )}
        <Row label="Runs">
          {runs.length
            ? (
              <ul className="ticket-list">
                {runs.map((r) => (
                  <li key={r.id}>
                    <Link to={`/runs/${r.id}`}>{r.agent} · {r.state}</Link>
                  </li>
                ))}
              </ul>
            )
            : <span className="muted">none yet</span>}
        </Row>
      </dl>
    </aside>
  );
}
