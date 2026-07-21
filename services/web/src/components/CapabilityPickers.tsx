import { useEffect, useState } from "react";
import { api, type Skill } from "../api";

// Loads the skill catalog (with icons) and the canonical tool list once, for
// the checkbox pickers below. Shared by the New-Agent wizard and the agent
// editor so both render the same options.
export function useCapabilities() {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [tools, setTools] = useState<string[]>([]);
  const [ready, setReady] = useState(false);
  useEffect(() => {
    Promise.all([
      api<Skill[]>("/api/skills").catch(() => []),
      api<{ tools: string[] }>("/api/agent-tools").then((r) => r.tools).catch(() => []),
    ]).then(([sk, tl]) => { setSkills(sk); setTools(tl); setReady(true); });
  }, []);
  return { skills, tools, ready };
}

function toggle(set: Set<string>, name: string): Set<string> {
  const next = new Set(set);
  next.has(name) ? next.delete(name) : next.add(name);
  return next;
}

export function SkillPicker({ skills, selected, onChange }: {
  skills: Skill[]; selected: Set<string>; onChange: (s: Set<string>) => void;
}) {
  if (skills.length === 0) return <p className="muted">No skills defined.</p>;
  return (
    <div className="check-grid">
      {skills.map((s) => (
        <label key={s.name} className={selected.has(s.name) ? "check-item on" : "check-item"}
               title={s.description}>
          <input type="checkbox" checked={selected.has(s.name)}
                 onChange={() => onChange(toggle(selected, s.name))} />
          <span className="check-icon">{s.icon || "🧩"}</span>
          <span className="check-name">{s.name}</span>
        </label>
      ))}
    </div>
  );
}

export function ToolPicker({ tools, selected, onChange }: {
  tools: string[]; selected: Set<string>; onChange: (s: Set<string>) => void;
}) {
  const allOn = tools.length > 0 && tools.every((t) => selected.has(t));
  return (
    <>
      <div className="check-grid">
        {tools.map((t) => (
          <label key={t} className={selected.has(t) ? "check-item on" : "check-item"}>
            <input type="checkbox" checked={selected.has(t)}
                   onChange={() => onChange(toggle(selected, t))} />
            <span className="check-name">{t}</span>
          </label>
        ))}
      </div>
      <p className="muted check-note">
        {allOn
          ? "All tools selected — the agent runs unrestricted (no tools: line is written)."
          : "Only the checked tools will be available to the agent."}
      </p>
    </>
  );
}
