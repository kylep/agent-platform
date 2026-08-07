import { useEffect, useState } from "react";
import { NavLink, useParams } from "react-router-dom";
import { api } from "../api";
import { Chip } from "@ap/ui/chip";
import { Markdown } from "@ap/ui/markdown";

// Help: the platform's concepts, served from the SAME docs that live in git
// (docs/building-blocks/ in the synced checkout — edit the doc, the page
// updates within a sync). Plus a Tools reference generated from the backend's
// tool registry, which a test keeps in lockstep with the capability picker.

type Topic = { slug: string; title: string };
type TopicDetail = Topic & { markdown: string };
type ToolHelp = { name: string; kind: string; description: string; sensitive: boolean };

function ToolsPage() {
  const [tools, setTools] = useState<ToolHelp[] | null>(null);
  useEffect(() => { api<ToolHelp[]>("/api/help/tools").then(setTools).catch(() => setTools([])); }, []);
  if (!tools) return <p className="muted">Loading…</p>;
  const claude = tools.filter((t) => t.kind === "claude");
  const platform = tools.filter((t) => t.kind === "platform");
  const row = (t: ToolHelp) => (
    <div key={t.name} className="help-tool">
      <div className="help-tool-head">
        <code>{t.name.replace("mcp__platform__", "")}</code>
        {t.sensitive && <Chip variant="warn">self-edit only</Chip>}
      </div>
      <p className="muted">{t.description}</p>
    </div>
  );
  return (
    <>
      <h1>Tools</h1>
      <p className="muted">
        What each checkbox on an agent's page actually grants. Only checked
        tools are available to the agent at runtime.
      </p>
      <h2>Claude Code tools</h2>
      <p className="muted">
        Run inside the agent's pod. The ones marked <em>self-edit only</em> are
        unconditionally denied for every other agent no matter what is
        declared — a shell or file access could read mounted credentials, so
        only the platform-coder (whose workspace is an ephemeral clone) gets
        them.
      </p>
      <div className="help-tools">{claude.map(row)}</div>
      <h2>Platform tools (MCP broker)</h2>
      <p className="muted">
        Act on the platform without a shell: each call goes through the MCP
        broker carrying the agent's own short-lived token, so its exact role
        scope applies and no credential ever enters the pod. They only
        function for agents that receive a token (system agents, or
        <code> memory</code>/<code>can_invoke</code> in the manifest).
      </p>
      <div className="help-tools">{platform.map(row)}</div>
    </>
  );
}

function TopicPage({ slug }: { slug: string }) {
  const [topic, setTopic] = useState<TopicDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setTopic(null);
    api<TopicDetail>(`/api/help/topics/${encodeURIComponent(slug)}`).then(setTopic)
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load topic."));
  }, [slug]);
  if (error) return <div className="error">{error}</div>;
  if (!topic) return <p className="muted">Loading…</p>;
  return <Markdown text={topic.markdown} className="md help-doc" />;
}

function Overview() {
  return (
    <>
      <h1>Help</h1>
      <p className="muted help-doc">
        The platform in one breath: <strong>configuration lives in git</strong> as
        self-describing building blocks (agents, skills, secrets, report
        types), reviewed through the change loop; <strong>runtime state lives in
        Postgres</strong>; <strong>secret values live only in k8s</strong>. Agents act
        through platform capabilities rather than holding credentials. Pick a
        concept on the left — each page is the same doc that lives in the
        repo, so it's always current.
      </p>
    </>
  );
}

export default function Help() {
  const { slug } = useParams();
  const [topics, setTopics] = useState<Topic[]>([]);
  useEffect(() => { api<Topic[]>("/api/help/topics").then(setTopics).catch(() => {}); }, []);
  return (
    <div className="help-layout">
      <nav className="help-subnav" aria-label="Help topics">
        <NavLink to="/help" end className={({ isActive }) => `help-link${isActive ? " active" : ""}`}>
          Overview
        </NavLink>
        <NavLink to="/help/tools" className={({ isActive }) => `help-link${isActive ? " active" : ""}`}>
          Tools
        </NavLink>
        <div className="help-subnav-label">Concepts</div>
        {topics.map((t) => (
          <NavLink key={t.slug} to={`/help/${t.slug}`}
                   className={({ isActive }) => `help-link${isActive ? " active" : ""}`}>
            {t.title}
          </NavLink>
        ))}
      </nav>
      <div className="help-content">
        {slug === undefined ? <Overview /> :
         slug === "tools" ? <ToolsPage /> : <TopicPage slug={slug} />}
      </div>
    </div>
  );
}
