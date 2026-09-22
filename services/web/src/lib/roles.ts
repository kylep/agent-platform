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
export const API_KEY_ROLES = ["reader", "annotator", "operator", "admin"] as const;
export const API_KEY_ROLE_DESC: Record<(typeof API_KEY_ROLES)[number], string> = {
  reader: "View agents, runs, schedules, reports, and other platform data. Cannot change anything.",
  annotator: "Reader access, plus run annotations, reports, notifications, and memories. Cannot start runs.",
  operator: "Annotator access, plus start runs and send Relay messages. Cannot manage platform settings.",
  admin: "Full platform access, including agents, secrets, API keys, schedules, and merges.",
};
