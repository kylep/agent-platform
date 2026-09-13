import { useState } from "react";
import { Button } from "@ap/ui/button";
import { FormDialog } from "@ap/ui/dialog";
import { Input } from "@ap/ui/field";
import { api, type RelayChannel, type RelayChannelDetail } from "../../api";
import { channelLabel, otherParticipant } from "../../lib/relay";
import { Face } from "./Face";

// The rail: the rooms, split the way people think about them — shared
// channels, and the people you talk to one-to-one. A DM is listed as WHO it
// is with, never as its row id.

function Row({ channel, me, selected, onSelect }: {
  channel: RelayChannel;
  me: string | null;
  selected: boolean;
  onSelect: (id: string) => void;
}) {
  // A group is drawn by its title when the API gave it one, and otherwise by
  // who is in it — the only other thing that tells one group from another.
  const group = channel.kind === "group";
  const other = channel.kind === "dm" ? otherParticipant(channel, me) : null;
  // The rail is narrow by design and the name is clipped to fit it, so the
  // full one rides along as the row's tooltip — for a group that member list
  // IS the name, and "3 members: news, pai, y…" names nothing. It sits on the
  // row rather than on the clipped span so that pointing anywhere along the
  // row, faces included, answers "who is in this one".
  const name = channel.kind === "channel" ? channel.name : channelLabel(channel, me);
  return (
    <button type="button" className={`relay-row${selected ? " active" : ""}`}
            aria-current={selected ? "true" : undefined}
            title={name ?? undefined}
            onClick={() => onSelect(channel.id)}>
      {group
        ? (
          <span className="relay-faces">
            {channel.participants.map((p) => <Face key={p} participant={p} size={18} />)}
          </span>
        )
        : other
          ? <Face participant={other} size={22} />
          : <span className="relay-hash" aria-hidden="true">#</span>}
      <span className="relay-row-name">{name}</span>
      {channel.unread > 0 && (
        // A dot, not a number: the count is the API's business, and a rail
        // full of digits reads as a to-do list.
        <span className="relay-unread" title={`${channel.unread} new`}>
          <span className="sr-only">{channel.unread} unread</span>
        </span>
      )}
    </button>
  );
}

export default function Rail({ channels, selected, me, loaded, onSelect, onCreated, open, onToggle }: {
  channels: RelayChannel[];
  selected: string | null;
  me: string | null;
  loaded: boolean;
  onSelect: (id: string) => void;
  onCreated: (channel: RelayChannelDetail) => void;
  // Narrow screens only: the rail is a drawer there, and always open above it.
  open: boolean;
  onToggle: () => void;
}) {
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [topic, setTopic] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const rooms = channels.filter((c) => c.kind === "channel");
  const dms = channels.filter((c) => c.kind !== "channel");

  async function create() {
    setBusy(true); setError(null);
    try {
      const made = await api<RelayChannelDetail>("/api/relay/channels", {
        method: "POST",
        body: JSON.stringify({ kind: "channel", name: name.trim(), topic: topic.trim() }),
      });
      setCreating(false); setName(""); setTopic("");
      onCreated(made);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create that channel.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <Button variant="secondary" className="relay-drawer-toggle" aria-expanded={open}
              onClick={onToggle}>
        {open ? "▾ Rooms" : "▸ Rooms"}
      </Button>
      <aside className="relay-rail" data-drawer={open ? "open" : "closed"} aria-label="Channels">
        <Button variant="secondary" className="justify-start border-dashed text-accent"
                onClick={() => setCreating(true)}>+ New channel</Button>

        <h2 className="relay-rail-head">Channels</h2>
        {rooms.map((c) => (
          <Row key={c.id} channel={c} me={me} selected={c.id === selected} onSelect={onSelect} />
        ))}
        {loaded && rooms.length === 0 && <p className="relay-rail-empty muted">No channels yet.</p>}

        <h2 className="relay-rail-head">Direct messages</h2>
        {dms.map((c) => (
          <Row key={c.id} channel={c} me={me} selected={c.id === selected} onSelect={onSelect} />
        ))}
        {loaded && dms.length === 0 && (
          <p className="relay-rail-empty muted">
            None yet — open an agent and use its Conversations tab.
          </p>
        )}
      </aside>

      <FormDialog
        open={creating}
        title="New channel"
        submitLabel={busy ? "Creating…" : "Create channel"}
        disabled={busy || !name.trim()}
        onSubmit={create}
        onCancel={() => { setCreating(false); setError(null); }}
      >
        <label className="field-label" htmlFor="relay-new-name">Name</label>
        <Input id="relay-new-name" value={name} autoFocus placeholder="deploys"
               onChange={(e) => setName(e.target.value)} />
        <label className="field-label" htmlFor="relay-new-topic">Topic</label>
        <Input id="relay-new-topic" value={topic} placeholder="what this room is for"
               onChange={(e) => setTopic(e.target.value)} />
        <p className="muted">
          Open to everyone: every human and every enabled agent is already a member.
        </p>
        {error && <div className="error">{error}</div>}
      </FormDialog>
    </>
  );
}
