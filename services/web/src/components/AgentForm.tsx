import { useState, type ReactNode } from "react";
import { asList, type AgentDef, type AgentEntrypoints, type CronEntry, type WebhookAuth, type WebhookEntry } from "../api";
import {
  generateWebhookSecret, secretLengthError, WEBHOOK_SECRET_HEADER, WEBHOOK_SECRET_MIN,
  type WebhookSecrets,
} from "../lib/webhook-secrets";
import { zoneOptions } from "../lib/cron";
import { SecretPicker, SkillPicker, ToolGrantPicker, type GrantCatalog } from "./CapabilityPickers";
import { CronBuilder } from "./CronBuilder";
import { HelpLabel, type AgentHelpKey } from "./AgentFieldHelp";
import { Button } from "@ap/ui/button";
import { CodeEditor, Input, Select, Textarea } from "@ap/ui/field";

// The agent definition form, shared by the editor and the New-Agent wizard so
// a field exists in exactly one place. Every field maps 1:1 to a column of the
// agent's row (docs/design/15) — the whole draft is what gets PUT/POSTed.

// Mirrors agentplatform.agentdefs.AGENT_ROLES: the auth roles an agent may
// declare (no `admin` — only the human session is admin; no `tools` — that one
// is derived from platform-tool grants at launch). `dev` is the Workbench's
// run profile (docs/design/24).
const EXECUTION_PROFILES = [
  { value: "operator", label: "Standard agent" },
  { value: "dev", label: "Workbench developer" },
];

const EMPTY_ENTRYPOINTS: AgentEntrypoints = { crons: [], webhooks: [], topics: [], timezone: "" };

// Defaults mirror the row's server-side defaults, so a create posts the same
// shape an update does.
export function emptyDef(): AgentDef {
  return {
    name: "", prompt: "", description: "", runtime: "claude", model: "", role: "operator",
    system: false, responds_to_all: true, can_invoke: false, concurrency: 1, timeout_seconds: 1800,
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
      crons: asList<CronEntry>(e?.crons).filter((c) => c && typeof c === "object")
        .map((c) => ({ ...c, model: typeof c.model === "string" ? c.model : "" })),
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
    model: typeof w.model === "string" ? w.model : "",
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
function Toggle({ label, checked, title, help, onChange }: {
  label: string; checked: boolean; title: string; help?: AgentHelpKey;
  onChange: (v: boolean) => void;
}) {
  return (
    <span className="toggle-with-help">
      <label className={checked ? "check-item on" : "check-item"} title={title}>
        <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
        <span className="toggle-name">{label}</span>
      </label>
      {help && <HelpLabel label="" help={help} />}
    </span>
  );
}

function Field({ label, hint, help, children }: {
  label: string; hint?: string; help?: AgentHelpKey; children: ReactNode;
}) {
  return (
    <div>
      {help ? <HelpLabel label={label} help={help} /> : <label className="field-label">{label}</label>}
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
        <Field label="Description" help="description" hint="One line — shown in listings and the agent picker.">
          <Input className="w-full" aria-label="Description" value={draft.description}
                 placeholder="What does this agent do?"
                 onChange={(e) => patch({ description: e.target.value })} />
        </Field>
        <Field label="Runtime" help="runtime" hint="Subscription-backed CLI used for this agent's runs.">
          <Select className="w-full" aria-label="Runtime" value={draft.runtime}
                  onChange={(e) => patch({ runtime: e.target.value as AgentDef["runtime"], model: "" })}>
            <option value="claude">Claude Code</option>
            <option value="codex">OpenAI Codex</option>
          </Select>
        </Field>
        <Field label="Model" help="model" hint={`Models available to the ${draft.runtime === "codex" ? "Codex" : "Claude Code"} runtime.`}>
          <Select className="w-full" aria-label="Model" value={draft.model}
                  onChange={(e) => patch({ model: e.target.value })}>
            <option value="">Platform default</option>
            {savedCustomModel && <option value={draft.model}>{draft.model} — saved custom value</option>}
            {models.map((model) => <option key={model.id} value={model.id}>{model.label}</option>)}
          </Select>
        </Field>
        <Field label="Execution profile" help="profile"
               hint={draft.role === "dev"
                 ? "Workbench checkout holds no git credential; the platform publishes verified changes."
                 : draft.role === "coder"
                   ? "This retired profile cannot run. Choose Standard or Workbench before saving."
                 : "Standard isolated run; tool grants determine access."}>
          <Select className="w-full" aria-label="Execution profile" value={draft.role}
                  onChange={(e) => patch({ role: e.target.value })}>
            {EXECUTION_PROFILES.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
            {draft.role === "coder" &&
              <option value="coder" disabled>Retired self-editor — choose a replacement</option>}
            {["reader", "annotator"].includes(draft.role) &&
              <option value={draft.role}>Legacy {draft.role} (standard execution)</option>}
          </Select>
        </Field>
        <Field label="App output topic" help="result-topic" hint="Successful final results are published here. Usually blank.">
          <Input className="w-full" aria-label="App output topic" value={draft.result_topic}
                 placeholder="e.g. app.news.item.ingested"
                 onChange={(e) => patch({ result_topic: e.target.value })} />
        </Field>
        <Field label="Run time limit (seconds)" help="timeout" hint="Hard wall-clock cap for one run.">
          <NumberField label="Run time limit (seconds)" value={draft.timeout_seconds} min={1}
                       onChange={(n) => n !== null && patch({ timeout_seconds: n })} />
        </Field>
        <Field label="Parallel runs" help="concurrency" hint="Extra runs wait when this many are active.">
          <NumberField label="Parallel runs" value={draft.concurrency} min={1}
                       onChange={(n) => n !== null && patch({ concurrency: n })} />
        </Field>
        <Field label="Transcript retention (days)" help="retention" hint="Blank uses the platform default.">
          <NumberField label="Transcript retention (days)" value={draft.transcript_retention_days}
                       nullable placeholder="platform default"
                       onChange={(n) => patch({ transcript_retention_days: n })} />
        </Field>
      </div>

      {/* `.toggle-row`, not the grants pickers' `.check-grid`: these two labels
          are prose, not grant identifiers, so they stay in the body font. */}
      <div className="toggle-row" style={{ marginTop: 12 }}>
        <Toggle label="Accept new runs" checked={draft.enabled} help="enabled"
                title="Disabled agents keep their definition but reject runs."
                onChange={(enabled) => patch({ enabled })} />
        <Toggle label="Include in @all" checked={draft.responds_to_all} help="responds-all"
                title="Include this agent when a human mentions everyone in a Relay room."
                onChange={(responds_to_all) => patch({ responds_to_all })} />
      </div>
    </>
  );
}

export function PromptField({ draft, patch }: { draft: AgentDef; patch: Patch }) {
  return (
    <>
      <HelpLabel label="Prompt" help="prompt" heading />
      <p className="muted">
        The agent's context and personality — exactly what the runner gives the selected runtime. Saving applies
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

function InvocationModel({ value, models, onChange }: {
  value: string; models: GrantCatalog["claudeModels"]; onChange: (model: string) => void;
}) {
  const custom = value && !models.some((m) => m.id === value);
  return <Select aria-label="Invocation model" value={value} onChange={(e) => onChange(e.target.value)}>
    <option value="">Agent default</option>
    {custom && <option value={value}>{value} — saved custom value</option>}
    {models.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
  </Select>;
}

function CronRow({ entry, zone, models, onChange, onRemove }: {
  entry: CronEntry; zone: string; models: GrantCatalog["claudeModels"];
  onChange: (e: CronEntry) => void; onRemove: () => void;
}) {
  return (
    <div className="cron-entry">
      <CronBuilder value={entry.schedule} timezone={zone}
                   onChange={(schedule) => onChange({ ...entry, schedule })} />
      <div className="grid gap-2 sm:grid-cols-[1fr_13rem_auto] items-start">
        <Input className="w-full" aria-label="Cron prompt" value={entry.prompt}
               placeholder="Prompt for this scheduled run (optional)"
               onChange={(e) => onChange({ ...entry, prompt: e.target.value })} />
        <InvocationModel value={entry.model} models={models}
                         onChange={(model) => onChange({ ...entry, model })} />
        <Button variant="secondary" size="sm" onClick={onRemove} aria-label="Remove cron">Remove</Button>
      </div>
    </div>
  );
}

const AUTH_LABELS: Record<WebhookAuth, string> = { none: "None", secret: "Secret" };

function WebhookRow({ entry, secrets, models, onChange, onRemove }: {
  entry: WebhookEntry; secrets: WebhookSecrets;
  models: GrantCatalog["claudeModels"];
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
      <div className="grid gap-2 sm:grid-cols-[1fr_9rem_13rem_auto] items-start">
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
        <InvocationModel value={entry.model} models={models}
                         onChange={(model) => onChange({ ...entry, model })} />
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

export function EntrypointsFields({ draft, patch, secrets, catalog }: {
  draft: AgentDef; patch: Patch; secrets: WebhookSecrets; catalog: GrantCatalog;
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
  const models = draft.runtime === "codex" ? catalog.codexModels : catalog.claudeModels;
  return (
    <>
      <h2>Entrypoints</h2>
      <p className="muted">
        The agent's durable triggers. Ad-hoc, prompt-carrying schedules belong in Jobs
        (the Schedules tab) — these are part of what the agent <em>is</em>.
      </p>

      <HelpLabel label="Built-in schedules" help="crons" />
      <div className="grid gap-2">
        {ep.crons.map((c, i) => (
          <CronRow key={i} entry={c} zone={previewZone} models={models}
                   onChange={(next) => set({ crons: ep.crons.map((x, j) => (j === i ? next : x)) })}
                   onRemove={() => set({ crons: ep.crons.filter((_, j) => j !== i) })} />
        ))}
      </div>
      <div className="row-actions" style={{ marginTop: 6 }}>
        <Button variant="secondary" size="sm"
                onClick={() => set({ crons: [...ep.crons, { schedule: "", prompt: "", model: "" }] })}>
          + Add cron
        </Button>
      </div>

      <HelpLabel label="Schedule timezone" help="timezone" />
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

      <HelpLabel label="Webhooks" help="webhooks" />
      <div className="grid gap-2">
        {ep.webhooks.map((w, i) => (
          <WebhookRow key={i} entry={w} secrets={secrets} models={models}
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
                onClick={() => set({ webhooks: [...ep.webhooks, { path: "", auth: "none", model: "", secret_set: false }] })}>
          + Add webhook
        </Button>
      </div>

      <HelpLabel label="Input topics (Kafka)" help="topics" />
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

      {draft.runtime === "claude" ? <>
        <HelpLabel label="Claude Code tools" help="harness-tools" />
        <ToolGrantPicker tools={catalog.harnessTools} selected={draft.harness_tools} role={draft.role}
                         onChange={(harness_tools) => patch({ harness_tools })} />
        <p className="muted check-note">
          ⚠ marks tools denied for standard agents. A “dev runs only” tool reaches a Workbench
          developer and nobody else.
        </p>
      </> : <>
        <HelpLabel label="Codex capabilities" help="codex-capabilities" />
        <p className="muted check-note">
          Codex capabilities come from the Codex runtime, selected skills, and the platform grants
          below. Claude Code tool switches do not apply.
        </p>
        {draft.harness_tools.length > 0 && <p className="text-warning check-note">
          This agent retains {draft.harness_tools.length} inactive Claude Code tool grant{draft.harness_tools.length === 1 ? "" : "s"} for compatibility.
        </p>}
      </>}

      <HelpLabel label="Platform tools" help="platform-tools" />
      <ToolGrantPicker tools={catalog.platformTools} selected={draft.platform_tools} platform
                       onChange={(platform_tools) => patch({ platform_tools })} />
      <p className="muted check-note">
        Brokered MCP tools — an agent with these acts on the platform through token-scoped API
        calls instead of a shell. Some of them also decide the agent's machine role.
      </p>

      <HelpLabel label="Skills" help="skills" />
      <SkillPicker skills={catalog.skills} selected={draft.skills}
                   onChange={(skills) => patch({ skills })} />
      <p className="muted check-note">Skills mount into the agent's pod and bind their required secrets.</p>

      <HelpLabel label="Secrets" help="secrets" />
      <SecretPicker secrets={catalog.secrets.filter(
        (secret) => !["claude-credentials", "codex-credentials"].includes(secret.name),
      )} selected={draft.secrets}
                    onChange={(secrets) => patch({ secrets })} />
      <p className="muted check-note">
        Granted secrets are injected into the run pod's environment. A required secret that is
        missing or invalid blocks the agent until it's fixed.
      </p>

      <div className="toggle-row">
        <Toggle label="Can invoke other agents" checked={draft.can_invoke} help="invoke"
                title="May dispatch runs of other agents (depth-guarded)."
                onChange={(can_invoke) => patch({ can_invoke })} />
      </div>

      {draft.platform_tools.includes("mcp__platform__quota_ok") && <div className="grid gap-3 sm:grid-cols-2">
        <Field label="5-hour quota ceiling (%)" help="quota" hint="quota_ok refuses above this usage.">
          <NumberField label="5-hour quota ceiling (%)" value={draft.quota_5h_max_pct} min={0} max={100}
                       onChange={(n) => n !== null && patch({ quota_5h_max_pct: n })} />
        </Field>
        <Field label="Weekly quota ceiling (%)" help="quota" hint="quota_ok refuses above this usage.">
          <NumberField label="Weekly quota ceiling (%)" value={draft.quota_7d_max_pct} min={0} max={100}
                       onChange={(n) => n !== null && patch({ quota_7d_max_pct: n })} />
        </Field>
      </div>}

      {/* The Workbench's two grants (docs/design/24). Like `can_invoke`, they
          are GRANT fields server-side: the row's field-level guard decides
          who may change them, and this form is the admin's. */}
      {draft.role === "dev" && <>
      <HelpLabel label="Auto-merge paths" help="push-paths" />
      <LinesField label="Auto-merge paths" value={draft.push_path_globs}
                  placeholder={"docs/**\nservices/web/src/**"}
                  onChange={(push_path_globs) => patch({ push_path_globs })} />
      <p className="muted check-note">
        One glob per line. Blank means every PR waits for review. Auto-merge is enabled only when every changed file matches.
      </p>

      <div className="toggle-row">
        <Toggle label="Permit test-file deletion" checked={draft.may_delete_tests} help="delete-tests"
                title="When off, deleting or renaming away a test file rejects the entire Workbench handoff."
                onChange={(may_delete_tests) => patch({ may_delete_tests })} />
      </div>
      </>}
    </>
  );
}
