import { useEffect, useState, type ReactNode } from "react";
import { api, type ModelOption, type SecretStatus, type Skill, type ToolHelp } from "../api";

// The grant catalogs the agent editor and the New-Agent wizard both render as
// checkboxes. Capability is code (docs/design/15): every option here comes from
// a registry the platform derives from the repo — the UI never invents one.
export type GrantCatalog = {
  skills: Skill[];
  harnessTools: ToolHelp[];    // Claude Code tools (kind: claude)
  platformTools: ToolHelp[];   // brokered mcp__…__ tools (kind: platform)
  secrets: SecretStatus[];
  models: ModelOption[];
  ready: boolean;
};

export function useGrantCatalog(): GrantCatalog {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [tools, setTools] = useState<ToolHelp[]>([]);
  const [secrets, setSecrets] = useState<SecretStatus[]>([]);
  const [models, setModels] = useState<ModelOption[]>([]);
  const [ready, setReady] = useState(false);
  useEffect(() => {
    Promise.all([
      api<Skill[]>("/api/skills").catch(() => []),
      api<ToolHelp[]>("/api/help/tools").catch(() => []),
      api<SecretStatus[]>("/api/secrets").catch(() => []),
      api<{ models: ModelOption[] }>("/api/agent-models").catch(() => ({ models: [] })),
    ]).then(([sk, tl, se, mo]) => {
      setSkills(sk);
      setTools(tl);
      setSecrets(se);
      setModels(mo.models.filter((m) => m.id));
      setReady(true);
    });
  }, []);
  return {
    skills,
    harnessTools: tools.filter((t) => t.kind === "claude"),
    platformTools: tools.filter((t) => t.kind !== "claude"),
    secrets,
    models,
    ready,
  };
}

function toggle(set: readonly string[], name: string): string[] {
  return set.includes(name) ? set.filter((n) => n !== name) : [...set, name];
}

type PickOption = {
  name: string;
  label?: string;
  icon?: string;
  title?: string;
  // Granting it does nothing for a normal agent (the runner's always-denied
  // set) — flagged inline rather than hidden, so the row reads honestly.
  warn?: string;
};

// One checkbox grid, shared by every grant list so they look and behave alike.
function CheckGrid({ options, selected, onChange, empty }: {
  options: PickOption[]; selected: string[]; onChange: (next: string[]) => void; empty: ReactNode;
}) {
  if (options.length === 0) return <p className="muted">{empty}</p>;
  return (
    <div className="check-grid">
      {options.map((o) => (
        <label key={o.name} className={selected.includes(o.name) ? "check-item on" : "check-item"}
               title={o.title}>
          <input type="checkbox" checked={selected.includes(o.name)}
                 onChange={() => onChange(toggle(selected, o.name))} />
          {o.icon && <span className="check-icon">{o.icon}</span>}
          <span className="check-name">{o.label ?? o.name}</span>
          {o.warn && <span className="text-warning" title={o.warn} aria-label={o.warn}>⚠</span>}
        </label>
      ))}
    </div>
  );
}

export function SkillPicker({ skills, selected, onChange }: {
  skills: Skill[]; selected: string[]; onChange: (next: string[]) => void;
}) {
  return (
    <CheckGrid
      options={skills.map((s) => ({ name: s.name, icon: s.icon || "🧩", title: s.description }))}
      selected={selected} onChange={onChange}
      empty="No skills defined."
    />
  );
}

// `mcp__<server>__<tool>` is the wire name; show it as just the tool, since the
// section heading already says where it came from.
const mcpLabel = (t: string) => t.split("__").slice(2).join("__") || t;

export function ToolGrantPicker({ tools, selected, onChange, platform }: {
  tools: ToolHelp[]; selected: string[]; onChange: (next: string[]) => void; platform?: boolean;
}) {
  // A grant the registry no longer knows about still shows (checked) so saving
  // can't silently drop it — unchecking is how you remove it.
  const unknown = selected.filter((n) => !tools.some((t) => t.name === n));
  const options: PickOption[] = [
    ...tools.map((t) => ({
      name: t.name,
      label: t.display_name ?? (platform ? mcpLabel(t.name) : t.name),
      title: t.description,
      warn: t.sensitive
        ? "Always denied by the runner for normal agents (self-edit only) — granting it does nothing."
        : undefined,
    })),
    ...unknown.map((n) => ({ name: n, title: "Not in the registry — a stale grant.", warn: "Unknown tool." })),
  ];
  return (
    <CheckGrid options={options} selected={selected} onChange={onChange}
               empty={platform ? "No platform tools registered." : "No harness tools available."} />
  );
}

export function SecretPicker({ secrets, selected, onChange }: {
  secrets: SecretStatus[]; selected: string[]; onChange: (next: string[]) => void;
}) {
  const unknown = selected.filter((n) => !secrets.some((s) => s.name === n));
  const options: PickOption[] = [
    ...secrets.map((s) => ({
      name: s.name,
      title: s.hint || (s.declared ? "Declared secret." : "Undeclared value in the store."),
      warn: s.status === "missing" || s.status === "invalid"
        ? `Secret is ${s.status} — granting it will block the agent until it's healthy.`
        : undefined,
    })),
    ...unknown.map((n) => ({ name: n, title: "Not in the secret store.", warn: "Unknown secret." })),
  ];
  return (
    <CheckGrid options={options} selected={selected} onChange={onChange}
               empty="No secrets declared." />
  );
}
