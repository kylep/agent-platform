import { useEffect, useState } from "react";
import { api, type AgentVersion, type AgentVersionDetail } from "../api";
import { Banner } from "@ap/ui/banner";
import { Button } from "@ap/ui/button";
import { Chip } from "@ap/ui/chip";
import { ConfirmDialog } from "@ap/ui/dialog";
import { Table, TD, TH } from "@ap/ui/table";

// The append-only change log for one agent (docs/design/15). Every write —
// UI, API or tool — leaves a full snapshot here; this panel is what replaced
// reviewing agent edits as pull-request diffs.

const when = (ts: string | undefined) => (ts ? new Date(ts).toLocaleString() : "—");

// `changed_via` is a machine token (admin | tool:agents_edit | import |
// rollback); colour the ones that mean "not a human at the console".
function viaVariant(via: string) {
  if (via === "rollback") return "warn" as const;
  if (via.startsWith("tool:")) return "accent" as const;
  return "neutral" as const;
}

export default function AgentVersions({ agent, onRolledBack }: {
  agent: string; onRolledBack: () => void;
}) {
  const [versions, setVersions] = useState<AgentVersion[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const [snapshot, setSnapshot] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);

  function load() {
    api<AgentVersion[]>(`/api/agents/${encodeURIComponent(agent)}/versions`)
      .then((rows) => setVersions([...rows].sort((a, b) => b.version - a.version)))
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load history."));
  }
  useEffect(() => { setVersions(null); setOpen(null); load(); /* eslint-disable-next-line */ }, [agent]);

  async function view(n: number) {
    if (open === n) { setOpen(null); return; }
    setOpen(n);
    setSnapshot(null);
    try {
      const row = await api<AgentVersionDetail>(`/api/agents/${encodeURIComponent(agent)}/versions/${n}`);
      // The API may nest the definition under `snapshot` or return it bare.
      const body = (row.snapshot ?? row) as Record<string, unknown>;
      setSnapshot(JSON.stringify(body, null, 2));
    } catch (err) {
      setSnapshot(err instanceof Error ? err.message : "Failed to load snapshot.");
    }
  }

  async function rollback(n: number) {
    setBusy(true); setError(null); setDone(null);
    try {
      await api(`/api/agents/${encodeURIComponent(agent)}/rollback/${n}`, { method: "POST" });
      setDone(`Rolled back to version ${n} — applied as a new version.`);
      setConfirming(null);
      setOpen(null);
      load();
      onRolledBack();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Rollback failed.");
      setConfirming(null);
    } finally {
      setBusy(false);
    }
  }

  if (error && !versions) return <div className="error">{error}</div>;
  if (!versions) return <p className="muted">Loading…</p>;

  const current = versions.length ? versions[0].version : null;

  return (
    <>
      <h2>Change log</h2>
      <p className="muted">
        Every change to this agent's definition, newest first. Rolling back re-applies an old
        snapshot as a <em>new</em> version — nothing is ever rewritten.
      </p>
      {done && <Banner variant="ok">{done}</Banner>}
      {error && <div className="error">{error}</div>}
      <Table>
        <thead>
          <tr><TH>Version</TH><TH>When</TH><TH>Changed by</TH><TH>Via</TH><TH>Actions</TH></tr>
        </thead>
        <tbody>
          {versions.map((v) => (
            <tr key={v.version}>
              <TD>
                v{v.version}{" "}
                {v.version === current && <Chip variant="ok">current</Chip>}
              </TD>
              <TD className="text-muted whitespace-nowrap">{when(v.created_at)}</TD>
              <TD className="text-muted">{v.changed_by || "—"}</TD>
              <TD><Chip variant={viaVariant(v.changed_via)}>{v.changed_via || "admin"}</Chip></TD>
              <TD>
                <div className="row-actions">
                  <Button variant="secondary" size="sm" onClick={() => view(v.version)}>
                    {open === v.version ? "Hide" : "View"}
                  </Button>
                  {v.version !== current && (
                    <Button variant="secondary" size="sm" disabled={busy}
                            onClick={() => setConfirming(v.version)}>
                      Roll back
                    </Button>
                  )}
                </div>
              </TD>
            </tr>
          ))}
          {versions.length === 0 && (
            <tr><TD colSpan={5} className="text-muted">No changes recorded yet.</TD></tr>
          )}
        </tbody>
      </Table>

      {open !== null && (
        <>
          <h2>Snapshot v{open}</h2>
          <pre className="agent-md">{snapshot ?? "Loading…"}</pre>
        </>
      )}

      <ConfirmDialog
        open={confirming !== null}
        title={`Roll back to v${confirming ?? ""}?`}
        confirmLabel={busy ? "Rolling back…" : "Roll back"}
        onConfirm={() => confirming !== null && rollback(confirming)}
        onCancel={() => setConfirming(null)}
      >
        This replaces the agent's live definition — prompt, config and grants — with the v{confirming}
        snapshot, and records it as a new version. Runs already in flight are unaffected.
      </ConfirmDialog>
    </>
  );
}
