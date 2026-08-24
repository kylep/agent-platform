import { useState, type ReactNode } from "react";
import type { AgentDef, AgentEntrypoints, CronEntry } from "../api";
import { cronEnglish, zoneOptions } from "../lib/cron";
import { SecretPicker, SkillPicker, ToolGrantPicker, type GrantCatalog } from "./CapabilityPickers";
import { Button } from "@ap/ui/button";
import { CodeEditor, Input, Select } from "@ap/ui/field";

// The agent definition form, shared by the editor and the New-Agent wizard so
// a field exists in exactly one place. Every field maps 1:1 to a column of the
// agent's row (docs/design/15) — the whole draft is what gets PUT/POSTed.

// Mirrors agentplatform.agentdefs.AGENT_ROLES: the auth roles an agent may
// declare (no `admin` — only the human session is admin; no `tools` — that one
// is derived from platform-tool grants at launch).
const AGENT_ROLES = ["reader", "annotator", "operator", "coder"];

const EMPTY_ENTRYPOINTS: AgentEntrypoints = { crons: [], webhooks: [], topics: [], timezone: "" };

// Defaults mirror the row's server-side defaults, so a create posts the same
// shape an update does.
export function emptyDef(): AgentDef {
  return {
    name: "", prompt: "", description: "", model: "", role: "operator",
    system: false, can_invoke: false, concurrency: 1, timeout_seconds: 1800,
    result_topic: "", transcript_retention_days: null,
    harness_tools: [], platform_tools: [], skills: [], secrets: [],
    entrypoints: { ...EMPTY_ENTRYPOINTS }, enabled: true,
  };
}

// Fill in anything the API left out, so one trimmed field can't blank the form
// (or send `undefined` back on save).
export function toDraft(def: Partial<AgentDef> & { name: string }): AgentDef {
  const e = def.entrypoints;
  return {
    ...emptyDef(),
    ...def,
    entrypoints: {
      crons: e?.crons ?? [],
      webhooks: e?.webhooks ?? [],
      topics: e?.topics ?? [],
      timezone: e?.timezone ?? "",
    },
    harness_tools: def.harness_tools ?? [],
    platform_tools: def.platform_tools ?? [],
    skills: def.skills ?? [],
    secrets: def.secrets ?? [],
  };
}

export type Patch = (p: Partial<AgentDef>) => void;

// A number field that keeps its own text so clearing it doesn't snap back
// mid-typing. `nullable` (retention) treats blank as "platform default";
// otherwise a blank/invalid entry simply doesn't move the draft.
function NumberField({ label, value, onChange, nullable, min = 0, placeholder }: {
  label: string; value: number | null; onChange: (n: number | null) => void;
  nullable?: boolean; min?: number; placeholder?: string;
}) {
  const [text, setText] = useState(value === null ? "" : String(value));
  return (
    <Input type="number" aria-label={label} min={min} value={text} placeholder={placeholder}
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
  return (
    <>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Description" hint="One line — shown in listings and the agent picker.">
          <Input className="w-full" aria-label="Description" value={draft.description}
                 placeholder="What does this agent do?"
                 onChange={(e) => patch({ description: e.target.value })} />
        </Field>
        <Field label="Model" hint="Blank uses the platform default. Any model string is accepted.">
          <Input className="w-full" aria-label="Model" list="agent-model-options" value={draft.model}
                 placeholder="platform default"
                 onChange={(e) => patch({ model: e.target.value.trim() })} />
          <datalist id="agent-model-options">
            {catalog.models.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
          </datalist>
        </Field>
        <Field label="Role" hint="The API role this agent's run token carries.">
          <Select className="w-full" aria-label="Role" value={draft.role}
                  onChange={(e) => patch({ role: e.target.value })}>
            {[...new Set([...AGENT_ROLES, draft.role])].filter(Boolean).map((r) => (
              <option key={r} value={r}>{r}</option>
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
        <Field label="Transcript retention (days)" hint="Blank uses the platform default.">
          <NumberField label="Transcript retention (days)" value={draft.transcript_retention_days}
                       nullable placeholder="platform default"
                       onChange={(n) => patch({ transcript_retention_days: n })} />
        </Field>
      </div>

      <div className="check-grid" style={{ marginTop: 12 }}>
        <label className={draft.enabled ? "check-item on" : "check-item"}
               title="Disabled agents keep their definition but reject runs.">
          <input type="checkbox" checked={draft.enabled}
                 onChange={(e) => patch({ enabled: e.target.checked })} />
          <span className="check-name">enabled</span>
        </label>
        <label className={draft.can_invoke ? "check-item on" : "check-item"}
               title="May dispatch runs of other agents (depth-guarded).">
          <input type="checkbox" checked={draft.can_invoke}
                 onChange={(e) => patch({ can_invoke: e.target.checked })} />
          <span className="check-name">can invoke agents</span>
        </label>
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
  const parsed = entry.schedule.trim() ? cronEnglish(entry.schedule, zone) : "";
  const ok = parsed !== entry.schedule;
  return (
    <div className="grid gap-2 sm:grid-cols-[14rem_1fr_auto] items-start">
      <div>
        <Input className="w-full" aria-label="Cron schedule" value={entry.schedule} placeholder="0 9 * * *"
               onChange={(e) => onChange({ ...entry, schedule: e.target.value })} />
        <p className={entry.schedule.trim() === "" || ok ? "muted check-note" : "error"}>
          {entry.schedule.trim() === "" ? "5-field cron." : ok ? `→ ${parsed}` : "Unrecognized cron expression."}
        </p>
      </div>
      <Input className="w-full" aria-label="Cron prompt" value={entry.prompt}
             placeholder="Prompt for this scheduled run (optional)"
             onChange={(e) => onChange({ ...entry, prompt: e.target.value })} />
      <Button variant="secondary" size="sm" onClick={onRemove} aria-label="Remove cron">Remove</Button>
    </div>
  );
}

export function EntrypointsFields({ draft, patch }: { draft: AgentDef; patch: Patch }) {
  const ep = draft.entrypoints;
  const set = (next: Partial<AgentEntrypoints>) => patch({ entrypoints: { ...ep, ...next } });
  const zones = zoneOptions();
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
          <CronRow key={i} entry={c} zone={ep.timezone}
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
      <p className="muted check-note">
        Blank means UTC. An IANA zone (e.g. America/Toronto) pins the crons to wall-clock time
        across daylight saving.
      </p>

      <label className="field-label">Webhooks</label>
      <div className="grid gap-2">
        {ep.webhooks.map((w, i) => (
          <div key={i} className="grid gap-2 sm:grid-cols-[1fr_auto] items-start">
            <div>
              <Input className="w-full" aria-label="Webhook path" value={w.path} placeholder="my-hook"
                     onChange={(e) => set({
                       webhooks: ep.webhooks.map((x, j) => (j === i ? { path: e.target.value.trim() } : x)),
                     })} />
              <p className="muted check-note">
                {w.path ? <>POST <code>/api/webhooks/{w.path}</code></> : "Path segment only — no slashes."}
              </p>
            </div>
            <Button variant="secondary" size="sm" aria-label="Remove webhook"
                    onClick={() => set({ webhooks: ep.webhooks.filter((_, j) => j !== i) })}>
              Remove
            </Button>
          </div>
        ))}
      </div>
      <div className="row-actions" style={{ marginTop: 6 }}>
        <Button variant="secondary" size="sm"
                onClick={() => set({ webhooks: [...ep.webhooks, { path: "" }] })}>
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
      <ToolGrantPicker tools={catalog.harnessTools} selected={draft.harness_tools}
                       onChange={(harness_tools) => patch({ harness_tools })} />
      <p className="muted check-note">
        ⚠ marks the tools the runner denies unconditionally for normal agents (Bash, Read, Write,
        Edit, NotebookEdit) — they are self-edit only, so checking one changes nothing.
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
      <SecretPicker secrets={catalog.secrets} selected={draft.secrets}
                    onChange={(secrets) => patch({ secrets })} />
      <p className="muted check-note">
        Granted secrets are injected into the run pod's environment. A required secret that is
        missing or invalid blocks the agent until it's fixed.
      </p>
    </>
  );
}
