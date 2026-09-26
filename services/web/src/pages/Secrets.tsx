import { Fragment, useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { api, type ChatIdentity, type EditResult, type PullRequest, type SecretKeyField, type SecretStatus } from "../api";
import { ADVANCED_SECRET_GUIDES, CONNECTION_GUIDES, GUIDE_CHECKED } from "../lib/connection-guides";
import { ChangePhaseBanner, PendingChangeBanner, useChangeLoop } from "../components/ChangeFlow";
import { Banner } from "@ap/ui/banner";
import { Button } from "@ap/ui/button";
import { Chip, StatusChip } from "@ap/ui/chip";
import { CodeEditor, Input, Textarea } from "@ap/ui/field";
import { Table, TD, TH } from "@ap/ui/table";

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
//
// A declared secret renders one field per declared key (a multi-key secret
// like `strava` needs all its keys, and the backend merges on save). An
// undeclared/bare secret has no known keys, so it falls back to a single
// value box plus an explicit key name.
function ValueEditor({ name, isNew, hint, suggestedKey, keys, onSaved, onCancel }: {
  name?: string; isNew?: boolean; hint?: string; suggestedKey?: string;
  keys?: SecretKeyField[]; onSaved: () => void; onCancel: () => void;
}) {
  const [secretName, setSecretName] = useState(name ?? "");
  const [keyName, setKeyName] = useState(suggestedKey ?? "");
  const [value, setValue] = useState("");
  // Per-key values, when the secret declares its keys.
  const [fields, setFields] = useState<Record<string, string>>({});
  const [state, setState] = useState<SaveState>("idle");

  const declaredKeys = keys ?? [];
  const perKey = declaredKeys.length > 0;
  // Only send the fields the admin actually filled — the backend merges, so a
  // blank field leaves that key untouched rather than clobbering it.
  const filled = declaredKeys.filter((k) => (fields[k.name] ?? "").trim());
  const canSave = perKey ? filled.length > 0 : Boolean(value.trim());

  async function save() {
    const n = secretName.trim();
    if (!n || !canSave) return;
    setState("saving");
    const data = perKey
      ? Object.fromEntries(filled.map((k) => [k.name, fields[k.name].trim()]))
      : toData(value, keyName);
    try {
      await api(`/api/secrets/${encodeURIComponent(n)}`, {
        method: "PUT", body: JSON.stringify({ data }),
      });
      setValue(""); setFields({});
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
      {perKey ? (
        <>
          {declaredKeys.length > 1 && (
            <div className="muted secret-hint">
              This secret has {declaredKeys.length} keys. Save the values you have;
              blank fields are left unchanged.
            </div>
          )}
          {declaredKeys.map((k) => (
            <div key={k.name} className="secret-field">
              <label className="secret-key-label"><code>{k.name}</code></label>
              {k.hint && <div className="muted secret-hint">{k.hint}</div>}
              <Textarea value={fields[k.name] ?? ""} rows={2}
                        aria-label={`Value for ${k.name}`}
                        placeholder={`Paste ${k.name}…`}
                        onChange={(e) => {
                          setFields((f) => ({ ...f, [k.name]: e.target.value }));
                          setState("idle");
                        }} />
            </div>
          ))}
        </>
      ) : (
        <>
          {hint && <div className="muted secret-hint">{hint}</div>}
          <Textarea placeholder={hint || "Paste the secret value…"} value={value} rows={3}
                    aria-label="Secret value"
                    onChange={(e) => { setValue(e.target.value); setState("idle"); }} autoFocus />
        </>
      )}
      <div className="row-actions">
        {!perKey && (
          <Input className="secret-key" placeholder="key (default: token)" value={keyName}
                 aria-label="Secret data key"
                 onChange={(e) => setKeyName(e.target.value)} />
        )}
        <Button onClick={save} disabled={state === "saving" || !canSave || (isNew && !secretName.trim())}>
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
  const [selected, setSelected] = useState<string | null>(null);
  const [identities, setIdentities] = useState<ChatIdentity[]>([]);
  const [identityName, setIdentityName] = useState("");
  const [identityId, setIdentityId] = useState("");
  const [identityToken, setIdentityToken] = useState("");
  const [identityError, setIdentityError] = useState<string | null>(null);
  const [identityBusy, setIdentityBusy] = useState(false);

  function load() {
    setLoading(true);
    Promise.all([api<SecretStatus[]>("/api/secrets"), api<ChatIdentity[]>("/api/chat-identities")])
      .then(([items, accounts]) => { setSecrets(items); setIdentities(accounts); })
      .finally(() => setLoading(false));
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

  async function addDiscordIdentity() {
    const id = identityId.trim().toLowerCase();
    const secret = `${id}-bot`;
    if (!/^discord-[a-z][a-z0-9-]{0,31}$/.test(id) || !identityName.trim() || !identityToken.trim()) {
      setIdentityError("Choose a name, an ID like discord-family, and a bot token."); return;
    }
    if (identities.some((item) => item.id === id)) {
      setIdentityError("That identity ID is already in use."); return;
    }
    setIdentityBusy(true); setIdentityError(null);
    try {
      await api(`/api/secrets/${encodeURIComponent(secret)}`, {
        method: "PUT", body: JSON.stringify({ data: { token: identityToken.trim() } }),
      });
      await api("/api/chat-identities", {
        method: "POST", body: JSON.stringify({ id, display_name: identityName.trim(), secret_name: secret }),
      });
      setIdentityName(""); setIdentityId(""); setIdentityToken("");
      load();
    } catch (err) {
      setIdentityError(`${err instanceof Error ? err.message : "Setup failed."} The token may already be saved; retrying with the same ID is safe.`);
    } finally { setIdentityBusy(false); }
  }

  async function changeIdentity(identity: ChatIdentity) {
    const next = identity.status === "active" ? "disabled" : "active";
    setIdentityBusy(true); setIdentityError(null);
    try {
      await api(`/api/chat-identities/${encodeURIComponent(identity.id)}/status`, {
        method: "PATCH", body: JSON.stringify({ status: next }),
      });
      load();
    } catch (err) {
      setIdentityError(err instanceof Error ? err.message : "Could not change account status.");
    } finally { setIdentityBusy(false); }
  }

  const guide = CONNECTION_GUIDES.find((item) => item.title === selected);
  const discordAccounts = identities.filter((identity) => identity.connector === "discord");
  const statusFor = (names: string[]) => {
    const matches = names.map((name) => secrets.find((item) => item.name === name));
    return matches.every((item) => item && item.status === "valid") ? "Ready"
      : matches.every((item) => item && item.status !== "missing") ? "Set · check status" : "Needs setup";
  };

  return (
    <div className="page">
      <h1>Connections</h1>
      <p className="muted">
        Set up the accounts and credentials your platform uses. Values are stored in the cluster;
        this page never shows them again. Choose a card for setup steps and status.
      </p>
      {banner && <Banner>{banner}</Banner>}
      {loading && <p className="muted">Loading…</p>}
      {!loading && <div className="connection-grid">
        {CONNECTION_GUIDES.map((item) => {
          const discordActive = discordAccounts.filter((account) => account.status === "active" && account.configured).length;
          const discordPending = discordAccounts.length - discordActive;
          const status = item.title === "Discord chat identities"
            ? (discordAccounts.length ? `${discordActive} active${discordPending ? ` · ${discordPending} paused/unset` : ""}` : "Needs setup")
            : statusFor(item.secrets);
          const ready = item.title === "Discord chat identities"
            ? discordActive > 0 && discordPending === 0 : status === "Ready";
          return <button key={item.title} type="button"
            className={`connection-card ${ready ? "connection-ready" : "connection-needs"}`}
            aria-pressed={selected === item.title} onClick={() => { setSelected(selected === item.title ? null : item.title); setOpenEditor(null); }}>
            <span className="connection-mark" aria-hidden="true">{item.mark}</span>
            <span><strong>{item.title}</strong><small>{item.purpose}</small></span>
            <Chip variant={ready ? "ok" : "warn"}>{status}</Chip>
          </button>;
        })}
      </div>}
      {guide && <section className="connection-detail">
        <div className="row-actions"><h2>{guide.title}</h2><Button variant="secondary" size="sm" onClick={() => { setSelected(null); setOpenEditor(null); }}>Close</Button></div>
        <p className="muted">Setup guide checked {GUIDE_CHECKED}. Provider screens can change. <a href={guide.docs.url} target="_blank" rel="noreferrer">{guide.docs.label} ↗</a></p>
        <ol>{guide.steps.map((step) => <li key={step}>{step}</li>)}</ol>
        {guide.title === "Discord chat identities" && <>
          <h3>Accounts</h3>
          {discordAccounts.map((account) => {
            const secretName = account.secret_refs.bot_token?.secret;
            const current = secrets.find((item) => item.name === secretName);
            return <div className="connection-account" key={account.id}>
              <div><strong>{account.display_name}</strong> <code>{account.id}</code><br />
                <span className="muted">{account.configured ? "Token set" : "Token missing"} · {account.status} · {account.bound_routes} room routes</span></div>
              <div className="row-actions">
                <Button variant="secondary" size="sm" onClick={() => setOpenEditor(`value:${secretName}`)}>Set token</Button>
                <Button variant="secondary" size="sm" disabled={identityBusy || (!account.configured && account.status !== "active")}
                  onClick={() => changeIdentity(account)}>{account.status === "active" ? "Pause" : "Resume"}</Button>
              </div>
              {openEditor === `value:${secretName}` && <ValueEditor name={secretName} keys={current?.keys ?? [{ name: "token" }]}
                onSaved={done} onCancel={() => setOpenEditor(null)} />}
              {account.id !== "discord-default" && <p className="muted">Connector deployment entry: <code>{`{ id: ${account.id}, secretName: ${secretName} }`}</code> in <code>connectors.discord.extraIdentities</code>. After deploy, resume the account and bind a Relay room.</p>}
            </div>;
          })}
          <h3>Add another Discord account</h3>
          <p className="muted">The account is registered paused. After saving, deploy a connector workload for its ID and secret, then resume it and bind a Relay room. Agent outbound identity is chosen in each agent’s Grants.</p>
          <div className="form-col">
            <Input aria-label="Discord account display name" placeholder="Display name, e.g. Family bot" value={identityName} onChange={(e) => setIdentityName(e.target.value)} />
            <Input aria-label="Discord identity ID" placeholder="discord-family" value={identityId} onChange={(e) => setIdentityId(e.target.value)} />
            <Textarea aria-label="Discord bot token" placeholder="Paste bot token" value={identityToken} rows={2} onChange={(e) => setIdentityToken(e.target.value)} />
            <Button disabled={identityBusy} onClick={addDiscordIdentity}>{identityBusy ? "Saving…" : "Save token and add account"}</Button>
          </div>
          {identityError && <p role="alert" className="error">{identityError}</p>}
        </>}
        {guide.secrets.map((name) => {
          if (guide.title === "Discord chat identities") return null;
          const item = secrets.find((entry) => entry.name === name);
          return <div className="connection-account" key={name}>
            <div className="row-actions"><strong>{name}</strong>{item && <StatusChip status={item.status} />}
              {item?.probeable && <Button variant="secondary" size="sm" disabled={verifying === name || item.status === "missing"} onClick={() => verify(name)}>{verifying === name ? "Checking…" : "Check connection"}</Button>}
            </div>
            {verifyResult[name] && <p className="muted">{verifyResult[name].detail || verifyResult[name].status}</p>}
            {item && <ValueEditor key={name} name={name} keys={item.keys} hint={item.hint} suggestedKey={item.key} onSaved={done} onCancel={() => setSelected(null)} />}
          </div>;
        })}
      </section>}
      {!loading && <details className="connection-advanced"><summary>Advanced: all secret declarations and values</summary><p className="muted">Internal and legacy values, plus declaration management for every connection. Values remain in Kubernetes; declarations live in git.</p>
      {(
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
                      // Detail can be a full sentence — keep a compact trigger
                      // and hand the text to a native title tooltip (renders in
                      // the browser overlay, so the table's overflow can't clip
                      // it the way an absolutely-positioned popover would).
                      <span className="muted secret-verify-note"
                            title={verifyResult[s.name].detail || verifyResult[s.name].status}>
                        {" "}{verifyResult[s.name].code != null
                          ? `(${verifyResult[s.name].code})` : "details"}
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
                    {ADVANCED_SECRET_GUIDES[s.name] && <div className="connection-setup-note">
                      <p className="muted">Setup guide checked {GUIDE_CHECKED}. {ADVANCED_SECRET_GUIDES[s.name].url &&
                        <a href={ADVANCED_SECRET_GUIDES[s.name].url} target="_blank" rel="noreferrer">Current provider instructions ↗</a>}</p>
                      <ol>{ADVANCED_SECRET_GUIDES[s.name].steps.map((step) => <li key={step}>{step}</li>)}</ol>
                    </div>}
                    <ValueEditor name={s.name} hint={s.hint} suggestedKey={s.key}
                                 keys={s.keys}
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
      </details>}
    </div>
  );
}
