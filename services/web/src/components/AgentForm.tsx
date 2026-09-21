import { useState, type ReactNode } from "react";
import { asList, type AgentDef, type AgentEntrypoints, type CronEntry, type WebhookAuth, type WebhookEntry } from "../api";
import {
  generateWebhookSecret, secretLengthError, WEBHOOK_SECRET_HEADER, WEBHOOK_SECRET_MIN,
  type WebhookSecrets,
} from "../lib/webhook-secrets";
import { zoneOptions } from "../lib/cron";
import { ROLE_DESC } from "../lib/roles";
import { SecretPicker, SkillPicker, ToolGrantPicker, type GrantCatalog } from "./CapabilityPickers";
import { CronBuilder } from "./CronBuilder";
import { Button } from "@ap/ui/button";
import { CodeEditor, Input, Select, Textarea } from "@ap/ui/field";

// The agent definition form, shared by the editor and the New-Agent wizard so
// a field exists in exactly one place. Every field maps 1:1 to a column of the
// agent's row (docs/design/15) — the whole draft is what gets PUT/POSTed.

// Mirrors agentplatform.agentdefs.AGENT_ROLES: the auth roles an agent may
// declare (no `admin` — only the human session is admin; no `tools` — that one
// is derived from platform-tool grants at launch). `dev` is the Workbench's
// run profile (docs/design/24).
const AGENT_ROLES = ["reader", "annotator", "operator", "coder", "dev"];

const EMPTY_ENTRYPOINTS: AgentEntrypoints = { crons: [], webhooks: [], topics: [], timezone: "" };

// Defaults mirror the row's server-side defaults, so a create posts the same
// shape an update does.
export function emptyDef(): AgentDef {
  return {
    name: "", prompt: "", description: "", runtime: "claude", model: "", role: "operator",
    system: false, can_invoke: false, concurrency: 1, timeout_seconds: 1800,
    result_topic: "", transcript_retention_days: null,
    harness_tools: [], platform_tools: [], skills: [], secrets: [],
    entrypoints: { ...EMPTY_ENTRYPOINTS }, enabled: true,
    push_path_globs: [], may_delete_tests: false, quota_5h_max_pct: 80, quota_7d_max_pct: 50,
  };
}

// Fill in anything the API left out, so one trimmed field can't blank the form
// (or send `undefined` back on save). `asList` because the entrypoints blob
// comes back unvalidated (see api.ts): the editor is what stands between a
// warped row and a blank page, and saving over it is the repair.
export function toDraft(def: Partial<AgentDef> & { name: string }): AgentDef {
  const e = def.entrypoints as Partial<AgentEntrypoints> | undefined;
  return {
    ...emptyDef(),
    ...def,
    entrypoints: {
      // Non-object entries are dropped, not rendered: a cron that is a bare
      // string would give the row's inputs an undefined value apiece.
      crons: asList<CronEntry>(e?.crons).filter((c) => c && typeof c === "object"),
      webhooks: asList<unknown>(e?.webhooks)
        .filter((w) => w && typeof w === "object").map(toWebhook),
      topics: asList<string>(e?.topics).filter((t) => typeof t === "string"),
      timezone: typeof e?.timezone === "string" ? e.timezone : "",
    },
    harness_tools: asList<string>(def.harness_tools),
    platform_tools: asList<string>(def.platform_tools),
    skills: asList<string>(def.skills),
    secrets: asList<string>(def.secrets),
    push_path_globs: asList<string>(def.push_path_globs),
  };
}

// One webhook entry, repaired. The mode is narrowed to what the server
// accepts — anything else reads as `none`, the safe end, so a warped blob
// can't leave a row claiming an auth mode that isn't real — and `secret_set`
// is forced to a boolean the row's state can be decided from.
function toWebhook(raw: unknown): WebhookEntry {
  const w = raw as Partial<WebhookEntry>;
  return {
    path: typeof w.path === "string" ? w.path : "",
    auth: w.auth === "secret" ? "secret" : "none",
    secret_set: w.secret_set === true,
  };
}

export type Patch = (p: Partial<AgentDef>) => void;

// A number field that keeps its own text so clearing it doesn't snap back
// mid-typing. `nullable` (retention) treats blank as "platform default";
// otherwise a blank/invalid entry simply doesn't move the draft.
function NumberField({ label, value, onChange, nullable, min = 0, max, placeholder }: {
  label: string; value: number | null; onChange: (n: number | null) => void;
  nullable?: boolean; min?: number; max?: number; placeholder?: string;
}) {
  const [text, setText] = useState(value === null ? "" : String(value));
  return (
    <Input type="number" aria-label={label} min={min} max={max} value={text} placeholder={placeholder}
           onChange={(ev) => {
             const raw = ev.target.value;
             setText(raw);
             if (raw.trim() === "") { if (nullable) onChange(null); return; }
             const n = Number(raw);
             if (Number.isFinite(n)) onChange(Math.trunc(n));
           }} />
  );
}

// Comma-separated list bound to a string[]. Keeps its own text so a trailing
// comma survives while you type the next item.
function CsvField({ label, value, onChange, placeholder }: {
  label: string; value: string[]; onChange: (next: string[]) => void; placeholder?: string;
}) {
  const [text, setText] = useState(value.join(", "));
  return (
    <Input className="w-full" aria-label={label} value={text} placeholder={placeholder}
           onChange={(e) => {
             setText(e.target.value);
             onChange(e.target.value.split(",").map((t) => t.trim()).filter(Boolean));
           }} />
  );
}

// One item per line bound to a string[] — the shape a list of globs is read
// in. Keeps its own text so a blank line survives while you type the next one;
// the draft only ever holds the trimmed, non-empty lines.
function LinesField({ label, value, onChange, placeholder }: {
  label: string; value: string[]; onChange: (next: string[]) => void; placeholder?: string;
}) {
  const [text, setText] = useState(value.join("\n"));
  return (
    <Textarea className="w-full" aria-label={label} value={text} placeholder={placeholder}
              rows={Math.min(8, Math.max(3, value.length + 1))} spellCheck={false}
              onChange={(e) => {
                setText(e.target.value);
                onChange(e.target.value.split("\n").map((t) => t.trim()).filter(Boolean));
              }} />
  );
}

// A boolean the agent carries, written as words. The grants pickers' checkbox
// chips look almost the same on purpose — same box, same accent-on-when-checked
// — but their labels are code names and stay mono; these are sentences.
function Toggle({ label, checked, title, onChange }: {
  label: string; checked: boolean; title: string; onChange: (v: boolean) => void;
}) {
  return (
    <label className={checked ? "check-item on" : "check-item"} title={title}>
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span className="toggle-name">{label}</span>
    </label>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <div>
      <label className="field-label">{label}</label>
      {children}
      {hint && <p className="muted check-note">{hint}</p>}
    </div>
  );
}

export function IdentityFields({ draft, patch, catalog }: {
  draft: AgentDef; patch: Patch; catalog: GrantCatalog;
}) {
  const models = draft.runtime === "codex" ? catalog.codexModels : catalog.claudeModels;
  const savedCustomModel = draft.model && !models.some((model) => model.id === draft.model);
  return (
    <>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Description" hint="One line — shown in listings and the agent picker.">
          <Input className="w-full" aria-label="Description" value={draft.description}
                 placeholder="What does this agent do?"
                 onChange={(e) => patch({ description: e.target.value })} />
        </Field>
        <Field label="Runtime" hint="Subscription-backed CLI used for this agent's runs.">
          <Select className="w-full" aria-label="Runtime" value={draft.runtime}
                  onChange={(e) => patch({ runtime: e.target.value as AgentDef["runtime"], model: "" })}>
            <option value="claude">Claude Code</option>
            <option value="codex">OpenAI Codex</option>
          </Select>
        </Field>
        <Field label="Model" hint={`Models available to the ${draft.runtime === "codex" ? "Codex" : "Claude Code"} runtime.`}>
          <Select className="w-full" aria-label="Model" value={draft.model}
                  onChange={(e) => patch({ model: e.target.value })}>
            <option value="">Platform default</option>
            {savedCustomModel && <option value={draft.model}>{draft.model} — saved custom value</option>}
            {models.map((model) => <option key={model.id} value={model.id}>{model.label}</option>)}
          </Select>
        </Field>
        <Field label="Role"
               hint={ROLE_DESC[draft.role] ?? "The API role this agent's run token carries."}>
          <Select className="w-full" aria-label="Role" value={draft.role}
                  onChange={(e) => patch({ role: e.target.value })}>
            {[...new Set([...AGENT_ROLES, draft.role])].filter(Boolean).map((r) => (
              <option key={r} value={r} title={ROLE_DESC[r]}>{r}</option>
            ))}
          </Select>
        </Field>
        <Field label="Result topic" hint="Kafka topic each run's result is published to. Blank = none.">
          <Input className="w-full" aria-label="Result topic" value={draft.result_topic}
                 placeholder="e.g. app.news.item.ingested"
                 onChange={(e) => patch({ result_topic: e.target.value.trim() })} />
        </Field>
        <Field label="Timeout (seconds)" hint="Hard cap on one run.">
          <NumberField label="Timeout (seconds)" value={draft.timeout_seconds} min={1}
                       onChange={(n) => n !== null && patch({ timeout_seconds: n })} />
        </Field>
        <Field label="Concurrency" hint="How many runs of this agent may be in flight at once.">
          <NumberField label="Concurrency" value={draft.concurrency} min={1}
                       onChange={(n) => n !== null && patch({ concurrency: n })} />
        </Field>
        {/* The Workbench's quota gate (docs/design/24): a dev run asks
            `quota_ok` before it clones, and the answer is these two numbers
            against the live usage bars. */}
        <Field label="Quota gate: 5-hour max %" hint="quota_ok refuses above these.">
          <NumberField label="Quota gate: 5-hour max %" value={draft.quota_5h_max_pct} min={0} max={100}
                       onChange={(n) => n !== null && patch({ quota_5h_max_pct: n })} />
        </Field>
        <Field label="Quota gate: 7-day max %" hint="quota_ok refuses above these.">
          <NumberField label="Quota gate: 7-day max %" value={draft.quota_7d_max_pct} min={0} max={100}
                       onChange={(n) => n !== null && patch({ quota_7d_max_pct: n })} />
        </Field>
        <Field label="Transcript retention (days)" hint="Blank uses the platform default.">
          <NumberField label="Transcript retention (days)" value={draft.transcript_retention_days}
                       nullable placeholder="platform default"
                       onChange={(n) => patch({ transcript_retention_days: n })} />
        </Field>
      </div>

      {/* `.toggle-row`, not the grants pickers' `.check-grid`: these two labels
          are prose, not grant identifiers, so they stay in the body font. */}
      <div className="toggle-row" style={{ marginTop: 12 }}>
        <Toggle label="Enabled" checked={draft.enabled}
                title="Disabled agents keep their definition but reject runs."
                onChange={(enabled) => patch({ enabled })} />
        <Toggle label="Can invoke agents" checked={draft.can_invoke}
                title="May dispatch runs of other agents (depth-guarded)."
                onChange={(can_invoke) => patch({ can_invoke })} />
      </div>
    </>
  );
}

export function PromptField({ draft, patch }: { draft: AgentDef; patch: Patch }) {
  return (
    <>
      <h2>Prompt</h2>
      <p className="muted">
        The agent's context and personality — exactly what the runner gives Claude. Saving applies
        it to the live agent immediately; the previous text stays in the change log.
      </p>
      <CodeEditor
        aria-label="Agent prompt"
        value={draft.prompt}
        placeholder="You are…"
        onChange={(e) => patch({ prompt: e.target.value })}
        rows={Math.min(30, Math.max(10, draft.prompt.split("\n").length + 2))}
      />
    </>
  );
}

function CronRow({ entry, zone, onChange, onRemove }: {
  entry: CronEntry; zone: string; onChange: (e: CronEntry) => void; onRemove: () => void;
}) {
  return (
    <div className="cron-entry">
      <CronBuilder value={entry.schedule} timezone={zone}
                   onChange={(schedule) => onChange({ ...entry, schedule })} />
      <div className="grid gap-2 sm:grid-cols-[1fr_auto] items-start">
        <Input className="w-full" aria-label="Cron prompt" value={entry.prompt}
               placeholder="Prompt for this scheduled run (optional)"
               onChange={(e) => onChange({ ...entry, prompt: e.target.value })} />
        <Button variant="secondary" size="sm" onClick={onRemove} aria-label="Remove cron">Remove</Button>
      </div>
    </div>
  );
}

const AUTH_LABELS: Record<WebhookAuth, string> = { none: "None", secret: "Secret" };

function WebhookRow({ entry, secrets, onChange, onRemove }: {
  entry: WebhookEntry; secrets: WebhookSecrets;
  onChange: (next: WebhookEntry) => void; onRemove: () => void;
}) {
  // The eye is purely local: whether the field is masked says nothing about
  // the draft, and it resets with the row.
  const [shown, setShown] = useState(false);
  const typed = secrets.values[entry.path] ?? "";
  const lengthError = secretLengthError(typed);
  // A set secret is unreadable, so the field only comes back when the operator
  // asks to rotate it. Until one is set, there is nothing else to show.
  const entering = entry.auth === "secret" && (!entry.secret_set || secrets.rotating[entry.path]);

  return (
    <div className="grid gap-2" title={entry.auth === "secret" ? WEBHOOK_SECRET_HEADER : undefined}>
      <div className="grid gap-2 sm:grid-cols-[1fr_9rem_auto] items-start">
        <div>
          <Input className="w-full" aria-label="Webhook path" value={entry.path} placeholder="my-hook"
                 onChange={(e) => {
                   const path = e.target.value.trim();
                   secrets.rename(entry.path, path);
                   onChange({ ...entry, path });
                 }} />
          <p className="muted check-note">
            {entry.path ? <>POST <code>/api/webhooks/{entry.path}</code></> : "Path segment only — no slashes."}
          </p>
        </div>
        <Select className="w-full" aria-label="Webhook auth" value={entry.auth}
                onChange={(e) => onChange({ ...entry, auth: e.target.value as WebhookAuth })}>
          {(Object.keys(AUTH_LABELS) as WebhookAuth[]).map((m) => (
            <option key={m} value={m}>{AUTH_LABELS[m]}</option>
          ))}
        </Select>
        <Button variant="secondary" size="sm" aria-label="Remove webhook"
                onClick={() => { secrets.forget(entry.path); onRemove(); }}>
          Remove
        </Button>
      </div>

      {entry.auth === "secret" && (entering ? (
        <div className="grid gap-2 sm:grid-cols-[1fr_auto_auto] items-start">
          <div>
            <Input className="w-full" type={shown ? "text" : "password"} aria-label="Webhook secret"
                   autoComplete="off" value={typed} placeholder={`at least ${WEBHOOK_SECRET_MIN} characters`}
                   onChange={(e) => secrets.set(entry.path, e.target.value)} />
            <p className={lengthError ? "error" : "muted check-note"}>
              {lengthError ?? <>Callers send <code>{WEBHOOK_SECRET_HEADER}</code>. Stored write-only —
                copy it now, it is never shown again.</>}
            </p>
          </div>
          <Button variant="secondary" size="sm" aria-label={shown ? "Hide secret" : "Show secret"}
                  onClick={() => setShown((s) => !s)}>
            {shown ? "🙈" : "👁"}
          </Button>
          {/* Unmasks what it generated: a secret you can't see is one you
              can't give to the caller. */}
          <Button variant="secondary" size="sm"
                  onClick={() => { secrets.set(entry.path, generateWebhookSecret()); setShown(true); }}>
            Generate
          </Button>
        </div>
      ) : (
        <p className="muted check-note">
          secret set ·{" "}
          <Button variant="secondary" size="sm" onClick={() => secrets.rotate(entry.path)}>rotate</Button>
        </p>
      ))}
    </div>
  );
}

export function EntrypointsFields({ draft, patch, secrets }: {
  draft: AgentDef; patch: Patch; secrets: WebhookSecrets;
}) {
  const ep = draft.entrypoints;
  const set = (next: Partial<AgentEntrypoints>) => patch({ entrypoints: { ...ep, ...next } });
  const zones = zoneOptions();
  const zone = ep.timezone.trim();
  const zoneOk = zone === "" || zones.length === 0 || zones.includes(zone);
  // A zone the platform won't accept is reported at the timezone field. The
  // cron rows are asked in UTC meanwhile, so every row under it doesn't answer
  // "what does this schedule mean?" with a complaint about a different field.
  const previewZone = zoneOk ? zone : "";
  return (
    <>
      <h2>Entrypoints</h2>
      <p className="muted">
        The agent's durable triggers. Ad-hoc, prompt-carrying schedules belong in Jobs
        (the Schedules tab) — these are part of what the agent <em>is</em>.
      </p>

      <label className="field-label">Crons</label>
      <div className="grid gap-2">
        {ep.crons.map((c, i) => (
          <CronRow key={i} entry={c} zone={previewZone}
                   onChange={(next) => set({ crons: ep.crons.map((x, j) => (j === i ? next : x)) })}
                   onRemove={() => set({ crons: ep.crons.filter((_, j) => j !== i) })} />
        ))}
      </div>
      <div className="row-actions" style={{ marginTop: 6 }}>
        <Button variant="secondary" size="sm"
                onClick={() => set({ crons: [...ep.crons, { schedule: "", prompt: "" }] })}>
          + Add cron
        </Button>
      </div>

      <label className="field-label">Timezone</label>
      <Input className="w-full sm:w-80" aria-label="Entrypoints timezone" list="agent-tz-options"
             value={ep.timezone} placeholder="UTC"
             onChange={(e) => set({ timezone: e.target.value.trim() })} />
      <datalist id="agent-tz-options">{zones.map((z) => <option key={z} value={z} />)}</datalist>
      <p className={zoneOk ? "muted check-note" : "error"}>
        {zoneOk
          ? <>Blank means UTC. An IANA zone (e.g. America/Toronto) pins the crons to wall-clock
              time across daylight saving.</>
          : "Unknown timezone — use an IANA name like America/Toronto. Saving this quarantines the agent."}
      </p>

      <label className="field-label">Webhooks</label>
      <div className="grid gap-2">
        {ep.webhooks.map((w, i) => (
          <WebhookRow key={i} entry={w} secrets={secrets}
                      onChange={(next) => set({ webhooks: ep.webhooks.map((x, j) => (j === i ? next : x)) })}
                      onRemove={() => set({ webhooks: ep.webhooks.filter((_, j) => j !== i) })} />
        ))}
      </div>
      <p className="muted check-note">
        Auth <strong>None</strong> keeps today's rule — the caller needs a platform operator key.
        <strong> Secret</strong> also accepts the shared secret in a header, which is how a service
        that can't hold a platform key (GitHub, IFTTT, a curl) reaches this webhook.
      </p>
      <div className="row-actions" style={{ marginTop: 6 }}>
        <Button variant="secondary" size="sm"
                onClick={() => set({ webhooks: [...ep.webhooks, { path: "", auth: "none", secret_set: false }] })}>
          + Add webhook
        </Button>
      </div>

      <label className="field-label">Kafka topics</label>
      <CsvField label="Kafka topics" value={ep.topics} onChange={(topics) => set({ topics })}
                placeholder="comma-separated topics this agent consumes" />
    </>
  );
}

export function GrantsFields({ draft, patch, catalog }: {
  draft: AgentDef; patch: Patch; catalog: GrantCatalog;
}) {
  return (
    <>
      <h2>Grants</h2>
      <p className="muted">
        What this agent is allowed to touch. Grants are frozen into the run token at launch, so a
        change applies to the next run, not one already in flight.
      </p>

      <label className="field-label">Harness tools</label>
      <ToolGrantPicker tools={catalog.harnessTools} selected={draft.harness_tools} role={draft.role}
                       onChange={(harness_tools) => patch({ harness_tools })} />
      <p className="muted check-note">
        ⚠ marks the tools the runner denies unconditionally for normal agents (Bash, Read, Write,
        Edit, NotebookEdit) — they are self-edit only, so checking one changes nothing. A tool
        marked "dev runs only" reaches a <code>dev</code> agent and nobody else.
      </p>

      <label className="field-label">Platform tools</label>
      <ToolGrantPicker tools={catalog.platformTools} selected={draft.platform_tools} platform
                       onChange={(platform_tools) => patch({ platform_tools })} />
      <p className="muted check-note">
        Brokered MCP tools — an agent with these acts on the platform through token-scoped API
        calls instead of a shell. Some of them also decide the agent's machine role.
      </p>

      <label className="field-label">Skills</label>
      <SkillPicker skills={catalog.skills} selected={draft.skills}
                   onChange={(skills) => patch({ skills })} />
      <p className="muted check-note">Skills mount into the agent's pod and bind their required secrets.</p>

      <label className="field-label">Secrets</label>
      <SecretPicker secrets={catalog.secrets.filter(
        (secret) => !["claude-credentials", "codex-credentials"].includes(secret.name),
      )} selected={draft.secrets}
                    onChange={(secrets) => patch({ secrets })} />
      <p className="muted check-note">
        Granted secrets are injected into the run pod's environment. A required secret that is
        missing or invalid blocks the agent until it's fixed.
      </p>

      {/* The Workbench's two grants (docs/design/24). Like `can_invoke`, they
          are GRANT fields server-side: the row's field-level guard decides
          who may change them, and this form is the admin's. */}
      <label className="field-label">Push path globs</label>
      <LinesField label="Push path globs" value={draft.push_path_globs}
                  placeholder={"docs/**\nservices/web/src/**"}
                  onChange={(push_path_globs) => patch({ push_path_globs })} />
      <p className="muted check-note">
        One glob per line. A publish that stays inside them lands with auto-merge; anything
        else — or an empty list — waits for review on Changes.
      </p>

      <div className="toggle-row">
        <Toggle label="May delete tests" checked={draft.may_delete_tests}
                title="A publish that removes a test file is refused unless this is on."
                onChange={(may_delete_tests) => patch({ may_delete_tests })} />
      </div>
    </>
  );
}
