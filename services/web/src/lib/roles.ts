// The auth roles, in one sentence each — what the Settings key minter and the
// agent editor's role select both say. Mirrors agentplatform.api.auth.ROLES
// minus `tools` and `relay`, which are derived at launch and never chosen.
export const ROLE_DESC: Record<string, string> = {
  reader: "Read-only: view agents, runs, schedules, and changes.",
  annotator: "Reader + annotate runs and write memories (system agents).",
  operator: "Reader + trigger runs and fire webhooks.",
  coder: "Operator + edit agents (self-edit / open PRs).",
  dev: "Dev run: shell + repo clone on the Workbench, publishes PRs through the platform, "
    + "holds no git credential.",
  admin: "Full control: secrets, API keys, merges, and settings.",
};
