import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Chip } from "@ap/ui/chip";
import { AppDataError, decideAppProposal, getAppProposal,
         type AppProposalReview } from "../lib/appData";

const when = (at: string | null) => at ? new Date(at).toLocaleString() : "never";

function Lines({ title, lines }: { title: string; lines: string[] }) {
  return <section className="state-app-section">
    <h2>{title}</h2>
    {lines.length ? <ul>{lines.map((line, i) => <li key={i}>{line}</li>)}</ul>
      : <p className="muted">None.</p>}
  </section>;
}

export default function ProposalReview() {
  const { id = "", proposalId = "" } = useParams();
  const [proposal, setProposal] = useState<AppProposalReview | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [reason, setReason] = useState("");

  useEffect(() => {
    let active = true;
    setProposal(null); setError("");
    getAppProposal(proposalId).then((p) => {
      if (!active) return;
      if (p.app_id !== id) { setError("This proposal belongs to a different App."); return; }
      setProposal(p);
    }).catch((e: unknown) => {
      if (active) setError(e instanceof Error ? e.message : "The proposal could not be loaded.");
    });
    return () => { active = false; };
  }, [id, proposalId]);

  async function decide(action: "approve" | "decline") {
    if (!proposal || busy) return;
    setBusy(true); setError("");
    try {
      await decideAppProposal(proposal.id, action, proposal.digest, reason);
      setProposal(await getAppProposal(proposal.id));
    } catch (e) {
      setError(e instanceof AppDataError ? e.message : "The decision failed. Review again.");
    } finally { setBusy(false); }
  }

  const stale = proposal !== null && (proposal.base_version !== proposal.current_approved_version
    || proposal.authority_generation !== proposal.current_authority_generation);
  const open = proposal?.state === "open";

  return <div className="page">
    <p><Link to={`/apps/state/${encodeURIComponent(id)}?tab=proposals`}>← App proposals</Link></p>
    <h1>Review App proposal</h1>
    {error && <p className="error" role="alert">{error}</p>}
    {!proposal && !error && <p className="muted">Loading proposal…</p>}
    {proposal && <>
      <p><Chip variant={open ? "warn" : "ok"}>{proposal.state}</Chip> · {proposal.kind} ·
        proposed by {proposal.proposer} {when(proposal.created_at)}</p>
      {proposal.reason && <p>{proposal.reason}</p>}
      <p>Based on App version {proposal.base_version ?? "unpublished"} and authority
        generation {proposal.authority_generation}.</p>
      {stale && <p className="error" role="status">The App changed since this proposal was
        made. Approval is unavailable; ask the builder for a fresh proposal.</p>}
      <Lines title="What gains access" lines={proposal.delta.widening} />
      <Lines title="Added authority" lines={proposal.delta.added} />
      <Lines title="Removed authority" lines={proposal.delta.removed} />
      <Lines title="Data affected" lines={proposal.validation.data_dropping} />
      <Lines title="Index changes" lines={proposal.validation.reindex} />
      <section className="state-app-section" aria-labelledby="definition-diff-h">
        <h2 id="definition-diff-h">Definition diff</h2>
        {proposal.diff.map((part, i) => <details className="definition" key={i}>
          <summary>{part.kind ?? part.field} {part.name}</summary>
          <div className="proposal-diff">
            <div><h3>Current</h3><pre className="json-view">{JSON.stringify(part.current, null, 2) ?? "None"}</pre></div>
            <div><h3>Proposed</h3><pre className="json-view">{JSON.stringify(part.proposed, null, 2) ?? "Removed"}</pre></div>
          </div>
        </details>)}
      </section>
      {open && <section className="state-app-section" aria-labelledby="decision-h">
        <h2 id="decision-h">Decision</h2>
        <p className="muted">Approval publishes the frozen change shown above. It cannot
          grant a platform Tool by itself.</p>
        <label htmlFor="decline-reason">Reason for declining (optional)</label>
        <textarea id="decline-reason" value={reason} maxLength={2000}
          onChange={(e) => setReason(e.target.value)} />
        <div className="proposal-actions">
          <button type="button" disabled={busy || stale} onClick={() => decide("approve")}>Approve</button>
          <button type="button" disabled={busy} onClick={() => decide("decline")}>Decline</button>
        </div>
      </section>}
      {proposal.outcome && <section className="state-app-section"><h2>Outcome</h2>
        <pre className="json-view">{JSON.stringify(proposal.outcome, null, 2)}</pre>
      </section>}
    </>}
  </div>;
}
