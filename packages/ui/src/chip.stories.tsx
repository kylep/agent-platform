import type { Meta, StoryObj } from "@storybook/react-vite";
import { useState } from "react";
import { Chip, ChipButton, StatusChip } from "./chip";

const meta: Meta = { title: "UI/Chip" };
export default meta;

export const Statuses: StoryObj = {
  render: () => (
    <div className="chip-row">
      {["valid", "unprobed", "missing", "invalid", "succeeded", "running",
        "rejected", "blocked", "quarantined", "archived"].map((s) => (
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

export const Variants: StoryObj = {
  render: () => (
    <div className="flex flex-wrap gap-2">
      <Chip>Unassigned</Chip>
      <Chip variant="ok">Ready</Chip>
      <Chip variant="warn">Pending</Chip>
      <Chip variant="danger">Blocked</Chip>
      <Chip variant="accent">Required</Chip>
    </div>
  ),
};

export const Toggle: StoryObj = {
  render: function Render() {
    const [pressed, setPressed] = useState(false);
    return (
      <ChipButton aria-pressed={pressed} variant={pressed ? "accent" : "neutral"}
                  onClick={() => setPressed(!pressed)}>
        Failures only
      </ChipButton>
    );
  },
};

export const Disabled: StoryObj = {
  render: () => <ChipButton disabled>History unavailable</ChipButton>,
};
