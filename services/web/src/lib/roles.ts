// Legacy role descriptions still used by the agent editor. The API key picker
// has a separate list below because a key role is not an execution profile.
export const ROLE_DESC: Record<string, string> = {
  reader: "Read-only: view agents, runs, schedules, and changes.",
  annotator: "Reader + annotate runs and write memories (system agents).",
  operator: "Reader + trigger runs and fire webhooks.",
  dev: "Dev run: shell + repo clone on the Workbench, publishes PRs through the platform, "
    + "holds no git credential.",
  admin: "Full control: secrets, API keys, merges, and settings.",
};

// Long-lived keys use API authorization roles, not agent execution profiles.
export const API_KEY_ROLES = ["reader", "operator", "admin"] as const;
export const API_KEY_ROLE_DESC: Record<(typeof API_KEY_ROLES)[number], string> = {
  reader: "View platform data without changing it.",
  operator: "View data, start agent runs, and send Relay messages. Best for an MCP client.",
  admin: "Manage everything, including agents, secrets, API keys, and schedules.",
};
