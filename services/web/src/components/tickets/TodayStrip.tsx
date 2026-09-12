import { Face } from "../relay/Face";
import type { TicketActorCount } from "../../lib/tickets";

// The standup, written by nobody: everyone who moved a ticket in the last 24
// hours, and how much. The API groups it — the same rows an agent's own
// #standup answer is built from — so this is the board agreeing with the room
// without an LLM in the middle.

export function TodayStrip({ moved }: { moved: TicketActorCount[] }) {
  return (
    <section className="ticket-today" aria-label="Moved in the last 24 hours">
      <h2 className="ticket-today-head">Today</h2>
      {moved.length === 0
        ? <p className="muted">Nothing has moved yet today.</p>
        : (
          <ul className="ticket-today-list">
            {moved.map((row) => (
              <li key={row.actor}>
                <Face participant={row.actor} face={row.face} size={28} />
                <span className="ticket-today-who">{row.label}</span>
                <span className="ticket-today-count">{row.count}</span>
              </li>
            ))}
          </ul>
        )}
    </section>
  );
}
