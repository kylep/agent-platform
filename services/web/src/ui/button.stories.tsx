import type { Meta, StoryObj } from "@storybook/react-vite";
import { Button } from "./button";

const meta: Meta<typeof Button> = { title: "UI/Button", component: Button };
export default meta;

export const Variants: StoryObj = {
  render: () => (
    <div className="row-actions">
      <Button>Accept</Button>
      <Button variant="secondary">Discard edits</Button>
      <Button variant="danger">Delete conversation</Button>
      <Button variant="link">review &amp; accept under Changes</Button>
      <Button disabled>Saving…</Button>
      <Button variant="secondary" size="sm">Verify</Button>
    </div>
  ),
};
