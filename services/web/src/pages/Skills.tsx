import { Fragment, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type Skill, type SkillDetail } from "../api";

function Body({ name }: { name: string }) {
  const [detail, setDetail] = useState<SkillDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api<SkillDetail>(`/api/skills/${encodeURIComponent(name)}`)
      .then(setDetail)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load skill."));
  }, [name]);
  if (error) return <div className="error">{error}</div>;
  if (!detail) return <p className="muted">Loading…</p>;
  return <pre className="agent-md">{detail.body}</pre>;
}

export default function Skills() {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => {
    api<Skill[]>("/api/skills")
      .then(setSkills)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load skills."))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="page">
      <h1>Skills</h1>
      <p className="muted">
        Reusable components agents can declare in their manifest (<code>skills:</code>). Each skill's
        required secrets are bound into the pods of agents that use it. Expand to read a skill.
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
                  <tr><td colSpan={5}><Body name={s.name} /></td></tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
