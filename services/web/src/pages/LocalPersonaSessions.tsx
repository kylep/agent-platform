import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type AgentSummary } from "../api";
import { Button } from "@ap/ui/button";
import { Input, Select } from "@ap/ui/field";
import { Chip } from "@ap/ui/chip";
import { useTitle } from "../lib/title";

type Session = { id: string; agent: string; harness: string; state: string;
  conversation_id: string; run_id: string; accepted_sequence: number;
  last_seen_at: string; expires_at: string; device_label?: string; context_stale?: boolean };
type Event = { sequence: number; local_turn_id: string; kind: string;
  payload: Record<string, unknown> };
type Mutation = { id: string; kind: string; target_id: string; before: unknown; after: unknown; reverted: boolean };
type Enrollment = { id: string; agent: string; device_label: string; revoked: boolean };

export default function LocalPersonaSessions() {
  useTitle("Local persona sessions");
  const [sessions, setSessions] = useState<Session[]>([]);
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [enrollments, setEnrollments] = useState<Enrollment[]>([]);
  const [agent, setAgent] = useState("");
  const [label, setLabel] = useState("");
  const [token, setToken] = useState("");
  const [error, setError] = useState("");
  const [selected, setSelected] = useState("");
  const [events, setEvents] = useState<Event[]>([]);
  const [after, setAfter] = useState(0);
  const [identitySession, setIdentitySession] = useState("");
  const [writes, setWrites] = useState<Mutation[]>([]);
  const [busy, setBusy] = useState(false);
  async function refresh() {
    try {
      const [rows, devices] = await Promise.all([
        api<Session[]>("/api/local-persona/sessions"),
        api<Enrollment[]>("/api/local-persona/enrollments"),
      ]);
      setSessions(rows); setEnrollments(devices); setError("");
    } catch (e) { setError(String(e)); }
  }
  useEffect(() => {
    void refresh();
    api<AgentSummary[]>("/api/agents").then(setAgents).catch(e => setError(String(e)));
  }, []);
  async function enroll() {
    setBusy(true); setToken("");
    try {
      const result = await api<{ token: string }>("/api/local-persona/enrollments", {
        method: "POST", body: JSON.stringify({ agent, device_id: crypto.randomUUID(), device_label: label }),
      });
      setToken(result.token); await refresh();
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  async function revoke(path: string) {
    setBusy(true);
    try { await api(path, { method: "DELETE" }); await refresh(); }
    catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  async function history(id: string, more = false) {
    try {
      const rows = await api<Event[]>(`/api/local-persona/sessions/${id}/history?after=${more ? after : 0}`);
      setEvents(prev => more ? [...prev, ...rows] : rows);
      setSelected(id); setAfter(rows.at(-1)?.sequence ?? 0);
    } catch (e) { setError(String(e)); }
  }
  async function reviewWrites(id: string) {
    try {
      const result = await api<Mutation[]>(`/api/local-persona/sessions/${id}/mutations`);
      setIdentitySession(id); setWrites(result);
    } catch (e) { setError(String(e)); }
  }
  async function revert(id: string) {
    setBusy(true);
    try {
      const result = await api<{ reverted: number }>(`/api/local-persona/sessions/${id}/revert`, { method: "POST" });
      setError(`${result.reverted} memory/profile writes reverted.`); await reviewWrites(id);
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  return <>
    <h1>Local persona sessions</h1>
    <p>Launch a persona in your native Claude Code or Codex terminal. AP records messages and tool activity.
      Continue work through <code>ap-persona resume</code>. Revoking AP access leaves the laptop process running.</p>
    {error && <p role="status">{error}</p>}
    <section>
      <h2>Enroll a device</h2>
      <div className="form-col">
        <Select aria-label="Persona" value={agent} onChange={e => { setAgent(e.target.value); setToken(""); }}>
          <option value="">Select persona</option>
          {agents.filter(a => a.agent_type === "persona" && a.enabled).map(a => <option key={a.name}>{a.name}</option>)}
        </Select>
        <Input aria-label="Device label" placeholder="Device label" value={label} onChange={e => setLabel(e.target.value)} />
        <Button disabled={!agent || !label || busy} onClick={enroll}>Create enrollment</Button>
      </div>
      {token && <div>
        <p>Shown once. Paste this into the private prompt from <code>ap-persona enroll --persona {agent} --origin &lt;AP URL&gt;</code>.</p>
        <Input aria-label="Enrollment token" type="password" readOnly value={token} />
        <Button variant="secondary" onClick={() => navigator.clipboard.writeText(token)}>Copy enrollment</Button>
        <Button variant="secondary" onClick={() => setToken("")}>Dismiss</Button>
      </div>}
      {enrollments.map(e => <p key={e.id}>{e.agent} · {e.device_label} · {e.revoked ? "revoked" :
        <Button size="sm" disabled={busy} onClick={() => revoke(`/api/local-persona/enrollments/${e.id}`)}>Revoke device</Button>}</p>)}
    </section>
    <section>
      <h2>Sessions</h2><Button variant="secondary" onClick={refresh}>Refresh</Button>
      {sessions.length === 0 && <p className="muted">No local persona sessions yet.</p>}
      {sessions.map(s => <article key={s.id}>
        <h3>{s.agent} · {s.harness} {s.device_label && `· ${s.device_label}`} <Chip>{s.state}</Chip></h3>
        {s.context_stale && <p className="muted">Persona context changed. Resume through the launcher to reload it.</p>}
        <p>Last contact {new Date(s.last_seen_at).toLocaleString()} · {s.accepted_sequence} records stored.
          Authority expires {new Date(s.expires_at).toLocaleString()}.</p>
        <div className="row-actions">
          <Link to={`/relay?channel=${s.conversation_id}`}>Conversation</Link>
          <Link to={`/runs/${s.run_id}`}>Execution</Link>
          <Button variant="secondary" onClick={() => history(s.id)}>View recorded activity</Button>
          {s.state !== "revoked" && <Button variant="danger" disabled={busy}
            onClick={() => revoke(`/api/local-persona/sessions/${s.id}`)}>Revoke AP access</Button>}
          {s.state !== "active" && <Button variant="secondary" disabled={busy} onClick={() => reviewWrites(s.id)}>Review identity writes</Button>}
        </div>
      </article>)}
    </section>
    {identitySession && <section><h2>Identity writes</h2>
      <p>Review the memory and profile changes below. Reverting refuses to overwrite later changes from other work.</p>
      {writes.length === 0 && <p className="muted">This session has no recorded identity writes.</p>}
      {writes.map(w => <details key={w.id}><summary>{w.kind} · {w.target_id} {w.reverted && "· reverted"}</summary>
        <p>Before</p><pre className="wrap">{JSON.stringify(w.before, null, 2)}</pre>
        <p>After</p><pre className="wrap">{JSON.stringify(w.after, null, 2)}</pre></details>)}
      <Button variant="danger" disabled={busy || !writes.some(w => !w.reverted)}
        onClick={() => revert(identitySession)}>Revert these memory/profile writes</Button>
    </section>}
    {selected && <section><h2>Recorded activity</h2>
      <p className="muted">Device-reported history. Unsupported or missing native records appear as gaps.</p>
      {events.map(e => <details key={e.sequence}><summary>{e.sequence} · {e.kind} · turn {e.local_turn_id}</summary>
        <pre className="wrap">{JSON.stringify(e.payload, null, 2)}</pre></details>)}
      {after > 0 && <Button onClick={() => history(selected, true)}>Load more</Button>}
    </section>}
  </>;
}
