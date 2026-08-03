import type { Meta, StoryObj } from "@storybook/react-vite";
import { Banner } from "./banner";

const meta: Meta<typeof Banner> = { title: "UI/Banner", component: Banner };
export default meta;

export const Variants: StoryObj = {
  render: () => (
    <div style={{ maxWidth: 560 }}>
      <Banner>This agent has a pending change (PR #12) — review &amp; accept it under Changes.</Banner>
      <Banner variant="ok">✓ Live — showing the current definition.</Banner>
      <Banner variant="danger">blocked: skill `discord` disabled — secret `discord-webhook` is not set</Banner>
    </div>
  ),
};
