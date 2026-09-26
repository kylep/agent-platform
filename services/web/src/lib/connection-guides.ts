// These are product instructions, not a secret registry. The registry remains
// secrets/*/secret.yaml; an unknown declaration stays available under Advanced.
export type ConnectionGuide = {
  title: string;
  mark: string;
  purpose: string;
  secrets: string[];
  steps: string[];
  docs: { label: string; url: string };
};

export const GUIDE_CHECKED = "2026-09-26";

export const CONNECTION_GUIDES: ConnectionGuide[] = [
  { title: "Discord chat identities", mark: "D", purpose: "Bots that post and receive Discord messages. Each account has its own token and outbound agent assignment.",
    secrets: ["discord-bot"], steps: [
      "Open the Discord Developer Portal and select an application, or create one.",
      "On Bot, reset or copy the bot token. Turn on Message Content Intent for conversational messages.",
      "On Installation, configure Guild Install with the bot scope and the channel permissions the bot needs, then install it in your server.",
      "Paste the bot token here. For another account, use Add account; it creates a separate secret and paused identity together.",
    ], docs: { label: "Discord setup guide", url: "https://docs.discord.com/developers/quick-start/getting-started" } },
  { title: "OpenAI image generation", mark: "O", purpose: "API-key image generation in Studio, separate from the Codex subscription allowance.",
    secrets: ["openai-api-key"], steps: ["Create an API key in the OpenAI Platform dashboard.", "Copy the key once and paste it into OPENAI_API_KEY below."],
    docs: { label: "OpenAI API quickstart", url: "https://platform.openai.com/docs/quickstart/make-your-first-api-request" } },
  { title: "Gemini image generation", mark: "G", purpose: "Gemini image models in Studio.", secrets: ["gemini-api-key"],
    steps: ["Open Google AI Studio's API Keys page.", "Create or select an API key for your project and paste it into GEMINI_API_KEY below."],
    docs: { label: "Gemini API keys", url: "https://ai.google.dev/gemini-api/docs/api-key" } },
  { title: "Black Forest Labs image generation", mark: "B", purpose: "FLUX image models in Studio.", secrets: ["bfl-api-key"],
    steps: ["Open the Black Forest Labs dashboard and create an API key.", "Paste it into BFL_API_KEY below."],
    docs: { label: "BFL account and API keys", url: "https://help.bfl.ai/articles/2699147243-managing-your-account" } },
  { title: "Strava", mark: "S", purpose: "Running activities and coaching data.", secrets: ["strava"],
    steps: ["Create or open your API application at Strava Settings → My API Application; copy its client ID and secret.",
      "Authorize your account for read, activity:read_all, and profile:read_all, then exchange the code for an access/refresh token pair.",
      "Paste all four seed values below. The connector stores later rotating tokens itself; do not reuse an old refresh token."],
    docs: { label: "Strava API authentication", url: "https://developers.strava.com/docs/authentication/" } },
  { title: "Linear", mark: "L", purpose: "Read and write Linear issues and projects.", secrets: ["linear-api-key"],
    steps: ["In Linear, open Settings → Account → Security & access → Personal API keys.", "Create a key and paste it into LINEAR_API_KEY below."],
    docs: { label: "Linear API and webhooks", url: "https://linear.app/docs/api-and-webhooks" } },
  { title: "GitHub App", mark: "GH", purpose: "Platform change publishing and pull requests.", secrets: ["github-app"],
    steps: ["Open your GitHub App settings and copy its App ID.", "Open its installation page and copy the installation ID from the URL.",
      "Generate a private key under the app settings, download the PEM, and paste its complete contents below."],
    docs: { label: "GitHub App private keys", url: "https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/managing-private-keys-for-github-apps" } },
  { title: "Claude subscription", mark: "C", purpose: "Claude Code runner authentication.", secrets: ["claude-credentials"],
    steps: ["Run claude setup-token on your own machine.", "Paste the resulting token below; the platform proxy holds it for agent runs."],
    docs: { label: "Claude Code authentication", url: "https://code.claude.com/docs/en/authentication" } },
  { title: "Codex subscription", mark: "Cx", purpose: "Codex runner authentication with your ChatGPT account.", secrets: ["codex-credentials"],
    steps: ["Run codex login locally with your ChatGPT account.", "Paste the complete contents of ~/.codex/auth.json into auth.json below. The broker persists refreshes."],
    docs: { label: "Codex quickstart", url: "https://learn.chatgpt.com/docs/quickstart" } },
];

export const ADVANCED_SECRET_GUIDES: Record<string, { steps: string[]; url?: string }> = {
  "discord-webhook": { steps: [
    "In Discord, open the destination channel's settings and create or select a webhook under Integrations.",
    "Copy its webhook URL and paste it as DISCORD_WEBHOOK_URL. This is a legacy sender; Chat Identities use bot tokens instead.",
  ], url: "https://support.discord.com/hc/en-us/articles/228383668-Intro-to-Webhooks" },
  "github-token": { steps: [
    "In GitHub Settings → Developer settings, create a personal access token with only the repository access this legacy git integration needs.",
    "Copy the token and paste it as GITHUB_TOKEN. Prefer the GitHub App connection for platform publishing.",
  ], url: "https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens" },
  "qa-web-login": { steps: [
    "The platform generates this login at boot. There is nothing to collect or paste.",
    "If it is missing, use the documented QA principal recovery procedure rather than setting an arbitrary password here.",
  ] },
};
