import { Fragment, useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { api, type EditResult, type PullRequest, type SecretStatus } from "../api";
import { ChangePhaseBanner, PendingChangeBanner, useChangeLoop } from "../components/ChangeFlow";
import { Banner } from "../ui/banner";
import { Button } from "../ui/button";
import { Chip, StatusChip } from "../ui/chip";
import { CodeEditor, Input, Textarea } from "../ui/field";
import { Table, TD, TH } from "../ui/table";

type SaveState = "idle" | "saving" | "error";

// Build the secret's key/value. An explicit key wins; otherwise use the
// heuristic (pasted JSON → credentials.json file, anything else → `token`).
function toData(value: string, key: string): Record<string, string> {
  const trimmed = value.trim();
  const k = key.trim() || (trimmed.startsWith("{") ? "credentials.json" : "token");
  return { [k]: trimmed };
}

// Set a secret's VALUE (k8s side — immediate, no PR). The declaration
// (git side) is edited separately below.
function ValueEditor({ name, isNew, hint, suggestedKey, onSaved, onCancel }: {
  name?: string; isNew?: boolean; hint?: string; suggestedKey?: string;
  onSaved: () => void; onCancel: () => void;
}) {
  const [secretName, setSecretName] = useState(name ?? "");
  const [keyName, setKeyName] = useState(suggestedKey ?? "");
  const [value, setValue] = useState("");
  const [state, setState] = useState<SaveState>("idle");

  async function save() {
    const n = secretName.trim();
    if (!n || !value.trim()) return;
    setState("saving");
    try {
      await api(`/api/secrets/${encodeURIComponent(n)}`, {
        method: "PUT",
        body: JSON.stringify({ data: toData(value, keyName) }),
      });
      onSaved();
    } catch {
      setState("error");
    }
  }

  return (
    <div className="secret-editor">
      {isNew && (
        <Input placeholder="secret name (e.g. discord-bot)" value={secretName}
               onChange={(e) => setSecretName(e.target.value)} />
      )}
      {hint && <div className="muted secret-hint">{hint}</div>}
      <Textarea placeholder={hint || "Paste the secret value…"} value={value} rows={3}
                aria-label="Secret value"
                onChange={(e) => { setValue(e.target.value); setState("idle"); }} autoFocus />
      <div className="row-actions">
        <Input className="secret-key" placeholder="key (default: token)" value={keyName}
               aria-label="Secret data key"
               onChange={(e) => setKeyName(e.target.value)} />
        <Button onClick={save} disabled={state === "saving" || !value.trim() || (isNew && !secretName.trim())}>
          {state === "saving" ? "Saving…" : "Save value"}
        </Button>
        <Button variant="secondary" onClick={onCancel}>Cancel</Button>
        {state === "error" && <span className="error">Save failed.</span>}
      </div>
    </div>
  );
}

// The raw secret.yaml editor — the git side of a secret, on the standard
// change loop (PR on coder/secret-<name>, locked while pending).
function DeclarationEditor({ name }: { name: string }) {
  const [raw, setRaw] = useState<string | null>(null);
  const [yamlText, setYamlText] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [noop, setNoop] = useState(false);

  function load() {
    api<{ raw: string }>(`/api/secrets/${encodeURIComponent(name)}/declaration`)
      .then((d) => { setRaw(d.raw); setYamlText(d.raw); })
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load declaration."));
  }
  useEffect(load, [name]);

  const { pr: pending, phase, adopt } = useChangeLoop(`coder/secret-${name}`, load);

  if (error && raw === null) return <div className="error">{error}</div>;
  if (raw === null) return <p className="muted">Loading…</p>;
  const dirty = yamlText !== raw;
  const locked = pending !== null;

  async function save() {
    setSaving(true); setError(null); setNoop(false);
    try {
      const r = await api<EditResult>(`/api/secrets/${encodeURIComponent(name)}/quick-edit`, {
        method: "POST", body: JSON.stringify({ value: yamlText }),
      });
      if (r.tier === 0) setNoop(true);
      else adopt({
        number: r.pr?.number ?? 0, title: `Edit secret declaration: ${name}`,
        url: r.pr?.url ?? "", branch: r.branch ?? `coder/secret-${name}`,
        author: "you", created_at: new Date().toISOString(),
      } as PullRequest);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      {pending && <PendingChangeBanner pr={pending} what="secret declaration" />}
      <ChangePhaseBanner phase={phase} what="declaration" />
      <p className="muted">
        The declaration (<code>secrets/{name}/secret.yaml</code>) — keys, hints, and how the
        platform verifies this secret. The <b>value</b> is set separately and never enters git.
      </p>
      <CodeEditor
        aria-label="Secret declaration (secret.yaml)"
        value={yamlText}
        onChange={(e) => setYamlText(e.target.value)}
        readOnly={locked}
        rows={Math.min(20, Math.max(6, yamlText.split("\n").length + 2))}
      />
      {noop && <Banner>No changes — the declaration already matches.</Banner>}
      {error && <div className="error">{error}</div>}
      <div className="row-actions" style={{ marginTop: 8 }}>
        <Button onClick={save} disabled={saving || locked || !dirty}>
          {saving ? "Saving…" : "Save declaration (opens PR)"}
        </Button>
        {dirty && !locked && (
          <Button variant="secondary" onClick={() => setYamlText(raw)}>Discard edits</Button>
        )}
      </div>
    </div>
  );
}

// Declare a new secret: a small form → deterministic secret.yaml scaffold →
// PR under Changes. No coding agent — declarations are data.
function DeclareWizard({ initialName, onCancel }: { initialName?: string; onCancel: () => void }) {
  const [name, setName] = useState(initialName ?? "");
  const [description, setDescription] = useState("");
  const [required, setRequired] = useState(false);
  const [keyName, setKeyName] = useState("");
  const [keyHint, setKeyHint] = useState("");
  const [probeUrl, setProbeUrl] = useState("");
  const [probeHeaders, setProbeHeaders] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [opened, setOpened] = useState<{ number?: number; url?: string } | null>(null);

  async function submit() {
    setSubmitting(true); setError(null);
    // headers: one per line, "Name: value"; values may use {KEY} placeholders
    const headers: Record<string, string> = {};
    for (const line of probeHeaders.split("\n")) {
      const i = line.indexOf(":");
      if (i > 0) headers[line.slice(0, i).trim()] = line.slice(i + 1).trim();
    }
    try {
      const r = await api<EditResult>("/api/secrets/declare", {
        method: "POST",
        body: JSON.stringify({
          name: name.trim(), description: description.trim(), required,
          keys: keyName.trim() ? [{ name: keyName.trim(), hint: keyHint.trim() }] : [],
          probe: probeUrl.trim() ? { url: probeUrl.trim(), headers } : null,
        }),
      });
      setOpened(r.pr ?? {});
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to declare.");
    } finally {
      setSubmitting(false);
    }
  }

  if (opened) {
    return (
      <Banner variant="ok">
        Declaration proposed{opened.number ? <> — <Link to={`/changes?open=${opened.number}`}>review &amp; accept PR #{opened.number} under Changes</Link></> : <> — review it under <Link to="/changes">Changes</Link></>}.
        Once accepted and synced, the secret appears here with its hints; then set its value.
        {" "}<Button variant="link" onClick={onCancel}>Done</Button>
      </Banner>
    );
  }

  return (
    <div className="secret-editor" style={{ marginTop: 12 }}>
      <h2>Declare a secret</h2>
      <p className="muted">
        Declaring creates <code>secrets/&lt;name&gt;/secret.yaml</code> — the secret's shape,
        hints, and verification — as a pending change. The value is pasted separately after the
        declaration is live. Needs a verify <em>script</em> (not a URL probe)? Write the folder by
        hand or let the New-Skill wizard scaffold it.
      </p>
      <label className="muted">Name (lowercase-with-hyphens)</label>
      <Input placeholder="e.g. notion-token" value={name} onChange={(e) => setName(e.target.value)} autoFocus />
      <label className="muted">What is it?</label>
      <Input placeholder="e.g. Notion internal integration token" value={description}
             onChange={(e) => setDescription(e.target.value)} />
      <label className="muted">Key (the env var a skill reads) + where to get the value</label>
      <div className="row-actions">
        <Input placeholder="e.g. NOTION_TOKEN" value={keyName} onChange={(e) => setKeyName(e.target.value)} />
        <Input placeholder="hint: e.g. notion.so/my-integrations → New integration" value={keyHint}
               onChange={(e) => setKeyHint(e.target.value)} style={{ flex: 1 }} />
      </div>
      <label className="muted">Verification probe (optional): a read-only URL that 2xxes when the credential works.
        Use <code>{"{KEY}"}</code> placeholders for the secret's data.</label>
      <Input placeholder="e.g. https://api.notion.com/v1/users/me" value={probeUrl}
             aria-label="Probe URL"
             onChange={(e) => setProbeUrl(e.target.value)} />
      <Textarea placeholder={"headers, one per line:\nAuthorization: Bearer {NOTION_TOKEN}\nNotion-Version: 2022-06-28"}
                rows={2} value={probeHeaders} aria-label="Probe headers"
                onChange={(e) => setProbeHeaders(e.target.value)} />
      <label>
        <input type="checkbox" className="accent-accent" checked={required}
               onChange={(e) => setRequired(e.target.checked)} />
        {" "}The platform can't operate without it (required)
      </label>
      {error && <div className="error">{error}</div>}
      <div className="row-actions">
        <Button onClick={submit} disabled={!name.trim() || submitting}>
          {submitting ? "Proposing…" : "Declare (opens PR)"}
        </Button>
        <Button variant="secondary" onClick={onCancel}>Cancel</Button>
      </div>
    </div>
  );
}

export default function Secrets() {
  const location = useLocation();
  const banner = (location.state as { banner?: string } | null)?.banner;
  const [secrets, setSecrets] = useState<SecretStatus[]>([]);
  const [loading, setLoading] = useState(true);
  const [verifying, setVerifying] = useState<string | null>(null);
  const [verifyResult, setVerifyResult] = useState<Record<string, { status: string; code: number | null; detail: string }>>({});
  // expanded editor per row: "value:<name>" | "decl:<name>" | "declare[:name]" | "value-new"
  const [openEditor, setOpenEditor] = useState<string | null>(null);

  function load() {
    setLoading(true);
    api<SecretStatus[]>("/api/secrets").then(setSecrets).finally(() => setLoading(false));
  }
  useEffect(load, []);

  function done() { setOpenEditor(null); load(); }

  async function verify(name: string) {
    setVerifying(name);
    try {
      const r = await api<{ status: string; code: number | null; detail: string }>(
        `/api/secrets/${encodeURIComponent(name)}/verify`, { method: "POST" });
      setVerifyResult((v) => ({ ...v, [name]: r }));
      load();
    } catch { /* status stays as-is */ }
    finally { setVerifying(null); }
  }

  return (
    <div className="page">
      <h1>Secrets</h1>
      <p className="muted">
        Declared secrets (from <code>secrets/</code> in git) plus any bare values found in the
        cluster. The <b>declaration</b> is the reviewable shape; the <b>value</b> is pasted here
        and lives only in k8s. A heartbeat re-verifies every declared secret continuously.
      </p>
      {banner && <Banner>{banner}</Banner>}
      {loading && <p className="muted">Loading…</p>}
      {!loading && (
        <Table>
          <thead>
            <tr><TH>Name</TH><TH>Status</TH><TH></TH></tr>
          </thead>
          <tbody>
            {secrets.map((s) => (
              <Fragment key={s.name}>
                <tr>
                  <TD>
                    <span className="secret-name">{s.name}</span>
                    {!s.declared && (
                      <Chip variant="warn" className="ml-2" title="No secrets/<name>/secret.yaml — the platform can't verify or describe this secret.">undeclared</Chip>
                    )}
                  </TD>
                  <TD>
                    {s.required && <Chip variant="accent">required</Chip>}{" "}
                    <StatusChip status={s.status} />
                    {verifyResult[s.name] && (
                      <span className="muted secret-verify-note">
                        {" "}({verifyResult[s.name].code ?? verifyResult[s.name].detail})
                      </span>
                    )}
                  </TD>
                  <TD>
                    <div className="row-actions">
                      {s.probeable && (
                        <Button variant="secondary" size="sm" onClick={() => verify(s.name)}
                                disabled={verifying === s.name || s.status === "missing"}>
                          {verifying === s.name ? "Verifying…" : "Verify"}
                        </Button>
                      )}
                      <Button size="sm"
                              // primary emphasis only where action is needed: a
                              // missing secret wants its value; the rest are quiet
                              variant={openEditor === `value:${s.name}` || s.status !== "missing" ? "secondary" : "primary"}
                              onClick={() => setOpenEditor(openEditor === `value:${s.name}` ? null : `value:${s.name}`)}>
                        {openEditor === `value:${s.name}` ? "Close" : "Set value"}
                      </Button>
                      {s.declared ? (
                        <Button variant="secondary" size="sm"
                                onClick={() => setOpenEditor(openEditor === `decl:${s.name}` ? null : `decl:${s.name}`)}>
                          {openEditor === `decl:${s.name}` ? "Close" : "Declaration"}
                        </Button>
                      ) : (
                        <Button variant="secondary" size="sm" onClick={() => setOpenEditor(`declare:${s.name}`)}>
                          Declare
                        </Button>
                      )}
                    </div>
                  </TD>
                </tr>
                {openEditor === `value:${s.name}` && (
                  <tr><TD colSpan={3}>
                    <ValueEditor name={s.name} hint={s.hint} suggestedKey={s.key}
                                 onSaved={done} onCancel={() => setOpenEditor(null)} />
                  </TD></tr>
                )}
                {openEditor === `decl:${s.name}` && (
                  <tr><TD colSpan={3}><DeclarationEditor name={s.name} /></TD></tr>
                )}
                {openEditor === `declare:${s.name}` && (
                  <tr><TD colSpan={3}>
                    <DeclareWizard initialName={s.name} onCancel={done} />
                  </TD></tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </Table>
      )}
      {!loading && openEditor === null && (
        <div className="row-actions" style={{ marginTop: 12 }}>
          <Button onClick={() => setOpenEditor("declare")}>Declare a secret</Button>
          <Button variant="secondary" onClick={() => setOpenEditor("value-new")}>Set a bare value</Button>
        </div>
      )}
      {openEditor === "declare" && <DeclareWizard onCancel={done} />}
      {openEditor === "value-new" && (
        <div style={{ marginTop: 12 }}>
          <ValueEditor isNew onSaved={done} onCancel={() => setOpenEditor(null)} />
        </div>
      )}
    </div>
  );
}
