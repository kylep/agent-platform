import { Fragment, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type EditResult, type PullRequest, type Skill, type SkillDetail } from "../api";
import { ChangePhaseBanner, PendingChangeBanner, useChangeLoop } from "../components/ChangeFlow";

// The raw SKILL.md editor: deterministic save — exactly what you type becomes
// the pending change (a PR on `coder/skill-{name}`), same contract as the
// agent definition editor. Rides the standard change loop: locked while its
// PR is open, auto-refreshes + flashes when the accepted change goes live.
function SkillEditor({ name }: { name: string }) {
  const [detail, setDetail] = useState<SkillDetail | null>(null);
  const [md, setMd] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [noop, setNoop] = useState(false);

  function load() {
    api<SkillDetail>(`/api/skills/${encodeURIComponent(name)}`)
      .then((d) => { setDetail(d); setMd(d.raw); })
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load skill."));
  }
  useEffect(load, [name]);

  const { pr: pending, phase, adopt } = useChangeLoop(`coder/skill-${name}`, load);

  if (error && !detail) return <div className="error">{error}</div>;
  if (!detail) return <p className="muted">Loading…</p>;
  const dirty = md !== detail.raw;
  const locked = pending !== null;

  async function save() {
    setSaving(true); setError(null); setNoop(false);
    try {
      const r = await api<EditResult>(`/api/skills/${encodeURIComponent(name)}/quick-edit`, {
        method: "POST", body: JSON.stringify({ value: md }),
      });
      if (r.tier === 0) setNoop(true);
      else adopt({
        number: r.pr?.number ?? 0, title: `Edit skill: ${name}`, url: r.pr?.url ?? "",
        branch: r.branch ?? `coder/skill-${name}`, author: "you",
        created_at: new Date().toISOString(),
      } as PullRequest);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      {pending && <PendingChangeBanner pr={pending} what="skill" />}
      <ChangePhaseBanner phase={phase} what="skill" />
      <textarea
        className="agent-md-editor"
        aria-label="Skill definition (SKILL.md)"
        value={md}
        onChange={(e) => setMd(e.target.value)}
        readOnly={locked}
        spellCheck={false}
        rows={Math.min(30, Math.max(10, md.split("\n").length + 2))}
      />
      {noop && <div className="banner">No changes — the skill already matches.</div>}
      {error && <div className="error">{error}</div>}
      <div className="row-actions" style={{ marginTop: 8 }}>
        <button onClick={save} disabled={saving || locked || !dirty}>
          {saving ? "Saving…" : "Save SKILL.md (opens PR)"}
        </button>
        {dirty && !locked && (
          <button className="secondary" onClick={() => setMd(detail.raw)}>Discard edits</button>
        )}
      </div>
    </div>
  );
}

// The New-Skill interview: a few key questions, then platform-coder authors
// the skill (and scaffolds a secrets/ folder if a credential is involved) as
// a pending change under Changes.
function SkillWizard({ onCancel }: { onCancel: () => void }) {
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [purpose, setPurpose] = useState("");
  const [whenToUse, setWhenToUse] = useState("");
  const [needsSecret, setNeedsSecret] = useState(false);
  const [secretName, setSecretName] = useState("");
  const [secretEnv, setSecretEnv] = useState("");
  const [secretDesc, setSecretDesc] = useState("");
  const [notes, setNotes] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const ready = name.trim() && purpose.trim() && (!needsSecret || secretName.trim());

  async function submit() {
    setSubmitting(true); setError(null);
    try {
      const r = await api<{ id: string }>("/api/skills/new", {
        method: "POST",
        body: JSON.stringify({
          name: name.trim(), purpose: purpose.trim(), when_to_use: whenToUse.trim(),
          notes: notes.trim(),
          secret: needsSecret ? {
            name: secretName.trim(), env_var: secretEnv.trim(), description: secretDesc.trim(),
          } : null,
        }),
      });
      navigate(`/runs/${r.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start the skill author run.");
      setSubmitting(false);
    }
  }

  return (
    <div className="secret-editor" style={{ marginTop: 12 }}>
      <h2>New skill</h2>
      <p className="muted">
        Answer a few questions; a coding agent authors the skill (and, if it needs a credential,
        scaffolds its <code>secrets/</code> folder). The result lands as a pending change to review
        under <Link to="/changes">Changes</Link> — nothing goes live until you accept it.
      </p>
      <label className="muted">Name (lowercase-with-hyphens)</label>
      <input placeholder="e.g. notion" value={name} onChange={(e) => setName(e.target.value)} autoFocus />
      <label className="muted">What should it do?</label>
      <textarea rows={3} placeholder="e.g. Create and update pages in a Notion workspace…"
                value={purpose} onChange={(e) => setPurpose(e.target.value)} />
      <label className="muted">When should an agent reach for it? (optional)</label>
      <textarea rows={2} placeholder="e.g. When asked to file notes or publish a summary to Notion."
                value={whenToUse} onChange={(e) => setWhenToUse(e.target.value)} />
      <label>
        <input type="checkbox" checked={needsSecret} onChange={(e) => setNeedsSecret(e.target.checked)} />
        {" "}It needs a credential (API token, webhook URL, …)
      </label>
      {needsSecret && (
        <div className="secret-editor">
          <input placeholder="secret name (e.g. notion-token)" value={secretName}
                 onChange={(e) => setSecretName(e.target.value)} />
          <input placeholder="env var the skill reads (e.g. NOTION_TOKEN)" value={secretEnv}
                 onChange={(e) => setSecretEnv(e.target.value)} />
          <input placeholder="what it is / where to get it" value={secretDesc}
                 onChange={(e) => setSecretDesc(e.target.value)} />
        </div>
      )}
      <label className="muted">Anything else the author should know? (optional)</label>
      <textarea rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} />
      {error && <div className="error">{error}</div>}
      <div className="row-actions">
        <button onClick={submit} disabled={!ready || submitting}>
          {submitting ? "Dispatching…" : "Author skill (opens PR)"}
        </button>
        <button className="secondary" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}

export default function Skills() {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [prs, setPrs] = useState<PullRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [wizard, setWizard] = useState(false);

  function load() {
    api<Skill[]>("/api/skills")
      .then(setSkills)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load skills."))
      .finally(() => setLoading(false));
    api<PullRequest[]>("/api/pull-requests").then(setPrs).catch(() => setPrs([]));
  }
  useEffect(load, []);

  const pendingFor = (name: string) =>
    prs.find((p) => p.branch === `coder/skill-${name}`) ?? null;

  return (
    <div className="page">
      <h1>Skills</h1>
      <p className="muted">
        Reusable components agents can declare in their manifest (<code>skills:</code>). Each skill's
        required secrets are bound into the pods of agents that use it. Expand a skill to read or
        edit its <code>SKILL.md</code> — saves become pending changes.
      </p>
      {loading && <p className="muted">Loading…</p>}
      {error && <div className="error">{error}</div>}
      {!loading && !error && skills.length === 0 && <p className="muted">No skills defined.</p>}
      {!loading && skills.length > 0 && (
        <table className="table">
          <thead>
            <tr><th></th><th>Name</th><th>Description</th><th>Secrets</th><th>Used by</th></tr>
          </thead>
          <tbody>
            {skills.map((s) => (
              <Fragment key={s.name}>
                <tr>
                  <td className="skill-icon">{s.icon || "🧩"}</td>
                  <td>
                    <button className="linkish" onClick={() => setOpen(open === s.name ? null : s.name)}>
                      {open === s.name ? "▾ " : "▸ "}{s.name}
                    </button>
                    {pendingFor(s.name) && <span className="chip chip-unprobed" title="Pending change">PR</span>}
                    {s.error && <div className="error">{s.error}</div>}
                  </td>
                  <td>{s.description || "—"}</td>
                  <td className="muted">{s.secrets.length ? s.secrets.join(", ") : "—"}</td>
                  <td className="muted">
                    {s.used_by.length
                      ? s.used_by.map((a, i) => (
                          <Fragment key={a}>
                            {i > 0 && ", "}
                            <Link to={`/agents/${a}`}>{a}</Link>
                          </Fragment>
                        ))
                      : "—"}
                  </td>
                </tr>
                {open === s.name && (
                  <tr><td colSpan={5}>
                    <SkillEditor name={s.name} />
                  </td></tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      )}
      {!loading && !wizard && (
        <button style={{ marginTop: 12 }} onClick={() => setWizard(true)}>New skill</button>
      )}
      {wizard && <SkillWizard onCancel={() => setWizard(false)} />}
    </div>
  );
}
