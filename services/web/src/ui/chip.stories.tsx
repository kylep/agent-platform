import type { Meta, StoryObj } from "@storybook/react-vite";
import { Chip, ChipButton, StatusChip } from "./chip";

const meta: Meta = { title: "UI/Chip" };
export default meta;

export const Statuses: StoryObj = {
  render: () => (
    <div className="chip-row">
      {["valid", "unprobed", "missing", "invalid", "succeeded", "running",
        "rejected", "blocked", "quarantined"].map((s) => (
        <StatusChip key={s} status={s} />
      ))}
      <Chip variant="accent">required</Chip>
      <Chip>agent: news</Chip>
    </div>
  ),
};

export const Clickable: StoryObj = {
  render: () => (
    <div className="chip-row">
      <ChipButton variant="ok">14d</ChipButton>
      <ChipButton>30d</ChipButton>
      <ChipButton>log ⇄</ChipButton>
      <ChipButton style={{ color: "var(--ds-chart-1)", borderColor: "var(--ds-chart-1)" }}>
        ● health-monitor
      </ChipButton>
    </div>
  ),
};
