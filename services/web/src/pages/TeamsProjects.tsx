import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type AgentSummary, type Project, type RelayChannel, type Team } from "../api";
import { useTitle } from "../lib/title";
import { Button } from "@ap/ui/button";
import { Input, Select } from "@ap/ui/field";

export default function TeamsProjects() {
  useTitle("Teams & Projects");
  const [teams, setTeams] = useState<Team[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [rooms, setRooms] = useState<RelayChannel[]>([]);
  const [error, setError] = useState("");
  const [teamSlug, setTeamSlug] = useState("");
  const [teamName, setTeamName] = useState("");
  const [projectSlug, setProjectSlug] = useState("");
  const [projectName, setProjectName] = useState("");

  function reload() {
    Promise.all([api<Team[]>("/api/teams"), api<Project[]>("/api/projects"),
      api<AgentSummary[]>("/api/agents"), api<RelayChannel[]>("/api/relay/channels")])
      .then(([t, p, a, r]) => { setTeams(t); setProjects(p); setAgents(a); setRooms(r); })
      .catch((e) => setError(String(e)));
  }
  useEffect(reload, []);

  async function create(kind: "team" | "project") {
    try {
      await api(`/api/${kind}s`, { method: "POST", body: JSON.stringify({
        slug: kind === "team" ? teamSlug : projectSlug,
        name: kind === "team" ? teamName : projectName,
      }) });
      setError("");
      if (kind === "team") { setTeamSlug(""); setTeamName(""); }
      else { setProjectSlug(""); setProjectName(""); }
      reload();
    } catch (e) { setError(String(e)); }
  }

  async function setMembers(kind: "team" | "project", slug: string, members: string[]) {
    await patch(kind, slug, { agents: members });
  }

  async function patch(kind: "team" | "project", slug: string, change: Record<string, unknown>) {
    try {
      await api(`/api/${kind}s/${slug}`, { method: "PATCH", body: JSON.stringify(change) });
      setError(""); reload();
    } catch (e) { setError(String(e)); }
  }

  const memberPicker = (kind: "team" | "project", slug: string, members: string[]) =>
    <details className="scope-details"><summary>{members.length} member{members.length === 1 ? "" : "s"} · edit</summary>
      <div className="scope-members">
        {agents.filter(a => !a.system).map(a =>
          <label key={a.name} style={{ marginRight: "1rem" }}>
            <input type="checkbox" checked={members.includes(a.name)} onChange={e =>
              setMembers(kind, slug, e.target.checked
                ? [...members, a.name] : members.filter(n => n !== a.name))} /> {a.name}
          </label>)}
      </div>
    </details>;

  return <div className="page page-scopes">
    <div className="page-header"><h1>Teams & Projects</h1></div>
    <p className="muted">Teams have a private Relay room and can be called with <code>@team:slug</code>. Projects link work across agents, rooms, runs and memories.</p>
    {error && <div className="error" role="alert">{error}</div>}
    <section><h2>Teams</h2>
      <div className="scope-create">
        <Input aria-label="Team slug" placeholder="team-slug" value={teamSlug} onChange={e => setTeamSlug(e.target.value)} />
        <Input aria-label="Team name" placeholder="Team name" value={teamName} onChange={e => setTeamName(e.target.value)} />
        <Button onClick={() => create("team")}>Create team</Button>
      </div>
      <div className="scope-grid">{teams.map(t => <article key={t.id} className="scope-card">
        <h3>{t.name} {t.archived && <small>Archived</small>}</h3>
        {t.description && <p className="muted">{t.description}</p>}
        <p className="scope-meta"><code>@team:{t.slug}</code> · <Link to={`/relay?channel=${t.relay_channel_id}`}>Open room ↗</Link></p>
        {memberPicker("team", t.slug, t.agents)}
        <details className="scope-details"><summary>Settings</summary>
          <label>Name <Input defaultValue={t.name} onBlur={e => e.target.value !== t.name && patch("team", t.slug, { name: e.target.value })} /></label>
          <label>Description <Input defaultValue={t.description} onBlur={e => e.target.value !== t.description && patch("team", t.slug, { description: e.target.value })} /></label>
          <label>People <Input aria-label={`${t.name} people`} defaultValue={t.humans.join(", ")}
            onBlur={e => { const humans = e.target.value.split(",").map(n => n.trim()).filter(Boolean);
              if (humans.join(",") !== t.humans.join(",")) patch("team", t.slug, { humans }); }} /></label>
          <p className="muted">Usernames, separated by commas. People can read and join the team's private Relay room.</p>
          <Button variant="secondary" onClick={() => patch("team", t.slug, { archived: !t.archived })}>{t.archived ? "Restore" : "Archive"}</Button>
        </details>
      </article>)}</div>
    </section>
    <section><h2>Projects</h2>
      <div className="scope-create">
        <Input aria-label="Project slug" placeholder="project-slug" value={projectSlug} onChange={e => setProjectSlug(e.target.value)} />
        <Input aria-label="Project name" placeholder="Project name" value={projectName} onChange={e => setProjectName(e.target.value)} />
        <Button onClick={() => create("project")}>Create project</Button>
      </div>
      <div className="scope-grid">{projects.map(p => <article key={p.id} className="scope-card">
        <h3>{p.name} {p.archived && <small>Archived</small>}</h3>
        {p.description && <p className="muted">{p.description}</p>}
        <p className="scope-meta"><code>{p.slug}</code>{p.team_slug ? ` · Team: ${p.team_slug}` : ""}</p>
        <p className="scope-meta">Conversations: {rooms.filter(r => r.project_id === p.id).map(r =>
          <span key={r.id}><Link to={`/relay?channel=${r.id}`}>{r.title || r.name || r.id.slice(0, 8)}</Link>{" "}</span>)}</p>
        {memberPicker("project", p.slug, p.agents)}
        <details className="scope-details"><summary>Settings</summary>
          <label>Name <Input defaultValue={p.name} onBlur={e => e.target.value !== p.name && patch("project", p.slug, { name: e.target.value })} /></label>
          <label>Description <Input defaultValue={p.description} onBlur={e => e.target.value !== p.description && patch("project", p.slug, { description: e.target.value })} /></label>
          <label>Owning team <Select value={p.team_slug ?? ""} onChange={e => patch("project", p.slug, { team_slug: e.target.value || null })}>
            <option value="">None</option>{teams.filter(t => !t.archived).map(t => <option key={t.id} value={t.slug}>{t.name}</option>)}
          </Select></label>
          <Button variant="secondary" onClick={() => patch("project", p.slug, { archived: !p.archived })}>{p.archived ? "Restore" : "Archive"}</Button>
        </details>
      </article>)}</div>
    </section>
  </div>;
}
