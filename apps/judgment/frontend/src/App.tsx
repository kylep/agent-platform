import { useEffect, useState } from "react";
import { BrowserRouter, Link, NavLink, Route, Routes, useLocation, useNavigate, useParams,
         useSearchParams } from "react-router-dom";
import { Banner } from "@ap/ui/banner";
import { Button } from "@ap/ui/button";
import { ChipButton } from "@ap/ui/chip";
import { FormDialog } from "@ap/ui/dialog";
import { Input, Select, Textarea } from "@ap/ui/field";
import { buildPlatformNav, SideNav, type AppNavInfo } from "@ap/ui/sidenav";
import { Stat, StatRow } from "@ap/ui/stat";
import { del, get, LIMITS, OUTCOMES, post, RESOLVING_OUTCOMES, type BeliefDetail, type BeliefRow,
         type Deleted, type Feedback, type Outcome, type PredictionDetail, type PredictionRow,
         type Review, type Version } from "./api";
import { Card, ConfidenceChip, DeleteButton, deletedText, errorText, FeedbackItem, Field, Flags,
         isPrivate, OutcomeChip, plural, Private, ProvenanceChip, short, SourceLink, Status,
         StatusChip, TimingChip, useLoad, When } from "./components";

// Judgment (docs/design/38): Kyle's private page over Kai's beliefs about him,
// the predictions Kai made from them, and the feedback that tests them.
//   /                 Beliefs; /beliefs/:id one belief with its full version trail
//   /predictions      pending and resolved; /predictions/:id one prediction
//   /feedback         relayed feedback waiting for Kyle's confirmation
//   /review           counts first (never a percentage), then resolved prospective items

// --- shell -----------------------------------------------------------------------------

function Shell({ children }: { children: React.ReactNode }) {
  // The shared platform sidebar (from @ap/ui), as every App draws it.
  const [apps, setApps] = useState<AppNavInfo[]>([{ name: "judgment", icon: "⚖️" }]);
  // The API refuses anyone but Kyle; asking once up front means a reader sees
  // why instead of four empty tabs.
  const [denied, setDenied] = useState(false);
  useEffect(() => {
    fetch("/api/apps", { credentials: "include" })
      .then((r) => (r.ok ? r.json() : []))
      .then((all: (AppNavInfo & { ui: boolean; ready: boolean | null })[]) =>
        setApps(all.filter((a) => a.ui && a.ready)))
      .catch(() => {});
    get<{ principal: string }>("/me").catch((e) => { if (isPrivate(e)) setDenied(true); });
  }, []);
  const { pathname } = useLocation();
  const tab = ({ isActive }: { isActive: boolean }) => `jd-tab${isActive ? " active" : ""}`;
  return (
    <div className="layout">
      <SideNav entries={buildPlatformNav(apps)} activePath="/apps/judgment/" />
      <main className="main">
        <div className="jd-shell">
          <header className="jd-top">
            <Link to="/" className="jd-brand">⚖️ Judgment</Link>
            {!denied && (
              <nav className="jd-tabs" aria-label="Judgment pages">
                <NavLink to="/" end className={({ isActive }) =>
                  tab({ isActive: isActive || pathname.startsWith("/beliefs/") })}>Beliefs</NavLink>
                <NavLink to="/predictions" className={tab}>Predictions</NavLink>
                <NavLink to="/feedback" className={tab}>Feedback</NavLink>
                <NavLink to="/review" className={tab}>Review</NavLink>
              </nav>
            )}
          </header>
          {denied ? <Private /> : children}
        </div>
      </main>
    </div>
  );
}

/** A row of filter chips bound to one search param. */
function Filter<T extends string>({ name, options, value, onChange }: {
  name: string; options: readonly T[]; value: T; onChange: (v: T) => void;
}) {
  return (
    <div className="jd-filters" role="group" aria-label={name}>
      {options.map((o) => (
        <ChipButton key={o} variant={o === value ? "accent" : "neutral"} aria-pressed={o === value}
                    onClick={() => onChange(o)}>{o}</ChipButton>
      ))}
    </div>
  );
}

function useParamChoice<T extends string>(key: string, options: readonly T[], fallback: T): [T, (v: T) => void] {
  const [params, setParams] = useSearchParams();
  const raw = params.get(key);
  const value = options.includes(raw as T) ? (raw as T) : fallback;
  return [value, (v: T) => {
    const next = new URLSearchParams(params);
    if (v === fallback) next.delete(key); else next.set(key, v);
    setParams(next, { replace: true });
  }];
}

/** A notice carried across a navigation (a delete lands back on the list). */
function useFlash(): [string | null, (v: string | null) => void] {
  const location = useLocation();
  const carried = (location.state as { notice?: string } | null)?.notice ?? null;
  const [notice, setNotice] = useState<string | null>(carried);
  useEffect(() => { if (carried) setNotice(carried); }, [carried]);
  return [notice, setNotice];
}

function Notice({ text, onClose }: { text: string | null; onClose: () => void }) {
  if (!text) return null;
  return (
    <Banner variant="ok" className="jd-notice">
      <span>{text}</span>
      <Button variant="link" onClick={onClose}>dismiss</Button>
    </Banner>
  );
}

// --- beliefs ----------------------------------------------------------------------------

const BELIEF_FILTERS = ["active", "superseded", "rejected", "all"] as const;

function BeliefsPage() {
  const [status, setStatus] = useParamChoice("status", BELIEF_FILTERS, "active");
  const { data, error } = useLoad(() => get<BeliefRow[]>(`/beliefs?status=${status}`), [status]);
  const [notice, setNotice] = useFlash();
  if (isPrivate(error)) return <Private />;
  return (
    <>
      <h1>Beliefs</h1>
      <Notice text={notice} onClose={() => setNotice(null)} />
      <Filter name="Belief status" options={BELIEF_FILTERS} value={status} onChange={setStatus} />
      <Status error={error} loading={!data} />
      {data && data.length === 0 && <Card><p className="muted">No {status === "all" ? "" : `${status} `}beliefs.</p></Card>}
      {data && data.length > 0 && (
        <ul className="jd-items">
          {data.map((b) => (
            <li key={b.id}>
              <Link to={`/beliefs/${b.id}`} className="jd-item">
                <span className="jd-item-title jd-text">
                  {b.current ? b.current.claim : <span className="muted">No current version</span>}
                </span>
                <span className="jd-row">
                  <StatusChip status={b.status} />
                  {b.current && <ProvenanceChip provenance={b.current.provenance} />}
                  {b.current && <ConfidenceChip confidence={b.current.confidence} />}
                  <span className="muted jd-small">v{b.current_version}</span>
                  {b.confirmed && b.confirmed.version !== b.current_version && (
                    <span className="muted jd-small">Kyle confirmed v{b.confirmed.version}</span>
                  )}
                  <span className="muted jd-small"><When iso={b.current?.created_at ?? b.created_at} /></span>
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

type BeliefAction = "correct" | "reject" | null;

function BeliefPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const { data, error, reload } = useLoad(() => get<BeliefDetail>(`/beliefs/${id}`), [id]);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<unknown>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [dialog, setDialog] = useState<BeliefAction>(null);
  const [claim, setClaim] = useState("");
  const [scope, setScope] = useState("");
  const [reason, setReason] = useState("");

  if (!data) return <><Crumb to="/" label="Beliefs" /><Status error={error} loading /></>;
  const { belief, versions, predictions, feedback } = data;
  const current = versions.find((v) => v.version === belief.current_version) ?? versions[versions.length - 1];
  const trail = [...versions].sort((a, b) => b.version - a.version);
  const expected_version = belief.current_version;

  async function act(fn: () => Promise<string>) {
    setBusy(true); setActionError(null); setNotice(null);
    try { setNotice(await fn()); reload(); } catch (e) { setActionError(e); } finally { setBusy(false); }
  }

  const openCorrect = () => {
    setClaim(current?.claim ?? ""); setScope(current?.scope ?? ""); setReason(""); setDialog("correct");
  };

  return (
    <>
      <Crumb to="/" label="Beliefs" />
      <h1 className="jd-text">{current?.claim ?? "(no versions)"}</h1>
      <div className="jd-row jd-gap-b">
        <StatusChip status={belief.status} />
        {current && <ProvenanceChip provenance={current.provenance} />}
        {current && <ConfidenceChip confidence={current.confidence} />}
        <span className="muted jd-small">v{belief.current_version} · created <When iso={belief.created_at} /></span>
      </div>

      <Notice text={notice} onClose={() => setNotice(null)} />
      {actionError != null && (
        <Banner variant="danger">
          {errorText(actionError)}{" "}
          <Button variant="link" onClick={() => { setActionError(null); reload(); }}>Reload</Button>
        </Banner>
      )}

      <Card title="Actions">
        <div className="jd-actions">
          <Button size="sm" disabled={busy || current?.provenance === "kyle_confirmed"}
                  title={current?.provenance === "kyle_confirmed" ? "The current version is already confirmed"
                    : "Writes a kyle_confirmed version of the current claim and scope"}
                  onClick={() => act(async () => {
                    await post(`/beliefs/${id}/confirm`, { expected_version });
                    return "Confirmed: a new kyle_confirmed version records the current claim.";
                  })}>Confirm</Button>
          <Button size="sm" variant="secondary" disabled={busy} onClick={openCorrect}>Correct in my words</Button>
          {belief.status !== "rejected" && (
            <Button size="sm" variant="secondary" disabled={busy}
                    onClick={() => { setReason(""); setDialog("reject"); }}>Reject</Button>
          )}
        </div>
        <DeleteButton what="belief" onDelete={async () => {
          setBusy(true); setActionError(null);
          try {
            const r = await del<Deleted>(`/beliefs/${id}`);
            navigate("/", { replace: true, state: { notice: deletedText(r) } });
          } catch (e) { setActionError(e); setBusy(false); }
        }} consequences={
          <p>
            This deletes the belief, all {plural(versions.length, "version")} and its prediction links.
            Predictions that relied on it keep their own text and show "linked belief deleted". Feedback
            that targeted only this belief is deleted; feedback that also targeted a prediction loses
            the link.
          </p>
        } />
      </Card>

      <Card title="Version trail" aside={`${plural(versions.length, "version")}, newest first`}>
        <ol className="jd-trail">
          {trail.map((v) => <VersionItem key={v.id} v={v} current={v.version === belief.current_version} />)}
        </ol>
      </Card>

      <Card title="Predictions that relied on it" aside={predictions.length || undefined}>
        {predictions.length === 0 ? <p className="muted">None.</p> : (
          <ul className="jd-items">
            {predictions.map((p) => (
              <li key={p.id}>
                <Link to={`/predictions/${p.id}`} className="jd-item">
                  <span className="jd-item-title jd-text">{p.scenario}</span>
                  <span className="jd-row">
                    <span className="jd-small jd-text">expected: {p.predicted_choice}</span>
                    <TimingChip timing={p.timing} />
                    <When iso={p.created_at} />
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title="Feedback on it" aside={feedback.length || undefined}>
        {feedback.length === 0 ? <p className="muted">None.</p> : feedback.map((f) => (
          <FeedbackItem key={f.id} fb={f} onChanged={(m) => { setNotice(m ?? null); reload(); }} />
        ))}
      </Card>

      <FormDialog open={dialog === "correct"} title="Correct this belief" submitLabel="Save as confirmed"
                  disabled={busy || !claim.trim() || claim.length > LIMITS.claim}
                  onCancel={() => setDialog(null)}
                  onSubmit={() => {
                    setDialog(null);
                    void act(async () => {
                      await post(`/beliefs/${id}/correct`, {
                        expected_version, claim: claim.trim(),
                        // Always sent: "" clears the scope; leaving it out would keep the old one.
                        scope: scope.trim(),
                        ...(reason.trim() ? { reason: reason.trim() } : {}),
                      });
                      return "Corrected: a new kyle_confirmed version in your words.";
                    });
                  }}>
        <div className="jd-form">
          <label className="jd-label" htmlFor="c-claim">Claim, in your words</label>
          <Textarea id="c-claim" rows={3} value={claim} maxLength={LIMITS.claim} onChange={(e) => setClaim(e.target.value)} />
          <label className="jd-label" htmlFor="c-scope">Scope (when it holds, exceptions)</label>
          <Textarea id="c-scope" rows={3} value={scope} maxLength={LIMITS.scope} onChange={(e) => setScope(e.target.value)} />
          <label className="jd-label" htmlFor="c-reason">Reason (optional)</label>
          <Input id="c-reason" value={reason} maxLength={LIMITS.reason} onChange={(e) => setReason(e.target.value)} />
        </div>
      </FormDialog>

      <FormDialog open={dialog === "reject"} title="Reject this belief" submitLabel="Reject"
                  disabled={busy || !reason.trim()}
                  onCancel={() => setDialog(null)}
                  onSubmit={() => {
                    setDialog(null);
                    void act(async () => {
                      await post(`/beliefs/${id}/reject`, { expected_version, reason: reason.trim() });
                      return "Rejected. A new version records your reason.";
                    });
                  }}>
        <label className="jd-label" htmlFor="r-reason">Why it's wrong</label>
        <Textarea id="r-reason" rows={3} value={reason} maxLength={LIMITS.reason} onChange={(e) => setReason(e.target.value)} />
      </FormDialog>
    </>
  );
}

function VersionItem({ v, current }: { v: Version; current: boolean }) {
  return (
    <li className={`jd-version${current ? " jd-version-current" : ""}`}>
      <div className="jd-row">
        <b>v{v.version}</b>
        {current && <span className="jd-small jd-accent">current</span>}
        <ProvenanceChip provenance={v.provenance} />
        <ConfidenceChip confidence={v.confidence} />
        <span className="muted jd-small">{v.author}</span>
        <When iso={v.created_at} />
      </div>
      <p className="jd-text jd-claim">{v.claim}</p>
      <Field name="Scope">{v.scope}</Field>
      <Field name="Evidence">{v.evidence}</Field>
      <Field name="Why this version">{v.reason}</Field>
      <div className="jd-row jd-small">
        <span className="muted">source</span> <SourceLink value={v.source_ref} />
        {v.feedback_id && <span className="muted">prompted by feedback {short(v.feedback_id)}</span>}
      </div>
    </li>
  );
}

function Crumb({ to, label }: { to: string; label: string }) {
  return <p className="jd-crumb"><Link to={to}>← {label}</Link></p>;
}

// --- predictions -------------------------------------------------------------------------

const PREDICTION_FILTERS = ["pending", "resolved", "all"] as const;

function PredictionsPage() {
  const [state, setState] = useParamChoice("state", PREDICTION_FILTERS, "pending");
  const { data, error } = useLoad(() => get<PredictionRow[]>(`/predictions?state=${state}`), [state]);
  const [notice, setNotice] = useFlash();
  if (isPrivate(error)) return <Private />;
  return (
    <>
      <h1>Predictions</h1>
      <Notice text={notice} onClose={() => setNotice(null)} />
      <Filter name="Prediction state" options={PREDICTION_FILTERS} value={state} onChange={setState} />
      <Status error={error} loading={!data} />
      {data && data.length === 0 && <Card><p className="muted">No {state === "all" ? "" : `${state} `}predictions.</p></Card>}
      {data && data.length > 0 && (
        <ul className="jd-items">
          {data.map((p) => (
            <li key={p.id}>
              <Link to={`/predictions/${p.id}`} className="jd-item">
                <span className="jd-item-title jd-text">{p.scenario}</span>
                <span className="jd-small jd-text">expected: <b>{p.predicted_choice}</b></span>
                <span className="jd-row">
                  <TimingChip timing={p.timing} />
                  <OutcomeChip outcome={p.resolution} />
                  <Flags flags={p.flags} />
                  <span className="muted jd-small">{plural(p.feedback_count, "feedback item")}</span>
                  <When iso={p.created_at} />
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

function PredictionPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const { data, error, reload } = useLoad(() => get<PredictionDetail>(`/predictions/${id}`), [id]);
  const [notice, setNotice] = useState<string | null>(null);
  const [actionError, setActionError] = useState<unknown>(null);

  if (!data) return <><Crumb to="/predictions" label="Predictions" /><Status error={error} loading /></>;
  const { prediction: p, links, feedback, resolution, flags } = data;

  async function remove() {
    setActionError(null);
    try {
      const r = await del<Deleted>(`/predictions/${id}`);
      navigate("/predictions", { replace: true, state: { notice: deletedText(r) } });
    } catch (e) { setActionError(e); }
  }

  return (
    <>
      <Crumb to="/predictions" label="Predictions" />
      <h1 className="jd-text">{p.scenario}</h1>
      <div className="jd-row jd-gap-b">
        <TimingChip timing={p.timing} />
        <OutcomeChip outcome={resolution} />
        <ConfidenceChip confidence={p.confidence} />
        <Flags flags={flags} />
        <span className="muted jd-small">{p.author} · <When iso={p.created_at} /></span>
      </div>
      <Notice text={notice} onClose={() => setNotice(null)} />
      {actionError != null && <Banner variant="danger">{errorText(actionError)}</Banner>}

      <Card title="What Kai expected">
        <Field name="Predicted choice"><b>{p.predicted_choice}</b></Field>
        {p.alternatives.length > 0 && (
          <Field name="Alternatives">
            <ul className="jd-list">{p.alternatives.map((a, i) => <li key={i}>{a}</li>)}</ul>
          </Field>
        )}
        <Field name="Rationale">{p.rationale}</Field>
        {p.question_ref && <Field name="Question Kai asked"><SourceLink value={p.question_ref} /></Field>}
        {p.timing === "prospective" && (
          <p className="muted jd-small">Prospective timing is Kai's attestation, not a proof; the flags mark patterns that undercut it.</p>
        )}
      </Card>

      <Card title="Beliefs it relied on" aside={links.length || undefined}>
        {links.length === 0 ? <p className="muted">None pinned.</p> : (
          <ul className="jd-items">
            {links.map((l) => (
              <li key={l.belief_id}>
                {l.claim == null
                  ? <span className="jd-item muted">linked belief deleted</span>
                  : (
                    <Link to={`/beliefs/${l.belief_id}`} className="jd-item">
                      <span className="jd-text">{l.claim}</span>
                      <span className="muted jd-small">pinned at v{l.belief_version}</span>
                    </Link>
                  )}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title="Feedback" aside={feedback.length || undefined}>
        {feedback.length === 0 && <p className="muted">None yet. No response is never feedback.</p>}
        {feedback.map((f) => (
          <FeedbackItem key={f.id} fb={f} showTargets={false}
                        onChanged={(m) => { setNotice(m ?? null); reload(); }} />
        ))}
        <AddFeedback predictionId={id} links={links}
                     onAdded={() => { setNotice("Feedback added, confirmed."); reload(); }} />
      </Card>

      <Card title="Delete">
        <DeleteButton what="prediction" onDelete={remove} consequences={
          <p>
            This deletes the prediction, its belief links, and feedback that targeted only it.
            Feedback that also targeted a belief version survives with the prediction link cleared.
          </p>
        } />
      </Card>
    </>
  );
}

function AddFeedback({ predictionId, links, onAdded }: {
  predictionId: string; links: PredictionDetail["links"]; onAdded: () => void;
}) {
  const [words, setWords] = useState("");
  const [outcome, setOutcome] = useState<Outcome>("supported");
  const [belief, setBelief] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const live = links.filter((l) => l.claim != null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null);
    const link = live.find((l) => l.belief_id === belief);
    try {
      await post<Feedback>(`/predictions/${predictionId}/feedback`, {
        kyle_words: words.trim(), outcome,
        ...(link ? { belief_id: link.belief_id, belief_version: link.belief_version } : {}),
      });
      setWords(""); setBelief("");
      onAdded();
    } catch (err) { setError(err); } finally { setBusy(false); }
  }

  return (
    <form className="jd-form jd-add" onSubmit={submit}>
      <h3 className="jd-h3">Add feedback</h3>
      <label className="jd-label" htmlFor="fb-words">What you said or did</label>
      <Textarea id="fb-words" rows={3} value={words} maxLength={LIMITS.kyle_words}
                onChange={(e) => setWords(e.target.value)} />
      <div className="jd-row">
        <label className="jd-label" htmlFor="fb-outcome">Outcome</label>
        <Select id="fb-outcome" value={outcome} onChange={(e) => setOutcome(e.target.value as Outcome)}>
          {OUTCOMES.map((o) => <option key={o} value={o}>{o.replace(/_/g, " ")}</option>)}
        </Select>
      </div>
      {live.length > 0 && (
        <div className="jd-row">
          <label className="jd-label" htmlFor="fb-belief">About belief</label>
          <Select id="fb-belief" className="jd-select" value={belief} onChange={(e) => setBelief(e.target.value)}>
            <option value="">(the prediction only)</option>
            {live.map((l) => (
              <option key={l.belief_id} value={l.belief_id}>
                v{l.belief_version}: {(l.claim ?? "").slice(0, 60)}
              </option>
            ))}
          </Select>
        </div>
      )}
      {error != null && <Banner variant="danger">{errorText(error)}</Banner>}
      <div>
        <Button type="submit" size="sm" disabled={busy || !words.trim()}>Add feedback</Button>
      </div>
    </form>
  );
}

// --- feedback --------------------------------------------------------------------------------

const FEEDBACK_FILTERS = ["unconfirmed", "confirmed", "all"] as const;
const FEEDBACK_QUERY = { unconfirmed: "false", confirmed: "true", all: "all" } as const;

function FeedbackPage() {
  const [which, setWhich] = useParamChoice("show", FEEDBACK_FILTERS, "unconfirmed");
  const { data, error, reload } = useLoad(
    () => get<Feedback[]>(`/feedback?confirmed=${FEEDBACK_QUERY[which]}`), [which]);
  const [notice, setNotice] = useState<string | null>(null);
  if (isPrivate(error)) return <Private />;
  return (
    <>
      <h1>Feedback</h1>
      <p className="muted jd-gap-b">
        What Kai relayed from Kyle, kept apart from what Kai made of it. Confirming confirms Kyle's
        words and the outcome; Kai's interpretation stays Kai's.
      </p>
      <Filter name="Feedback" options={FEEDBACK_FILTERS} value={which} onChange={setWhich} />
      <Notice text={notice} onClose={() => setNotice(null)} />
      <Status error={error} loading={!data} />
      {data && data.length === 0 && <Card><p className="muted">Nothing {which === "unconfirmed" ? "waiting for confirmation" : "here"}.</p></Card>}
      {data && data.map((f) => (
        <Card key={f.id}>
          <FeedbackItem fb={f} onChanged={(m) => { setNotice(m ?? null); reload(); }} />
        </Card>
      ))}
    </>
  );
}

// --- review ----------------------------------------------------------------------------------

function ReviewPage() {
  const { data, error } = useLoad(() => get<Review>("/review"), []);
  if (!data) return <><h1>Review</h1><Status error={error} loading /></>;
  const { counts, items } = data;
  const resolved = RESOLVING_OUTCOMES.reduce((n, o) => n + (counts.prospective_resolved[o] ?? 0), 0);
  return (
    <>
      <h1>Review</h1>
      <p className="muted jd-gap-b">Counts, not a score: there is no accuracy percentage here, on purpose.</p>
      <StatRow>
        <Stat label="Prospective resolved" value={resolved} sub={
          <>
            {RESOLVING_OUTCOMES.map((o) => (
              <span key={o}>{counts.prospective_resolved[o] ?? 0} {o.replace(/_/g, " ")}</span>
            ))}
          </>
        } />
        <Stat label="Resolved, Kyle confirmed" value={counts.prospective_resolved_confirmed} />
        <Stat label="Resolved, relayed only" value={counts.prospective_resolved_relayed_only}
              warn={counts.prospective_resolved_relayed_only > 0} />
        <Stat label="Prospective pending" value={counts.prospective_pending} />
        <Stat label="Retrospective" value={counts.retrospective} />
        <Stat label="Flagged" value={counts.flagged} warn={counts.flagged > 0} />
        <Stat label="Relayed, unconfirmed" value={counts.feedback_unconfirmed}
              warn={counts.feedback_unconfirmed > 0} to="/feedback" />
      </StatRow>

      <h2 className="jd-gap-b">Resolved prospective predictions</h2>
      {items.length === 0 && <Card><p className="muted">None resolved yet.</p></Card>}
      {items.map(({ prediction: p, feedback, resolution, flags }) => (
        <Card key={p.id} title={<Link to={`/predictions/${p.id}`} className="jd-text">{p.scenario}</Link>}>
          <div className="jd-row jd-gap-b">
            <OutcomeChip outcome={resolution} />
            <Flags flags={flags} />
            <When iso={p.created_at} />
          </div>
          <Field name="Expected"><b>{p.predicted_choice}</b></Field>
          {p.alternatives.length > 0 && <Field name="Alternatives">{p.alternatives.join(" · ")}</Field>}
          <div className="jd-label">Feedback</div>
          <ul className="jd-review-fb">
            {feedback.map((f) => (
              <li key={f.id}>
                <div className="jd-row">
                  <OutcomeChip outcome={f.outcome} />
                  <span className={`jd-small ${f.confirmed_at ? "jd-ok" : "jd-warn"}`}>
                    {f.confirmed_at ? "confirmed" : "relayed only"}
                  </span>
                  <SourceLink value={f.source_ref} />
                </div>
                <blockquote className="jd-text jd-quote">{f.kyle_words}</blockquote>
              </li>
            ))}
          </ul>
        </Card>
      ))}
    </>
  );
}

// --- app ---------------------------------------------------------------------------------------

export default function App() {
  return (
    <BrowserRouter basename="/apps/judgment">
      <Shell>
        <Routes>
          <Route path="/" element={<BeliefsPage />} />
          <Route path="/beliefs/:id" element={<BeliefPage />} />
          <Route path="/predictions" element={<PredictionsPage />} />
          <Route path="/predictions/:id" element={<PredictionPage />} />
          <Route path="/feedback" element={<FeedbackPage />} />
          <Route path="/review" element={<ReviewPage />} />
        </Routes>
      </Shell>
    </BrowserRouter>
  );
}
