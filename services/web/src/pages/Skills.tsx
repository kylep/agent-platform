import { Fragment, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type EditResult, type PullRequest, type Skill, type SkillDetail } from "../api";
import { ChangePhaseBanner, PendingChangeBanner, useChangeLoop } from "../components/ChangeFlow";
import { Banner } from "../ui/banner";
import { Button } from "../ui/button";
import { Chip } from "../ui/chip";
import { CodeEditor, Input, Textarea } from "../ui/field";
import { Table, TD, TH } from "../ui/table";

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
      <CodeEditor
        aria-label="Skill definition (SKILL.md)"
        value={md}
        onChange={(e) => setMd(e.target.value)}
        readOnly={locked}
        rows={Math.min(30, Math.max(10, md.split("\n").length + 2))}
      />
      {noop && <Banner>No changes — the skill already matches.</Banner>}
      {error && <div className="error">{error}</div>}
      <div className="row-actions" style={{ marginTop: 8 }}>
        <Button onClick={save} disabled={saving || locked || !dirty}>
          {saving ? "Saving…" : "Save SKILL.md (opens PR)"}
        </Button>
        {dirty && !locked && (
          <Button variant="secondary" onClick={() => setMd(detail.raw)}>Discard edits</Button>
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
      <Input placeholder="e.g. notion" value={name} onChange={(e) => setName(e.target.value)} autoFocus />
      <label className="muted">What should it do?</label>
      <Textarea rows={3} placeholder="e.g. Create and update pages in a Notion workspace…"
                value={purpose} onChange={(e) => setPurpose(e.target.value)} />
      <label className="muted">When should an agent reach for it? (optional)</label>
      <Textarea rows={2} placeholder="e.g. When asked to file notes or publish a summary to Notion."
                value={whenToUse} onChange={(e) => setWhenToUse(e.target.value)} />
      <label>
        <input type="checkbox" className="accent-accent" checked={needsSecret}
               onChange={(e) => setNeedsSecret(e.target.checked)} />
        {" "}It needs a credential (API token, webhook URL, …)
      </label>
      {needsSecret && (
        <div className="secret-editor">
          <Input placeholder="secret name (e.g. notion-token)" value={secretName}
                 onChange={(e) => setSecretName(e.target.value)} />
          <Input placeholder="env var the skill reads (e.g. NOTION_TOKEN)" value={secretEnv}
                 onChange={(e) => setSecretEnv(e.target.value)} />
          <Input placeholder="what it is / where to get it" value={secretDesc}
                 onChange={(e) => setSecretDesc(e.target.value)} />
        </div>
      )}
      <label className="muted">Anything else the author should know? (optional)</label>
      <Textarea rows={2} value={notes} onChange={(e) => setNotes(e.target.value)}
                aria-label="Additional notes for the skill author" />
      {error && <div className="error">{error}</div>}
      <div className="row-actions">
        <Button onClick={submit} disabled={!ready || submitting}>
          {submitting ? "Dispatching…" : "Author skill (opens PR)"}
        </Button>
        <Button variant="secondary" onClick={onCancel}>Cancel</Button>
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
        <Table>
          <thead>
            <tr><TH></TH><TH>Name</TH><TH>Description</TH><TH>Secrets</TH><TH>Used by</TH></tr>
          </thead>
          <tbody>
            {skills.map((s) => (
              <Fragment key={s.name}>
                <tr>
                  <TD className="skill-icon">{s.icon || "🧩"}</TD>
                  <TD>
                    <Button variant="link" className="text-default no-underline hover:text-accent hover:no-underline"
                            onClick={() => setOpen(open === s.name ? null : s.name)}>
                      {open === s.name ? "▾ " : "▸ "}{s.name}
                    </Button>
                    {pendingFor(s.name) && <Chip variant="warn" className="ml-2" title="Pending change">PR</Chip>}
                    {s.error && <div className="error">{s.error}</div>}
                  </TD>
                  <TD>{s.description || "—"}</TD>
                  <TD className="text-muted">{s.secrets.length ? s.secrets.join(", ") : "—"}</TD>
                  <TD className="text-muted">
                    {s.used_by.length
                      ? s.used_by.map((a, i) => (
                          <Fragment key={a}>
                            {i > 0 && ", "}
                            <Link to={`/agents/${a}`}>{a}</Link>
                          </Fragment>
                        ))
                      : "—"}
                  </TD>
                </tr>
                {open === s.name && (
                  <tr><TD colSpan={5}>
                    <SkillEditor name={s.name} />
                  </TD></tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </Table>
      )}
      {!loading && !wizard && (
        <Button style={{ marginTop: 12 }} onClick={() => setWizard(true)}>New skill</Button>
      )}
      {wizard && <SkillWizard onCancel={() => setWizard(false)} />}
    </div>
  );
}
