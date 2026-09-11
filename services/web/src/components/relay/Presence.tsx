import type { RelayPresence } from "../../api";
import { Face } from "./Face";
import { AGENT } from "../../lib/relay";

// "news is thinking…" is not decoration and not a guess: presence is derived
// from the agent's own active run in THIS channel (GET /api/relay/presence,
// then `presence` events off the stream). If the row is showing, something is
// actually running.

function sentence(names: string[]): string {
  if (names.length === 1) return `${names[0]} is thinking…`;
  if (names.length === 2) return `${names[0]} and ${names[1]} are thinking…`;
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]} are thinking…`;
}

export function Thinking({ who }: { who: RelayPresence[] }) {
  // aria-live even when empty: the region has to exist before the update lands
  // or the announcement is lost with the node that would have carried it.
  return (
    <div className="relay-thinking" aria-live="polite">
      {who.length > 0 && (
        <>
          {who.map((p) => (
            <Face key={p.agent} participant={AGENT + p.agent} face={p.face} size={20} thinking />
          ))}
          <span>{sentence(who.map((p) => p.agent))}</span>
          <span className="relay-dots" aria-hidden="true"><i /><i /><i /></span>
        </>
      )}
    </div>
  );
}
