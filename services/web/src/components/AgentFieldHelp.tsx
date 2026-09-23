import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { InfoDialog } from "@ap/ui/dialog";

export type AgentHelpKey =
  | "description" | "runtime" | "model" | "profile" | "prompt" | "result-topic"
  | "timeout" | "concurrency" | "retention" | "enabled" | "responds-all"
  | "harness-tools" | "codex-capabilities" | "platform-tools" | "skills" | "secrets" | "invoke"
  | "quota" | "push-paths" | "delete-tests" | "crons" | "timezone"
  | "webhooks" | "topics";

type Help = { title: string; body: ReactNode };

const HELP: Record<AgentHelpKey, Help> = {
  description: { title: "Description", body: <>A short summary shown in agent lists and pickers. It does not enter the agent's prompt.</> },
  runtime: { title: "Runtime", body: <><p>The subscription-backed CLI that executes each run.</p><p><strong>Claude Code</strong> uses Claude models and its selectable built-in tools. <strong>OpenAI Codex</strong> uses Codex models and capabilities. Both runtimes receive the same prompt, skills, platform tools, and granted secrets.</p></> },
  model: { title: "Model", body: <>The runtime model for this agent. Platform default follows the configured default for the selected runtime. A cron, webhook, or other invocation may override it for that run.</> },
  profile: { title: "Execution profile", body: <>
    <p>The execution profile controls the agent's workspace and publishing workflow. It does not change its runtime, model, tools, skills, secrets, or invocation permissions.</p>
    <table className="profile-help-table">
      <thead><tr><th>Profile</th><th>What happens</th></tr></thead>
      <tbody>
        <tr><td><strong>Standard agent</strong></td><td>Runs in a lightweight isolated pod for chat, research, schedules, and platform tools. There is no repository checkout or code handoff.</td></tr>
        <tr><td><strong>Workbench developer</strong></td><td>Clones the repository into a larger development pod with Python, Node, Playwright, and test tooling. At the end, the platform verifies its changes and opens or updates a pull request.</td></tr>
      </tbody>
    </table>
  </> },
  prompt: { title: "Prompt", body: <>The durable instructions and personality sent to the selected runtime on every run. Trigger-specific text is added as the user's request; it does not replace this prompt.</> },
  "result-topic": { title: "App output topic", body: <><p>After a successful run, the recorder publishes the final text to this Kafka topic as an <code>agent.result</code> event with the run and agent IDs.</p><p>Use it when an app consumes structured agent output. Leave it blank when the result belongs only in run history or Relay.</p></> },
  timeout: { title: "Run time limit", body: <>The hard wall-clock limit for one run, including pod startup and model/tool work. Kubernetes stops the job when it expires. A queued run has not started this clock.</> },
  concurrency: { title: "Parallel runs", body: <>The maximum number of this agent's runs that may be dispatched or running at once. Extra work waits in the queue. The platform-wide capacity can impose a lower effective limit.</> },
  retention: { title: "Transcript retention", body: <>How long detailed transcript events are kept. Blank uses the platform default. Run metadata and the final result have their own lifecycle.</> },
  enabled: { title: "Accept new runs", body: <>Turn this off to keep the definition and history while rejecting new work from every trigger.</> },
  "responds-all": { title: "Include in @all", body: <>Lets human <code>@all</code> messages in Relay wake this agent. Direct mentions, assignments, schedules, and webhooks still work when it is off.</> },
  "harness-tools": { title: "Claude Code tools", body: <>Claude Code built-ins allowed for this agent. These switches are interpreted only by the Claude runtime. Sensitive shell and file-editing tools remain denied for standard agents; the Workbench supplies its own development environment.</> },
  "codex-capabilities": { title: "Codex capabilities", body: <>Codex's built-in capabilities are controlled by the Codex runtime rather than Claude's tool allow-list. Add portable instructions through Skills and grant platform access through Platform tools. Any saved Claude Code tool grants are retained but inactive while Codex is selected.</> },
  "platform-tools": { title: "Platform tools", body: <>Brokered MCP capabilities such as Relay, tickets, memory, or app access. They work with both runtimes and determine the least-privileged API role minted into each run.</> },
  skills: { title: "Skills", body: <>Optional, reusable workflow guides mounted in either runtime. Put this agent's job in its prompt and API details in the tool or app. A skill may declare a required secret, which becomes a readiness dependency.</> },
  secrets: { title: "Secrets", body: <>Extra encrypted values injected into the run pod. Provider credentials are managed by the runtime proxies and are intentionally absent here.</> },
  invoke: { title: "Can invoke other agents", body: <>Allows this agent to launch other agents through the platform. Relay hop and budget guards still apply. This is an authority grant, so it is grouped with tools and secrets.</> },
  quota: { title: "Quota gate thresholds", body: <>Thresholds used by the <code>quota_ok</code> platform tool. They have no effect unless that tool is granted and the agent calls it before expensive work.</> },
  "push-paths": { title: "Auto-merge paths", body: <><p>Workbench always opens or updates a pull request; the agent never pushes or merges code itself.</p><p>Leave this blank to require human review for every PR. Add path globs only when a PR may auto-merge after verification if <strong>every</strong> changed file matches one of them. A non-matching file makes the PR wait for review.</p><p>Platform-protected files are always refused.</p></> },
  "delete-tests": { title: "Permit test-file deletion", body: <><p>Controls whether a Workbench handoff may delete a test file or rename it out of a test path.</p><p>When off, the platform refuses the entire handoff before updating the pull request. When on, the deletion is allowed but still follows the normal review or auto-merge path rules.</p></> },
  crons: { title: "Built-in schedules", body: <>Durable schedules that are part of this agent's identity. Each can supply a prompt and model override. Use the Schedules page for operational jobs that should be managed separately.</> },
  timezone: { title: "Schedule timezone", body: <>The IANA timezone used by all built-in cron schedules. Blank means UTC. Named zones preserve wall-clock intent across daylight saving changes.</> },
  webhooks: { title: "Webhooks", body: <>HTTP entrypoints that launch this agent. A secret-authenticated webhook can be called without a platform API key; an unsecreted one still requires an operator key.</> },
  topics: { title: "Input topics", body: <>Kafka topics this agent consumes as triggers. This is input routing; App output topic is the separate successful-result destination.</> },
};

export function HelpLabel({ label, help, heading = false }: {
  label: string; help: AgentHelpKey; heading?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const content = <>
    {label && <span>{label}</span>}
    <button type="button" className="field-help" aria-label="Explain this setting"
            title={`About ${label || HELP[help].title}`}
            onClick={() => setOpen(true)}>?</button>
    <InfoDialog open={open} title={HELP[help].title} onClose={() => setOpen(false)}>
      <div className="agent-field-help-body">{HELP[help].body}</div>
      <p><Link to="/help/agents" onClick={() => setOpen(false)}>Read the full Agents guide →</Link></p>
    </InfoDialog>
  </>;
  return heading ? <h2 className="help-heading">{content}</h2>
    : <span className={label ? "field-label help-label" : "help-label help-only"}>{content}</span>;
}
