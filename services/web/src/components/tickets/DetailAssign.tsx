import { useEffect, useRef, useState } from "react";
import { FormDialog } from "@ap/ui/dialog";
import { Input, Select } from "@ap/ui/field";
import { participantLabel } from "../../lib/relay";
import type { Ticket } from "../../lib/tickets";

// Handing a ticket over. Assigning an agent is a summons (docs/design/20) —
// the assignment posts a real mention in the ticket's thread — so `notify` is
// on by default and the dialog says what it does. Turning it off is the quiet
// case: recording who owns something without waking anybody at 3am.

/** Who a ticket can be given to. Humans are not a roster the platform holds —
 * a participant may be anyone with a login — so the one human offered is the
 * admin, and anybody else is reachable through the agent's own hand-off. */
function assignCandidates(agents: string[]): string[] {
  return [...agents.map((a) => `agent:${a}`), "user:admin"];
}

export function DetailAssign({ open, ticket, me, agents, onClose, onAssign }: {
  open: boolean;
  ticket: Ticket;
  me: string | null;
  /** Enabled, non-quarantined agents — the ones a mention can actually wake. */
  agents: string[];
  onClose: () => void;
  onAssign: (body: { to: string | null; notify: boolean; reason?: string }) => Promise<void>;
}) {
  const [to, setTo] = useState(ticket.assignee ?? "");
  // Read by the effect below without subscribing it to the value: what the
  // dialog seeds from is whoever held the ticket at the moment it opened.
  const assignee = useRef(ticket.assignee);
  assignee.current = ticket.assignee;
  const [notify, setNotify] = useState(true);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Re-seeded when the dialog OPENS and not once more: the ticket may have been
  // handed over by somebody else while this page was sitting there, and the
  // live page will absorb that — but doing it mid-dialog would wipe a
  // half-typed reason the moment a stream frame landed.
  useEffect(() => {
    if (!open) return;
    setTo(assignee.current ?? "");
    setReason("");
    setError(null);
  }, [open]);

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      await onAssign({ to: to || null, notify, ...(reason.trim() ? { reason: reason.trim() } : {}) });
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "The ticket was not reassigned.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <FormDialog open={open} title={`Assign ${ticket.key}`}
                submitLabel={busy ? "Handing over…" : "Hand it over"}
                disabled={busy} onSubmit={submit} onCancel={onClose}>
      <label className="field-label" htmlFor="ticket-assign-to">Assign to</label>
      <Select id="ticket-assign-to" value={to} onChange={(e) => setTo(e.target.value)}>
        <option value="">unassigned</option>
        {assignCandidates(agents).map((p) => (
          <option key={p} value={p}>{participantLabel(p, me)}</option>
        ))}
      </Select>
      <label className="ticket-assign-notify">
        <input type="checkbox" checked={notify}
               onChange={(e) => setNotify(e.target.checked)} />
        {" "}Notify the assignee
      </label>
      <p className="muted">
        An agent assignee is mentioned in the ticket's thread and wakes up — the ask
        and the assignment are one act. Untick to record the owner quietly.
      </p>
      <label className="field-label" htmlFor="ticket-assign-reason">Reason</label>
      <Input id="ticket-assign-reason" value={reason} placeholder="why it is moving (optional)"
             onChange={(e) => setReason(e.target.value)} />
      {error && <div className="error">{error}</div>}
    </FormDialog>
  );
}
