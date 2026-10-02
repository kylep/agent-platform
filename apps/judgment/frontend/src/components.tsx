import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Banner } from "@ap/ui/banner";
import { Button } from "@ap/ui/button";
import { Chip } from "@ap/ui/chip";
import { ConfirmDialog, FormDialog } from "@ap/ui/dialog";
import { Textarea } from "@ap/ui/field";
import { ApiError, del, get, LIMITS, post, type BeliefStatus, type Confidence, type DeletePreview,
         type Deleted, type Feedback, type Outcome, type Provenance, type Timing } from "./api";

// Shared display pieces for the judgment page. Colours are design tokens
// only (the platform's no-raw-hex gate scans app frontends too).

// --- loading ------------------------------------------------------------------------

/** One fetch per view: `null` while loading, the error on failure, and a
 * `reload` for after a write. */
export function useLoad<T>(load: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let live = true;
    setError(null);
    load().then((d) => { if (live) setData(d); })
      .catch((e) => { if (live) { setData(null); setError(e); } });
    return () => { live = false; };
  }, [...deps, tick]);
  return { data, error, reload: () => setTick((t) => t + 1) };
}

export function errorText(e: unknown): string {
  if (e instanceof ApiError && e.status === 409) {
    return "This belief changed under you since the page loaded. Reload to see the newest version, then try again.";
  }
  if (e instanceof ApiError && e.status === 403) return "This page is private to Kyle.";
  return e instanceof Error ? e.message : "Something went wrong.";
}

export function isPrivate(e: unknown): boolean {
  return e instanceof ApiError && e.status === 403;
}

/** What the API says to anyone who is not Kyle: no data, and why. */
export function Private() {
  return (
    <Card>
      <h2>This page is private to Kyle</h2>
      <p className="muted jd-gap">
        Judgment holds Kai's beliefs and predictions about Kyle. Only Kyle's own admin login can
        open it; reader logins, app keys and admin API keys are refused.
      </p>
    </Card>
  );
}

export function Status({ error, loading }: { error: unknown; loading: boolean }) {
  if (error) return isPrivate(error) ? <Private /> : <Banner variant="danger">{errorText(error)}</Banner>;
  if (loading) return <p className="muted">Loading…</p>;
  return null;
}

// --- formatting -----------------------------------------------------------------------

export function fmtWhen(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

export function When({ iso }: { iso: string | null | undefined }) {
  return <time className="muted jd-when" dateTime={iso ?? undefined} title={iso ?? undefined}>{fmtWhen(iso)}</time>;
}

export function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

export const short = (id: string) => id.slice(0, 8);

function label(s: string): string {
  return s.replace(/_/g, " ");
}

// --- vocabulary chips -------------------------------------------------------------------

type Variant = "ok" | "warn" | "danger" | "neutral" | "accent";

const STATUS: Record<BeliefStatus, Variant> = { active: "ok", superseded: "neutral", rejected: "danger" };
// Only Kyle's own confirmation is green; `imported` is never confirmation,
// whatever the text claims, so it reads as a caution.
const PROVENANCE: Record<Provenance, Variant> = {
  kyle_confirmed: "ok", kyle_relayed: "accent", observed: "neutral", inference: "neutral", imported: "warn",
};
const OUTCOME: Record<Outcome, Variant> = {
  supported: "ok", contradicted: "danger", mixed: "warn", context_changed: "neutral", unresolved: "neutral",
};

export function StatusChip({ status }: { status: BeliefStatus }) {
  return <Chip variant={STATUS[status] ?? "neutral"}>{status}</Chip>;
}

export function ProvenanceChip({ provenance }: { provenance: Provenance }) {
  return <Chip variant={PROVENANCE[provenance] ?? "neutral"} title="provenance">{label(provenance)}</Chip>;
}

export function ConfidenceChip({ confidence }: { confidence: Confidence | null }) {
  if (!confidence) return null;
  return <Chip title="confidence">{confidence} confidence</Chip>;
}

export function OutcomeChip({ outcome }: { outcome: Outcome | null }) {
  if (!outcome) return <Chip variant="warn">pending</Chip>;
  return <Chip variant={OUTCOME[outcome] ?? "neutral"}>{label(outcome)}</Chip>;
}

export function TimingChip({ timing }: { timing: Timing }) {
  return <Chip variant={timing === "prospective" ? "accent" : "neutral"}>{timing}</Chip>;
}

export function Flags({ flags }: { flags: string[] }) {
  if (!flags.length) return null;
  return <span className="jd-chips">{flags.map((f) => <Chip key={f} variant="warn">⚑ {f}</Chip>)}</span>;
}

// --- source links -----------------------------------------------------------------------

const RELAY_REF = /^relay:([0-9a-f]{32})\/([0-9a-f]{32})$/;
const DISCORD_REF = /^discord:(\d+)\/(\d+)$/;

/** A `source_ref` Kyle can follow to check what it points at. A Relay ref
 * opens its room in the console with the message's thread (a full page load,
 * by design). A Discord ref is shown as its ids: a server-channel link needs
 * the server id, which the ref doesn't carry. */
export function SourceLink({ value }: { value: string | null }) {
  if (!value) return <span className="muted">no source</span>;
  const relay = RELAY_REF.exec(value);
  if (relay) {
    return (
      <a href={`/relay?channel=${relay[1]}&thread=${relay[2]}`} className="jd-ref" title={value}>
        Relay message {short(relay[2])}
      </a>
    );
  }
  const discord = DISCORD_REF.exec(value);
  if (discord) {
    return <code className="jd-ref" title={value}>Discord #{discord[1]} · message {discord[2]}</code>;
  }
  return <code className="jd-ref">{value}</code>;
}

// --- layout -------------------------------------------------------------------------------

export function Card({ title, aside, children, className }: {
  title?: React.ReactNode; aside?: React.ReactNode; children: React.ReactNode; className?: string;
}) {
  return (
    <section className={`jd-card ${className ?? ""}`}>
      {title && (
        <div className="jd-card-head">
          <h2>{title}</h2>
          {aside && <span className="muted">{aside}</span>}
        </div>
      )}
      {children}
    </section>
  );
}

/** A labelled value in a detail view; nothing when the value is empty. */
export function Field({ name, children }: { name: string; children: React.ReactNode }) {
  if (children == null || children === "") return null;
  return (
    <div className="jd-field">
      <div className="jd-label">{name}</div>
      <div className="jd-text">{children}</div>
    </div>
  );
}

// --- deletion -----------------------------------------------------------------------------

/** Design 38's deletion policy: the page says plainly what a delete can't reach. */
export function DeletionReach({ compact = false }: { compact?: boolean }) {
  if (compact) {
    return (
      <p className="jd-reach">
        Deletion can't reach Kai's run transcripts or the encrypted backups.
      </p>
    );
  }
  return (
    <p className="jd-reach">
      Deleting removes it from Judgment only. It can't reach <b>Kai's run transcripts</b> (its tool
      calls and their output stay until Kai's transcript retention expires) or the <b>encrypted
      backups</b> (the record survives in older backups until they rotate out, and restoring one
      brings it back).
    </p>
  );
}

export function deletedText(r: Deleted): string {
  const parts = Object.entries(r.deleted).filter(([, n]) => n > 0).map(([k, n]) => `${n} ${k}`);
  return parts.length ? `Deleted ${parts.join(", ")}.` : "Nothing left to delete.";
}

/** Delete with a confirm step; the dialog repeats what deletion can't reach. */
export function DeleteButton({ what, consequences, onDelete }: {
  what: string; consequences: React.ReactNode; onDelete: () => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <div className="jd-delete">
        <Button variant="danger" size="sm" onClick={() => setOpen(true)}>Delete {what}</Button>
        <DeletionReach compact />
      </div>
      <ConfirmDialog open={open} title={`Delete this ${what}?`} confirmLabel="Delete"
                     onCancel={() => setOpen(false)}
                     onConfirm={() => { setOpen(false); void onDelete(); }}>
        {consequences}
        <DeletionReach />
      </ConfirmDialog>
    </>
  );
}

// --- feedback -------------------------------------------------------------------------------

/** One feedback item: Kyle's words and Kai's reading kept visibly apart, the
 * way the table keeps them. Unconfirmed items carry confirm and edit. */
export function FeedbackItem({ fb, onChanged, showTargets = true }: {
  fb: Feedback; onChanged: (message?: string) => void; showTargets?: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [editing, setEditing] = useState(false);
  const [words, setWords] = useState(fb.kyle_words);
  const [preview, setPreview] = useState<DeletePreview | null>(null);
  const confirmed = fb.confirmed_at != null;

  async function run(fn: () => Promise<string | undefined>) {
    setBusy(true); setError(null);
    try { onChanged(await fn()); } catch (e) { setError(e); } finally { setBusy(false); }
  }

  const confirm = (kyle_words?: string) => run(async () => {
    await post<Feedback>(`/feedback/${fb.id}/confirm`, kyle_words === undefined ? {} : { kyle_words });
    return "Feedback confirmed.";
  });

  // The preview comes first: deleting feedback also deletes every belief
  // version that cites it, and Kyle sees that list before saying yes.
  async function askDelete() {
    setBusy(true); setError(null);
    try { setPreview(await get<DeletePreview>(`/feedback/${fb.id}/delete-preview`)); }
    catch (e) { setError(e); } finally { setBusy(false); }
  }

  return (
    <article className="jd-fb">
      <div className="jd-row">
        <OutcomeChip outcome={fb.outcome} />
        {confirmed
          ? <Chip variant="ok" title={fb.confirmed_at ?? undefined}>confirmed by Kyle</Chip>
          : <Chip variant="warn">relayed, unconfirmed</Chip>}
        <SourceLink value={fb.source_ref} />
        {fb.source_at && <span className="muted jd-small">said <When iso={fb.source_at} /></span>}
        <span className="muted jd-small">recorded <When iso={fb.created_at} /></span>
      </div>
      {showTargets && (fb.prediction_id || fb.belief_id) && (
        <div className="jd-row jd-small">
          {fb.prediction_id && <Link to={`/predictions/${fb.prediction_id}`}>prediction {short(fb.prediction_id)}</Link>}
          {fb.belief_id && (
            <Link to={`/beliefs/${fb.belief_id}`}>
              belief {short(fb.belief_id)}{fb.belief_version != null ? ` v${fb.belief_version}` : ""}
            </Link>
          )}
        </div>
      )}
      <div className="jd-split">
        <div className="jd-words">
          <div className="jd-label">Kyle's words</div>
          <blockquote className="jd-text">{fb.kyle_words}</blockquote>
        </div>
        <div className="jd-reading">
          <div className="jd-label">Kai's interpretation{confirmed ? " (not confirmed)" : ""}</div>
          <p className="jd-text">{fb.interpretation || <span className="muted">none recorded</span>}</p>
        </div>
      </div>
      {error != null && <Banner variant="danger">{errorText(error)}</Banner>}
      <div className="jd-actions">
        {!confirmed && (
          <>
            <Button size="sm" disabled={busy} onClick={() => confirm()}
                    title="Confirms Kyle's words and the outcome, not Kai's interpretation">Confirm</Button>
            <Button size="sm" variant="secondary" disabled={busy}
                    onClick={() => { setWords(fb.kyle_words); setEditing(true); }}>Edit words &amp; confirm</Button>
          </>
        )}
        <Button size="sm" variant="danger" disabled={busy} onClick={askDelete}>Delete feedback</Button>
      </div>
      {!confirmed && (
        <p className="muted jd-small">Confirming confirms Kyle's words and the outcome, never Kai's interpretation.</p>
      )}
      <DeletionReach compact />

      <FormDialog open={editing} title="Edit Kyle's words and confirm" submitLabel="Save and confirm"
                  disabled={!words.trim() || words.length > LIMITS.kyle_words}
                  onCancel={() => setEditing(false)}
                  onSubmit={() => { setEditing(false); void confirm(words.trim()); }}>
        <label className="jd-label" htmlFor={`words-${fb.id}`}>Kyle's words</label>
        <Textarea id={`words-${fb.id}`} rows={5} value={words} maxLength={LIMITS.kyle_words}
                  onChange={(e) => setWords(e.target.value)} />
      </FormDialog>

      <ConfirmDialog open={preview != null} title="Delete this feedback?" confirmLabel="Delete"
                     onCancel={() => setPreview(null)}
                     onConfirm={() => {
                       setPreview(null);
                       void run(async () => deletedText(await del<Deleted>(`/feedback/${fb.id}`)));
                     }}>
        {preview && <DeletePreviewList preview={preview} />}
        <DeletionReach />
      </ConfirmDialog>
    </article>
  );
}

function DeletePreviewList({ preview }: { preview: DeletePreview }) {
  if (!preview.versions.length && !preview.feedback.length && !preview.feedback_unlinked.length) {
    return <p>Only this feedback goes. No belief version cites it.</p>;
  }
  return (
    <>
      <p>This feedback goes, and so does every belief version that cites it, since a derived claim can repeat its words:</p>
      <ul className="jd-list">
        {preview.versions.map((v) => (
          <li key={`${v.belief_id}:${v.version}`}>
            belief {short(v.belief_id)} v{v.version}: <span className="jd-text">{v.claim}</span>
          </li>
        ))}
      </ul>
      {preview.beliefs_emptied.length > 0 && (
        <p>
          {plural(preview.beliefs_emptied.length, "belief")} would be left with no versions and will be deleted
          too: {preview.beliefs_emptied.map(short).join(", ")}.
        </p>
      )}
      {preview.feedback.length > 0 && (
        <p>
          {plural(preview.feedback.length, "other feedback item")} spoke only to what goes, so will be deleted
          too: {preview.feedback.map(short).join(", ")}.
        </p>
      )}
      {preview.feedback_unlinked.length > 0 && (
        <p>
          {plural(preview.feedback_unlinked.length, "feedback item")} will keep{" "}
          {preview.feedback_unlinked.length === 1 ? "its prediction but lose its" : "their predictions but lose their"}{" "}
          belief link: {preview.feedback_unlinked.map(short).join(", ")}.
        </p>
      )}
    </>
  );
}
