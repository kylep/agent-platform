import type { Meta, StoryObj } from "@storybook/react-vite";
import { Markdown } from "./markdown";

const meta: Meta<typeof Markdown> = { title: "UI/Markdown", component: Markdown };
export default meta;

const runSummary = [
  "## Run summary",
  "",
  "The health check passed.",
  "The next check starts in five minutes.",
  "",
  "- API responded successfully",
  "- No runs are waiting in the queue",
  "",
  "```text",
  "status: healthy",
  "```",
].join("\n");

export const Chat: StoryObj<typeof Markdown> = {
  args: { text: runSummary },
};

export const Flow: StoryObj<typeof Markdown> = {
  args: { text: runSummary, flow: true },
};

export const Sanitized: StoryObj<typeof Markdown> = {
  args: { text: "The run finished **successfully**. <script>unsafe()</script><img src='/image'>" },
};
