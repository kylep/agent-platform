import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Banner } from "@ap/ui/banner";
import { Button, buttonVariants } from "@ap/ui/button";
import { cn } from "@ap/ui/cn";
import { ConfirmDialog, FormDialog } from "@ap/ui/dialog";
import { Select } from "@ap/ui/field";
import { api, setAgentImage, type AgentSummary, type Artifact } from "../../api";
import { safeArtifactUrl } from "../../lib/artifacts";
import { Provenance } from "../artifacts/Provenance";
import { errorDetail } from "./generate";

// The right column (docs/design/23): the picture, or the wait for it, or
// the invitation to ask. Each of the three is drawn on purpose — an empty
// stage says what to do, the wait shows which model is painting and the
// seconds ticking over a shimmer, and a result is the hero with everything
// known about it underneath.

export function Stage({ artifact: a, pending, elapsed, modelLabel, me, missing, onIterate, onDelete, onWorn }: {
  artifact: Artifact | null;
  pending: boolean;
  elapsed: number;
  modelLabel: string | null;
  me: string | null;
  /** The id the URL named, when nobody has it. */
  missing: string | null;
  onIterate: (a: Artifact) => void;
  onDelete: (a: Artifact) => Promise<void>;
  /** An agent now wears the picture; the page says so. */
  onWorn: (agent: string) => void;
}) {
  const [wearing, setWearing] = useState(false);
  const [confirming, setConfirming] = useState(false);
  // One write at a time, whichever dialog started it; every refusal lands in
  // this section's Banner, so a dialog can close without taking the reason
  // with it.
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const content = a ? safeArtifactUrl(a.content_url) : null;

  async function attempt(fn: () => Promise<void>) {
    if (busy) return;
    setBusy(true); setError(null);
    try {
      await fn();
    } catch (err) {
      setError(errorDetail(err, "That did not work."));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!a) return;
    await attempt(() => onDelete(a));
    setConfirming(false);
  }

  async function wear(agent: string) {
    if (!a) return;
    await attempt(async () => { await setAgentImage(agent, a.id); onWorn(agent); });
    setWearing(false);
  }

  return (
    <section className="studio-stage" aria-label="Stage" aria-busy={pending || undefined}
             data-state={pending ? "pending" : a ? "result" : "empty"}>
      {pending ? (
        <div className="studio-wait">
          <div className="studio-shimmer" aria-hidden="true" />
          <p className="studio-wait-line" aria-live="polite">
            Painting with <strong>{modelLabel ?? "the model"}</strong> · {elapsed} s
          </p>
          <p className="muted">A picture can take a minute or two. The page waits with you.</p>
        </div>
      ) : a ? (
        <>
          <figure className="studio-result">
            {content
              ? <img src={content} alt={a.name} />
              : <div className="studio-empty-box"><span className="muted">This picture's bytes are off the artifacts routes and are not shown.</span></div>}
            <figcaption className="studio-result-name" title={a.name}>{a.name}</figcaption>
          </figure>
          <div className="studio-actions">
            <Button variant="secondary" size="sm" disabled={busy} onClick={() => setWearing(true)}>
              Use as agent image
            </Button>
            <Button variant="secondary" size="sm" disabled={busy} onClick={() => onIterate(a)}>
              Iterate
            </Button>
            <Button variant="secondary" size="sm" disabled title="next task">Mark up</Button>
            {content && (
              <a href={content} download={a.name}
                 className={cn(buttonVariants({ variant: "secondary", size: "sm" }),
                               "no-underline hover:no-underline")}>
                Download
              </a>
            )}
            <Button variant="danger" size="sm" disabled={busy} onClick={() => setConfirming(true)}>
              Delete
            </Button>
            <Link to={`/artifacts/${a.id}`} className="studio-open-link">open in Artifacts ↗</Link>
          </div>
          {error && <Banner variant="danger">{error}</Banner>}
          <Provenance artifact={a} me={me} />
        </>
      ) : (
        <div className="studio-empty-box">
          <span className="studio-empty-glyph" aria-hidden="true">🎨</span>
          <p className="studio-empty-title">
            {missing ? "There is no such artifact." : "Nothing on the stage yet."}
          </p>
          <p className="muted">
            {missing
              ? <>The picture <code>{missing.slice(0, 8)}…</code> may have been deleted. </>
              : null}
            Describe a picture on the left and press Generate — or pick one from the
            strip below to start from.
          </p>
        </div>
      )}

      {wearing && a && (
        <WearDialog artifact={a} applying={busy} onUse={wear}
                    onCancel={() => setWearing(false)} />
      )}
      {a && (
        <ConfirmDialog open={confirming} title="Delete this artifact?" confirmLabel="Delete"
                       onConfirm={remove} onCancel={() => { if (!busy) setConfirming(false); }}>
          <p><code>{a.name}</code> stops being served everywhere it is shown — every card
            that names it becomes "artifact not found". A face wearing it goes back to
            its emoji.</p>
        </ConfirmDialog>
      )}
    </section>
  );
}

// Which agent wears it. The listing is asked for when the dialog opens, not
// when the page does: most visits to the Studio never put a picture on
// anyone, and the list is the only thing this dialog needs that the page
// does not. The write is the stage's (the picker's shape): while it is out,
// Cancel, Escape and the overlay are ignored, and the stage closes the
// dialog when the write has an answer.
function WearDialog({ artifact: a, applying, onUse, onCancel }: {
  artifact: Artifact;
  applying: boolean;
  onUse: (agent: string) => void;
  onCancel: () => void;
}) {
  const [agents, setAgents] = useState<AgentSummary[] | null>(null);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<AgentSummary[]>("/api/agents")
      .then((rows) => { setAgents(rows); setName(rows[0]?.name ?? ""); })
      .catch((err) => { setAgents([]); setError(errorDetail(err, "Could not list the agents.")); });
  }, []);

  return (
    <FormDialog open title="Use as agent image"
                submitLabel={applying ? "Setting…" : "Use as profile image"}
                disabled={applying || !name || !agents}
                onSubmit={() => { if (name) onUse(name); }}
                onCancel={() => { if (!applying) onCancel(); }}>
      {error && <Banner variant="danger">{error}</Banner>}
      <p>
        <code>{a.name}</code> becomes the face the agent wears everywhere it is drawn.
        Its current picture, if any, stays in Artifacts.
      </p>
      <label className="field-label" htmlFor="studio-wear-agent">Agent</label>
      <Select id="studio-wear-agent" value={name} disabled={!agents || applying}
              onChange={(e) => setName(e.target.value)}>
        {(agents ?? []).map((r) => <option key={r.name} value={r.name}>{r.name}</option>)}
      </Select>
    </FormDialog>
  );
}
