import { useState } from "react";
import { FormDialog } from "@ap/ui/dialog";
import { Input, Select, Textarea } from "@ap/ui/field";
import type { RelayPresence } from "../../api";
import { priorityLabel, type Ticket, type TicketProject } from "../../lib/tickets";

// Opening a ticket by hand. A project is required — a ticket without one has no
// key, no card and nowhere to be discussed — so the picker has no empty option;
// everything else has a sane default, because the fastest way to record work is
// a title and Enter.

const PRIORITIES = ["p0", "p1", "p2", "p3"];

// An agent that is switched off or quarantined cannot be summoned, so offering
// it is offering a hand-off that never happens — the same rule Relay's mention
// list follows.
const ASSIGNABLE = ["idle", "thinking"];

export function NewTicketDialog({ open, projects, presence, me, onClose, onCreate }: {
  open: boolean;
  projects: TicketProject[];
  presence: RelayPresence[];
  /** The signed-in human, who is as assignable as any agent. */
  me: string | null;
  onClose: () => void;
  onCreate: (body: {
    channel: string; title: string; body: string;
    priority: string; assignee: string | null; labels: string[];
  }) => Promise<Ticket>;
}) {
  const [channel, setChannel] = useState("");
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [priority, setPriority] = useState("p2");
  const [assignee, setAssignee] = useState("");
  const [labels, setLabels] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const project = channel || projects[0]?.id || "";

  function close() {
    setTitle(""); setBody(""); setLabels(""); setError(null);
    onClose();
  }

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      await onCreate({
        channel: project, title: title.trim(), body: body.trim(), priority,
        assignee: assignee || null,
        labels: labels.split(",").map((l) => l.trim()).filter(Boolean),
      });
      close();
    } catch (err) {
      setError(err instanceof Error ? err.message : "The ticket was not opened.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <FormDialog open={open} title="New ticket"
                submitLabel={busy ? "Opening…" : "Open ticket"}
                disabled={busy || !title.trim() || !project}
                onSubmit={submit} onCancel={close}>
      <label className="field-label" htmlFor="ticket-new-project">Project</label>
      <Select id="ticket-new-project" value={project}
              onChange={(e) => setChannel(e.target.value)}>
        {projects.map((p) => (
          <option key={p.id} value={p.id}>{p.prefix} · {p.title ?? p.name}</option>
        ))}
      </Select>
      <label className="field-label" htmlFor="ticket-new-title">Title</label>
      <Input id="ticket-new-title" value={title} autoFocus placeholder="what needs doing"
             onChange={(e) => setTitle(e.target.value)} />
      <label className="field-label" htmlFor="ticket-new-body">Detail</label>
      <Textarea id="ticket-new-body" rows={3} value={body}
                placeholder="what a stranger would need to know"
                onChange={(e) => setBody(e.target.value)} />
      <div className="ticket-new-row">
        <span>
          <label className="field-label" htmlFor="ticket-new-priority">Priority</label>
          <Select id="ticket-new-priority" value={priority}
                  onChange={(e) => setPriority(e.target.value)}>
            {PRIORITIES.map((p) => (
              <option key={p} value={p}>{p} · {priorityLabel(p)}</option>
            ))}
          </Select>
        </span>
        <span>
          {/* Assigning an agent also summons it (docs/design/20) — the ticket
              lands in the room as a mention, not as a silent field change. */}
          <label className="field-label" htmlFor="ticket-new-assignee">Assignee</label>
          <Select id="ticket-new-assignee" value={assignee}
                  onChange={(e) => setAssignee(e.target.value)}>
            <option value="">unassigned</option>
            {me && <option value={me}>you</option>}
            {presence.filter((p) => ASSIGNABLE.includes(p.state)).map((p) => (
              <option key={p.agent} value={`agent:${p.agent}`}>{p.agent}</option>
            ))}
          </Select>
        </span>
      </div>
      <label className="field-label" htmlFor="ticket-new-labels">Labels</label>
      <Input id="ticket-new-labels" value={labels} placeholder="weather, dedup"
             onChange={(e) => setLabels(e.target.value)} />
      {error && <div className="error">{error}</div>}
    </FormDialog>
  );
}
