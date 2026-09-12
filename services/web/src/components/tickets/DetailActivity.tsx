import { Link } from "react-router-dom";
import { Face } from "../relay/Face";
import { participantLabel } from "../../lib/relay";
import { ago } from "../../lib/time";
import { stateLabel, type TicketEvent } from "../../lib/tickets";

// The ticket's history, one line each. Everything here is also in the thread,
// in words — this is the same story with the prose taken out, so "when did it
// go to blocked, and who put it there" is one glance rather than a scroll.

/** What an event did, in the platform's own voice. The values are the wire's
 * (`in_progress`, `agent:news`); what a person reads never is. */
function sentence(event: TicketEvent, me: string | null): string {
  const to = event.to_value;
  switch (event.kind) {
    case "created": return "opened it";
    case "moved": return `moved it from ${stateLabel(event.from_value ?? "?")}`
      + ` to ${stateLabel(to ?? "?")}`;
    case "reopened": return `reopened it as ${stateLabel(to ?? "open")}`;
    case "assigned": return to ? `assigned it to ${participantLabel(to, me)}` : "unassigned it";
    case "edited": return "edited the fields";
    case "commented": return "commented";
    default: return event.kind;
  }
}

export function DetailActivity({ events, me }: { events: TicketEvent[]; me: string | null }) {
  return (
    <section className="ticket-activity" aria-label="Activity">
      <h2 className="ticket-side-head">Activity</h2>
      {events.length === 0
        ? <p className="muted">Nothing has happened to this ticket yet.</p>
        : (
          <ul className="ticket-activity-list">
            {events.map((e) => (
              <li key={e.id}>
                <Face participant={e.actor} face={e.actor_face} size={18} />
                <span>
                  <strong>{participantLabel(e.actor, me)}</strong> {sentence(e, me)}
                  {e.reason && <> — “{e.reason}”</>}
                  {/* Every line an agent wrote links to the run that wrote it. */}
                  {e.run_id && (
                    <> · <Link to={`/runs/${e.run_id}`}>view run ↗</Link></>
                  )}
                </span>
                <span className="muted ticket-activity-when">{ago(e.created_at)}</span>
              </li>
            ))}
          </ul>
        )}
    </section>
  );
}
